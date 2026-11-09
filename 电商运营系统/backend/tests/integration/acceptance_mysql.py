"""MySQL-mode acceptance run for the FastAPI + MySQL 8 migration.

Boots the real ``python run.py`` server against the project-local MySQL instance with an
isolated application database (``ece_app_test``) and checks the required acceptance list:

* login / dashboard 2011-11-10~2011-12-09 = 851,403 / 1,694,068.71 / 2,913 / 86,861
* orders total 2,913 and product search
* upload small sample -> new MySQL batch database -> duplicate -> switch -> switch back
* forecast job returns 7 rows and a CSV export
* simulated inventory 100+20-30=90, idempotent, 91 rejected, candidate 10
* AI status / ask

Every batch database created by the run is dropped afterwards (the pre-existing ones are
left alone); ``ece_app_test`` is recreated from scratch.

    python scripts/acceptance_mysql.py [--keep]

Evidence log: ``03_测试与验证/系统测试/runs/20261029_mysql/mysql_acceptance.log``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent            # ...\电商运营系统\scripts
SYSTEM_ROOT = HERE.parents[2]                         # ...\电商运营系统
ROOT = SYSTEM_ROOT.parent                         # ...\学校项目
TESTS_API = SYSTEM_ROOT / 'backend/tests/api'
EVIDENCE = ROOT / '03_测试与验证' / '系统测试' / 'runs' / '20261029_mysql'
for candidate in (str(TESTS_API), str(SYSTEM_ROOT / 'vendor/python-packages'), str(SYSTEM_ROOT / 'backend')):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
try:  # £ / non-GBK characters in AI answers must not break the console
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, OSError):  # pragma: no cover - older interpreters
    pass

import pymysql  # noqa: E402

import apiharness  # noqa: E402
import fixtures  # noqa: E402

RANGE = dict(apiharness.ORIGINAL_RANGE)
EXPECTED = dict(apiharness.ORIGINAL_TOTALS)
APP_DB = 'ece_app_test'
ADMIN_PASSWORD = apiharness.ADMIN_PASSWORD
OPERATOR_PASSWORD = apiharness.OPERATOR_PASSWORD
STOCK = 'QA-MYSQL-1'
SCENARIO_NAME = 'MySQL验收模拟场景'
LOG_LINES: list[str] = []
FAILURES: list[str] = []


def emit(text: str = '') -> None:
    print(text, flush=True)
    LOG_LINES.append(text)


def check(label: str, actual, expected) -> bool:
    ok = actual == expected
    emit(f'  {"PASS" if ok else "FAIL"} {label}: {actual!r}'
         + ('' if ok else f'  (expected {expected!r})'))
    if not ok:
        FAILURES.append(label)
    return ok


def check_true(label: str, value, detail='') -> bool:
    ok = bool(value)
    emit(f'  {"PASS" if ok else "FAIL"} {label}{(": " + str(detail)) if detail else ""}')
    if not ok:
        FAILURES.append(label)
    return ok


def read_mysql_env() -> dict:
    path = SYSTEM_ROOT / 'config/local/mysql.env'
    if not path.is_file():
        raise SystemExit(f'missing {path}; run scripts\\mysql\\mysql_init.ps1 first')
    values: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        text = line.strip()
        if not text or text.startswith('#') or '=' not in text:
            continue
        key, value = text.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def admin_connection(env: dict, database=None):
    return pymysql.connect(host=env['ECE_MYSQL_HOST'], port=int(env['ECE_MYSQL_PORT']),
                           user=env['ECE_MYSQL_USER'], password=env['ECE_MYSQL_PASSWORD'],
                           database=database, charset='utf8mb4', autocommit=True,
                           connect_timeout=10)


def databases(env: dict, like: str) -> list[str]:
    connection = admin_connection(env)
    try:
        with connection.cursor() as cur:
            cur.execute('SHOW DATABASES LIKE %s', (like,))
            return sorted(row[0] for row in cur.fetchall())
    finally:
        connection.close()


def scalar(env: dict, database: str, sql: str, params=()):
    connection = admin_connection(env, database)
    try:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        connection.close()


def drop_database(env: dict, name: str) -> None:
    connection = admin_connection(env)
    try:
        with connection.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS `{name}`')
    finally:
        connection.close()


def poll_job(client, path: str, timeout=300.0, kind='job') -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        if response.status != 200:
            raise AssertionError(f'{path} -> {response.status} {response.text()[:300]}')
        payload = response.json()
        status = (payload.get(kind) or {}).get('status')
        if status in ('succeeded', 'failed', 'duplicate'):
            return payload
        time.sleep(0.5)
    raise AssertionError(f'{path} not finished in {timeout}s: {json.dumps(payload)[:400]}')


def poll_import(client, job_id: str, timeout=300.0) -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f'/api/imports/jobs/{job_id}')
        if response.status != 200:
            raise AssertionError(f'import job {job_id} -> {response.status} {response.text()[:300]}')
        payload = response.json()
        status = (payload.get('job') or {}).get('status')
        if status in ('succeeded', 'failed', 'duplicate'):
            return payload
        time.sleep(0.5)
    raise AssertionError(f'import job {job_id} not finished in {timeout}s: {json.dumps(payload)[:400]}')


def main() -> int:
    parser = argparse.ArgumentParser(description='MySQL mode acceptance run')
    parser.add_argument('--keep', action='store_true', help='keep ece_app_test and batch databases')
    args = parser.parse_args()

    env = read_mysql_env()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    emit('=' * 78)
    emit('MySQL 模式验收（真实 python run.py 子进程 + 真实 MySQL 8 实例）')
    emit(f'MySQL  {env["ECE_MYSQL_HOST"]}:{env["ECE_MYSQL_PORT"]}  user={env["ECE_MYSQL_USER"]}')
    emit(f'应用库 {APP_DB}（隔离）；原始数据 ece_original（只读使用）')
    emit(f'命令   {Path(sys.executable).name} scripts\\acceptance_mysql.py')
    emit('=' * 78)

    # -- clean isolated state ------------------------------------------------
    before_batches = set(databases(env, 'ece\\_batch\\_%'))
    emit(f'既有批次库 {len(before_batches)} 个（运行后新增的会删除）: '
         f'{", ".join(sorted(before_batches)) or "无"}')
    drop_database(env, APP_DB)
    emit(f'已重建隔离应用库 {APP_DB}')

    extra_env = {
        'ECE_DB_BACKEND': 'mysql',
        'ECE_MYSQL_HOST': env['ECE_MYSQL_HOST'],
        'ECE_MYSQL_PORT': env['ECE_MYSQL_PORT'],
        'ECE_MYSQL_USER': env['ECE_MYSQL_USER'],
        'ECE_MYSQL_PASSWORD': env['ECE_MYSQL_PASSWORD'],
        'ECE_MYSQL_APP_DB': APP_DB,
        'ECE_MYSQL_ORIGINAL_DB': env.get('ECE_MYSQL_ORIGINAL_DB', 'ece_original'),
        'ECE_ADMIN_PASSWORD': ADMIN_PASSWORD,
        'ECE_OPERATOR_PASSWORD': OPERATOR_PASSWORD,
        'ECE_AI_BASE_URL': '',
        'ECE_AI_API_KEY': '',
        'ECE_AI_MODEL': '',
    }
    server = apiharness.ServerProcess('mysql-acceptance', extra_env=extra_env)
    started = time.monotonic()
    try:
        server.start()
        emit(f'服务已启动 {server.base}（{time.monotonic() - started:.1f}s）')
        client = apiharness.ApiClient(server.base)

        # -- 1. health / login ------------------------------------------------
        emit('\n[1] 健康检查与登录')
        health = client.get('/api/health')
        check('health.status', health.status, 200)
        payload = health.json()
        check('health.batch.id', (payload.get('batch') or {}).get('id'), 'original')
        login = client.login()
        check('login.status', login.status, 200)
        user = (login.json().get('user') or {})
        check('login.user', user.get('username'), apiharness.ADMIN_USER)
        check('login.role', user.get('role'), 'admin')
        me = client.get('/api/auth/me')
        check('auth.me.status', me.status, 200)
        emit(f'  MySQL 实际连接: database={scalar(env, APP_DB, "select database()")} '
             f'user={scalar(env, APP_DB, "select current_user()")}')

        # -- 2. dashboard / orders / products ---------------------------------
        emit('\n[2] 看板 / 订单 / 商品（MySQL 查询）')
        response = client.get('/api/dashboard', RANGE)
        check('dashboard.status', response.status, 200)
        dashboard = response.json()
        totals = dashboard.get('totals') or {}
        check('dashboard.quantity', int(totals.get('quantity', 0)), EXPECTED['quantity'])
        check('dashboard.revenue', round(float(totals.get('revenue', 0)), 2), EXPECTED['revenue'])
        check('dashboard.orders', int(totals.get('orders', 0)), EXPECTED['orders'])
        check('dashboard.lines', int(totals.get('lines', 0)), EXPECTED['lines'])
        check('dashboard.batch.id', (dashboard.get('batch') or {}).get('id'), 'original')
        # The window has no Saturday records (same as SQLite: 6 weekday groups, 0..5).
        check('dashboard.weekdays rows', len(dashboard.get('weekdays') or []), 6)
        check('dashboard.weekdays ids', sorted(row.get('weekday') for row in dashboard.get('weekdays') or []),
              [0, 1, 2, 3, 4, 5])
        observed = [row for row in dashboard.get('trend') or [] if row.get('observed')]
        emit(f'  覆盖范围 {dashboard.get("coverage")}，有记录日 {len(observed)}/'
             f'{len(dashboard.get("trend") or [])}')

        orders = client.get('/api/orders', dict(RANGE, page=1, page_size=5))
        check('orders.status', orders.status, 200)
        orders_payload = orders.json()
        check('orders.total', int(orders_payload.get('total', 0)), EXPECTED['orders'])
        check('orders.page rows', len(orders_payload.get('rows') or []), 5)

        products = client.get('/api/products', {'q': 'heart'})
        check('products.status', products.status, 200)
        product_rows = products.json()
        check_true('products rows>0', isinstance(product_rows, list) and len(product_rows) > 0,
                   f'{len(product_rows) if isinstance(product_rows, list) else "?"} rows')
        emit(f'  MySQL 原始库行数 transactions={scalar(env, env.get("ECE_MYSQL_ORIGINAL_DB", "ece_original"), "select count(*) from transactions"):,}')

        # -- 3. upload sample -> new MySQL batch db ---------------------------
        emit('\n[3] 上传小样例 -> MySQL 批次库 -> duplicate -> 切换 -> 切回')
        content = fixtures.sample_xlsx_bytes()
        upload = client.upload('/api/imports', 'mysql-qc-sample.xlsx', content,
                               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                               {'label': 'MySQL验收小样例'})
        check('import.submit', upload.status, 202)
        import_job = poll_import(client, upload.json()['job_id'])
        job = import_job.get('job') or {}
        check('import.job.status', job.get('status'), 'succeeded')
        report = import_job.get('report') or {}
        check('import.report.raw_rows', report.get('raw_rows'), 4)
        check('import.report.positive_sale_rows', report.get('positive_sale_rows'), 4)
        check('import.report.date_min', report.get('date_min'), fixtures.SAMPLE_TOTALS['date_min'])
        check('import.report.date_max', report.get('date_max'), fixtures.SAMPLE_TOTALS['date_max'])
        upload_batch = import_job.get('batch') or {}
        upload_batch_id = upload_batch.get('id')
        check('import.batch.is_active', upload_batch.get('is_active'), False)
        batch_row = None
        listing = client.get('/api/imports/batches').json()
        check('batches.active_batch_id', listing.get('active_batch_id'), 'original')
        for row in listing.get('batches') or []:
            if row.get('id') == upload_batch_id:
                batch_row = row
        check_true('batch registered', batch_row is not None)
        db_path = scalar(env, APP_DB, 'select db_path from batches where id=%s', (upload_batch_id,)) or ''
        check_true('batch db_path is mysql:<db>', db_path.startswith('mysql:ece_batch_'), db_path)
        batch_database = db_path.split(':', 1)[1] if db_path.startswith('mysql:') else ''
        if batch_database:
            emit(f'  批次库 {batch_database} transactions='
                 f'{scalar(env, batch_database, "select count(*) from transactions")} '
                 f'sale=1 {scalar(env, batch_database, "select count(*) from transactions where sale=1")}')
        else:
            FAILURES.append('batch database name')

        duplicate = client.upload('/api/imports', 'mysql-qc-sample.xlsx', content,
                                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', {})
        check('duplicate.submit', duplicate.status, 202)
        duplicate_job = poll_import(client, duplicate.json()['job_id'])
        check('duplicate.job.status', (duplicate_job.get('job') or {}).get('status'), 'duplicate')
        check('duplicate.batch_id', (duplicate_job.get('batch') or {}).get('id'), upload_batch_id)

        activate = client.post(f'/api/imports/batches/{upload_batch_id}/activate', {})
        check('activate.status', activate.status, 200)
        # No date range: defaults follow the active batch manifest (2011-02-01~02-05).
        switched_dashboard = client.get('/api/dashboard').json()
        switched = switched_dashboard['totals']
        sample = fixtures.SAMPLE_TOTALS
        check('switched.quantity', int(switched.get('quantity', 0)), sample['quantity'])
        check('switched.revenue', round(float(switched.get('revenue', 0)), 2), sample['revenue'])
        check('switched.orders', int(switched.get('orders', 0)), sample['orders'])
        check('switched.batch.id', (switched_dashboard.get('batch') or {}).get('id'), upload_batch_id)
        switched_orders = client.get('/api/orders', {'page': 1, 'page_size': 50}).json()
        check('switched.orders.total', int(switched_orders.get('total', 0)), sample['orders'])

        back = client.post('/api/imports/batches/original/activate', {})
        check('switch-back.status', back.status, 200)
        restored = client.get('/api/dashboard', dict(RANGE)).json()
        check('switch-back.batch.id', (restored.get('batch') or {}).get('id'), 'original')
        check('switch-back.quantity', int(restored['totals']['quantity']), EXPECTED['quantity'])
        check('switch-back.orders', int(restored['totals']['orders']), EXPECTED['orders'])

        # -- 4. forecast ------------------------------------------------------
        emit('\n[4] 预测任务（MySQL）')
        forecast = client.post('/api/forecast', {})
        check('forecast.submit', forecast.status, 202)
        forecast_job_id = forecast.json()['job_id']
        forecast_payload = poll_job(client, f'/api/jobs/{forecast_job_id}')
        forecast_job = forecast_payload.get('job') or {}
        check('forecast.job.status', forecast_job.get('status'), 'succeeded')
        result = forecast_job.get('result') or {}
        check('forecast.origin', result.get('origin'), '2011-12-09')
        check('forecast.horizon', result.get('horizon'), 7)
        check('forecast.rows', len(result.get('rows') or []), 7)
        check('forecast.actual_days', result.get('actual_days'), 0)
        check_true('forecast rows have numeric prediction',
                   all(isinstance(row.get('prediction'), (int, float))
                       for row in result.get('rows') or []))
        export = client.get(f'/api/jobs/{forecast_job_id}/export')
        check('forecast.export.status', export.status, 200)
        check_true('forecast.export csv', 'csv' in export.header('content-type').lower(),
                   export.header('content-type'))
        emit(f'  预测方法 {result.get("actual_method")}；CSV {len(export.body)} bytes')

        # -- 5. simulated inventory ------------------------------------------
        emit('\n[5] 模拟库存固定样例（100+20-30=90 / 幂等 / 91 拒绝 / 候选 10）')
        created = client.post('/api/inventory/scenarios', {
            'name': SCENARIO_NAME, 'note': 'MySQL 验收（模拟数据，不代表真实库存）',
            'stock_codes': [STOCK, 'QA-MYSQL-2']})
        check('inventory.scenario.create', created.status, 201)
        scenario_id = (created.json().get('scenario') or {}).get('id')
        check_true('inventory.scenario.id', bool(scenario_id))
        item = client.put(f'/api/inventory/scenarios/{scenario_id}/items/{STOCK}', {
            'name': 'MySQL验收商品', 'initial_qty': 100, 'lead_time_days': 8,
            'safety_stock': 30, 'daily_demand': 10})
        check('inventory.item.update', item.status, 200)
        item_payload = item.json().get('item') or {}
        check('inventory.item.balance', item_payload.get('balance'), 100)
        check('inventory.item.candidate', round(float(item_payload.get('replenishment_candidate') or 0), 4), 10)
        inbound = client.post(f'/api/inventory/scenarios/{scenario_id}/movements', {
            'stock_code': STOCK, 'kind': 'in', 'qty': 20,
            'idempotency_key': 'qa-mysql-in-1', 'note': '验收入库（模拟）'})
        check('inventory.in.status', inbound.status, 201)
        check('inventory.in.balance', (inbound.json().get('item') or {}).get('balance'), 120)
        outbound = client.post(f'/api/inventory/scenarios/{scenario_id}/movements', {
            'stock_code': STOCK, 'kind': 'out', 'qty': 30,
            'idempotency_key': 'qa-mysql-out-1', 'note': '验收入库（模拟）'})
        check('inventory.out.status', outbound.status, 201)
        check('inventory.out.balance', (outbound.json().get('item') or {}).get('balance'), 90)
        again = client.post(f'/api/inventory/scenarios/{scenario_id}/movements', {
            'stock_code': STOCK, 'kind': 'out', 'qty': 30, 'idempotency_key': 'qa-mysql-out-1'})
        check('inventory.idempotent.status', again.status, 200)
        check('inventory.idempotent.flag', again.json().get('duplicate'), True)
        check('inventory.idempotent.balance', (again.json().get('item') or {}).get('balance'), 90)
        rejected = client.post(f'/api/inventory/scenarios/{scenario_id}/movements', {
            'stock_code': STOCK, 'kind': 'out', 'qty': 91, 'idempotency_key': 'qa-mysql-out-2'})
        check('inventory.91.status', rejected.status, 400)
        items = client.get(f'/api/inventory/scenarios/{scenario_id}/items').json()
        balance = None
        for row in items.get('items') or []:
            if row.get('stock_code') == STOCK:
                balance = row.get('balance')
        check('inventory.final.balance', balance, 90)
        movements = client.get(f'/api/inventory/scenarios/{scenario_id}/movements').json()
        check('inventory.movements.rows', len(movements.get('movements') or []), 2)
        # Confirm 10 units in transit (simulated): candidate = max(0, 80 + 30 - 90 - 10) = 10
        expected_date = (date.today() + timedelta(days=3)).isoformat()
        confirmed = client.post(f'/api/inventory/scenarios/{scenario_id}/replenishment/confirm', {
            'stock_code': STOCK, 'qty': 10, 'expected_date': expected_date,
            'idempotency_key': 'qa-mysql-confirm-1'})
        check('inventory.in_transit.confirm', confirmed.status, 201)
        confirmed_payload = confirmed.json()
        check('inventory.in_transit.balance', (confirmed_payload.get('item') or {}).get('balance'), 90)
        check('inventory.in_transit.effective',
              round(float((confirmed_payload.get('item') or {}).get('in_transit_effective') or 0), 4), 10)
        replenishment = client.get(f'/api/inventory/scenarios/{scenario_id}/replenishment').json()
        candidate = None
        for row in replenishment.get('items') or []:
            if row.get('stock_code') == STOCK:
                candidate = round(float(row.get('candidate') or 0), 4)
        check('inventory.replenishment.candidate', candidate, 10)
        check_true('inventory.simulated flag', movements.get('simulated'), movements.get('simulated'))

        # -- 6. AI ------------------------------------------------------------
        emit('\n[6] AI 助手（未配置真实大模型时的本地规则回退）')
        status = client.get('/api/ai/status')
        check('ai.status.http', status.status, 200)
        ai_status = status.json()
        check('ai.llm_configured', ai_status.get('llm_configured'), False)
        check('ai.fallback', ai_status.get('fallback'), 'local-rule-planner')
        check_true('ai.tools_count>=5', int(ai_status.get('tools_count') or 0) >= 5,
                   ai_status.get('tools_count'))
        ask = client.post('/api/ai/ask', {'question': '2011-11-10 到 2011-12-09 的销量是多少？'})
        check('ai.ask.http', ask.status, 200)
        answer = ask.json()
        check_true('ai.ask.answer', bool(answer.get('answer')), str(answer.get('answer'))[:80])
        emit(f'  AI 状态: {json.dumps({k: ai_status.get(k) for k in ("llm_configured", "fallback", "tools_count")}, ensure_ascii=False)}')

        # -- 7. MySQL 持久化核对 ----------------------------------------------
        emit('\n[7] 应用状态确实写入 MySQL（ece_app_test）')
        for table in ('users', 'batches', 'inventory_scenarios', 'inventory_movements',
                      'jobs', 'ai_runs'):
            count = scalar(env, APP_DB, f'select count(*) from `{table}`')
            emit(f'  {table}: {count} 行')
        check_true('users seeded', (scalar(env, APP_DB, 'select count(*) from users') or 0) >= 2)
        check_true('original batch row',
                   scalar(env, APP_DB, "select db_path from batches where id='original'")
                   == f'mysql:{env.get("ECE_MYSQL_ORIGINAL_DB", "ece_original")}',
                   scalar(env, APP_DB, "select db_path from batches where id='original'"))
    except Exception as exc:  # noqa: BLE001 - the report must show what broke
        FAILURES.append(f'exception: {type(exc).__name__}')
        emit(f'\nFAIL 运行异常 {type(exc).__name__}: {exc}')
        emit(traceback.format_exc())
        emit('服务日志尾部:')
        emit(server.log_tail(60))
    finally:
        server.stop()
        created_batches = set(databases(env, 'ece\\_batch\\_%')) - before_batches
        if args.keep:
            emit(f'\n[cleanup] --keep: 保留 {APP_DB} 与 {len(created_batches)} 个批次库')
        else:
            for name in sorted(created_batches, reverse=True):
                drop_database(env, name)
            drop_database(env, APP_DB)
            emit(f'\n[cleanup] 已删除 {APP_DB} 与本次新增的 {len(created_batches)} 个批次库')

    emit('\n' + '=' * 78)
    elapsed = time.monotonic() - started
    if FAILURES:
        emit(f'结果: FAIL（{len(FAILURES)} 项）耗时 {elapsed:.1f}s')
        for label in FAILURES:
            emit(f'  - {label}')
    else:
        emit(f'结果: PASS（全部检查通过）耗时 {elapsed:.1f}s')
    emit('=' * 78)
    log_path = EVIDENCE / 'mysql_acceptance.log'
    log_path.write_text('\n'.join(LOG_LINES) + '\n', encoding='utf-8')
    print(f'evidence -> {log_path}')
    return 0 if not FAILURES else 1


if __name__ == '__main__':
    raise SystemExit(main())
