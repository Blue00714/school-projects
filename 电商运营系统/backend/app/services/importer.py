"""OWNER: data-api — Excel/CSV ingestion pipeline for uploaded data batches.

The upload endpoint only stores the bytes and the request row; the actual work runs in a
background thread so the browser gets ``202`` immediately and polls
``GET /api/imports/jobs/{id}``:

1. parse the workbook/CSV into DataFrames (read-only on the uploaded bytes);
2. reject a file whose SHA-256 already belongs to a ready batch (``duplicate``);
3. run the *same* cleaning policy as ``app.services.data.prepare`` via
   ``app.services.data.build_from_frames`` (cross-sheet copies, sales flags, integer amount
   units) into ``runtime/batches/<batch_id>/data.sqlite3`` (MySQL mode: the
   ``ece_batch_<batch_id>`` database);
4. register the batch with ``kind='upload'`` and **without** activating it;
5. persist the manifest counters, human-readable issues and final status on the job.

Nothing here touches ``runtime/retail.sqlite3`` or the original Excel file.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from app.services import data as data_service
from app.services.data import COLS, DataError

from app.core import config
from app import db
from app.services import batches as batch_service

log = logging.getLogger('ece.imports')

MAX_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = ('.xlsx', '.xls', '.csv')
ISSUE_LIMIT = 200
ISSUE_PER_CATEGORY = 20

# One import at a time: the batches/app databases are small SQLite files and a sequential
# pipeline keeps progress deterministic and write contention away.
_IMPORT_LOCK = threading.Lock()


def _noop(*_args, **_kwargs) -> None:
    pass


def new_batch_id() -> str:
    return 'b' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + uuid4().hex[:6]


def _short_text(value, limit: int = 200) -> str:
    return str(value or '').strip()[:limit]


def _insert_job(job_id: str, file_name: str, source_sha256: str, *, status: str, user_id,
                batch_id: str | None = None, report: dict | None = None,
                issues: list | None = None, error: str | None = None,
                finished: bool = False) -> None:
    with db.connect() as cx:
        cx.execute(
            'insert into import_jobs(id,file_name,source_sha256,status,batch_id,report_json,'
            'issues_json,error,created_by,created_at,finished_at) values(?,?,?,?,?,?,?,?,?,?,?)',
            (job_id, file_name, source_sha256, status, batch_id,
             json.dumps(report or {}, ensure_ascii=False),
             json.dumps(issues or [], ensure_ascii=False), error, user_id,
             db.now_iso(), db.now_iso() if finished else None))


def _finish_job(job_id: str, *, status: str, batch_id: str | None = None,
                report: dict | None = None, issues: list | None = None,
                error: str | None = None) -> None:
    with db.connect() as cx:
        cx.execute(
            'update import_jobs set status=?,batch_id=?,report_json=?,issues_json=?,error=?,finished_at=? '
            'where id=?',
            (status, batch_id, json.dumps(report or {}, ensure_ascii=False),
             json.dumps(issues or [], ensure_ascii=False), error, db.now_iso(), job_id))


def _set_status(job_id: str, status: str) -> None:
    with db.connect() as cx:
        cx.execute('update import_jobs set status=? where id=?', (status, job_id))


def get_job(job_id: str) -> dict | None:
    """Job row with parsed ``report``/``issues`` (``None`` when unknown)."""
    with db.connect() as cx:
        row = cx.execute('select * from import_jobs where id=?', (job_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data['report'] = json.loads(data.pop('report_json') or '{}')
    data['issues'] = json.loads(data.pop('issues_json') or '[]')
    return data


def _store_source(job_id: str, file_name: str, content: bytes) -> Path:
    folder = config.RUNTIME_DIR / 'imports' / job_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(file_name).suffix.lower()
    target = folder / f'source{suffix}'
    target.write_bytes(content)
    return target


def submit(*, file_name: str, content: bytes, label: str = '', user_id=None) -> dict:
    """Persist the upload and start the background import; returns the 202 payload."""
    file_name = _short_text(Path(_short_text(file_name, 400).replace('\\', '/')).name, 200) or 'upload'
    digest = sha256(content).hexdigest()
    job_id = uuid4().hex
    existing = batch_service.find_batch_by_hash(digest)
    if existing:
        report = {'duplicate': True, 'batch_id': existing['id'], 'source_sha256': digest,
                  'manifest': existing.get('manifest') or {}}
        _insert_job(job_id, file_name, digest, status='duplicate', user_id=user_id,
                    batch_id=existing['id'], report=report, finished=True)
        return {'job_id': job_id, 'status': 'duplicate', 'batch_id': existing['id']}
    _insert_job(job_id, file_name, digest, status='queued', user_id=user_id)
    source_path = _store_source(job_id, file_name, content)
    thread = threading.Thread(
        target=_run,
        args=(job_id, source_path, file_name, _short_text(label, 120), digest, user_id),
        name=f'import-{job_id[:8]}', daemon=True)
    thread.start()
    return {'job_id': job_id, 'status': 'queued'}


def _run(job_id: str, source_path: Path, file_name: str, label: str, digest: str, user_id) -> None:
    with _IMPORT_LOCK:
        _set_status(job_id, 'running')
        begun = time.monotonic()
        batch_id = None
        batch_dir = None
        registered = False
        try:
            existing = batch_service.find_batch_by_hash(digest)
            if existing:
                _finish_job(job_id, status='duplicate', batch_id=existing['id'], report={
                    'duplicate': True, 'batch_id': existing['id'], 'source_sha256': digest})
                return
            sheets = read_sheets(source_path)
            _validate_sheets(sheets)
            batch_id = new_batch_id()
            if db.is_mysql():
                # MySQL mode: one database per batch, e.g. ece_batch_b20261029T...
                batch_dir = None
                db_path = db.MySQLTarget(db.batch_database(batch_id))
            else:
                batch_dir = config.BATCH_DIR / batch_id
                db_path = batch_dir / 'data.sqlite3'
            manifest, result = data_service.build_from_frames(
                sheets, db_path, source_file=file_name, source_sha256=digest,
                source_kind='upload', progress=_noop, begun=begun)
            issues = collect_issues(result, manifest)
            if not manifest['positive_sale_rows']:
                raise DataError('文件中没有可用于统计的正向销售记录（需数量>0、非取消、日期有效、非跨表副本）')
            if str(manifest['date_min']) in ('', 'nan', 'NaT', 'None'):
                raise DataError('文件中没有可解析的交易日期，请核对 InvoiceDate 列格式')
            batch_service.register_batch(
                batch_id=batch_id, label=label or f'上传 {file_name}', kind='upload',
                db_path=db_path, source_file=file_name, source_sha256=digest,
                policy_version=manifest['policy_version'], manifest=manifest,
                status='ready', created_by=user_id)
            registered = True
            _finish_job(job_id, status='succeeded', batch_id=batch_id, report=manifest, issues=issues)
            log.info('import %s succeeded -> batch %s (%.1fs)', job_id, batch_id,
                     time.monotonic() - begun)
        except Exception as exc:  # noqa: BLE001 - failure must be reported, never masked
            if not registered:
                if batch_dir is not None:
                    shutil.rmtree(batch_dir, ignore_errors=True)
                if batch_id and db.is_mysql():
                    try:  # never leave a half-written batch database behind
                        db.drop_database(db.batch_database(batch_id))
                    except (sqlite3.Error, ValueError, RuntimeError):
                        pass
            error = str(exc) if isinstance(exc, DataError) else f'{type(exc).__name__}: {exc}'
            if not isinstance(exc, DataError):
                log.exception('import %s failed', job_id)
            _finish_job(job_id, status='failed', error=error,
                        issues=[{'level': 'error', 'row': None, 'field': 'file', 'message': error}])
            log.warning('import %s failed: %s', job_id, error)


# --------------------------------------------------------------------------- parsing

def _read_csv(path: Path) -> pd.DataFrame:
    last_error = None
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            head = pd.read_csv(path, nrows=0, encoding=encoding)
        except UnicodeDecodeError as exc:  # try the next encoding
            last_error = exc
            continue
        except pd.errors.EmptyDataError:
            raise DataError('CSV 文件为空或缺少表头') from None
        except Exception as exc:  # noqa: BLE001
            raise DataError(f'CSV 文件无法解析：{exc}') from None
        columns = [str(col).strip() for col in head.columns]
        dtype = {col: str for col in ('Invoice', 'StockCode') if col in columns}
        try:
            frame = pd.read_csv(path, encoding=encoding, dtype=dtype or None)
        except Exception as exc:  # noqa: BLE001
            raise DataError(f'CSV 文件无法解析：{exc}') from None
        frame.columns = columns
        return frame
    raise DataError(f'CSV 文件编码无法识别（支持 UTF-8 / GB18030）：{last_error}')


def _read_workbook(path: Path, engine) -> dict:
    try:
        sheets = pd.read_excel(path, sheet_name=None, engine=engine,
                               dtype={'Invoice': str, 'StockCode': str})
    except ImportError as exc:
        raise DataError(f'读取 {path.suffix} 文件缺少依赖（{exc}）；请另存为 .xlsx 后重试') from None
    except Exception as exc:  # noqa: BLE001
        raise DataError(f'Excel 文件无法解析：{exc}') from None
    if not isinstance(sheets, dict):
        sheets = {'Sheet1': sheets}
    return {str(name): frame for name, frame in sheets.items() if len(frame.columns) > 0}


def read_sheets(path: Path) -> dict:
    """Load an uploaded file into ``{sheet_name: DataFrame}`` with stripped column names."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.csv':
        sheets = {'CSV': _read_csv(path)}
    elif suffix == '.xlsx':
        sheets = _read_workbook(path, 'openpyxl')
    elif suffix == '.xls':
        sheets = _read_workbook(path, None)
    else:
        raise DataError('仅支持 .xlsx/.xls/.csv 文件')
    cleaned = {}
    for name, frame in sheets.items():
        frame = frame.copy()
        frame.columns = [str(col).strip() for col in frame.columns]
        cleaned[name] = frame
    return cleaned


