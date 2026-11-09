"""M2 data-api acceptance probe (isolated server on 127.0.0.1:8782)."""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8782'
OUT = Path(__file__).resolve().parent
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def req(path, method='GET', data=None, headers=None, timeout=180):
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=headers or {})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def api(path, **kwargs):
    status, raw, head = req(path, **kwargs)
    return status, raw, head


def jload(raw):
    return json.loads(raw.decode('utf-8'))


result = {}

# ---------------------------------------------------------------- login
status, raw, _ = api('/api/auth/login', method='POST',
                     data=json.dumps({'username': 'admin', 'password': 'AdminTest123'}).encode(),
                     headers={'Content-Type': 'application/json'})
result['login'] = {'status': status, 'body': jload(raw) if status == 200 else raw.decode()[:200]}
assert status == 200, result['login']

RANGE = 'start=2011-11-10&end=2011-12-09'

# ---------------------------------------------------------------- dashboard
t0 = time.monotonic()
status, raw, _ = api('/api/dashboard?' + RANGE)
elapsed = round(time.monotonic() - t0, 3)
dash = jload(raw)
totals = dash['totals']
result['dashboard'] = {
    'status': status, 'elapsed_s': elapsed,
    'totals': {k: totals[k] for k in ('quantity', 'revenue', 'orders', 'lines', 'products', 'amount_rows')},
    'batch': dash.get('batch'), 'filters': dash['filters'],
    'trend_days': len(dash['trend']), 'warnings': dash['warnings'],
    'keys_present': ['batch' in dash, 'policy_version' in dash, 'quality' in dash],
}
assert totals['quantity'] == 851403, totals
assert abs(totals['revenue'] - 1694068.71) < 1e-6, totals
assert totals['orders'] == 2913, totals
assert totals['lines'] == 86861, totals
assert dash['batch'] == {'id': 'original', 'label': dash['batch']['label'], 'kind': 'original'}

# ---------------------------------------------------------------- orders (all pages)
t0 = time.monotonic()
orders, page, total = [], 1, None
while True:
    status, raw, _ = api(f'/api/orders?{RANGE}&page={page}&page_size=200')
    assert status == 200, (status, raw[:200])
    payload = jload(raw)
    total = payload['total']
    orders.extend(payload['rows'])
    if page == 1:
        result['orders_first_page'] = {
            'status': status, 'page': payload['page'], 'page_size': payload['page_size'],
            'total': payload['total'], 'filters': payload['filters'], 'batch': payload['batch'],
            'row0': payload['rows'][0],
            'row_keys': sorted(payload['rows'][0].keys()),
            'max_product_codes': max(len(r['product_codes']) for r in payload['rows']),
            'total_consistent_with_dashboard': payload['total'] == totals['orders'],
        }
    page += 1
    if len(orders) >= total:
        break
order_elapsed = round(time.monotonic() - t0, 3)
sum_qty = sum(r['quantity'] for r in orders)
sum_rev = sum(r['revenue'] for r in orders)
result['orders_all_pages'] = {
    'pages': page - 1, 'rows': len(orders), 'elapsed_s': order_elapsed,
    'sum_quantity': sum_qty, 'sum_revenue': round(sum_rev, 2),
    'matches_dashboard_quantity': sum_qty == totals['quantity'],
    'revenue_delta_vs_dashboard': round(sum_rev - totals['revenue'], 6),
    'total_equals_dashboard_orders': total == totals['orders'],
    'unique_order_keys': len({r['order_key'] for r in orders}),
}
assert sum_qty == totals['quantity'], result['orders_all_pages']
assert abs(sum_rev - totals['revenue']) < 0.01, result['orders_all_pages']

# ---------------------------------------------------------------- order detail
head = orders[0]
status, raw, _ = api(f"/api/orders/{head['order_key']}?{RANGE}")
detail = jload(raw)
detail_qty = sum(r['quantity'] for r in detail['rows'])
detail_rev = sum((r['revenue'] or 0) for r in detail['rows'])
result['order_detail'] = {
    'status': status, 'order_key': head['order_key'], 'header': detail['order'],
    'lines': len(detail['rows']), 'detail_quantity': detail_qty,
    'detail_revenue': round(detail_rev, 6),
    'quantity_matches_header': detail_qty == detail['order']['quantity'],
    'revenue_matches_header': abs(detail_rev - detail['order']['revenue']) < 1e-6,
    'row_keys': sorted(detail['rows'][0].keys()), 'batch': detail['batch'],
}
assert status == 200
assert detail_qty == detail['order']['quantity']
assert abs(detail_rev - detail['order']['revenue']) < 1e-6

# ---------------------------------------------------------------- exports
status, raw, head_headers = api('/api/export/trend?' + RANGE)
trend_text = raw.decode('utf-8-sig')
result['export_trend'] = {
    'status': status, 'bom': raw[:3] == b'\xef\xbb\xbf',
    'content_type': head_headers.get('Content-Type'),
    'content_disposition': head_headers.get('Content-Disposition'),
    'lines': len(trend_text.splitlines()),
    'header': trend_text.splitlines()[0],
}
assert len(trend_text.splitlines()) == 31  # header + 30 days

