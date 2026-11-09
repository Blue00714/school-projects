"""Read-only source ingestion, lineage and a local analytical cache.

Two storage backends are supported through the dialect layer in ``api/db.py``:

* SQLite (default/tests): one database file per data batch;
* MySQL 8 (``ECE_DB_BACKEND=mysql``): one *database* per data batch
  (``ece_original`` for the original workbook, ``ece_batch_<id>`` for uploads).

Everything else — the cleaning policy, manifest counters, analytical SQL — is shared, so
``manifest()``/``series()`` and every consumer behave identically on both backends. The
dialect module is imported lazily so this package stays usable from plain CLI scripts.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import pandas as pd

from app.core.config import APP_ROOT as ROOT
DEFAULT_SOURCE = ROOT.parent / '20261014_电商数据与页面选型' / 'online_retail_II.xlsx'
DEFAULT_DB = ROOT / 'runtime' / 'retail.sqlite3'
POLICY = 'cross-sheet-multiset-v1-sales-v2-invoice-order'
COLS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']
RULES = [
    '仅排除跨表完全相同记录的重复副本，按出现次数匹配；同表重复候选保留。',
    '正向销量：日期/编号有效、数量大于0、非取消、非跨表副本。',
    '交易销售额：正向交易中单价有效且非负的行，数量×英镑单价；零单价保留。',
    '订单数：按订单号去重。正向订单号已核查无跨日期、客户、国家冲突；同单分钟级时间差不拆单。',
    '无记录日补0仅是构造连续记录销量，不代表已确认营业或市场需求为0。',
]
# Standard auxiliary objects of an analytical database; also makes order-level
# aggregation (group by order_key) cheap on freshly built or uploaded batches.
AUX_SQL = '''
    CREATE INDEX ix_transactions_date ON transactions(date);
    CREATE INDEX ix_transactions_sale ON transactions(sale,date);
    CREATE INDEX ix_transactions_stock ON transactions(stock_code,date);
    CREATE INDEX ix_transactions_country ON transactions(country,date);
    CREATE INDEX ix_transactions_order ON transactions(order_key);
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE forecast_runs(id TEXT PRIMARY KEY,created_at TEXT,origin TEXT,method TEXT,payload TEXT);
'''
# MySQL 8 dialect of the same layout: TEXT keys -> VARCHAR, TEXT payloads -> MEDIUMTEXT.
# The column order/types keep every shared query and the manifest counters identical.
TRANSACTIONS_DDL_MYSQL = '''
CREATE TABLE IF NOT EXISTS transactions(
    invoice VARCHAR(255) NULL,
    stock_code VARCHAR(255) NULL,
    description TEXT NULL,
    quantity DOUBLE NULL,
    unit_price DOUBLE NULL,
    invoice_time VARCHAR(32) NULL,
    date VARCHAR(16) NULL,
    customer_id VARCHAR(255) NULL,
    country VARCHAR(255) NULL,
    sheet VARCHAR(255) NULL,
    source_row BIGINT NULL,
    cross_duplicate BIGINT NULL,
    within_duplicate_candidate BIGINT NULL,
    cancelled BIGINT NULL,
    sale BIGINT NULL,
    amount_eligible BIGINT NULL,
    amount_units BIGINT NULL,
    order_key VARCHAR(255) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
'''
AUX_SQL_MYSQL = '''
    CREATE INDEX ix_transactions_date ON transactions(date);
    CREATE INDEX ix_transactions_sale ON transactions(sale,date);
    CREATE INDEX ix_transactions_stock ON transactions(stock_code,date);
    CREATE INDEX ix_transactions_country ON transactions(country,date);
    CREATE INDEX ix_transactions_order ON transactions(order_key);
    CREATE TABLE IF NOT EXISTS metadata(`key` VARCHAR(64) NOT NULL PRIMARY KEY, value MEDIUMTEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS forecast_runs(id VARCHAR(64) NOT NULL PRIMARY KEY, created_at VARCHAR(40),
        origin VARCHAR(40), method VARCHAR(64), payload MEDIUMTEXT);
'''
MYSQL_INSERT_CHUNK = 2000
MANIFEST_SQL = 'select value from metadata where `key`=?'


class DataError(ValueError):
    pass


# --------------------------------------------------------------------------- dialect bridge


def _dialect():
    """The shared dialect layer (``api/db.py``), imported lazily to avoid import cycles."""
    from app import db as dialect  # noqa: PLC0415 - lazy on purpose

    return dialect


def resolve_target(db=None) -> tuple[str, object]:
    """Return ``('sqlite', Path)`` or ``('mysql', database_name)`` for a store target."""
    dialect = _dialect()
    if db is None:
        if dialect.is_mysql():
            return 'mysql', dialect.config.MYSQL_ORIGINAL_DB
        return 'sqlite', Path(DEFAULT_DB)
    return dialect.resolve_target(db)


def _target_value(kind: str, value):
    return _dialect().MySQLTarget(value) if kind == 'mysql' else Path(value)


def _connect_target(kind: str, value):
    return _dialect().connect(_target_value(kind, value))


def _target_ready(kind: str, value) -> bool:
    dialect = _dialect()
    if kind == 'mysql':
        return dialect.mysql_database_exists(str(value))
    return Path(value).is_file()


def file_hash(path: Path) -> str:
    h = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identifier(value) -> str:
    if pd.isna(value):
        return ''
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def clean_frames(sheets: dict, progress=print) -> tuple[pd.DataFrame, list, pd.DataFrame]:
    """Apply the documented preparation policy to already-loaded worksheets.

    ``sheets`` maps worksheet name -> DataFrame using the original column names
    (``COLS``). The mapping is consumed (each frame is released after it has been
    copied) so a large workbook never holds two full copies in memory.

    Returns ``(result, sheet_stats, raw)``. This is pure DataFrame work: it performs
    no I/O, so both the Excel rebuild path (``prepare``) and uploaded CSV/Excel files
    (``app.services.importer``) go through exactly the same cleaning rules.
    """
    frames, sheet_stats = [], []
    for name in list(sheets.keys()):
        frame = sheets.pop(name)
        if not set(COLS).issubset(frame.columns):
            raise DataError(f'{name} 缺少必要字段')
        frame = frame[COLS].copy()
        frame['sheet'] = str(name)
        frame['source_row'] = np.arange(2, len(frame) + 2)
        frames.append(frame)
        progress(f'{name}: {len(frame):,}行', flush=True)
    if not frames:
        raise DataError('文件中没有可读取的数据表')
    raw = pd.concat(frames, ignore_index=True)
    # Full-column equality, not a saved hash, establishes cross-sheet copies.
    # Match nth occurrence across sheets; retain genuine within-sheet repeats.
    canonical = raw[COLS+['sheet']].copy()
    for col in ['Invoice','StockCode','Description','Customer ID','Country']:
        canonical[col] = canonical[col].map(identifier)
    canonical['InvoiceDate'] = pd.to_datetime(canonical['InvoiceDate'],errors='coerce')
    canonical['occurrence'] = canonical.groupby(COLS+['sheet'],dropna=False,sort=False).cumcount()
    raw['cross_duplicate'] = canonical.duplicated(subset=COLS+['occurrence']).astype(int)
    raw['within_duplicate_candidate'] = canonical.duplicated(subset=COLS+['sheet']).astype(int)
    for name, group in raw.groupby('sheet',sort=False):
        copies = int(group['cross_duplicate'].sum())
        sheet_stats.append({'sheet':name,'raw_rows':len(group),'cross_sheet_copies_excluded':copies})
        progress(f'{name}: 排除跨表副本{copies:,}行',flush=True)
    del canonical, frames
    result = pd.DataFrame()
    result['invoice'] = raw['Invoice'].map(identifier)
    result['stock_code'] = raw['StockCode'].map(identifier)
    result['description'] = raw['Description'].map(identifier)
    result['quantity'] = pd.to_numeric(raw['Quantity'], errors='coerce')
    result['unit_price'] = pd.to_numeric(raw['Price'], errors='coerce')
    dates = pd.to_datetime(raw['InvoiceDate'], errors='coerce')
    result['invoice_time'] = dates.dt.strftime('%Y-%m-%d %H:%M:%S')
    result['date'] = dates.dt.strftime('%Y-%m-%d')
    result['customer_id'] = raw['Customer ID'].map(identifier)
    result['country'] = raw['Country'].map(identifier)
    for key in ['sheet', 'source_row', 'cross_duplicate', 'within_duplicate_candidate']:
        result[key] = raw[key]
    result['cancelled'] = result['invoice'].str.upper().str.startswith('C').astype(int)
    valid = result['date'].notna() & result['quantity'].notna() & np.isfinite(result['quantity']) & result['invoice'].ne('') & result['stock_code'].ne('')
    result['sale'] = (valid & result['quantity'].gt(0) & result['cancelled'].eq(0) & result['cross_duplicate'].eq(0)).astype(int)
    result['amount_eligible'] = (result['sale'].eq(1) & result['unit_price'].notna() & np.isfinite(result['unit_price']) & result['unit_price'].ge(0)).astype(int)
    # Preserve 4 decimal places of GBP per transaction line, sum integer units.
    amounts = (result['quantity'] * result['unit_price'] * 10000).round()
    result['amount_units'] = amounts.where(result['amount_eligible'].eq(1), 0).astype('int64')
    result['order_key'] = result['invoice']
    return result, sheet_stats, raw


def _manifest_identity_conflicts(cx) -> int:
    """Cross-date/customer/country conflicts of the positive-sale order key.

    MySQL requires a derived-table alias (SQLite tolerates its absence).
    """
    return cx.execute(
        "select count(*) from (select invoice from transactions where sale=1 group by invoice "
        "having count(distinct date)>1 or count(distinct country)>1 or "
        "count(distinct nullif(customer_id,''))>1) as conflicting_orders").fetchone()[0]


def _manifest_payload(result: pd.DataFrame, raw: pd.DataFrame, sheet_stats: list,
                      source_file: str, source_sha256: str, source_kind: str,
                      order_conflicts: int, identity_conflicts: int,
                      started: float) -> dict:
    useful = result[result['cross_duplicate'].eq(0)]
    return {
        'source_file': source_file, 'source_sha256': source_sha256, 'source_kind': source_kind,
        'official_byte_comparison_completed': False, 'policy_version': POLICY,
        'prepared_at': datetime.now().isoformat(timespec='seconds'), 'sheets': sheet_stats,
        'raw_rows': len(raw), 'cross_sheet_copies_excluded': int(result['cross_duplicate'].sum()),
        'retained_rows': len(useful), 'positive_sale_rows': int(result['sale'].sum()),
        'within_sheet_duplicate_candidates_retained': int(useful['within_duplicate_candidate'].sum()),
        'cancelled_rows': int(useful['cancelled'].sum()),
        'nonpositive_quantity_rows': int(useful['quantity'].le(0).sum()),
        'invalid_dates': int(useful['date'].isna().sum()),
        'quantity_invalid_rows': int((~np.isfinite(useful['quantity'])).sum()),
        'sale_rows_without_valid_price': int(((result['sale']==1)&(result['amount_eligible']==0)).sum()),
        'zero_price_sale_rows': int(((result['sale']==1)&(result['unit_price']==0)).sum()),
        'invoice_with_multiple_times': int(order_conflicts),
        'order_identity_conflicts': int(identity_conflicts), 'order_key': 'invoice',
        'date_min': str(result.loc[result['date'].notna(), 'date'].min()),
        'date_max': str(result.loc[result['date'].notna(), 'date'].max()),
        'elapsed_seconds': round(time.monotonic()-started, 2),
        'rules': list(RULES),
    }


def _insert_transactions_mysql(cx, result: pd.DataFrame, progress) -> None:
    """Chunked ``executemany`` insert into a MySQL analytical database."""
    dialect = _dialect()
    columns = list(result.columns)
    placeholders = ','.join('?' for _ in columns)
    statement = (f"insert into transactions({','.join('`' + column + '`' for column in columns)}) "
                 f'values({placeholders})')
    adapt = dialect.adapt_value
    total = len(result)
    step = max(MYSQL_INSERT_CHUNK, 1)
    done = 0
    for start in range(0, total, step):
        block = result.iloc[start:start + step]
        rows = [tuple(adapt(value) for value in row) for row in
                block.itertuples(index=False, name=None)]
        cx.executemany(statement, rows)
        done += len(rows)
        if progress is not None and (done == total or done % (step * 20) == 0):
            progress(f'写入 MySQL {done:,}/{total:,} 行', flush=True)


def build_from_frames(sheets: dict, db, *, source_file: str, source_sha256: str,
                      source_kind: str = 'upload', progress=print,
                      begun: float | None = None) -> tuple[dict, pd.DataFrame]:
    """Clean loaded worksheets, build a fresh analytical database, return (manifest, result).

    Shared by :func:`prepare` (whole original workbook) and the upload importer
    (single CSV or workbook). The database layout, indexes and manifest fields are
    identical for both paths; ``source_kind`` only records where the bytes came from.
    """
    started = time.monotonic() if begun is None else begun
    kind, target = resolve_target(db)
    result, sheet_stats, raw = clean_frames(sheets, progress)
    useful = result[result['cross_duplicate'].eq(0)]
    order_conflicts = useful.groupby('invoice')['invoice_time'].nunique().gt(1).sum()
    if kind == 'mysql':
        dialect = _dialect()
        name = dialect.check_database_name(str(target))
        dialect.ensure_database(name)
        with _connect_target(kind, name) as cx:
            cx.execute('DROP TABLE IF EXISTS transactions')
            cx.execute('DROP TABLE IF EXISTS metadata')
            cx.execute('DROP TABLE IF EXISTS forecast_runs')
            cx.executescript(TRANSACTIONS_DDL_MYSQL)
            _insert_transactions_mysql(cx, result, progress)
            cx.executescript(AUX_SQL_MYSQL)
            identity_conflicts = _manifest_identity_conflicts(cx)
            if identity_conflicts:
                raise DataError('正向订单号存在跨日期、客户或国家冲突，需核查后确定订单键')
            manifest = _manifest_payload(result, raw, sheet_stats, source_file, source_sha256,
                                         source_kind, order_conflicts, identity_conflicts, started)
            cx.execute("insert into metadata(`key`,value) values(?,?)",
                       ('manifest', json.dumps(manifest, ensure_ascii=False)))
        return manifest, result

    path = Path(target).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.building.sqlite3')
    if temp.exists():
        temp.unlink()
    try:
        with closing(sqlite3.connect(temp)) as cx:
            result.to_sql('transactions', cx, index=False, chunksize=10000)
            cx.executescript(AUX_SQL)
            identity_conflicts = _manifest_identity_conflicts(cx)
            if identity_conflicts:
                raise DataError('正向订单号存在跨日期、客户或国家冲突，需核查后确定订单键')
            manifest = _manifest_payload(result, raw, sheet_stats, source_file, source_sha256,
                                         source_kind, order_conflicts, identity_conflicts, started)
            cx.execute('insert into metadata values (?,?)', ('manifest', json.dumps(manifest, ensure_ascii=False)))
            cx.commit()
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    temp.replace(path)
    (path.parent/'data_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest, result


def _source_manifest(kind: str, target, digest: str) -> dict | None:
    """Read the cached manifest if the source hash and policy version still match."""
    if not _target_ready(kind, target):
        return None
    try:
        with _connect_target(kind, target) as cx:
            row = cx.execute(MANIFEST_SQL, ('manifest',)).fetchone()
        meta = json.loads(row[0])
    except (sqlite3.Error, TypeError, KeyError, json.JSONDecodeError, IndexError):
        return None
    if meta.get('source_sha256') != digest:
        return None
    if meta.get('policy_version') == POLICY:
        return meta
    if meta.get('policy_version') == 'cross-sheet-multiset-v1-positive-sales-v1':
        # Documented cache migration: time differences within an invoice are not
        # separate orders. Recheck identity before merging counts.
        with _connect_target(kind, target) as cx:
            conflicts = _manifest_identity_conflicts(cx)
            if conflicts:
                raise DataError('正向订单号存在跨日期、客户或国家冲突，需核查后确定订单键')
            cx.execute('update transactions set order_key=invoice')
            meta['policy_version'] = POLICY
            meta['order_identity_conflicts'] = conflicts
            meta['order_key'] = 'invoice'
            meta['rules'] = [('订单数：按订单号去重。正向订单号已核查无跨日期、客户、国家冲突；同单分钟级时间差不拆单。' if r.startswith('订单数：') else r) for r in meta['rules']]
            cx.execute('update metadata set value=? where `key`=?',
                       (json.dumps(meta, ensure_ascii=False), 'manifest'))
        if kind == 'sqlite':
            (Path(target).parent/'data_manifest.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        return meta
    return None


def prepare(source=DEFAULT_SOURCE, db=None, rebuild=False, progress=print):
    source = Path(source).resolve()
    if not source.is_file():
        raise DataError(f'找不到数据文件：{source}')
    digest = file_hash(source)
    kind, target = resolve_target(db)
    if not rebuild:
        meta = _source_manifest(kind, target, digest)
        if meta is not None:
            return meta
    begun = time.monotonic()
    progress('读取交易数据，原始Excel保持只读…', flush=True)
    sheets = pd.read_excel(source, sheet_name=None, engine='openpyxl', dtype={'Invoice': str, 'StockCode': str})
    manifest, _ = build_from_frames(sheets, _target_value(kind, target), source_file=source.name,
                                    source_sha256=digest, source_kind='public_mirror',
                                    progress=progress, begun=begun)
    progress(f'本地缓存就绪，用时{manifest["elapsed_seconds"]}秒', flush=True)
    return manifest


class Store:
    """Read access to one analytical database (SQLite file or MySQL database)."""

    def __init__(self, db=None):
        self.kind, self.target = resolve_target(db)
        self.db = _target_value(self.kind, self.target)
        if not _target_ready(self.kind, self.target):
            raise DataError('数据未准备，请先运行 python manage.py prepare')

    @property
    def backend(self) -> str:
        return self.kind

    @property
    def database(self):
        return self.target if self.kind == 'mysql' else None

    def connect(self):
        return _connect_target(self.kind, self.target)

    def manifest(self):
        with self.connect() as cx:
            return json.loads(cx.execute(MANIFEST_SQL, ('manifest',)).fetchone()[0])

    def series(self):
        with self.connect() as cx:
            rows = cx.execute('select date,sum(quantity) qty from transactions where sale=1 group by date order by date').fetchall()
        s = pd.Series([r['qty'] for r in rows], index=pd.to_datetime([r['date'] for r in rows]), dtype=float)
        if s.empty:
            raise DataError('没有可预测的正向交易记录')
        meta = self.manifest()
        return s.reindex(pd.date_range(meta['date_min'], meta['date_max']), fill_value=0)
