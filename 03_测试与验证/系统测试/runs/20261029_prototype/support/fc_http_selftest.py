"""End-to-end HTTP self-test for M3 (forecast jobs) and M4 (simulated inventory).

Runs the real entry point (``python run.py --port 8783``) against an isolated runtime
directory that points its original batch at the real read-only ``runtime/retail.sqlite3``.
Authentication uses a directly inserted session row because the auth module is being
built by another teammate in parallel (M1); no auth file is modified.
"""
import csv
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(r'C:\Users\Administrator\11111\数据\学校项目')
SYS = ROOT / '电商运营系统'
RT = ROOT / '.tmp' / 'rt_fc'
PORT = 8783
BASE = f'http://127.0.0.1:{PORT}'
LOG_PATH = RT / 'server.log'

ENV = dict(os.environ)
ENV.update({
    'ECE_RUNTIME_DIR': str(RT),
    'ECE_ORIGINAL_DB': str(SYS / 'runtime' / 'retail.sqlite3'),
    'ECE_APP_DB': str(RT / 'app.sqlite3'),
    'ECE_ADMIN_PASSWORD': 'Selftest-Admin-123',
    'ECE_OPERATOR_PASSWORD': 'Selftest-Operator-123',
    'PYTHONIOENCODING': 'utf-8',
})
# The test process itself must resolve the same isolated state database as the server.
os.environ.update(ENV)

sys.path.insert(0, str(SYS))
from api import config, db, security  # noqa: E402  (loads .pydeps via api/__init__)
from api.services import batches as batch_service  # noqa: E402

FAILURES = []
CHECKS = 0


def check(label, condition, detail=''):
    global CHECKS
    CHECKS += 1
    text = f"{'PASS' if condition else 'FAIL'} {label}"
    if detail != '':
        text += f' | {detail}'
    print(text, flush=True)
    if not condition:
        FAILURES.append(label)


def start_server():
    RT.mkdir(parents=True, exist_ok=True)
    log = open(LOG_PATH, 'ab')
    proc = subprocess.Popen([sys.executable, 'run.py', '--port', str(PORT)], cwd=str(SYS),
                            env=ENV, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'server exited early with {proc.returncode}; see {LOG_PATH}')
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.2)
    raise RuntimeError('server did not open its port in 60s')


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def make_session(user_id=1, hours=12):
    token = security.new_session_token()
    with db.connect() as cx:
        cx.execute('insert into sessions(token_hash,user_id,created_at,expires_at,revoked,user_agent) '
                   'values(?,?,?,?,0,?)',
                   (security.token_digest(token), user_id, security.utc_now(),
                    security.session_expiry(hours), 'fc-selftest'))
    return token


def _normalise(result):
    status, raw, headers, timeout = result
    return status, raw, {str(key).lower(): value for key, value in headers.items()}