t0 = time.monotonic()
status, raw, head_headers = api('/api/export/orders?' + RANGE)
orders_csv = raw.decode('utf-8-sig')
orders_lines = orders_csv.splitlines()
result['export_orders'] = {
    'status': status, 'elapsed_s': round(time.monotonic() - t0, 3),
    'bom': raw[:3] == b'\xef\xbb\xbf', 'content_type': head_headers.get('Content-Type'),
    'content_disposition': head_headers.get('Content-Disposition'),
    'lines': len(orders_lines), 'header': orders_lines[0],
    'rows_equal_total': len(orders_lines) - 1 == totals['orders'],
}
assert len(orders_lines) - 1 == totals['orders']

t0 = time.monotonic()
status, raw, head_headers = api('/api/export/transactions?' + RANGE, timeout=300)
trans_csv = raw.decode('utf-8-sig')
trans_lines = trans_csv.splitlines()
result['export_transactions'] = {
    'status': status, 'elapsed_s': round(time.monotonic() - t0, 3), 'bytes': len(raw),
    'bom': raw[:3] == b'\xef\xbb\xbf', 'content_type': head_headers.get('Content-Type'),
    'content_disposition': head_headers.get('Content-Disposition'),
    'lines': len(trans_lines), 'header': trans_lines[0],
    'rows_equal_dashboard_lines': len(trans_lines) - 1 == totals['lines'],
    'sample_row': trans_lines[1],
}
assert len(trans_lines) - 1 == totals['lines'], result['export_transactions']

# ---------------------------------------------------------------- products / transactions
status, raw, _ = api('/api/products?q=A')
products = jload(raw)
result['products'] = {'status': status, 'type': type(products).__name__, 'count': len(products),
                      'sample': products[0] if products else None,
                      'max_50': len(products) <= 50}
assert status == 200 and isinstance(products, list) and len(products) <= 50

status, raw, _ = api('/api/transactions?' + RANGE + '&page=1&page_size=5')
trans = jload(raw)
result['transactions'] = {'status': status, 'total': trans['total'], 'page': trans['page'],
                          'page_size': trans['page_size'], 'rows': len(trans['rows']),
                          'batch': trans['batch'],
                          'total_equals_dashboard_lines': trans['total'] == totals['lines']}
assert trans['total'] == totals['lines']

# ---------------------------------------------------------------- batches / template
status, raw, _ = api('/api/imports/batches')
batches = jload(raw)
result['batches'] = {'status': status, 'active_batch_id': batches['active_batch_id'],
                     'ids': [b['id'] for b in batches['batches']],
                     'first_keys': sorted(batches['batches'][0].keys())}

status, raw, head_headers = api('/api/imports/template.csv')
tpl = raw.decode('utf-8-sig').splitlines()
result['template'] = {'status': status, 'bom': raw[:3] == b'\xef\xbb\xbf',
                      'lines': len(tpl), 'header': tpl[0],
                      'content_disposition': head_headers.get('Content-Disposition'),
                      'sample': tpl[1]}
assert len(tpl) == 3

# ---------------------------------------------------------------- error contract
errors = {}
for path, expect in [('/api/orders?page=0&start=2011-11-10&end=2011-12-09', 400),
                     ('/api/orders?page_size=201&start=2011-11-10&end=2011-12-09', 400),
                     ('/api/orders?start=2011-12-09&end=2011-11-10', 400),
                     ('/api/orders?start=bad-date&end=2011-12-09', 400),
                     ('/api/orders/NOT-AN-ORDER?' + RANGE, 404),
                     ('/api/imports/jobs/not-a-job', 404),
                     ('/api/imports/batches/nope/activate', 404)]:
    status, raw, _ = api(path, method='POST' if 'activate' in path else 'GET')
    try:
        body = jload(raw)
    except Exception:
        body = raw.decode('utf-8', 'replace')[:200]
    errors[path] = {'status': status, 'expected': expect, 'code': body.get('code') if isinstance(body, dict) else None,
                    'error': body.get('error') if isinstance(body, dict) else body}
    assert status == expect, (path, status)
result['errors'] = errors

# ---------------------------------------------------------------- openapi coverage
status, raw, _ = api('/api/../../openapi.json' if False else '/openapi.json')
openapi = jload(raw) if status == 200 else {}
paths = sorted(openapi.get('paths', {}))
result['openapi'] = {'status': status, 'paths': [p for p in paths if p.startswith(('/api/orders', '/api/imports', '/api/export', '/api/dashboard', '/api/products', '/api/transactions'))]}

(OUT / 'probe1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True, indent=1)[:6000])
print('PROBE1 OK')
