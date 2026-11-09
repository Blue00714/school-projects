"""M5 AI assistant acceptance runner (真实执行，不造假).

Phase 1: isolated unconfigured server on 127.0.0.1:8784, real login, 30 fixed questions,
         brief generation/download, parameter validation and 401 checks.
Phase 2: isolated server on 127.0.0.1:8785 with a fake key/base URL -> config failure path,
         settings override merge and secret-leak scan.
Phase 3: in-process simulation of a missing inventory module (tolerant import path).

Results are written to .tmp/ai_accept/ (JSON + server logs).  The Markdown report is
generated from these real results by a separate renderer; nothing is hand-written here.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(r'C:\Users\Administrator\11111\数据\学校项目')
SYS = ROOT / '电商运营系统'
OUT = ROOT / '.tmp' / 'ai_accept'
OUT.mkdir(parents=True, exist_ok=True)
PYDEPS = str(SYS / '.pydeps')
ORIGINAL_DB = str(SYS / 'runtime' / 'retail.sqlite3')
PY = sys.executable
SENTINEL = 'sk-acceptance-SENTINEL-do-not-leak-7f3a9c'

RESULTS = {'phase1': {}, 'phase2': {}, 'phase3': {}, 'questions': [], 'extra_checks': []}


# --------------------------------------------------------------------------- utilities
def wait_port(port, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.3)
    return False


def wait_health(port, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, body, _ = http_json(port, 'GET', '/api/health', cookie=None)
            if status == 200 and body.get('ok'):
                return True
        except urllib.error.URLError:
            pass
        time.sleep(0.3)
    return False


def http_json(port, method, path, payload=None, cookie=None):
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if cookie:
        headers['Cookie'] = cookie
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode('utf-8', 'replace')
            status = response.status
            response_headers = response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', 'replace')
        status = exc.code
        response_headers = exc.headers
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {'_raw': raw}
    return status, body, response_headers


def http_text(port, path, cookie):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                     headers={'Cookie': cookie})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode('utf-8', 'replace'), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace'), exc.headers


def login(port, username='admin', password='AdminPass123!'):
    status, body, headers = http_json(port, 'POST', '/api/auth/login',
                                      {'username': username, 'password': password})
    if status != 200:
        raise RuntimeError(f'login failed: {status} {body}')
    cookie = headers.get('Set-Cookie', '').split(';')[0]
    if not cookie.startswith('ece_session='):
        raise RuntimeError(f'no session cookie: {headers}')
    return cookie


def pick_port(preferred):
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', int(preferred)))
            return int(preferred)
    except OSError:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return int(sock.getsockname()[1])


def start_server(port, runtime_dir, extra_env=None):
    port = pick_port(port)
    runtime_dir = Path(runtime_dir)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        'PYTHONPATH': PYDEPS,
        'ECE_RUNTIME_DIR': str(runtime_dir),
        'ECE_ORIGINAL_DB': ORIGINAL_DB,
        'ECE_APP_DB': str(runtime_dir / 'app.sqlite3'),
        'ECE_ADMIN_PASSWORD': 'AdminPass123!',
        'ECE_OPERATOR_PASSWORD': 'Operator123!',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUTF8': '1',
        'PYTHONUNBUFFERED': '1',
    })
    for key in ('ECE_AI_BASE_URL', 'ECE_AI_API_KEY', 'ECE_AI_MODEL'):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    log_path = OUT / f'server_{port}.log'
    log = log_path.open('w', encoding='utf-8')
    proc = subprocess.Popen([PY, 'run.py', '--port', str(port)], cwd=str(SYS), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    if not wait_port(port):
        proc.kill()
        raise RuntimeError(f'server on {port} did not start; see {log_path}')
    if proc.poll() is not None:
        raise RuntimeError(f'server on {port} exited early; see {log_path}')
    if not wait_health(port):
        proc.kill()
        raise RuntimeError(f'health check on {port} failed; see {log_path}')
    return proc, log, port


def stop_server(proc, log):
    try:
        proc.terminate()
        proc.wait(timeout=20)
    except Exception:
        proc.kill()
    try:
        log.close()
    except Exception:
        pass


def contains(text, value):
    return str(value) in str(text)


def scan_secret(payload):
    text = json.dumps(payload, ensure_ascii=False, default=str) if not isinstance(payload, str) else payload
    hits = []
    if SENTINEL in text:
        hits.append('sentinel')
    if 'sk-' in text:
        hits.append('sk-')
    return hits


# --------------------------------------------------------------- independent expected numbers
def load_expected():
    sys.path.insert(0, str(SYS))
    from services.analytics import Analytics  # noqa: E402
    from services.data import Store  # noqa: E402
    from services.forecasting import forecast  # noqa: E402

    store = Store(Path(ORIGINAL_DB))
    analytics = Analytics(store)

    def dash(start, end, **kw):
        return analytics.dashboard({'start': start, 'end': end, **kw})['totals']

    expected = {
        'q1': dash('2011-11-10', '2011-12-09'),
        'q2': dash('2011-12-09', '2011-12-09'),
        'q4': dash('2011-01-01', '2011-03-31', country='United Kingdom'),
        'q5': dash('2011-11-10', '2011-12-09', product='85123A'),
        'q6': dash('2020-01-01', '2020-01-31'),
        'q7': analytics.dashboard({'start': '2011-11-10', 'end': '2011-12-09'})['ranking'],
        'q8': analytics.dashboard({'start': '2011-12-09', 'end': '2011-12-09'})['ranking'],
        'q9': analytics.dashboard({'start': '2011-11-10', 'end': '2011-12-09',
                                   'country': 'Germany'})['ranking'],
    }
    series = store.series()
    total_7 = sum(row['prediction'] for row in forecast(series)['rows'])
    total_7b = sum(row['prediction'] for row in forecast(series, origin='2011-11-30')['rows'])
    expected['q19_total'] = round(total_7)
    expected['q20_total'] = round(total_7b)
    with store.connect() as cx:
        row = cx.execute(
            'select order_key, min(date) date, count(*) lines, sum(quantity) quantity, '
            'sum(amount_units)/10000.0 revenue from transactions where sale=1 and order_key=? '
            'group by order_key', ('536365',)).fetchone()
        expected['q17'] = dict(row) if row else None
    return expected


# ------------------------------------------------------------------- phase 1 questions
def build_questions(expected):
    q1, q2, q4, q5, q6 = expected['q1'], expected['q2'], expected['q4'], expected['q5'], expected['q6']
    top7 = expected['q7'][0]
    top8 = expected['q8'][0]
    top9 = expected['q9'][0]
    order17 = expected['q17'] or {}
    return [
        {'id': 1, 'question': '2011-11-10到2011-12-09全店卖了多少',
         'expect_status': 'ok', 'expect_tools': ['dashboard_summary'],
         'expect_params': {'dashboard_summary': {'start': '2011-11-10', 'end': '2011-12-09'}},
         'must_contain': [str(q1['quantity']), f"{q1['revenue']:.2f}", str(q1['orders']),
                          '未调用大模型', '/api/dashboard'],
         'numeric': [str(q1['quantity']), f"{q1['revenue']:.2f}", str(q1['orders'])],
         'note': '与 /api/dashboard 同口径，数字必须完全一致'},
        {'id': 2, 'question': '2011年12月9日全店销售额是多少',
         'expect_status': 'ok', 'expect_tools': ['dashboard_summary'],
         'expect_params': {'dashboard_summary': {'start': '2011-12-09', 'end': '2011-12-09'}},
         'must_contain': [str(q2['quantity']), f"{q2['revenue']:.2f}", str(q2['orders'])],
         'numeric': [str(q2['quantity']), f"{q2['revenue']:.2f}"],
         'note': '单日日期解析（年月日写法）'},
        {'id': 3, 'question': '最近30天全店销量',
         'expect_status': 'ok', 'expect_tools': ['dashboard_summary'],
         'expect_params': {'dashboard_summary': {'start': '2011-11-10', 'end': '2011-12-09'}},
         'must_contain': [str(q1['quantity']), str(q1['orders']), '未调用大模型'],
         'numeric': [str(q1['quantity']), f"{q1['revenue']:.2f}"],
         'note': '相对日期按数据覆盖末日回推，与看板默认一致'},
        {'id': 4, 'question': '2011-01-01到2011-03-31英国卖了多少',
         'expect_status': 'ok', 'expect_tools': ['dashboard_summary'],
         'expect_params': {'dashboard_summary': {'start': '2011-01-01', 'end': '2011-03-31',
                                                 'country': 'United Kingdom'}},
         'must_contain': [str(q4['quantity']), f"{q4['revenue']:.2f}", 'United Kingdom'],
         'numeric': [str(q4['quantity']), f"{q4['revenue']:.2f}", str(q4['orders'])],
         'note': '国家中文→英文映射（英国=United Kingdom）'},
        {'id': 5, 'question': '2011-11-10到2011-12-09商品85123A卖了多少',
         'expect_status': 'ok', 'expect_tools': ['product_search', 'dashboard_summary'],
         'expect_params': {'product_search': {'q': '85123A'},
                           'dashboard_summary': {'product': '85123A', 'start': '2011-11-10',
                                                 'end': '2011-12-09'}},
         'must_contain': [str(q5['quantity']), f"{q5['revenue']:.2f}", '85123A'],
         'numeric': [str(q5['quantity']), f"{q5['revenue']:.2f}", str(q5['orders'])],
         'note': '商品编号精确匹配 + 看板商品筛选'},
        {'id': 6, 'question': '2020-01-01到2020-01-31全店销量',
         'expect_status': 'ok', 'expect_tools': ['dashboard_summary'],
         'expect_params': {'dashboard_summary': {'start': '2020-01-01', 'end': '2020-01-31'}},
         'must_contain': ['0 件', '空结果', '超出数据覆盖范围'],
         'numeric': [],
         'note': '空结果/超覆盖：返回0并给出按无记录显示的提示'},
        {'id': 7, 'question': '最近哪些商品销量最高',
         'expect_status': 'ok', 'expect_tools': ['product_ranking'],
         'expect_params': {}, 'must_contain': [top7['stock_code'], str(top7['quantity'])],
         'numeric': [top7['stock_code'], str(top7['quantity'])],
         'note': '排名工具默认最近30天、前10'},
        {'id': 8, 'question': '2011-12-09销量前5的商品',
         'expect_status': 'ok', 'expect_tools': ['product_ranking'],
         'expect_params': {'product_ranking': {'start': '2011-12-09', 'end': '2011-12-09',
                                               'top_n': 5}},
         'must_contain': [top8['stock_code'], str(top8['quantity']), '第5名'],
         'numeric': [top8['stock_code'], str(top8['quantity'])],
         'note': '“前5”解析为 top_n=5'},
        {'id': 9, 'question': '2011-11-10到2011-12-09德国销量最高的商品',
         'expect_status': 'ok', 'expect_tools': ['product_ranking'],
         'expect_params': {'product_ranking': {'country': 'Germany', 'start': '2011-11-10',
                                               'end': '2011-12-09'}},
         'must_contain': [top9['stock_code'], str(top9['quantity']), 'Germany'],
         'numeric': [top9['stock_code'], str(top9['quantity'])],
         'note': '国家过滤 + 排名；国家名不得被误当商品名'},
        {'id': 10, 'question': '搜索 PAPER CRAFT',
         'expect_status': 'ok', 'expect_tools': ['product_search'],
         'expect_params': {'product_search': {'q': 'PAPER CRAFT'}},
         'must_contain': ['23843', 'PAPER CRAFT'], 'numeric': ['23843'], 'note': '商品检索'},
        {'id': 11, 'question': 'JUMBO BAG 卖了多少',
         'expect_status': 'need_clarification', 'expect_tools': ['product_search'],
         'expect_params': {'product_search': {'q': 'JUMBO BAG'}},
         'must_contain': ['85099B', '请指定商品编号'], 'numeric': [], 'note': '商品重名歧义→追问'},
        {'id': 12, 'question': '商品85123A的库存',
         'expect_status': 'ok', 'expect_tools': ['product_search', 'inventory_query'],
         'expect_params': {'inventory_query': {'stock_code': '85123A'}},
         'must_contain': ['模拟库存', '非真实库存', '余额 90', '提前期需求 80', '安全库存 30',
                          '有效在途 10', '补货候选 10'],
         'numeric': ['90', '80', '30', '10'], 'note': '固定样例 100+20−30=90，候选10'},
        {'id': 13, 'question': 'WHITE HANGING HEART T-LIGHT HOLDER 库存',
         'expect_status': 'ok', 'expect_tools': ['product_search', 'inventory_query'],
         'expect_params': {'inventory_query': {'stock_code': '85123A'}},
         'must_contain': ['WHITE HANGING HEART', '模拟库存', '余额 90'],
         'numeric': ['85123A', '90'], 'note': '名称精确匹配到 85123A（同名大小写取销量最高）'},
        {'id': 14, 'question': '某商品库存',
         'expect_status': 'need_clarification', 'expect_tools': [],
         'expect_params': {}, 'must_contain': ['商品编号', '模拟库存'], 'numeric': [],
         'note': '缺商品→追问'},
        {'id': 15, 'question': '商品洗衣机的库存',
         'expect_status': 'need_clarification', 'expect_tools': ['product_search'],
         'expect_params': {}, 'must_contain': ['没有找到', '洗衣机'], 'numeric': [],
         'note': '商品不存在→追问（不伪造）'},
        {'id': 16, 'question': '最近订单',
         'expect_status': 'ok', 'expect_tools': ['orders_query'], 'expect_params': {},
         'must_contain': ['2913 笔订单', '历史实际订单查询'], 'numeric': ['2913'],
         'note': '订单数与看板订单数一致（按 invoice 去重）'},
        {'id': 17, 'question': '订单536365 详情',
         'expect_status': 'ok', 'expect_tools': ['orders_query'],
         'expect_params': {'orders_query': {'order_key': '536365'}},
         'must_contain': ['536365', str(order17.get('quantity', '')), f"{float(order17.get('revenue') or 0):.2f}"],
         'numeric': [str(order17.get('quantity', '')), f"{float(order17.get('revenue') or 0):.2f}"],
         'note': '订单号识别 + 单笔明细'},
        {'id': 18, 'question': '2011-12-09 最近5笔交易',
         'expect_status': 'ok', 'expect_tools': ['orders_query'],
         'expect_params': {'orders_query': {'start': '2011-12-09', 'end': '2011-12-09',
                                            'page_size': 5}},
         'must_contain': ['44 笔订单', '5 笔'], 'numeric': ['44'], 'note': '日期+条数解析'},
        {'id': 19, 'question': '未来7天全店销量预测',
         'expect_status': 'ok', 'expect_tools': ['forecast_7d'], 'expect_params': {},
         'must_contain': ['预测（非历史实际）', 'blend_rule', str(expected['q19_total']),
                          '2011-12-10', '2011-12-16'],
         'numeric': [str(expected['q19_total'])], 'note': '预测合计与 services.forecasting 一致'},
        {'id': 20, 'question': '以2011-11-30为起点预测未来7天',
         'expect_status': 'ok', 'expect_tools': ['forecast_7d'],
         'expect_params': {'forecast_7d': {'origin': '2011-11-30'}},
         'must_contain': ['2011-12-01', '2011-12-07', str(expected['q20_total'])],
         'numeric': [str(expected['q20_total'])], 'note': '显式起点解析'},
        {'id': 21, 'question': '未来7天某商品销量预测',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['全店', '不支持按商品'], 'numeric': [], 'note': '商品级预测不支持→追问'},
        {'id': 22, 'question': '未来3天销量预测',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['只支持未来7天'], 'numeric': [], 'note': '非7天预测→追问'},
        {'id': 23, 'question': '当前数据批次和数据范围',
         'expect_status': 'ok', 'expect_tools': ['batch_info'], 'expect_params': {},
         'must_contain': ['original', '2009-12-01', '2011-12-09', 'cross-sheet-multiset-v1-sales-v2-invoice-order'],
         'numeric': ['2009-12-01', '2011-12-09'], 'note': '批次信息工具'},
        {'id': 24, 'question': '商品23843的库存',
         'expect_status': 'ok', 'expect_tools': ['product_search', 'inventory_query'],
         'expect_params': {'inventory_query': {'stock_code': '23843'}},
         'must_contain': ['23843', '没有商品 23843'], 'numeric': [],
         'note': '工具失败（场景无该商品）被如实说明，未 500'},
        {'id': 25, 'question': '帮我写一首诗',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['只做本系统的只读数据问答'], 'numeric': [], 'note': '非数据问题→追问'},
        {'id': 26, 'question': '利润是多少',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['成本', '无法计算利润'], 'numeric': [], 'note': '缺成本列→追问，不编造利润'},
        {'id': 27, 'question': '帮我生成运营简报',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['/api/ai/brief'], 'numeric': [], 'note': '简报走专门接口'},
        {'id': 28, 'question': '2011-12-09到2011-11-10全店销量',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['晚于'], 'numeric': [], 'note': '倒序日期→追问'},
        {'id': 29, 'question': '比上个月增长了多少',
         'expect_status': 'need_clarification', 'expect_tools': [], 'expect_params': {},
         'must_contain': ['对比的两个时间段'], 'numeric': [], 'note': '缺基准区间→追问'},
        {'id': 30, 'question': '以2020-01-01为起点预测未来7天',
         'expect_status': 'failed', 'expect_tools': ['forecast_7d'],
         'expect_params': {'forecast_7d': {'origin': '2020-01-01'}},
         'must_contain': ['工具执行失败', '未调用大模型', '数据覆盖范围'], 'numeric': [],
         'note': '工具失败（起点超出覆盖）→failed，保留错误不重试'},
    ]


def check_question(port, cookie, item, expected):
    status, body, headers = http_json(port, 'POST', '/api/ai/ask',
                                      {'question': item['question']}, cookie=cookie)
    record = {
        'id': item['id'], 'question': item['question'], 'http': status,
        'expected_status': item['expect_status'],
        'expected_tools': item['expect_tools'], 'expected_note': item['note'],
        'actual_status': body.get('status'),
        'actual_tools': [c.get('name') for c in body.get('tool_calls') or []],
        'actual_params': {c.get('name'): c.get('params') for c in body.get('tool_calls') or []},
        'tool_ok': {c.get('name'): c.get('ok') for c in body.get('tool_calls') or []},
        'answer': body.get('answer'),
        'clarification': body.get('clarification'),
        'error': body.get('error'),
        'range': body.get('range'), 'batch': body.get('batch'),
        'warnings': body.get('warnings'), 'provider': body.get('provider'),
        'llm_used': body.get('llm_used'),
        'elapsed_seconds': body.get('elapsed_seconds'),
        'secret_hits': scan_secret(body),
    }
    failures = []
    param_failures = []
    if status != 200:
        failures.append(f'HTTP {status} 而非 200')
    if record['actual_status'] != item['expect_status']:
        failures.append(f"status={record['actual_status']} 期望 {item['expect_status']}")
    if record['actual_tools'] != item['expect_tools']:
        failures.append(f"工具={record['actual_tools']} 期望 {item['expect_tools']}")
    text = (body.get('answer') or '') + (body.get('clarification') or '') + \
           (' '.join(body.get('warnings') or [])) + (body.get('error') or '') + \
           ' '.join(str(c.get('summary', '')) for c in body.get('tool_calls') or [])
    for expected_key, expected_value in (item.get('expect_params') or {}).items():
        actual = record['actual_params'].get(expected_key) or {}
        for key, value in expected_value.items():
            if actual.get(key) != value:
                param_failures.append(f'参数 {expected_key}.{key}={actual.get(key)!r} 期望 {value!r}')
    failures.extend(param_failures)
    missing = [token for token in item['must_contain'] if not contains(text, token)]
    if missing:
        failures.append('缺少关键内容: ' + ' / '.join(missing))
    if record['actual_status'] == 'ok' and not body.get('llm_used'):
        if '未调用大模型' not in (body.get('answer') or ''):
            failures.append('本地规则答案未注明“未调用大模型”')
    numeric_ok = True
    numeric_missing = []
    source = body.get('answer') or ''
    for token in item.get('numeric') or []:
        if token and not contains(source, token):
            numeric_ok = False
            numeric_missing.append(token)
    if numeric_missing:
        failures.append('数字不一致/缺失: ' + ' / '.join(numeric_missing))
    if record['secret_hits']:
        failures.append('响应疑似含密钥: ' + ','.join(record['secret_hits']))
    for key in ('range', 'batch', 'warnings', 'tool_calls'):
        if key not in body:
            failures.append(f'缺少响应字段 {key}')
    if not body.get('batch') or 'id' not in (body.get('batch') or {}):
        failures.append('batch 缺少 id')
    record['numeric_expected'] = item.get('numeric') or []
    record['numeric_ok'] = numeric_ok
    record['numeric_missing'] = numeric_missing
    record['param_failures'] = param_failures
    record['params_ok'] = not param_failures
    record['tool_selection_ok'] = record['actual_tools'] == item['expect_tools']
    record['status_ok'] = record['actual_status'] == item['expect_status']
    record['pass'] = not failures
    record['failures'] = failures
    return record


# -------------------------------------------------------------------------- phase 1
def phase1(expected):
    proc, log, port = start_server(8784, ROOT / '.tmp' / 'rt_ai_accept')
    try:
        cookie = login(port)
        checks = []

        status, body, _ = http_json(port, 'GET', '/api/ai/status', cookie=cookie)
        checks.append({'name': 'GET /api/ai/status（未配置）', 'http': status,
                       'body': body,
                       'pass': status == 200 and body.get('llm_configured') is False
                       and body.get('provider') == 'local-rule-planner'
                       and body.get('fallback') == 'local-rule-planner'
                       and body.get('key_present') is False
                       and body.get('tools_count') == 7
                       and not scan_secret(body),
                       'note': '契约5.7：未配置时 fallback=local-rule-planner，不含密钥'})

        status, body, _ = http_json(port, 'GET', '/api/ai/tools', cookie=cookie)
        names = [t.get('name') for t in body.get('tools') or []]
        required = {'dashboard_summary', 'product_search', 'product_ranking', 'orders_query',
                    'forecast_7d', 'inventory_query', 'batch_info'}
        checks.append({'name': 'GET /api/ai/tools（7个只读工具）', 'http': status,
                       'body': {'names': names, 'policy': body.get('policy')},
                       'pass': status == 200 and set(names) == required
                       and all(t.get('read_only') for t in body.get('tools') or [])
                       and bool(body.get('policy')),
                       'note': '工具白名单与参数 schema'})

        status, body, _ = http_json(port, 'GET', '/api/ai/status', cookie=None)
        checks.append({'name': '未登录访问 /api/ai/status', 'http': status, 'pass': status == 401,
                       'note': '认证中间件生效', 'body': body})

        operator_cookie = login(port, 'operator', 'Operator123!')
        status, body, _ = http_json(port, 'GET', '/api/ai/status', cookie=operator_cookie)
        status2, body2, _ = http_json(port, 'POST', '/api/ai/ask',
                                      {'question': '当前数据批次和数据范围'}, cookie=operator_cookie)
        checks.append({'name': 'operator 账号可读 AI 状态与问答', 'http': status,
                       'body': {'status': body.get('status'), 'ask': body2.get('status')},
                       'pass': status == 200 and status2 == 200 and body2.get('status') == 'ok',
                       'note': '普通登录用户均可读（契约5.7）'})

        for name, payload in (
                ('空问题 400', {'question': ''}),
                ('scope 日期格式非法 400', {'question': '全店销量', 'scope': {'start': '2011-13-40'}}),
                ('scope start>end 400', {'question': '全店销量',
                                         'scope': {'start': '2011-12-09', 'end': '2011-11-10'}}),
                ('scope 超过4年 400', {'question': '全店销量',
                                      'scope': {'start': '2009-01-01', 'end': '2014-01-01'}}),
                ('scope 起始日超出覆盖 400', {'question': '全店销量',
                                            'scope': {'start': '2020-01-01'}}),
                ('scope 未知国家 400', {'question': '全店销量',
                                       'scope': {'country': 'Narnia'}}),
                ('未知字段 400', {'question': '全店销量', 'bad': 1})):
            status, body, _ = http_json(port, 'POST', '/api/ai/ask', payload, cookie=cookie)
            checks.append({'name': name, 'http': status, 'body': body,
                           'pass': status == 400 and body.get('code') == 'bad_request',
                           'note': '参数校验错误必须是 400 且带 error/code'})

        # no scenario yet -> honest degradation instead of 500
        status, body, _ = http_json(port, 'POST', '/api/ai/ask',
                                    {'question': '商品85123A的库存'}, cookie=cookie)
        text = (body.get('answer') or '') + (body.get('clarification') or '') + \
               ' '.join(body.get('warnings') or []) + \
               ' '.join(c.get('summary', '') for c in body.get('tool_calls') or [])
        checks.append({'name': '无模拟库存场景时的库存降级', 'http': status,
                       'body': {'status': body.get('status'), 'error': body.get('error'),
                                'warnings': body.get('warnings'),
                                'tools': [(c.get('name'), c.get('ok')) for c in body.get('tool_calls') or []]},
                       'pass': status == 200 and body.get('status') == 'ok'
                       and '尚未建立任何模拟库存场景' in text,
                       'note': '库存模块未就绪/无场景时不 500，如实说明'})

        # seed the fixed inventory sample through the real HTTP API
        status, body, _ = http_json(port, 'POST', '/api/inventory/scenarios',
                                    {'name': 'AI验收场景',
                                     'note': '合同固定样例：100+20-30=90，候选10',
                                     'stock_codes': ['85123A']}, cookie=cookie)
        scenario_id = (body.get('scenario') or {}).get('id')
        seed_ok = status == 201 and bool(scenario_id)
        if seed_ok:
            status, body, _ = http_json(
                port, 'PUT', f'/api/inventory/scenarios/{scenario_id}/items/85123A',
                {'initial_qty': 100, 'lead_time_days': 8, 'safety_stock': 30, 'daily_demand': 10},
                cookie=cookie)
            seed_ok = seed_ok and status == 200
            for kind, qty, key in (('in', 20, 'acc-in'), ('out', 30, 'acc-out')):
                status, body, _ = http_json(
                    port, 'POST', f'/api/inventory/scenarios/{scenario_id}/movements',
                    {'stock_code': '85123A', 'kind': kind, 'qty': qty, 'idempotency_key': key},
                    cookie=cookie)
                seed_ok = seed_ok and status in (200, 201)
            status, body, _ = http_json(
                port, 'POST', f'/api/inventory/scenarios/{scenario_id}/replenishment/confirm',
                {'stock_code': '85123A', 'qty': 10,
                 'expected_date': (date.today() + timedelta(days=3)).isoformat(),
                 'idempotency_key': 'acc-transit'}, cookie=cookie)
            seed_ok = seed_ok and status == 201
        checks.append({'name': '准备模拟库存固定样例（HTTP）', 'http': status,
                       'body': {'scenario_id': scenario_id}, 'pass': seed_ok,
                       'note': '通过 /api/inventory 接口写入，非直接改库'})

        # inventory cross-check through the public endpoint
        if seed_ok:
            status, body, _ = http_json(port, 'GET',
                                        f'/api/inventory/scenarios/{scenario_id}/items',
                                        cookie=cookie)
            item = next((row for row in body.get('items') or [] if row.get('stock_code') == '85123A'), {})
            checks.append({'name': '库存接口固定样例核对', 'http': status, 'body': item,
                           'pass': status == 200 and item.get('balance') == 90
                           and item.get('replenishment_candidate') == 10
                           and body.get('simulated') is True,
                           'note': '余额90、候选10（契约5.6固定样例）'})

        questions = build_questions(expected)
        RESULTS['question_items'] = [
            {key: item[key] for key in ('id', 'question', 'expect_status', 'expect_tools',
                                        'expect_params', 'note')} for item in questions]
        records = []
        for item in questions:
            records.append(check_question(port, cookie, item, expected))

        # dashboard cross-check with the real endpoint
        status, body, _ = http_json(port, 'GET',
                                    '/api/dashboard?start=2011-11-10&end=2011-12-09',
                                    cookie=cookie)
        totals = body.get('totals') or {}
        checks.append({'name': 'POST /api/ai/ask 与 GET /api/dashboard 数字一致', 'http': status,
                       'body': totals,
                       'pass': status == 200 and totals.get('quantity') == expected['q1']['quantity']
                       and round(float(totals.get('revenue') or 0), 2) == round(expected['q1']['revenue'], 2)
                       and totals.get('orders') == expected['q1']['orders'],
                       'note': '851403 / 1694068.71 / 2913'})

        # brief
        status, body, _ = http_json(port, 'POST', '/api/ai/brief', {}, cookie=cookie)
        markdown = body.get('markdown') or ''
        brief_ok = (status == 201 and body.get('provider') == 'template'
                    and bool((body.get('brief') or {}).get('url')))
        must = ['历史实际', '预测', '模拟', 'simulated', 'provider=template', '未调用大模型',
                '2009-12-01', '2011-12-09', str(expected['q1']['quantity']),
                f"{expected['q1']['revenue']:.2f}", '销售额不等于利润']
        missing = [token for token in must if token not in markdown]
        checks.append({'name': 'POST /api/ai/brief（确定性模板）', 'http': status,
                       'body': {'brief': body.get('brief'), 'provider': body.get('provider'),
                                'markdown_chars': len(markdown), 'missing': missing},
                       'pass': brief_ok and not missing and not scan_secret(markdown),
                       'note': '含历史实际/预测/模拟标识、批次与口径、核心数字'})
        url = (body.get('brief') or {}).get('url')
        if url:
            md_status, md_text, md_headers = http_text(port, url, cookie)
            checks.append({'name': 'GET 简报下载（Markdown 附件）', 'http': md_status,
                           'body': {'content_type': md_headers.get('Content-Type'),
                                    'disposition': md_headers.get('Content-Disposition'),
                                    'chars': len(md_text)},
                           'pass': md_status == 200
                           and 'text/markdown' in (md_headers.get('Content-Type') or '')
                           and 'attachment' in (md_headers.get('Content-Disposition') or '')
                           and md_text == markdown,
                           'note': 'url 可直接下载且内容与响应 markdown 一致'})
        status, body, _ = http_json(port, 'GET', '/api/ai/briefs/doesnotexist.md', cookie=cookie)
        checks.append({'name': '不存在的简报 404', 'http': status,
                       'pass': status == 404 and body.get('code') == 'not_found',
                       'note': '错误契约', 'body': body})

        RESULTS['phase1'] = {'checks': checks, 'note': f'isolated runtime rt_ai_accept, port {port}'}
        RESULTS['questions'] = records
    finally:
        stop_server(proc, log)


# -------------------------------------------------------------------------- phase 2
def phase2():
    extra = {'ECE_AI_BASE_URL': 'http://127.0.0.1:9/v1',
             'ECE_AI_API_KEY': SENTINEL,
             'ECE_AI_MODEL': 'fake-model'}
    proc, log, port = start_server(8785, ROOT / '.tmp' / 'rt_ai_accept_llm', extra)
    checks = []
    try:
        cookie = login(port)
        status, body, _ = http_json(port, 'GET', '/api/ai/status', cookie=cookie)
        checks.append({'name': '配置密钥后 /api/ai/status', 'http': status, 'body': body,
                       'pass': status == 200 and body.get('llm_configured') is True
                       and body.get('provider') == 'openai-compatible'
                       and body.get('fallback') is None
                       and body.get('key_present') is True
                       and not scan_secret(body),
                       'note': '密钥只来自环境变量；响应不回显'})

        # settings override merge (non-secret part only)
        status, body, _ = http_json(port, 'PUT', '/api/settings',
                                    {'ai': {'base_url': 'http://127.0.0.1:9/v2',
                                            'model': 'settings-model',
                                            'timeout_seconds': 5}}, cookie=cookie)
        settings_ok = status == 200
        status2, body2, _ = http_json(port, 'GET', '/api/ai/status', cookie=cookie)
        checks.append({'name': 'settings.ai 覆盖 base_url/model/timeout', 'http': status,
                       'body': {'put': body, 'status': body2},
                       'pass': settings_ok and status2 == 200
                       and body2.get('model') == 'settings-model'
                       and body2.get('timeout_seconds') == 5
                       and body2.get('llm_configured') is True
                       and not scan_secret(body2),
                       'note': 'PUT /api/settings 后生效；密钥仍只读环境变量'})

        status, body, _ = http_json(port, 'POST', '/api/ai/ask',
                                    {'question': '2011-11-10到2011-12-09全店卖了多少'},
                                    cookie=cookie)
        hits = scan_secret(body)
        checks.append({'name': '大模型不可达 → failed 保留工具结果', 'http': status,
                       'body': {'status': body.get('status'), 'provider': body.get('provider'),
                                'llm_used': body.get('llm_used'), 'error': body.get('error'),
                                'tools': [(c.get('name'), c.get('ok')) for c in body.get('tool_calls') or []],
                                'answer': (body.get('answer') or '')[:300]},
                       'pass': status == 200 and body.get('status') == 'failed'
                       and body.get('llm_used') is False
                       and bool(body.get('error'))
                       and [c.get('name') for c in body.get('tool_calls') or []] == ['dashboard_summary']
                       and all(c.get('ok') for c in body.get('tool_calls') or [])
                       and not hits,
                       'note': '不无限重试；工具数字保留；响应不含密钥'})
        RESULTS['phase1'].setdefault('checks', [])
    finally:
        stop_server(proc, log)
    RESULTS['phase2'] = {'checks': checks, 'note': f'configured+failure, port {port}'}


# -------------------------------------------------------------------- phase 3 (tolerant import)
def phase3():
    os.environ['ECE_RUNTIME_DIR'] = str(ROOT / '.tmp' / 'rt_ai_accept')
    os.environ['ECE_ORIGINAL_DB'] = ORIGINAL_DB
    os.environ['ECE_APP_DB'] = str(ROOT / '.tmp' / 'rt_ai_accept' / 'app.sqlite3')
    for key in ('ECE_AI_BASE_URL', 'ECE_AI_API_KEY', 'ECE_AI_MODEL'):
        os.environ.pop(key, None)
    for name in list(sys.modules):
        if name == 'api' or name.startswith('api.') or name.startswith('services.'):
            del sys.modules[name]
    import builtins
    import importlib
    real_import = builtins.__import__

    def blocking_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'api.services.inventory':
            raise ImportError('simulated missing inventory module')
        if level and 'inventory' in (fromlist or ()):
            # `from . import inventory` calls __import__('', fromlist=('inventory',), level=1)
            raise ImportError('simulated missing inventory module')
        if name in ('api.services', 'services') and 'inventory' in (fromlist or ()):
            raise ImportError('simulated missing inventory module')
        return real_import(name, globals, locals, fromlist, level)

    from api.deps import active_batch
    from api.services import assistant

    batch = active_batch()
    builtins.__import__ = blocking_import
    checks = []
    try:
        result = assistant.execute_tool('inventory_query', {'stock_code': '85123A'}, batch)
        checks.append({'name': '库存模块导入失败 → 工具降级（不 500）',
                       'body': {'ok': result.get('ok'), 'summary': result.get('summary'),
                                'warnings': result.get('warnings'), 'data': result.get('data')},
                       'pass': result.get('ok') is True
                       and any('库存模块未就绪' in w for w in result.get('warnings') or [])
                       and result['data']['item']['balance'] == 90,
                       'note': '模拟 api.services.inventory 不可导入，回退只读表仍按契约公式给出90'})
        answer = assistant.answer_question('商品85123A的库存', None, {'id': 1})
        checks.append({'name': '库存模块导入失败 → /ask 降级回答',
                       'body': {'status': answer.get('status'),
                                'tools': [(c.get('name'), c.get('ok')) for c in answer.get('tool_calls') or []],
                                'warnings': answer.get('warnings'),
                                'answer': (answer.get('answer') or '')[:300]},
                       'pass': answer.get('status') == 'ok'
                       and any('库存模块未就绪' in w for w in answer.get('warnings') or [])
                       and '模拟库存' in (answer.get('answer') or ''),
                       'note': '降级路径仍返回模拟库存与提示，不伪造真实库存'})
    finally:
        builtins.__import__ = real_import
    RESULTS['phase3'] = {'checks': checks, 'note': 'in-process import-failure simulation'}


# --------------------------------------------------------------------------- main
def main():
    started = time.time()
    expected = load_expected()
    phase1(expected)
    phase2()
    phase3()
    questions = RESULTS['questions']
    total = len(questions)
    tool_ok = sum(1 for r in questions if r['tool_selection_ok'])
    status_ok = sum(1 for r in questions if r['status_ok'])
    numeric_questions = [r for r in questions if r['numeric_expected']]
    numeric_pass = sum(1 for r in numeric_questions if r['numeric_ok'])
    params_pass = sum(1 for r in questions if r['params_ok'])
    passed = sum(1 for r in questions if r['pass'])
    clarification = sum(1 for r in questions if r['actual_status'] == 'need_clarification')
    failed_status = sum(1 for r in questions if r['actual_status'] == 'failed')
    http_failures = sum(1 for r in questions if r['http'] != 200)
    summary = {
        'questions_total': total,
        'questions_passed': passed,
        'tool_selection_pass': tool_ok,
        'params_pass': params_pass,
        'numeric_pass': numeric_pass,
        'numeric_total': len(numeric_questions),
        'clarification_count': clarification,
        'failed_status_count': failed_status,
        'http_failures': http_failures,
        'elapsed_seconds': round(time.time() - started, 1),
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S%z'),
    }
    try:
        import sqlite3
        cx = sqlite3.connect(ROOT / '.tmp' / 'rt_ai_accept' / 'app.sqlite3')
        summary['ai_runs_rows'] = cx.execute('select count(*) from ai_runs').fetchone()[0]
        summary['briefs_rows'] = cx.execute('select count(*) from briefs').fetchone()[0]
        summary['ai_runs_statuses'] = dict(cx.execute(
            'select status,count(*) from ai_runs group by status').fetchall())
        cx.close()
    except Exception as exc:  # pragma: no cover - evidence only
        summary['persistence_error'] = f'{type(exc).__name__}: {exc}'
    RESULTS['summary'] = summary
    (OUT / 'question_items.json').write_text(
        json.dumps(RESULTS.get('question_items') or [], ensure_ascii=False, indent=1), encoding='utf-8')
    (OUT / 'acceptance_ai.json').write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed = [r for r in questions if not r['pass']]
    if failed:
        print('FAILED QUESTIONS:')
        for r in failed:
            print(' ', r['id'], r['question'], r['failures'])
    checks_failed = []
    for phase in ('phase1', 'phase2', 'phase3'):
        for check in RESULTS[phase].get('checks') or []:
            if not check.get('pass'):
                checks_failed.append((phase, check['name']))
    print('FAILED CHECKS:', checks_failed)
    return 0 if (not failed and not checks_failed) else 1


if __name__ == '__main__':
    sys.exit(main())
