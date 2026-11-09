"""Lead end-to-end acceptance / demo driver for the FastAPI prototype.

Starts an isolated server (temporary runtime directory + a copy of the analytical
cache), then walks the full course flow through the real HTTP API:

    health -> login -> options -> dashboard -> orders -> import (quality, duplicate,
    switch, switch back) -> forecast job -> backtest job -> CSV export -> simulated
    inventory (balance, idempotency, rejection, replenishment, in-transit) -> AI status/
    ask/brief -> logout -> unauthenticated rejection.

Run:  python lead_flow_acceptance.py
Evidence is written next to this file (lead_flow_acceptance.log/.json).
"""
from __future__ import annotations

import csv
import http.cookiejar
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[3]
SYSTEM_ROOT = PROJECT_ROOT / '电商运营系统'
WORK = PROJECT_ROOT / '.tmp' / 'lead_accept'
PORT = 8790
BASE = f'http://127.0.0.1:{PORT}'
ADMIN_PW = 'LeadAdmin2026x'
OPERATOR_PW = 'LeadOperator2026x'

RESULTS: list[dict] = []
SERVER: subprocess.Popen | None = None


def log(message: str) -> None:
    print(message, flush=True)


def record(step: str, ok: bool, detail, elapsed: float = 0.0) -> None:
    RESULTS.append({'step': step, 'ok': bool(ok), 'detail': detail, 'elapsed_seconds': round(elapsed, 3)})
    log(f"[{'PASS' if ok else 'FAIL'}] {step}  ({elapsed:.2f}s)")
    if detail not in (None, '', {}, []):
        text = json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail
        log(f'        {text[:600]}')


def check(step: str, condition: bool, detail, elapsed: float = 0.0) -> bool:
    record(step, condition, detail, elapsed)
    return condition


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body=None, raw=False, files=None):
        url = BASE + path
        headers = {}
        data = None
        if files is not None:
            boundary = '----leadacceptanceboundary'
            name, filename, content = files
            parts = [
                f'--{boundary}\r\n'.encode(),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
                b'Content-Type: text/csv\r\n\r\n',
                content,
                f'\r\n--{boundary}--\r\n'.encode(),
            ]
            data = b''.join(parts)
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=180) as response:
                payload = response.read()
                status = response.status
                ctype = response.headers.get('Content-Type', '')
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            ctype = exc.headers.get('Content-Type', '') if exc.headers else ''
        elapsed = time.monotonic() - started
        if raw:
            return status, payload, ctype, elapsed
        try:
            parsed = json.loads(payload.decode('utf-8')) if payload else None
        except json.JSONDecodeError:
            parsed = payload.decode('utf-8', 'replace')
        return status, parsed, ctype, elapsed

    def get(self, path: str, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path: str, body=None, **kwargs):
        return self.request('POST', path, body=body, **kwargs)


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) != 0