def request(method, path, body=None, token=None, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json; charset=utf-8')
    if token:
        req.add_header('Cookie', f'ece_session={token}')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = dict(resp.headers)
            return resp.status, resp.read(), headers, timeout
    except urllib.error.HTTPError as exc:
        headers = dict(exc.headers) if exc.headers else {}
        return exc.code, exc.read(), headers, timeout


def json_request(method, path, body=None, token=None, timeout=180):
    status, raw, headers, _timeout = request(method, path, body, token, timeout)
    headers = {str(key).lower(): value for key, value in headers.items()}
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return status, payload, headers, raw


def wait_job(token, job_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload, _, _ = json_request('GET', f'/api/jobs/{job_id}', token=token)
        if status != 200:
            return {'status': f'http-{status}', 'error': payload, 'id': job_id}
        job = payload['job']
        if job['status'] in ('succeeded', 'failed'):
            return job
        time.sleep(1.0)
    return {'status': 'timeout', 'id': job_id}


def parse_csv(raw):
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = raw.decode('utf-8-sig')
    rows = list(csv.DictReader(io.StringIO(text)))
    header = text.splitlines()[0].strip() if text else ''
    return bom, header, rows


def main():
    stop_server(None)
    shutil.rmtree(RT, ignore_errors=True)
    RT.mkdir(parents=True)
    server = start_server()
    try:
        token = make_session()
        print(f'started http://127.0.0.1:{PORT}  runtime={RT}', flush=True)

        # ---- auth gate -----------------------------------------------------------------
        status, payload, _, _ = json_request('GET', '/api/jobs/abc')
        check('未登录访问 /api/jobs/{id} → 401', status == 401 and payload.get('code') == 'unauthorized',
              f'{status} {payload}')

        # ---- M3 forecast: original batch, next 7 days ----------------------------------
        status, payload, _, _ = json_request('POST', '/api/forecast', {}, token=token)
        check('POST /api/forecast {} → 202 {job_id,status:queued,batch_id}',
              status == 202 and set(payload) == {'job_id', 'status', 'batch_id'}
              and payload['status'] == 'queued' and payload['batch_id'] == 'original',
              f'{status} {payload}')
        job_id = payload['job_id']
        job = wait_job(token, job_id)
        result = job.get('result') or {}
        rows = result.get('rows') or []
        check('未来7天预测任务成功', job['status'] == 'succeeded', f"status={job['status']} error={job.get('error')}")
        check('结果 7 行且日期连续 2011-12-10..16',
              [r['date'] for r in rows] == [f'2011-12-{d}' for d in range(10, 17)], [r['date'] for r in rows])
        check('未来窗口 actual 全为 null', all(r['actual'] is None for r in rows))
        check('每行含 baseline/h/abs_error 字段且 baseline 非空',
              all(set(r) >= {'date', 'h', 'prediction', 'baseline', 'actual', 'abs_error'}
                  and r['baseline'] is not None for r in rows))
        check('结果含 metrics/baseline_metrics/WAPE 字段',
              'metrics' in result and 'baseline_metrics' in result
              and 'WAPE' in result['metrics'] and 'WAPE' in result['baseline_metrics'],
              {'metrics': result.get('metrics'), 'baseline_metrics': result.get('baseline_metrics')})
        check('结果追加 batch_id/data_policy_version/model_version',
              result.get('batch_id') == 'original'
              and result.get('data_policy_version') == 'cross-sheet-multiset-v1-sales-v2-invoice-order'
              and result.get('model_version') == 'daily-v1-hw-fixed-lgbm400',
              {'batch_id': result.get('batch_id'), 'policy': result.get('data_policy_version'),
               'model_version': result.get('model_version')})
        check('任务记录 started/finished/elapsed',
              all(job.get(k) for k in ('created_at', 'started_at', 'finished_at'))
              and isinstance(job.get('elapsed_seconds'), (int, float)),
              {k: job.get(k) for k in ('created_at', 'started_at', 'finished_at', 'elapsed_seconds')})
        check('job.batch 为创建时批次 original', (job.get('batch') or {}).get('id') == 'original')
        print(f"  forecast job_id={job_id} elapsed={job['elapsed_seconds']}s "
              f"origin={result.get('origin')} method={result.get('actual_method')} "
              f"training_rows={result.get('training_rows')}", flush=True)

        # ---- CSV export of the forecast -------------------------------------------------
        status, raw, headers, _timeout = request('GET', f'/api/jobs/{job_id}/export', token=token)
        headers = {str(key).lower(): value for key, value in headers.items()}
        bom, header, csv_rows = parse_csv(raw) if status == 200 else (False, '', [])
        expected_header = ('origin,date,h,prediction,baseline,actual,abs_error,'
                           'model_version,data_policy_version,batch_id,actual_method')
        check('预测 CSV 200 + UTF-8 BOM + 表头 + 7行',
              status == 200 and bom and header == expected_header and len(csv_rows) == 7
              and 'text/csv' in headers.get('content-type', ''),
              f'{status} bom={bom} rows={len(csv_rows)} ctype={headers.get("Content-Type")}')
        check('预测 CSV 数据列正确（origin/batch/policy/method）',
              csv_rows and all(r['origin'] == '2011-12-09' and r['batch_id'] == 'original'
                               and r['data_policy_version'] == 'cross-sheet-multiset-v1-sales-v2-invoice-order'
                               and r['actual_method'] == 'blend_rule' and r['actual'] == '' for r in csv_rows),
              csv_rows[:1])
        check('CSV Content-Disposition 为附件下载',
              'attachment' in headers.get('content-disposition', ''), headers.get('content-disposition'))

        # ---- M3 forecast scored window (origin inside data → WAPE numeric) -------------
        status, payload, _, _ = json_request('POST', '/api/forecast', {'origin': '2011-11-01',
                                                                      'model': 'blend_rule'}, token=token)
        scored_job = wait_job(token, payload['job_id'])
        scored = scored_job.get('result') or {}
        check('覆盖窗口内起点预测成功且 WAPE/指标为数值',
              scored_job['status'] == 'succeeded' and scored['metrics']['n'] == 7
              and isinstance(scored['metrics']['WAPE'], float)
              and isinstance(scored['baseline_metrics']['WAPE'], float),
              {'n': scored.get('metrics', {}).get('n'),
               'WAPE': scored.get('metrics', {}).get('WAPE'),
               'baseline_WAPE': scored.get('baseline_metrics', {}).get('WAPE')})
        print(f"  scored forecast origin=2011-11-01 elapsed={scored_job['elapsed_seconds']}s "
              f"metrics={scored.get('metrics')}", flush=True)

        # ---- M3 backtest: small window --------------------------------------------------
        status, payload, _, _ = json_request('POST', '/api/backtest',
                                             {'start': '2011-10-01', 'end': '2011-10-07'}, token=token)
        check('POST /api/backtest 小窗口 → 202', status == 202 and payload['status'] == 'queued',
              f'{status} {payload}')
        bt_id = payload['job_id']
        bt = wait_job(token, bt_id)
        btr = bt.get('result') or {}
        check('小窗口回测成功且 scored_count>0',
              bt['status'] == 'succeeded' and btr.get('scored_count', 0) > 0,
              {'status': bt['status'], 'forecast_count': btr.get('forecast_count'),
               'scored_count': btr.get('scored_count'), 'origins_attempted': btr.get('origins_attempted')})
        check('回测按起点返回 origin_results（7 起点×7 行=49）',
              len(btr.get('origin_results') or []) == 7 and btr.get('forecast_count') == 49,
              {'origins': len(btr.get('origin_results') or []), 'forecast_count': btr.get('forecast_count')})
        check('回测 WAPE 为数值', isinstance(btr.get('metrics', {}).get('WAPE'), float),
              btr.get('metrics'))
        status, raw, _headers, _timeout = request('GET', f'/api/jobs/{bt_id}/export', token=token)
        _, _, bt_csv = parse_csv(raw) if status == 200 else (False, '', [])
        check('回测 CSV 49 行且 origin/actual_method 逐行填写',
              status == 200 and len(bt_csv) == 49
              and all(r['origin'] and r['actual_method'] for r in bt_csv),
              {'rows': len(bt_csv), 'first': bt_csv[0] if bt_csv else None})
        print(f"  backtest job_id={bt_id} elapsed={bt['elapsed_seconds']}s "
              f"scored={btr.get('scored_count')} WAPE={btr.get('metrics', {}).get('WAPE')}", flush=True)

        # ---- M3 validation errors -------------------------------------------------------
        status, payload, _, _ = json_request('POST', '/api/forecast', {'model': 'no_such_method'}, token=token)
        check('未知 model → 400', status == 400 and payload.get('code') == 'bad_request', f'{status} {payload}')
        status, payload, _, _ = json_request('POST', '/api/forecast', {'origin': '2012-06-01'}, token=token)
        check('origin 超出覆盖范围 → 400', status == 400 and payload.get('code') == 'bad_request',
              f'{status} {payload}')
        status, payload, _, _ = json_request('POST', '/api/backtest',
                                             {'start': '2011-10-01', 'end': '2011-12-01'}, token=token)
        check('回测窗口超56天 → 400', status == 400 and payload.get('code') == 'bad_request',
              f'{status} {payload}')
        status, payload, _, _ = json_request('GET', '/api/jobs/does-not-exist', token=token)
        check('不存在任务 → 404', status == 404 and payload.get('code') == 'not_found', f'{status} {payload}')

        # ---- M3 batch binding: switch active batch, stored result unchanged --------------
        original = batch_service.get_batch('original')
        batch_service.register_batch(
            batch_id='selftest-snapshot', label='自测切换批次', kind='upload', db_path=original['db_path'],
            source_file=original.get('source_file', ''), source_sha256='selftest',
            policy_version='selftest-policy',
            manifest={'date_min': '2009-12-01', 'date_max': '2011-12-09',
                      'policy_version': 'selftest-policy'})
        batch_service.set_active('selftest-snapshot')
        status, payload, _, _ = json_request('GET', f'/api/jobs/{job_id}', token=token)
        kept = payload['job']
        check('切换批次后旧任务仍绑定 original 且结果不变',
              kept['batch']['id'] == 'original' and kept['result']['batch_id'] == 'original'
              and len(kept['result']['rows']) == 7, {'batch': kept['batch']['id'],
                                                     'result_batch': kept['result']['batch_id']})
        status, payload, _, _ = json_request('POST', '/api/forecast', {'model': 'naive_week'}, token=token)
        check('切换后新任务绑定新批次 selftest-snapshot',
              status == 202 and payload['batch_id'] == 'selftest-snapshot', f'{status} {payload}')
        switched_job = wait_job(token, payload['job_id'])
        check('新批次任务成功且结果标注新批次',
              switched_job['status'] == 'succeeded'
              and switched_job['result']['batch_id'] == 'selftest-snapshot'
              and switched_job['result']['data_policy_version'] == 'selftest-policy',
              {'batch_id': switched_job.get('result', {}).get('batch_id'),
               'policy': switched_job.get('result', {}).get('data_policy_version')})
        batch_service.set_active('original')

        # ---- M4 inventory: fixed acceptance sample over HTTP ---------------------------
        status, payload, _, _ = json_request('POST', '/api/inventory/scenarios',
                                             {'name': 'HTTP验收场景', 'note': '固定样例'}, token=token)
        scenario = payload.get('scenario') or {}
        sid = scenario.get('id')
        check('POST 场景（stock_codes 省略）→ 201 + simulated',
              status == 201 and payload.get('simulated') is True and bool(sid)
              and scenario.get('item_count') == 0, f'{status} {payload}' if status != 201 else '')
        status, payload, _, _ = json_request('PUT', f'/api/inventory/scenarios/{sid}/items/C001',
                                             {'name': 'Delta Box', 'initial_qty': 100, 'lead_time_days': 8,
                                              'safety_stock': 30, 'daily_demand': 10}, token=token)
        check('PUT 商品 upsert（C001 不在历史批次也可创建）→ 202/200 余额100',
              status == 200 and payload['item']['balance'] == 100
              and payload['item']['name'] == 'Delta Box' and payload['item']['lead_time_days'] == 8
              and payload['simulated'] is True,
              payload.get('item') if isinstance(payload, dict) else payload)
        status, payload, _, _ = json_request('PUT', f'/api/inventory/scenarios/{sid}/items/C002',
                                             {'initial_qty': 5}, token=token)
        check('PUT 未给 name 时默认用 stock_code',
              status == 200 and payload['item']['name'] == 'C002' and payload['item']['balance'] == 5,
              payload.get('item') if isinstance(payload, dict) else payload)
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/movements',
                                             {'stock_code': 'C001', 'kind': 'in', 'qty': 20,
                                              'idempotency_key': 'http-in-1', 'note': '模拟入库'}, token=token)
        check('入库20 → 201 + 余额120',
              status == 201 and payload['movement']['balance_after'] == 120
              and payload['item']['balance'] == 120 and payload['simulated'] is True,
              f"{status} {payload.get('movement')}")
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/movements',
                                             {'stock_code': 'C001', 'kind': 'out', 'qty': 30,
                                              'idempotency_key': 'http-out-1'}, token=token)
        first_movement_id = payload['movement']['id']
        check('出库30 → 201 + 余额90',
              status == 201 and payload['movement']['balance_after'] == 90
              and payload['item']['balance'] == 90, f"{status} {payload.get('movement')}")
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/movements',
                                             {'stock_code': 'C001', 'kind': 'out', 'qty': 30,
                                              'idempotency_key': 'http-out-1', 'note': '重复提交'}, token=token)
        check('同 key 重复出库 → 200 duplicate:true 且不重复记账',
              status == 200 and payload.get('duplicate') is True
              and payload['movement']['id'] == first_movement_id
              and payload['item']['balance'] == 90, f'{status} {payload}')
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/movements', token=token)
        check('流水仍为2条且倒序 + simulated',
              status == 200 and len(payload['movements']) == 2
              and payload['movements'][0]['kind'] == 'out' and payload['simulated'] is True,
              f'{status} {len(payload.get("movements", []))}')
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/movements',
                                             {'stock_code': 'C001', 'kind': 'out', 'qty': 91,
                                              'idempotency_key': 'http-out-91'}, token=token)
        check('出库91被拒 400 且说明余额',
              status == 400 and payload.get('code') == 'bad_request' and '余额' in payload.get('error', ''),
              f'{status} {payload}')
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/replenishment/confirm',
                                             {'stock_code': 'C001', 'qty': 10,
                                              'expected_date': '2011-12-15',
                                              'idempotency_key': 'http-po-1'}, token=token)
        check('确认补货 → 201 只登记在途（余额仍90）',
              status == 201 and payload['item']['balance'] == 90
              and payload['in_transit']['status'] == 'in_transit'
              and payload['simulated'] is True, f'{status} {payload.get("item")}')
        transit_id = payload['in_transit']['id']
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/replenishment', token=token)
        row = next((i for i in payload['items'] if i['stock_code'] == 'C001'), {})
        check('补货候选 = 80+30-90-10 = 10，公式字符串返回',
              status == 200 and row.get('lead_time_demand') == 80 and row.get('current_qty') == 90
              and row.get('effective_in_transit') == 10 and row.get('safety_stock') == 30
              and row.get('candidate') == 10
              and payload.get('formula') == 'max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)'
              and payload.get('simulated') is True, row)
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/in-transit/{transit_id}/receive',
                                             token=token)
        check('在途到货 → 200 入库流水 + status=received + 余额100（同事务）',
              status == 200 and payload['movement']['kind'] == 'in'
              and payload['movement']['qty'] == 10 and payload['item']['balance'] == 100
              and payload['in_transit']['status'] == 'received' and payload['simulated'] is True,
              f'{status} {payload.get("movement")}')
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/in-transit', token=token)
        check('在途列表 1 条且状态 received',
              status == 200 and len(payload['items']) == 1
              and payload['items'][0]['status'] == 'received' and payload['simulated'] is True,
              payload.get('items'))
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/items', token=token)
        item = next((i for i in payload['items'] if i['stock_code'] == 'C001'), {})
        check('items 返回全部计算字段且 simulated',
              payload.get('simulated') is True and item.get('balance') == 100
              and {'in_transit_effective', 'in_transit_total', 'replenishment_candidate'} <= set(item),
              item)
        status, payload, _, _ = json_request('POST', '/api/inventory/scenarios',
                                             {'name': '批次商品场景', 'stock_codes': ['85123A']}, token=token)
        batch_sid = (payload.get('scenario') or {}).get('id')
        check('创建场景可按 stock_codes 从当前批次建商品',
              status == 201 and payload['scenario']['item_count'] == 1 and payload['simulated'] is True,
              payload.get('scenario'))
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{batch_sid}/items', token=token)
        named = (payload.get('items') or [{}])[0]
        check('批次商品名称已从历史数据回填',
              named.get('stock_code') == '85123A' and bool(named.get('name')), named)
        status, payload, _, _ = json_request('GET', '/api/inventory/scenarios', token=token)
        listed = next((s for s in payload['scenarios'] if s['id'] == sid), {})
        check('场景列表带 item_count + simulated',
              status == 200 and listed.get('item_count') == 2 and payload['simulated'] is True,
              listed)
        status, payload, _, _ = json_request('POST', f'/api/inventory/scenarios/{sid}/movements',
                                             {'stock_code': 'C001', 'kind': 'out', 'qty': 0}, token=token)
        check('qty=0 → 400', status == 400 and payload.get('code') == 'bad_request', f'{status} {payload}')
        status, payload, _, _ = json_request('PUT', f'/api/inventory/scenarios/{sid}/items/C001',
                                             {'lead_time_days': 400}, token=token)
        check('提前期400 → 400', status == 400 and payload.get('code') == 'bad_request', f'{status} {payload}')
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/movements?limit=999',
                                             token=token)
        check('limit 超上限 → 400', status == 400 and payload.get('code') == 'bad_request', f'{status} {payload}')
        status, payload, _, _ = json_request('GET', '/api/inventory/scenarios/not-a-scenario/items', token=token)
        check('未知场景 → 404', status == 404 and payload.get('code') == 'not_found', f'{status} {payload}')

        # ---- restart: persisted job result + inventory state ---------------------------
        print('restarting server ...', flush=True)
        stop_server(server)
        server = start_server()
        status, payload, _, _ = json_request('GET', f'/api/jobs/{job_id}', token=token)
        after = payload.get('job') if status == 200 else None
        check('重启后 succeeded 任务仍可查（结果持久化）',
              status == 200 and after and after['status'] == 'succeeded'
              and len(after['result']['rows']) == 7 and after['result']['batch_id'] == 'original',
              {'status': status, 'job_status': (after or {}).get('status'),
               'rows': len((after or {}).get('result', {}).get('rows', []))})
        status, raw, _headers, _timeout = request('GET', f'/api/jobs/{job_id}/export', token=token)
        _, _, restart_csv = parse_csv(raw) if status == 200 else (False, '', [])
        check('重启后 CSV 导出仍可用（7行）', status == 200 and len(restart_csv) == 7,
              {'status': status, 'rows': len(restart_csv)})
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/items', token=token)
        restart_item = next((i for i in payload['items'] if i['stock_code'] == 'C001'), {})
        check('重启后库存场景与余额仍可查（100，3条流水）',
              status == 200 and restart_item.get('balance') == 100,
              {'balance': restart_item.get('balance')})
        status, payload, _, _ = json_request('GET', f'/api/inventory/scenarios/{sid}/movements', token=token)
        check('重启后流水 3 条', status == 200 and len(payload['movements']) == 3,
              len(payload.get('movements', [])))
    finally:
        stop_server(server)
    print(f'\nCHECKS={CHECKS} FAILURES={FAILURES if FAILURES else "none"}', flush=True)
    return 0 if not FAILURES else 1


if __name__ == '__main__':
    import traceback
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        code = 1
    print(f'EXIT_CODE={code}', flush=True)
    sys.exit(code)
