"""Build ``acceptance_prototype.json`` from a real, isolated prototype run.

This is not a mock: it starts ``python run.py`` in a subprocess with a temporary runtime
directory and a private copy of ``runtime/retail.sqlite3``, then probes the acceptance
numbers that ``交接清单`` M6 / ``API契约.md`` require:

* historical window 2011-11-10..2011-12-09 = 851,403 / 1,694,068.71 / 2,913;
* import sample -> inactive snapshot -> activate -> 46 / 135.5 / 6 -> duplicate ->
  switch back;
* inventory 100 + 20 - 30 = 90, idempotent repeat, 91 rejected, replenishment 10;
* 7-row forecast with null actuals and a 7-row CSV export;
* AI answers in the unconfigured local-rule mode repeat the dashboard numbers and the
  brief downloads as Markdown;
* error codes and OpenAPI/route consistency.

Usage:
    python "电商运营系统/backend/tests/api/build_acceptance.py"
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EVIDENCE_DIR = ROOT / '03_测试与验证' / '系统测试' / 'runs' / datetime.now().strftime('%Y%m%d_regression')
OUT = EVIDENCE_DIR / 'acceptance_prototype.json'
sys.path.insert(0, str(HERE))

import apiharness as h  # noqa: E402
from fixtures import all_numbers, extra_numbers_from_batch, number_approved  # noqa: E402

# Import sample matching the Lead end-to-end flow: 46 pieces / 135.5 GBP / 6 orders.
IMPORT_ROWS = [
    ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price',
     'Customer ID', 'Country'],
    ['S1001', 'A001', 'Alpha Lamp', 10, '2011-12-01 09:15', 2.5, 10001, 'United Kingdom'],
    ['S1001', 'A002', 'Beta Chair', 2, '2011-12-01 09:20', 15.0, 10001, 'United Kingdom'],
    ['S1002', 'B001', 'Gamma Mug', 6, '2011-12-02 11:00', 1.25, 10002, 'France'],
    ['S1002', 'A001', 'Alpha Lamp', 4, '2011-12-02 11:05', 2.5, 10002, 'France'],
    ['S1003', 'C001', 'Delta Box', 8, '2011-12-03 14:30', 3.0, 10003, 'Germany'],
    ['S1004', 'A002', 'Beta Chair', 1, '2011-12-04 10:00', 15.0, 10004, 'United Kingdom'],
    ['S1005', 'B001', 'Gamma Mug', 12, '2011-12-05 16:45', 1.25, 10005, 'Spain'],
    ['S1006', 'C001', 'Delta Box', 3, '2011-12-06 08:20', 3.0, 10006, 'United Kingdom'],
]
IMPORT_TOTALS = {'quantity': 46, 'revenue': 135.5, 'orders': 6}
INVENTORY_ITEM = 'QA-ACCEPT-1'


def import_sample_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerows(IMPORT_ROWS)
    return buffer.getvalue().encode('utf-8')


def main() -> int:
    checks: list[dict] = []
    numbers: dict = {}

    def record(check_id: str, name: str, expected, actual, passed: bool,
               elapsed: float = 0.0, note: str = '') -> None:
        checks.append({
            'id': check_id, 'name': name, 'expected': expected, 'actual': actual,
            'passed': bool(passed), 'elapsed_seconds': round(elapsed, 3), 'note': note,
        })

    def timed(call):
        started = time.monotonic()
        try:
            return call(), time.monotonic() - started, None
        except Exception as exc:  # noqa: BLE001 - recorded as a failure, never swallowed
            return None, time.monotonic() - started, f'{type(exc).__name__}: {exc}'

    official_before = h.official_file_state() if hasattr(h, 'official_file_state') else None
    started_at = time.monotonic()
    server = h.ServerProcess('acceptance').start()
    client = h.ApiClient(server.base)
    login = client.login(h.ADMIN_USER, h.ADMIN_PASSWORD)
    if login.status != 200:
        raise SystemExit(f'管理员登录失败：{login.status} {login.text()[:300]}')

    try:
        # ---------------------------------------------------------- M2 dashboard
        (dashboard, elapsed, error) = timed(lambda: client.get(
            '/api/dashboard', h.ORIGINAL_RANGE).json())
        totals = (dashboard or {}).get('totals') or {}
        numbers['historical_window'] = {
            'range': h.ORIGINAL_RANGE,
            'quantity': totals.get('quantity'),
            'revenue': totals.get('revenue'),
            'orders': totals.get('orders'),
            'lines': totals.get('lines'),
        }
        record('m2.dashboard.historical', '历史窗口全店汇总（含首尾日）',
               {'quantity': 851403, 'revenue': 1694068.71, 'orders': 2913, 'lines': 86861},
               numbers['historical_window'], error is None and
               totals.get('quantity') == 851403
               and abs((totals.get('revenue') or 0) - 1694068.71) < 0.01
               and totals.get('orders') == 2913 and totals.get('lines') == 86861,
               elapsed, error or '')

        # --------------------------------------------------------------- orders
        orders = client.get('/api/orders', {**h.ORIGINAL_RANGE, 'page': 1,
                                            'page_size': 200}).json()
        orders_total = orders.get('total')
        first = (orders.get('rows') or [{}])[0]
        detail = client.get('/api/orders/' + str(first.get('order_key')),
                            h.ORIGINAL_RANGE)
        detail_payload = detail.json()
        header = detail_payload.get('order') or {}
        lines = detail_payload.get('rows') or []
        detail_ok = (detail.status == 200 and lines
                     and sum(row['quantity'] for row in lines) == header.get('quantity')
                     and abs(sum(row.get('revenue') or 0 for row in lines)
                             - (header.get('revenue') or 0)) < 0.01)
        record('m2.orders.total', '订单列表 total 与看板订单数一致',
               {'orders': 2913, 'dashboard': totals.get('orders')},
               {'orders_total': orders_total, 'dashboard': totals.get('orders')},
               orders_total == totals.get('orders') == 2913)
        record('m2.orders.detail', '订单明细行相加等于订单头',
               {'order_key': first.get('order_key')},
               {'order_key': first.get('order_key'), 'header_quantity': header.get('quantity'),
                'header_revenue': header.get('revenue'), 'lines': len(lines)},
               detail_ok)
        numbers['orders'] = {'total': orders_total,
                             'sample_order': first.get('order_key'),
                             'sample_lines': len(lines),
                             'sample_quantity': header.get('quantity'),
                             'sample_revenue': header.get('revenue')}

        # --------------------------------------------------------- M2 imports
        uploaded = client.upload('/api/imports', 'acceptance_sample.csv',
                                 import_sample_csv(), 'text/csv', {'label': '验收小样例'})
        upload_payload = uploaded.json()
        job = None
        if uploaded.status == 202:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                job = client.get('/api/imports/jobs/' + upload_payload['job_id']).json()
                if (job.get('job') or {}).get('status') in ('succeeded', 'failed', 'duplicate'):
                    break
                time.sleep(0.4)
        job_header = (job or {}).get('job') or {}
        report = (job or {}).get('report') or {}
        batch = (job or {}).get('batch') or {}
        upload_ok = (uploaded.status == 202 and job_header.get('status') == 'succeeded'
                     and batch.get('is_active') is False)
        record('m2.import.upload', '小样例上传→异步任务成功→快照未自动切换',
               {'http': 202, 'job_status': 'succeeded', 'is_active': False},
               {'http': uploaded.status, 'job_status': job_header.get('status'),
                'is_active': batch.get('is_active'), 'batch_id': batch.get('id'),
                'report': {key: report.get(key) for key in
                           ('raw_rows', 'positive_sale_rows', 'cancelled_rows',
                            'date_min', 'date_max')}},
               upload_ok)
        numbers['import'] = {'batch_id': batch.get('id'),
                             'job_status': job_header.get('status'),
                             'report': {key: report.get(key) for key in
                                        ('raw_rows', 'retained_rows', 'positive_sale_rows',
                                         'date_min', 'date_max')}}
        upload_batch_id = batch.get('id')

        duplicate = client.upload('/api/imports', 'acceptance_sample.csv',
                                  import_sample_csv(), 'text/csv')
        duplicate_job = None
        if duplicate.status == 202:
            duplicate_payload = duplicate.json()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                duplicate_job = client.get(
                    '/api/imports/jobs/' + duplicate_payload['job_id']).json()
                if (duplicate_job.get('job') or {}).get('status') in ('succeeded', 'failed',
                                                                      'duplicate'):
                    break
                time.sleep(0.3)
        duplicate_status = ((duplicate_job or {}).get('job') or {}).get('status')
        duplicate_batch = ((duplicate_job or {}).get('batch') or {}).get('id')
        record('m2.import.duplicate', '同一文件重复导入为 duplicate 且不新建批次',
               {'status': 'duplicate', 'batch_id': upload_batch_id},
               {'status': duplicate_status, 'batch_id': duplicate_batch},
               duplicate_status == 'duplicate' and duplicate_batch == upload_batch_id)

        activated = client.post(f'/api/imports/batches/{upload_batch_id}/activate', {})
        active_dashboard = client.get('/api/dashboard',
                                      {'start': '2011-12-01', 'end': '2011-12-06'}).json()
        active_totals = active_dashboard.get('totals') or {}
        record('m2.import.activate', '显式切换后看板随新批次变化（46/135.5/6）',
               IMPORT_TOTALS,
               {'quantity': active_totals.get('quantity'),
                'revenue': active_totals.get('revenue'),
                'orders': active_totals.get('orders'),
                'previous_batch_id': (activated.json() or {}).get('previous_batch_id'),
                'batch': (active_dashboard.get('batch') or {}).get('id')},
               activated.status == 200
               and active_totals.get('quantity') == 46
               and abs((active_totals.get('revenue') or 0) - 135.5) < 0.01
               and active_totals.get('orders') == 6)
        numbers['import']['active_totals'] = {
            'quantity': active_totals.get('quantity'),
            'revenue': active_totals.get('revenue'),
            'orders': active_totals.get('orders')}

        back = client.post('/api/imports/batches/original/activate', {})
        back_dashboard = client.get('/api/dashboard', h.ORIGINAL_RANGE).json()
        record('m2.import.switch_back', '切回原始批次恢复历史基线',
               {'quantity': 851403},
               {'quantity': (back_dashboard.get('totals') or {}).get('quantity'),
                'batch': (back_dashboard.get('batch') or {}).get('id')},
               back.status == 200
               and (back_dashboard.get('totals') or {}).get('quantity') == 851403)

        # ------------------------------------------------------------ M3 forecast
        forecast_response = client.post('/api/forecast', {})
        forecast_job = None
        if forecast_response.status == 202:
            job_id = forecast_response.json()['job_id']
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                forecast_job = client.get('/api/jobs/' + job_id).json()
                if (forecast_job.get('job') or {}).get('status') in ('succeeded', 'failed'):
                    break
                time.sleep(0.5)
        forecast_header = (forecast_job or {}).get('job') or {}
        result = forecast_header.get('result') or {}
        rows = result.get('rows') or []
        export = client.get('/api/jobs/' + str(forecast_header.get('id')) + '/export')
        export_rows = []
        if export.status == 200:
            export_rows = list(csv.reader(io.StringIO(export.text().lstrip('\ufeff'))))
        record('m3.forecast.7rows', '全店未来7天预测：7行、actual 全为空',
               {'rows': 7, 'actual_all_null': True,
                'model_version': 'daily-v3-robust-blend-mad3'},
               {'status': forecast_header.get('status'), 'rows': len(rows),
                'actual_all_null': all(row.get('actual') is None for row in rows),
                'model_version': result.get('model_version'),
                'origin': result.get('origin'), 'actual_method': result.get('actual_method')},
               forecast_response.status == 202 and forecast_header.get('status') == 'succeeded'
               and len(rows) == 7 and all(row.get('actual') is None for row in rows))
        record('m3.forecast.export', '预测 CSV 导出 7 行且列齐全',
               {'data_rows': 7, 'columns': 11},
               {'http': export.status, 'rows': max(len(export_rows) - 1, 0),
                'columns': len(export_rows[0]) if export_rows else 0},
               export.status == 200 and len(export_rows) == 8
               and len(export_rows[0]) == 11)
        numbers['forecast'] = {'status': forecast_header.get('status'),
                              'origin': result.get('origin'),
                              'rows': len(rows),
                              'model_version': result.get('model_version'),
                              'actual_method': result.get('actual_method'),
                              'export_rows': max(len(export_rows) - 1, 0)}

        (backtest, elapsed, error) = timed(lambda: client.post('/api/backtest', {
            'start': '2011-11-20', 'end': '2011-12-01'}))
        backtest_job = None
        if backtest is not None and backtest.status == 202:
            backtest_id = backtest.json()['job_id']
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                backtest_job = client.get('/api/jobs/' + backtest_id).json()
                if (backtest_job.get('job') or {}).get('status') in ('succeeded', 'failed'):
                    break
                time.sleep(0.5)
        backtest_result = ((backtest_job or {}).get('job') or {}).get('result') or {}
        record('m3.backtest', '小窗口回测（12个起点，WAPE 可计算）',
               {'origins_attempted': 12, 'forecast_count': 84},
               {'origins_attempted': backtest_result.get('origins_attempted'),
                'forecast_count': backtest_result.get('forecast_count'),
                'scored_count': backtest_result.get('scored_count'),
                'WAPE': (backtest_result.get('metrics') or {}).get('WAPE')},
               backtest_result.get('origins_attempted') == 12
               and backtest_result.get('forecast_count') == 84
               and (backtest_result.get('metrics') or {}).get('WAPE') is not None)

        # ----------------------------------------------------------- M4 inventory
        scenario = client.post('/api/inventory/scenarios', {
            'name': '验收模拟场景', 'note': '原型验收用模拟数据',
            'stock_codes': [INVENTORY_ITEM]}).json()['scenario']
        sid = scenario['id']
        client.put(f'/api/inventory/scenarios/{sid}/items/{INVENTORY_ITEM}', {
            'name': '验收商品', 'initial_qty': 100, 'lead_time_days': 8,
            'safety_stock': 30, 'daily_demand': 10})
        client.post(f'/api/inventory/scenarios/{sid}/movements', {
            'stock_code': INVENTORY_ITEM, 'kind': 'in', 'qty': 20,
            'idempotency_key': 'accept-in-1'})
        client.post(f'/api/inventory/scenarios/{sid}/movements', {
            'stock_code': INVENTORY_ITEM, 'kind': 'out', 'qty': 30,
            'idempotency_key': 'accept-out-1'})
        duplicate_move = client.post(f'/api/inventory/scenarios/{sid}/movements', {
            'stock_code': INVENTORY_ITEM, 'kind': 'out', 'qty': 30,
            'idempotency_key': 'accept-out-1'})
        dup_balance = ((duplicate_move.json() or {}).get('item') or {}).get('balance')
        rejected = client.post(f'/api/inventory/scenarios/{sid}/movements', {
            'stock_code': INVENTORY_ITEM, 'kind': 'out', 'qty': 91,
            'idempotency_key': 'accept-out-2'})
        expected_date = (date.today() + timedelta(days=3)).isoformat()
        confirmed = client.post(
            f'/api/inventory/scenarios/{sid}/replenishment/confirm', {
                'stock_code': INVENTORY_ITEM, 'qty': 10, 'expected_date': expected_date,
                'idempotency_key': 'accept-confirm-1'})
        replenishment = client.get(
            f'/api/inventory/scenarios/{sid}/replenishment').json()
        row = {item['stock_code']: item for item in replenishment.get('items') or []}[
            INVENTORY_ITEM]
        balance_now = ((client.get(
            f'/api/inventory/scenarios/{sid}/items').json() or {}).get('items') or [{}])[0]
        record('m4.inventory.sample', '模拟库存固定样例：100+20-30=90、幂等、出库91被拒',
               {'balance': 90, 'duplicate_balance': 90, 'out_91': 400},
               {'balance_after_out': balance_now.get('balance'),
                'duplicate_balance': dup_balance, 'out_91_status': rejected.status,
                'simulated': replenishment.get('simulated')},
               balance_now.get('balance') == 90 and dup_balance == 90
               and rejected.status == 400 and replenishment.get('simulated') is True)
        record('m4.inventory.replenishment', '补货候选 = max(0, 80+30-90-10) = 10',
               {'lead_time_demand': 80, 'effective_in_transit': 10, 'candidate': 10,
                'confirmed': 201},
               {'lead_time_demand': row.get('lead_time_demand'),
                'effective_in_transit': row.get('effective_in_transit'),
                'candidate': row.get('candidate'), 'confirm_http': confirmed.status},
               row.get('lead_time_demand') == 80
               and row.get('effective_in_transit') == 10
               and row.get('candidate') == 10 and confirmed.status == 201)
        numbers['inventory'] = {'balance': balance_now.get('balance'),
                                'duplicate_balance': dup_balance,
                                'out_91_status': rejected.status,
                                'lead_time_demand': row.get('lead_time_demand'),
                                'effective_in_transit': row.get('effective_in_transit'),
                                'candidate': row.get('candidate')}

        # ----------------------------------------------------------------- M5 AI
        ai_status = client.get('/api/ai/status').json()
        dashboard_payload = client.get('/api/dashboard', h.ORIGINAL_RANGE).json()
        ask = client.post('/api/ai/ask', {
            'question': '2011-11-10到2011-12-09的销量是多少？',
            'scope': h.ORIGINAL_RANGE}).json()
        answer = ask.get('answer') or ''
        quoted = h.extract_numbers(h.flatten_text({'answer': answer,
                                                   'tool_calls': ask.get('tool_calls')}))
        # Numbers an honest local-rule answer may quote: dashboard for the range, the
        # order page the order tool prints, and the active batch manifest.
        orders_page = client.get('/api/orders', {**h.ORIGINAL_RANGE, 'page': 1,
                                                 'page_size': 200}).json()
        batches = client.get('/api/imports/batches').json()
        approved = all_numbers(dashboard_payload) | all_numbers(orders_page)
        for candidate in batches.get('batches') or []:
            if candidate.get('is_active'):
                approved |= extra_numbers_from_batch(candidate)
        unapproved = sorted({value for value in quoted
                             if abs(value) >= 1000 and not number_approved(value, approved)})
        key_numbers_mentioned = {name: any(abs(value - target) <= 0.51
                                           for value in quoted)
                                 for name, target in (('quantity', 851403),
                                                      ('revenue', 1694068.71),
                                                      ('orders', 2913))}
        record('m5.ai.status', 'AI 未配置密钥：本地规则层，不冒充大模型',
               {'llm_configured': False, 'key_present': False,
                'provider': 'local-rule-planner'},
               {'llm_configured': ai_status.get('llm_configured'),
                'key_present': ai_status.get('key_present'),
                'provider': ask.get('provider'), 'tools_count': ai_status.get('tools_count')},
               ai_status.get('llm_configured') is False
               and ai_status.get('key_present') is False
               and ask.get('provider') == 'local-rule-planner'
               and ask.get('llm_used') is False)
        record('m5.ai.numbers', 'AI 回答数字与工具结果一致（无工具外数字）',
               {'unapproved': [], 'key_numbers_mentioned': True},
               {'status': ask.get('status'), 'tool_calls': len(ask.get('tool_calls') or []),
                'unapproved': unapproved, 'key_numbers_mentioned': key_numbers_mentioned,
                'answer_head': answer[:160]},
               ask.get('status') == 'ok' and not unapproved
               and all(key_numbers_mentioned.values()))
        brief = client.post('/api/ai/brief', {'scope': h.ORIGINAL_RANGE})
        brief_payload = brief.json() or {}
        brief_url = (brief_payload.get('brief') or {}).get('url') or ''
        download = client.get(brief_url) if brief_url else None
        record('m5.ai.brief', '运营简报生成并可下载 Markdown',
               {'http': 201, 'download': 200, 'markdown': '>200 chars'},
               {'http': brief.status, 'download': getattr(download, 'status', None),
                'markdown_chars': len(brief_payload.get('markdown') or ''),
                'provider': brief_payload.get('provider')},
               brief.status == 201 and getattr(download, 'status', None) == 200
               and len(brief_payload.get('markdown') or '') > 200)
        numbers['ai'] = {'llm_configured': ai_status.get('llm_configured'),
                         'provider': ask.get('provider'),
                         'tools_count': ai_status.get('tools_count'),
                         'ask_status': ask.get('status'),
                         'unapproved_numbers': unapproved,
                         'brief_markdown_chars': len(brief_payload.get('markdown') or '')}

        # --------------------------------------------------------- contract/HTTP
        openapi = client.get('/openapi.json').json()
        documented = {(method.upper(), h.normalise_path(path))
                      for path, operations in (openapi.get('paths') or {}).items()
                      for method in operations
                      if method.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')}
        documented = {(method, path) for method, path in documented
                      if path.startswith('/api')}
        registered = {(method.upper(), h.normalise_path(path))
                      for path, methods in server.dump_routes()
                      for method in methods
                      if path.startswith('/api') and method.upper() != 'OPTIONS'}
        record('contract.openapi', 'openapi.json 与实际注册路由一致',
               {'count': len(documented)},
               {'documented': len(documented), 'registered': len(registered),
                'missing_in_docs': sorted(registered - documented),
                'missing_in_routes': sorted(documented - registered)},
               documented == registered and len(documented) >= 41)
        anonymous = h.ApiClient(server.base)
        status_codes = {
            '401': anonymous.get('/api/dashboard').status,
            '403': client.post('/api/forecast', {}, headers={'Origin': 'https://evil.example'}).status,
            '404': client.get('/api/orders/999999999').status,
            '400': client.get('/api/dashboard',
                              {'start': '2011-12-09', 'end': '2011-11-10'}).status,
        }
        record('contract.errors', '错误码 400/401/403/404 与契约一致',
               {'400': 400, '401': 401, '403': 403, '404': 404}, status_codes,
               status_codes == {'400': 400, '401': 401, '403': 403, '404': 404})
        numbers['contract'] = {'openapi_endpoints': len(documented),
                               'status_codes': status_codes}
    finally:
        server.stop()
        elapsed_total = time.monotonic() - started_at

    official_after = h.official_file_state() if hasattr(h, 'official_file_state') else None
    passed = sum(1 for check in checks if check['passed'])
    payload = {
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'suite': 'tests_api/acceptance_prototype',
        'scope': 'FastAPI 课程原型（不是完整交付；真实 AI 未接通，AI 仅本地规则层）',
        'environment': h.environment_record(),
        'isolation': {
            'mode': 'subprocess python run.py --port <free>，临时 ECE_RUNTIME_DIR + '
                    '复制的 retail.sqlite3（ECE_ORIGINAL_DB）',
            'temp_dir': str(server.dir),
            'official_files_before': official_before,
            'official_files_after': official_after,
            'official_files_unchanged': (official_before == official_after)
            if official_before is not None else None,
        },
        'key_numbers': numbers,
        'checks': checks,
        'summary': {
            'total': len(checks),
            'passed': passed,
            'failed': len(checks) - passed,
            'all_assertions_passed': passed == len(checks),
            'elapsed_seconds': round(elapsed_total, 2),
        },
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(payload['summary'], ensure_ascii=False))
    for check in checks:
        mark = 'PASS' if check['passed'] else 'FAIL'
        print(f"[{mark}] {check['id']} {check['name']}")
        if not check['passed']:
            print(f"        expected={check['expected']}")
            print(f"        actual={check['actual']}  {check['note']}")
    print(f'acceptance json: {OUT}')
    return 0 if payload['summary']['all_assertions_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