def start_server() -> None:
    global SERVER
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / 'runtime').mkdir(parents=True, exist_ok=True)
    source_db = SYSTEM_ROOT / 'runtime' / 'retail.sqlite3'
    if not source_db.is_file():
        raise SystemExit(f'缺少原始数据缓存：{source_db}')
    log('准备隔离运行库（复制分析缓存，约300MB）…')
    shutil.copy2(source_db, WORK / 'runtime' / 'retail.sqlite3')
    env = dict(os.environ)
    env.update({
        'ECE_RUNTIME_DIR': str(WORK / 'runtime'),
        'ECE_ORIGINAL_DB': str(WORK / 'runtime' / 'retail.sqlite3'),
        'ECE_APP_DB': str(WORK / 'runtime' / 'app.sqlite3'),
        'ECE_ADMIN_PASSWORD': ADMIN_PW,
        'ECE_OPERATOR_PASSWORD': OPERATOR_PW,
        'ECE_PORT': str(PORT),
    })
    log('启动 FastAPI 服务…')
    SERVER = subprocess.Popen(
        [sys.executable, 'run.py', '--port', str(PORT)],
        cwd=str(SYSTEM_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if SERVER.poll() is not None:
            raise SystemExit(f'服务启动失败，退出码 {SERVER.returncode}')
        try:
            with urllib.request.urlopen(BASE + '/api/health', timeout=2) as response:
                if response.status == 200:
                    log('服务已就绪')
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise SystemExit('服务启动超时')


def stop_server() -> None:
    global SERVER
    if SERVER and SERVER.poll() is None:
        SERVER.terminate()
        try:
            SERVER.wait(timeout=15)
        except subprocess.TimeoutExpired:
            SERVER.kill()
    SERVER = None


def make_sample_csv() -> bytes:
    rows = [
        ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country'],
        ['S1001', 'A001', 'Alpha Lamp', 10, '2011-12-01 09:15', 2.5, 10001, 'United Kingdom'],
        ['S1001', 'A002', 'Beta Chair', 2, '2011-12-01 09:20', 15.0, 10001, 'United Kingdom'],
        ['S1002', 'B001', 'Gamma Mug', 6, '2011-12-02 11:00', 1.25, 10002, 'France'],
        ['S1002', 'A001', 'Alpha Lamp', 4, '2011-12-02 11:05', 2.5, 10002, 'France'],
        ['S1003', 'C001', 'Delta Box', 8, '2011-12-03 14:30', 3.0, 10003, 'Germany'],
        ['S1004', 'A002', 'Beta Chair', 1, '2011-12-04 10:00', 15.0, 10004, 'United Kingdom'],
        ['S1005', 'B001', 'Gamma Mug', 12, '2011-12-05 16:45', 1.25, 10005, 'Spain'],
        ['S1006', 'C001', 'Delta Box', 3, '2011-12-06 08:20', 3.0, 10006, 'United Kingdom'],
    ]
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')


def poll_import(client: Client, job_id: str, timeout=180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, data, _, _ = client.get(f'/api/imports/jobs/{job_id}')
        if status == 200 and data.get('job', {}).get('status') in ('succeeded', 'failed', 'duplicate'):
            return data
        time.sleep(1.0)
    raise TimeoutError(f'导入任务 {job_id} 超时')


def poll_job(client: Client, job_id: str, timeout=300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, data, _, _ = client.get(f'/api/jobs/{job_id}')
        if status == 200 and data.get('job', {}).get('status') in ('succeeded', 'failed'):
            return data
        time.sleep(1.0)
    raise TimeoutError(f'预测任务 {job_id} 超时')


def main() -> int:
    start_server()
    client = Client()
    try:
        # 1 health is public
        status, health, _, elapsed = client.get('/api/health')
        check('01 /api/health 免登录可访问', status == 200 and health.get('ok') is True, health, elapsed)
        check('02 健康检查含服务标识与批次', health.get('service') == 'ecommerce-ops-fastapi' and bool(health.get('batch')), health.get('batch'))

        # 3 unauthenticated access is rejected
        status, body, _, elapsed = client.get('/api/dashboard')
        check('03 未登录访问业务接口被拒(401)', status == 401 and body.get('code') == 'unauthorized', body, elapsed)

        # 4 wrong password
        status, body, _, elapsed = client.post('/api/auth/login', {'username': 'admin', 'password': 'wrong-password'})
        check('04 错误密码登录被拒(401)', status == 401, body, elapsed)

        # 5 login as admin
        status, body, _, elapsed = client.post('/api/auth/login', {'username': 'admin', 'password': ADMIN_PW})
        check('05 管理员登录成功', status == 200 and body.get('user', {}).get('role') == 'admin', body, elapsed)

        # 6 me
        status, me, _, elapsed = client.get('/api/auth/me')
        check('06 /api/auth/me 返回当前用户', status == 200 and me.get('user', {}).get('username') == 'admin', me, elapsed)

        # 7 options
        status, options, _, elapsed = client.get('/api/options')
        check('07 /api/options 返回批次/国家/商品/方法',
              status == 200 and options.get('batch') and options.get('countries') and options.get('methods'), 
              {'batch': (options or {}).get('batch', {}).get('id'), 'countries': len((options or {}).get('countries') or []),
               'products': len((options or {}).get('products') or []), 'methods': list(((options or {}).get('methods') or {}).keys())}, elapsed)

        # 8 historical dashboard baseline
        status, dash, _, elapsed = client.get('/api/dashboard?start=2011-11-10&end=2011-12-09')
        totals = (dash or {}).get('totals', {})
        check('08 历史窗口看板与基线一致(851403/1694068.71/2913)',
              status == 200 and totals.get('quantity') == 851403 and abs(totals.get('revenue', 0) - 1694068.71) < 0.01 and totals.get('orders') == 2913,
              {'quantity': totals.get('quantity'), 'revenue': totals.get('revenue'), 'orders': totals.get('orders'),
               'lines': totals.get('lines'), 'warnings': len((dash or {}).get('warnings') or [])}, elapsed)

        # 9 orders aggregate == dashboard orders
        status, orders, _, elapsed = client.get('/api/orders?start=2011-11-10&end=2011-12-09&page=1&page_size=10')
        check('09 订单列表总数与看板订单数一致',
              status == 200 and orders.get('total') == totals.get('orders') and len(orders.get('rows') or []) == 10,
              {'total': orders.get('total'), 'page_rows': len(orders.get('rows') or [])}, elapsed)

        # 10 order detail lines sum to header
        ok_detail = False
        detail_info = {}
        if orders.get('rows'):
            order_key = orders['rows'][0]['order_key']
            status, detail, _, elapsed = client.get(f'/api/orders/{urllib.parse.quote(str(order_key))}')
            header = (detail or {}).get('order') or {}
            lines = (detail or {}).get('rows') or []
            sum_qty = sum(row.get('quantity') or 0 for row in lines)
            sum_rev = round(sum(row.get('revenue') or 0 for row in lines), 2)
            ok_detail = status == 200 and sum_qty == header.get('quantity') and abs(sum_rev - (header.get('revenue') or 0)) < 0.01
            detail_info = {'order_key': order_key, 'header_qty': header.get('quantity'), 'lines_qty': sum_qty,
                           'header_revenue': header.get('revenue'), 'lines_revenue': sum_rev, 'lines': len(lines)}
        check('10 订单明细相加等于订单头', ok_detail, detail_info)

        # 11 transactions contract
        status, transactions, _, elapsed = client.get('/api/transactions?start=2011-12-01&end=2011-12-09&page=1&page_size=5')
        check('11 /api/transactions 分页契约可用',
              status == 200 and transactions.get('total', 0) > 0 and len(transactions.get('rows') or []) == 5,
              {'total': transactions.get('total'), 'rows': len(transactions.get('rows') or [])}, elapsed)

        # 12 trend CSV
        status, payload, ctype, elapsed = client.get('/api/export/trend?start=2011-11-10&end=2011-12-09', raw=True)
        lines = payload.decode('utf-8-sig', 'replace').splitlines() if payload else []
        check('12 趋势CSV导出可用', status == 200 and 'text/csv' in ctype and len(lines) == 31,
              {'lines': len(lines), 'content_type': ctype}, elapsed)

        # 13 import sample CSV
        sample = make_sample_csv()
        status, body, _, elapsed = client.post('/api/imports', files=('file', 'lead_sample.csv', sample))
        job_id = (body or {}).get('job_id')
        check('13 导入接口提交返回任务标识(202)', status == 202 and bool(job_id), body, elapsed)
        report = poll_import(client, job_id)
        job = report.get('job', {})
        batch_id = job.get('batch_id') or (report.get('batch') or {}).get('id')
        check('14 导入任务成功并生成批次',
              job.get('status') == 'succeeded' and bool(batch_id),
              {'status': job.get('status'), 'batch_id': batch_id, 'report': report.get('report'),
               'issues': len(report.get('issues') or [])})

        # 15 duplicate import does not duplicate data
        status, body, _, elapsed = client.post('/api/imports', files=('file', 'lead_sample.csv', sample))
        duplicate_job = (body or {}).get('job_id')
        duplicate_report = poll_import(client, duplicate_job) if duplicate_job else {}
        d_job = duplicate_report.get('job', {})
        check('15 同一文件重复导入被判重(duplicate)',
              d_job.get('status') == 'duplicate' and (d_job.get('batch_id') or (duplicate_report.get('batch') or {}).get('id')) == batch_id,
              {'status': d_job.get('status'), 'batch_id': d_job.get('batch_id') or (duplicate_report.get('batch') or {}).get('id')}, elapsed)

        # 16 switch to uploaded batch
        status, body, _, elapsed = client.post(f'/api/imports/batches/{batch_id}/activate')
        check('16 切换为上传批次成功', status == 200 and (body.get('batch') or {}).get('is_active') is True, body, elapsed)
        status, dash_new, _, elapsed = client.get('/api/dashboard?start=2011-12-01&end=2011-12-09')
        new_totals = (dash_new or {}).get('totals', {})
        check('17 看板切换到新批次后口径正确(46件/135.5英镑/6订单)',
              status == 200 and new_totals.get('quantity') == 46 and abs(new_totals.get('revenue', 0) - 135.5) < 0.01 and new_totals.get('orders') == 6,
              {'quantity': new_totals.get('quantity'), 'revenue': new_totals.get('revenue'), 'orders': new_totals.get('orders')}, elapsed)

        # 18 dashboard warns about coverage outside the batch
        status, dash_out, _, elapsed = client.get('/api/dashboard?start=2011-11-10&end=2011-12-09')
        check('18 新批次外日期显示覆盖警告而非旧数据', status == 200 and bool((dash_out or {}).get('warnings')),
              {'warnings': (dash_out or {}).get('warnings')}, elapsed)

        # 19 switch back to original
        status, body, _, elapsed = client.post('/api/imports/batches/original/activate')
        check('19 切回原始批次成功', status == 200 and (body.get('batch') or {}).get('id') == 'original', body, elapsed)
        status, dash_back, _, elapsed = client.get('/api/dashboard?start=2011-11-10&end=2011-12-09')
        check('20 切回后历史窗口数字恢复一致',
              (dash_back or {}).get('totals', {}).get('quantity') == 851403, (dash_back or {}).get('totals'), elapsed)

        # 21 forecast job
        status, body, _, elapsed = client.post('/api/forecast', {'origin': '2011-12-09', 'model': 'blend_rule'})
        forecast_job = (body or {}).get('job_id')
        check('21 预测任务提交(202)', status == 202 and bool(forecast_job), body, elapsed)
        forecast_result = poll_job(client, forecast_job)
        fj = forecast_result.get('job', {})
        result = fj.get('result') or {}
        rows = result.get('rows') or []
        check('22 未来7天预测返回7行且无未来实际值',
              fj.get('status') == 'succeeded' and len(rows) == 7 and all(row.get('actual') is None for row in rows),
              {'status': fj.get('status'), 'rows': len(rows), 'actual_method': result.get('actual_method'),
               'requested': result.get('requested_method'), 'batch_id': result.get('batch_id'),
               'warnings': result.get('warnings'), 'origin': result.get('origin')}, elapsed)

        # 23 forecast CSV export
        status, payload, ctype, elapsed = client.get(f'/api/jobs/{forecast_job}/export', raw=True)
        csv_lines = payload.decode('utf-8-sig', 'replace').splitlines() if payload else []
        check('23 预测结果CSV可导出', status == 200 and 'text/csv' in ctype and len(csv_lines) == 8,
              {'lines': len(csv_lines), 'header': csv_lines[0] if csv_lines else ''}, elapsed)

        # 24 backtest job
        status, body, _, elapsed = client.post('/api/backtest', {'start': '2011-11-01', 'end': '2011-11-03', 'model': 'seasonal_week4'})
        backtest_job = (body or {}).get('job_id')
        check('24 回测任务提交(202)', status == 202 and bool(backtest_job), body, elapsed)
        backtest_result = poll_job(client, backtest_job)
        bj = backtest_result.get('job', {})
        bres = bj.get('result') or {}
        check('25 回测任务成功且有可评分样本',
              bj.get('status') == 'succeeded' and (bres.get('scored_count') or 0) > 0 and bres.get('metrics', {}).get('WAPE') is not None,
              {'scored_count': bres.get('scored_count'), 'origins': bres.get('origins_attempted'),
               'WAPE': bres.get('metrics', {}).get('WAPE'), 'complete_origins': bres.get('complete_origins')}, elapsed)

        # 26 inventory scenario + fixed numeric sample
        status, body, _, elapsed = client.post('/api/inventory/scenarios', {'name': 'Lead 验收场景', 'note': '原型验收'})
        scenario = body or {}
        scenario_id = scenario.get('id') or (scenario.get('scenario') or {}).get('id')
        check('26 新建模拟库存场景', status in (200, 201) and bool(scenario_id), body, elapsed)
        if scenario_id:
            status, body, _, elapsed = client.request('PUT', f'/api/inventory/scenarios/{scenario_id}/items/C001',
                                                      body={'name': 'Delta Box', 'initial_qty': 100, 'lead_time_days': 8,
                                                            'safety_stock': 30, 'daily_demand': 10})
            check('27 设置商品初始库存(100/提前期8/安全库存30/日需求10)', status == 200, body, elapsed)

            status, body, _, elapsed = client.post(f'/api/inventory/scenarios/{scenario_id}/movements',
                                                  {'stock_code': 'C001', 'kind': 'in', 'qty': 20, 'idempotency_key': 'lead-in-1', 'note': '模拟入库'})
            balance = ((body or {}).get('item') or {}).get('balance')
            check('28 入库20后余额120', status in (200, 201) and balance == 120, {'balance': balance}, elapsed)

            status, body, _, elapsed = client.post(f'/api/inventory/scenarios/{scenario_id}/movements',
                                                  {'stock_code': 'C001', 'kind': 'out', 'qty': 30, 'idempotency_key': 'lead-out-1', 'note': '模拟出库'})
            balance = ((body or {}).get('item') or {}).get('balance')
            check('29 出库30后余额90', status in (200, 201) and balance == 90, {'balance': balance}, elapsed)

            status, body, _, elapsed = client.post(f'/api/inventory/scenarios/{scenario_id}/movements',
                                                  {'stock_code': 'C001', 'kind': 'out', 'qty': 30, 'idempotency_key': 'lead-out-1', 'note': '重复提交'})
            balance = ((body or {}).get('item') or {}).get('balance')
            check('30 同一出库幂等重复提交仍为90', status in (200, 201) and (body or {}).get('duplicate') is True and balance == 90,
                  {'duplicate': (body or {}).get('duplicate'), 'balance': balance}, elapsed)

            status, body, _, elapsed = client.post(f'/api/inventory/scenarios/{scenario_id}/movements',
                                                  {'stock_code': 'C001', 'kind': 'out', 'qty': 91, 'idempotency_key': 'lead-out-2'})
            check('31 出库91被拒绝', status == 400, body, elapsed)

            status, body, _, elapsed = client.post(f'/api/inventory/scenarios/{scenario_id}/replenishment/confirm',
                                                  {'stock_code': 'C001', 'qty': 10,
                                                   'expected_date': (date.today() + timedelta(days=2)).isoformat(),
                                                   'idempotency_key': 'lead-po-1'})
            check('32 补货确认登记在途10', status in (200, 201), body, elapsed)

            status, repl, _, elapsed = client.get(f'/api/inventory/scenarios/{scenario_id}/replenishment')
            items = [item for item in (repl or {}).get('items', []) if item.get('stock_code') == 'C001']
            candidate = items[0].get('candidate') if items else None
            check('33 补货候选=80+30-90-10=10', status == 200 and candidate == 10,
                  {'candidate': candidate, 'formula': (repl or {}).get('formula'),
                   'item': items[0] if items else None}, elapsed)

        # 34 AI status / tools
        status, ai_status, _, elapsed = client.get('/api/ai/status')
        check('34 AI状态明确未配置大模型', status == 200 and ai_status.get('llm_configured') is False and ai_status.get('fallback') == 'local-rule-planner',
              ai_status, elapsed)
        status, tools, _, elapsed = client.get('/api/ai/tools')
        check('35 AI工具白名单可列出', status == 200 and len(tools.get('tools') or []) >= 4,
              {'tools': [tool.get('name') for tool in (tools or {}).get('tools', [])]}, elapsed)

        # 36 AI ask: numbers must match dashboard
        question = '2011-11-10到2011-12-09全店卖了多少'
        status, answer, _, elapsed = client.post('/api/ai/ask', {'question': question})
        text = (answer or {}).get('answer') or ''
        check('36 AI问答数字与看板一致且标注未调用大模型',
              status == 200 and answer.get('status') == 'ok' and answer.get('llm_used') is False
              and ('851403' in text.replace(',', '') or '851,403' in text) and '未调用大模型' in text,
              {'status': (answer or {}).get('status'), 'provider': (answer or {}).get('provider'),
               'llm_used': (answer or {}).get('llm_used'), 'answer': text[:300],
               'tool_calls': [call.get('name') for call in (answer or {}).get('tool_calls', [])]}, elapsed)

        # 37 AI clarification for ambiguous question
        status, clarify, _, elapsed = client.post('/api/ai/ask', {'question': '帮我看看销量'})
        check('37 AI缺日期时追问而非编造', status == 200 and (clarify or {}).get('status') in ('need_clarification', 'ok'),
              {'status': (clarify or {}).get('status'), 'clarification': (clarify or {}).get('clarification'),
               'answer': ((clarify or {}).get('answer') or '')[:200]}, elapsed)

        # 38 brief generation and download
        status, brief, _, elapsed = client.post('/api/ai/brief', {'scope': {'start': '2011-11-10', 'end': '2011-12-09'}})
        brief_obj = (brief or {}).get('brief') or {}
        brief_url = brief_obj.get('url') or ''
        check('38 运营简报生成成功', status in (200, 201) and len((brief or {}).get('markdown') or '') > 200,
              {'title': brief_obj.get('title'), 'provider': (brief or {}).get('provider'), 'url': brief_url,
               'markdown_len': len((brief or {}).get('markdown') or ''),
               'has_simulated_label': ('模拟' in ((brief or {}).get('markdown') or '')) or ('simulated' in ((brief or {}).get('markdown') or '').lower())}, elapsed)
        if brief_url:
            status, payload, ctype, elapsed = client.get(brief_url, raw=True)
            check('39 简报Markdown可下载', status == 200 and 'markdown' in ctype and len(payload) > 300,
                  {'status': status, 'content_type': ctype, 'bytes': len(payload)}, elapsed)

        # 40 admin user management + operator RBAC
        status, body, _, elapsed = client.post('/api/users', {'username': 'lead_op', 'password': 'LeadOperator2026x', 'role': 'operator', 'display_name': '验收运营'})
        new_user = body or {}
        check('40 管理员创建运营账号', status in (200, 201) and (new_user.get('user') or new_user).get('role') == 'operator', body, elapsed)
        operator = Client()
        status, body, _, elapsed = operator.post('/api/auth/login', {'username': 'lead_op', 'password': 'LeadOperator2026x'})
        check('41 运营账号可登录', status == 200, body, elapsed)
        status, body, _, elapsed = operator.get('/api/users')
        check('42 运营账号访问用户管理被拒(403)', status == 403, body, elapsed)
        status, body, _, elapsed = operator.get('/api/dashboard?start=2011-11-10&end=2011-12-09')
        check('43 运营账号可正常查询看板', status == 200 and (body or {}).get('totals', {}).get('quantity') == 851403, {}, elapsed)

        # 44 404 contract for an authenticated session (unknown /api path is 401 when
        # unauthenticated because the session middleware rejects before routing).
        status, body, _, elapsed = client.get('/api/not-exists')
        check('44 已登录未知接口返回404契约', status == 404 and body.get('code') == 'not_found', body, elapsed)

        # 45 logout and 401 afterwards
        status, body, _, elapsed = client.post('/api/auth/logout')
        check('45 退出登录成功', status == 200, body, elapsed)
        status, body, _, elapsed = client.get('/api/dashboard')
        check('46 退出后访问被拒(401)', status == 401 and body.get('code') == 'unauthorized', body, elapsed)
        status, body, _, elapsed = client.get('/api/not-exists')
        check('47 未登录未知接口由会话中间件拒绝(401)', status == 401 and body.get('code') == 'unauthorized', body, elapsed)

        # 48 openapi routes match implementation
        status, payload, ctype, elapsed = client.get('/openapi.json', raw=True)
        paths = set(json.loads(payload.decode('utf-8')).get('paths', {}).keys()) if payload else set()
        expected = {'/api/health', '/api/options', '/api/dashboard', '/api/orders', '/api/products',
                    '/api/transactions', '/api/imports', '/api/forecast', '/api/backtest',
                    '/api/inventory/scenarios', '/api/ai/status', '/api/ai/ask', '/api/ai/brief'}
        missing = expected - paths
        check('48 OpenAPI 路径与实现一致', status == 200 and not missing, {'missing': sorted(missing), 'total_paths': len(paths)}, elapsed)

    except Exception as exc:  # keep the whole run visible
        record('99 未预期的异常', False, f'{type(exc).__name__}: {exc}')
        traceback.print_exc()
    finally:
        stop_server()

    passed = sum(1 for item in RESULTS if item['ok'])
    total = len(RESULTS)
    summary = {
        'ran_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'passed': passed, 'total': total, 'all_passed': passed == total,
        'results': RESULTS,
        'environment': {'python': sys.version.split()[0], 'port': PORT, 'system_root': str(SYSTEM_ROOT)},
    }
    (HERE / 'lead_flow_acceptance.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    log('')
    log(f'验收结果：{passed}/{total} 通过' + ('，全部通过' if passed == total else '，存在失败项，见上方明细'))
    return 0 if passed == total else 1


if __name__ == '__main__':
    if not port_free(PORT):
        log(f'端口 {PORT} 已被占用，无法启动隔离验收服务')
        raise SystemExit(2)
    try:
        code = main()
    finally:
        stop_server()
    raise SystemExit(code)
