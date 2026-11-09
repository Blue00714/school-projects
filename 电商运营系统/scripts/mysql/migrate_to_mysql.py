"""Migrate the existing SQLite analytical cache into MySQL 8 (``ece_original``).

Copying ``runtime/retail.sqlite3`` row-for-row is much faster than re-parsing the 1M-row
Excel workbook and keeps every counter identical to the SQLite prototype (same rows, same
manifest, same analytical SQL). Then the application state schema is created in
``ece_app`` (users seeded, ``original`` batch registered).

    python scripts/migrate_to_mysql.py [--source runtime/retail.sqlite3]
                                        [--database ece_original] [--chunk 5000]

Requires ``config/local/mysql.env`` (scripts/mysql/mysql_init.ps1) and a running mysqld on 3307.
Exit code 0 only when the row counts and the manifest match the source database.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYDEPS = ROOT / 'vendor/python-packages'
for candidate in (str(PYDEPS), str(ROOT / 'backend')):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pymysql  # noqa: E402  (after sys.path setup)

from app.core import config  # noqa: E402
from app import db as app_db  # noqa: E402
from app.services.data import AUX_SQL_MYSQL, TRANSACTIONS_DDL_MYSQL  # noqa: E402

DEFAULT_SOURCE = config.RUNTIME_DIR / 'retail.sqlite3'
EXPECTED = {'transactions': 1_067_371, 'sale_rows': 1_022_290}
WINDOW = {'start': '2011-11-10', 'end': '2011-12-09',
          'quantity': 851_403, 'amount_units': 16_940_687_100, 'orders': 2_913, 'lines': 86_861}


def connect_admin():
    if not config.MYSQL_PASSWORD:
        raise SystemExit('config/local/mysql.env missing or ECE_MYSQL_PASSWORD empty: '
                         'run scripts\\mysql\\mysql_init.ps1 first')
    return pymysql.connect(host=config.MYSQL_HOST, port=int(config.MYSQL_PORT),
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           charset='utf8mb4', autocommit=True, connect_timeout=10,
                           max_allowed_packet=256 * 1024 * 1024)


def sqlite_scalar(cx, sql, params=()):
    return cx.execute(sql, params).fetchone()[0]


def copy_transactions(source: Path, database: str, chunk: int) -> tuple[int, list[str]]:
    """Stream rows from SQLite into MySQL with chunked multi-row INSERTs."""
    with sqlite3.connect(str(source)) as src:
        src.row_factory = sqlite3.Row
        columns = [row['name'] for row in src.execute('PRAGMA table_info(transactions)')]
        if not columns:
            raise SystemExit(f'{source} has no transactions table')
        connection = connect_admin()
        try:
            with connection.cursor() as cur:
                cur.execute(f'DROP TABLE IF EXISTS `{database}`.transactions')
                cur.execute(f'DROP TABLE IF EXISTS `{database}`.metadata')
                cur.execute(f'DROP TABLE IF EXISTS `{database}`.forecast_runs')
                for statement in TRANSACTIONS_DDL_MYSQL.strip().rstrip(';').split(';'):
                    if statement.strip():
                        cur.execute(f'USE `{database}`')
                        cur.execute(statement)
                cur.execute(f'USE `{database}`')
                cur.execute('SET SESSION unique_checks=0')
                cur.execute('SET SESSION foreign_key_checks=0')
            insert = (f"INSERT INTO `{database}`.transactions"
                      f"({','.join('`' + column + '`' for column in columns)}) "
                      f"VALUES({','.join('%s' for _ in columns)})")
            cursor = src.execute(f"SELECT {','.join('`' + c + '`' for c in columns)} FROM transactions")
            connection.autocommit(False)
            done = 0
            batches = 0
            started = time.monotonic()
            while True:
                rows = cursor.fetchmany(chunk)
                if not rows:
                    break
                with connection.cursor() as cur:
                    cur.executemany(insert, [tuple(row) for row in rows])
                done += len(rows)
                batches += 1
                if batches % 10 == 0:
                    connection.commit()
                if batches % 40 == 0 or done == EXPECTED['transactions']:
                    elapsed = max(time.monotonic() - started, 1e-6)
                    print(f'  rows {done:,} ({done / elapsed:,.0f}/s)', flush=True)
            connection.commit()
            with connection.cursor() as cur:
                cur.execute(f'USE `{database}`')
                cur.execute('SET SESSION unique_checks=1')
                cur.execute('SET SESSION foreign_key_checks=1')
        finally:
            connection.close()
        return len(columns), columns


def copy_metadata(source: Path, database: str) -> dict:
    with sqlite3.connect(str(source)) as src:
        rows = src.execute('SELECT key, value FROM metadata').fetchall()
    connection = connect_admin()
    try:
        connection.autocommit(False)
        with connection.cursor() as cur:
            cur.execute(f'USE `{database}`')
            cur.execute('CREATE TABLE IF NOT EXISTS metadata('
                        '`key` VARCHAR(64) NOT NULL PRIMARY KEY, value MEDIUMTEXT NOT NULL)')
            for key, value in rows:
                cur.execute(
                    f'INSERT INTO `{database}`.metadata(`key`,value) VALUES(%s,%s) '
                    'ON DUPLICATE KEY UPDATE value=VALUES(value)', (key, value))
        connection.commit()
    finally:
        connection.close()
    return {key: value for key, value in rows}


def create_indexes(database: str) -> None:
    connection = connect_admin()
    attempted = ignored = 0
    try:
        with connection.cursor() as cur:
            cur.execute(f'USE `{database}`')
            for raw in AUX_SQL_MYSQL.split(';'):
                statement = raw.strip()
                if not statement:
                    continue
                attempted += 1
                try:
                    cur.execute(statement)
                except pymysql.err.MySQLError as exc:
                    if exc.args and exc.args[0] in (1050, 1061, 1060):
                        ignored += 1
                    else:
                        raise
            # Fresh indexes have stale statistics; without ANALYZE the optimizer
            # falls back to full table scans (dashboard went from 30s to 2.4s).
            cur.execute('ANALYZE TABLE transactions')
    finally:
        connection.close()
    print(f'  indexes: {attempted - ignored} created, {ignored} already existed (analyzed)')


def mysql_counts(database: str) -> dict:
    connection = connect_admin()
    try:
        with connection.cursor() as cur:
            cur.execute(f'USE `{database}`')
            out = {}
            cur.execute('SELECT COUNT(*) FROM transactions')
            out['transactions'] = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM transactions WHERE sale=1')
            out['sale_rows'] = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM transactions WHERE cross_duplicate=0')
            out['retained_rows'] = cur.fetchone()[0]
            cur.execute('SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(amount_units),0), '
                        'COUNT(DISTINCT order_key), MIN(date), MAX(date) FROM transactions WHERE sale=1')
            quantity, amount_units, orders, date_min, date_max = cur.fetchone()
            out.update(quantity=int(quantity), amount_units=int(amount_units),
                       orders=int(orders), date_min=date_min, date_max=date_max)
            cur.execute('SELECT COALESCE(SUM(cancelled),0), COALESCE(SUM(within_duplicate_candidate),0) '
                        'FROM transactions WHERE cross_duplicate=0')
            out['cancelled_rows'], out['duplicate_candidates'] = [int(v) for v in cur.fetchone()]
            cur.execute(f'SELECT COUNT(*), COALESCE(SUM(quantity),0), COALESCE(SUM(amount_units),0), '
                        f'COUNT(DISTINCT order_key) FROM transactions WHERE sale=1 AND date>=%s AND date<=%s',
                        (WINDOW['start'], WINDOW['end']))
            lines, quantity, amount_units, orders = cur.fetchone()
            out['window'] = {'lines': int(lines), 'quantity': int(quantity),
                             'amount_units': int(amount_units), 'orders': int(orders)}
            cur.execute("SELECT COUNT(*) FROM metadata WHERE `key`='manifest'")
            out['manifest_rows'] = cur.fetchone()[0]
            return out
    finally:
        connection.close()


def sqlite_counts(source: Path) -> dict:
    connection = sqlite3.connect(str(source))
    try:
        out = {}
        out['transactions'] = sqlite_scalar(connection, 'SELECT COUNT(*) FROM transactions')
        out['sale_rows'] = sqlite_scalar(connection, 'SELECT COUNT(*) FROM transactions WHERE sale=1')
        out['retained_rows'] = sqlite_scalar(connection, 'SELECT COUNT(*) FROM transactions WHERE cross_duplicate=0')
        quantity, amount_units, orders, date_min, date_max = connection.execute(
            'SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(amount_units),0), '
            'COUNT(DISTINCT order_key), MIN(date), MAX(date) FROM transactions WHERE sale=1').fetchone()
        out.update(quantity=int(quantity), amount_units=int(amount_units), orders=int(orders),
                   date_min=date_min, date_max=date_max)
        out['cancelled_rows'], out['duplicate_candidates'] = [
            int(v) for v in connection.execute(
                'SELECT COALESCE(SUM(cancelled),0), COALESCE(SUM(within_duplicate_candidate),0) '
                'FROM transactions WHERE cross_duplicate=0').fetchone()]
        return out
    finally:
        connection.close()


def check(label: str, actual, expected, failures: list) -> None:
    ok = actual == expected
    print(f'  {"PASS" if ok else "FAIL"} {label}: {actual!r}' + ('' if ok else f' (expected {expected!r})'))
    if not ok:
        failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser(description='Migrate runtime/retail.sqlite3 into MySQL ece_original.')
    parser.add_argument('--source', default=str(DEFAULT_SOURCE))
    parser.add_argument('--database', default=config.MYSQL_ORIGINAL_DB)
    parser.add_argument('--chunk', type=int, default=5000)
    parser.add_argument('--skip-app', action='store_true', help='only migrate the analytical database')
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_absolute():
        source = (config.APP_ROOT / source)
    if not source.is_file():
        raise SystemExit(f'source SQLite database not found: {source}')
    database = args.database
    failures: list[str] = []

    print(f'[migrate] {source} ({source.stat().st_size / 1048576:.1f} MB) -> MySQL '
          f'{config.MYSQL_HOST}:{config.MYSQL_PORT}/{database}')
    started = time.monotonic()

    connection = connect_admin()
    try:
        with connection.cursor() as cur:
            cur.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` '
                        'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
            cur.execute('SELECT VERSION()')
            print(f'[migrate] server {cur.fetchone()[0]}')
    finally:
        connection.close()

    step = time.monotonic()
    count, columns = copy_transactions(source, database, args.chunk)
    print(f'[migrate] transactions copied ({count} columns) in {time.monotonic() - step:.1f}s')

    step = time.monotonic()
    metadata = copy_metadata(source, database)
    print(f'[migrate] metadata copied ({len(metadata)} keys: {", ".join(sorted(metadata))}) '
          f'in {time.monotonic() - step:.1f}s')

    step = time.monotonic()
    create_indexes(database)
    print(f'[migrate] indexes ready in {time.monotonic() - step:.1f}s')

    print('[migrate] verifying row counts / aggregates / manifest')
    source_counts = sqlite_counts(source)
    target_counts = mysql_counts(database)
    check('transactions', target_counts['transactions'], EXPECTED['transactions'], failures)
    check('transactions==sqlite', target_counts['transactions'], source_counts['transactions'], failures)
    check('sale=1', target_counts['sale_rows'], EXPECTED['sale_rows'], failures)
    check('sale=1==sqlite', target_counts['sale_rows'], source_counts['sale_rows'], failures)
    for key in ('retained_rows', 'quantity', 'amount_units', 'orders', 'cancelled_rows',
                'duplicate_candidates', 'date_min', 'date_max'):
        check(f'{key}==sqlite', target_counts[key], source_counts[key], failures)
    check('metadata manifest rows', target_counts['manifest_rows'], 1, failures)
    check('window 2011-11-10~12-09 lines', target_counts['window']['lines'], WINDOW['lines'], failures)
    check('window quantity', target_counts['window']['quantity'], WINDOW['quantity'], failures)
    check('window amount_units', target_counts['window']['amount_units'], WINDOW['amount_units'], failures)
    check('window orders', target_counts['window']['orders'], WINDOW['orders'], failures)

    connection = connect_admin()
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT value FROM `{database}`.metadata WHERE `key`=%s', ('manifest',))
            mysql_manifest = json.loads(cur.fetchone()[0])
    finally:
        connection.close()
    sqlite_manifest = json.loads(metadata['manifest'])
    check('manifest fields identical', mysql_manifest, sqlite_manifest, failures)
    if mysql_manifest.get('source_file') != sqlite_manifest.get('source_file'):
        failures.append('manifest source_file')
    print(f'  manifest: source_sha256={mysql_manifest.get("source_sha256", "")[:12]}… '
          f'policy={mysql_manifest.get("policy_version")} '
          f'range={mysql_manifest.get("date_min")}~{mysql_manifest.get("date_max")}')

    if not args.skip_app:
        print('[migrate] initializing application state database')
        if config.DB_BACKEND != 'mysql':
            print('  WARN ECE_DB_BACKEND is not mysql; set it (or keep config/local/mysql.env) '
                  'before initializing ece_app', file=sys.stderr)
            failures.append('ECE_DB_BACKEND')
        else:
            path = app_db.init_app_db()
            with app_db.connect() as connection2:
                users = connection2.execute('select username,role from users order by id').fetchall()
                batches = connection2.execute(
                    'select id,kind,db_path,is_active from batches order by id').fetchall()
            print(f'  app database: {path}')
            accounts = ', '.join(f'{row["username"]}/{row["role"]}' for row in users)
            print(f'  accounts: {accounts}')
            for row in batches:
                print(f'  batch {row["id"]}: {row["db_path"]} active={row["is_active"]}')
            if not batches:
                failures.append('original batch registered')

    print(f'[migrate] finished in {time.monotonic() - started:.1f}s, '
          f'{len(failures)} check(s) failed' if failures else
          f'[migrate] finished in {time.monotonic() - started:.1f}s, all checks passed')
    if failures:
        print('[migrate] FAILED: ' + ', '.join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