def _validate_sheets(sheets: dict) -> None:
    if not sheets:
        raise DataError('文件中没有可读取的数据表')
    missing = {}
    for name, frame in sheets.items():
        lack = [col for col in COLS if col not in frame.columns]
        if lack:
            missing[name] = lack
    if missing:
        detail = '；'.join(f'{name} 缺少列 {", ".join(cols)}' for name, cols in missing.items())
        raise DataError(f'缺少必要字段：{detail}。示例表头见 /api/imports/template.csv')
    if not any(len(frame) for frame in sheets.values()):
        raise DataError('文件中没有数据行（只有表头）')


# --------------------------------------------------------------------------- issues

def _int_row(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number


def collect_issues(result: pd.DataFrame, manifest: dict, limit: int = ISSUE_LIMIT,
                   per_category: int = ISSUE_PER_CATEGORY) -> list:
    """Human-readable quality report for an accepted file (counts also live in the manifest)."""
    issues: list = []

    def add(level: str, field: str, message: str, mask=None) -> None:
        if mask is None:
            return
        rows = result.loc[mask]
        if rows.empty:
            return
        for _, row in rows.head(per_category).iterrows():
            row_no = _int_row(row.get('source_row'))
            issues.append({
                'level': level, 'row': row_no, 'field': field,
                'message': f'工作表 {row.get("sheet", "")} 第 {row_no} 行：{message}',
            })

    quantity = result['quantity']
    qty_invalid = quantity.isna() | ~np.isfinite(quantity.fillna(0))
    add('warning', 'InvoiceDate', '日期无法解析，该行不计入销量与销售额', mask=result['date'].isna())
    add('warning', 'Quantity', '数量无法解析为数字，该行不参与统计', mask=qty_invalid)
    add('warning', 'Quantity', '数量小于等于0，不计入正向销量', mask=quantity.le(0))
    add('info', 'Invoice', '取消/负向交易行，不计入正向销量', mask=result['cancelled'].eq(1))
    add('warning', 'Price', '单价缺失或为负，销售额不含该行',
        mask=result['sale'].eq(1) & result['amount_eligible'].eq(0))
    add('info', 'Price', '单价为0的正向销售行（销售额计0，销量保留）',
        mask=result['sale'].eq(1) & result['unit_price'].eq(0))
    add('info', 'Duplicate', '同表重复候选（按口径保留，不排除）',
        mask=result['within_duplicate_candidate'].eq(1))
    add('info', 'Duplicate', '跨表重复副本（已排除）', mask=result['cross_duplicate'].eq(1))
    if len(issues) > limit:
        issues = issues[:limit]
        issues.append({'level': 'info', 'row': None, 'field': '',
                       'message': f'问题条目过多，仅显示前 {limit} 条；完整计数见 report 统计字段'})
    return issues
