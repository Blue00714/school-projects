"""M2 extra probe: import concurrency, batch db schema, CSV hardening, read-only proof."""
import hashlib
import http.cookiejar
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = 'http://127.0.0.1:8782'
OUT = Path(__file__).resolve().parent
ROOT = Path(r'C:\Users\Administrator\11111\数据\学校项目')
RT = ROOT / '.tmp' / 'rt_data'
COLS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
result = {}


def req(path, method='GET', data=None, headers=None, timeout=300):
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def jload(raw):
    return json.loads(raw.decode('utf-8'))


def multipart(fields, files):
    boundary = '----m2probe' + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8')
    for name, (filename, content, ctype) in files.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n').encode('utf-8')
        body += content + b'\r\n'
    body += f'--{boundary}--\r\n'.encode('utf-8')
    return bytes(body), f'multipart/form-data; boundary={boundary}'


def upload(path: Path, label=''):
    content = path.read_bytes()
    fields = {'label': label} if label else {}
    body, ctype = multipart(fields, {'file': (path.name, content, 'application/octet-stream')})
    return req('/api/imports', method='POST', data=body, headers={'Content-Type': ctype})


def wait_job(job_id, timeout=600, probe_each=None):
    t0 = time.monotonic()
    seen = []
    while time.monotonic() - t0 < timeout:
        status, raw = req('/api/imports/jobs/' + job_id)
        payload = jload(raw)
        seen.append(payload['job']['status'])
        if probe_each and payload['job']['status'] in ('queued', 'running'):
            probe_each(payload['job']['status'])
        if payload['job']['status'] not in ('queued', 'running'):
            return payload, round(time.monotonic() - t0, 2), seen
        time.sleep(0.2)
    raise AssertionError('job timeout: ' + job_id)


def dashboard(query=''):
    status, raw = req('/api/dashboard' + query)
    assert status == 200, (status, raw[:300])
    return jload(raw)


# ------------------------------------------------------------------ login
status, raw = req('/api/auth/login', method='POST',
                  data=json.dumps({'username': 'admin', 'password': 'AdminTest123'}).encode(),
                  headers={'Content-Type': 'application/json'})
assert status == 200, raw

# ------------------------------------------------------------------ large CSV
N_ORDERS = 40000
LINES_PER_ORDER = 3
special_rows = [
    ['123456789012345678', 'LONG1', '=SUM(1,2)', 2, '2011-05-01 09:00:00', 1.5, '99999', 'United Kingdom'],
    ['-700000', 'NEG1', 'Normal description', 1, '2011-05-01 09:05:00', 2.5, '99998', 'France'],
]
rows = list(special_rows)
for i in range(N_ORDERS):
    invoice = str(600000 + i)
    for line in range(LINES_PER_ORDER):
        qty = (i + line) % 3 + 1
        price = round(1.0 + ((i + line) % 50) * 0.1, 2)
        rows.append([invoice, f'S{(i + line) % 40:03d}', f'Item {i % 97}',
                     qty, f'2011-05-{1 + i % 27:02d} 10:{line:02d}:00', price, str(800000 + i % 5000),
                     ['United Kingdom', 'France', 'Germany', 'Spain'][i % 4]])
big_csv = OUT / 'large_upload.csv'
import csv as _csv
with big_csv.open('w', newline='', encoding='utf-8-sig') as stream:
    writer = _csv.writer(stream)
    writer.writerow(COLS)
    writer.writerows(rows)

expected_qty = sum(int(row[3]) for row in rows if int(row[3]) > 0 and not str(row[0]).upper().startswith('C')
                   and str(row[4])[:4].isdigit())
expected_rev = sum(round(int(row[3]) * float(row[5]) * 10000) / 10000 for row in rows
                   if int(row[3]) > 0 and not str(row[0]).upper().startswith('C') and str(row[4])[:4].isdigit()
                   and float(row[5]) >= 0)
expected_orders = len({row[0] for row in rows
                       if int(row[3]) > 0 and not str(row[0]).upper().startswith('C') and str(row[4])[:4].isdigit()})
result['large_expected'] = {'raw_rows': len(rows), 'quantity': expected_qty,
                            'revenue': round(expected_rev, 2), 'orders': expected_orders}

# baseline while importing
observed = {'statuses': [], 'dashboard_samples': [], 'order_samples': []}


def probe_while_running(job_status):
    t0 = time.monotonic()
    dash = dashboard('?start=2011-11-10&end=2011-12-09')
    d_elapsed = round(time.monotonic() - t0, 3)
    t0 = time.monotonic()
    status, raw = req('/api/orders?start=2011-11-10&end=2011-12-09&page=1&page_size=5')
    o_elapsed = round(time.monotonic() - t0, 3)
    orders = jload(raw)
    observed['statuses'].append(job_status)
    observed['dashboard_samples'].append({
        'job_status': job_status, 'elapsed_s': d_elapsed, 'batch': dash['batch']['id'],
        'quantity': dash['totals']['quantity'], 'revenue': dash['totals']['revenue'],
        'orders': dash['totals']['orders'],
        'unchanged': (dash['totals']['quantity'] == 851403
                      and abs(dash['totals']['revenue'] - 1694068.71) < 1e-6
                      and dash['totals']['orders'] == 2913)})
    observed['order_samples'].append({'job_status': job_status, 'http': status,
                                      'elapsed_s': o_elapsed, 'total': orders['total'],
                                      'unchanged': orders['total'] == 2913})


status, raw = upload(big_csv, label='M2 大数据导入')
body = jload(raw)
result['large_upload'] = {'http': status, 'body': body}
assert status == 202 and body['status'] == 'queued'
job, waited, statuses = wait_job(body['job_id'], probe_each=probe_while_running)
result['large_import'] = {
    'status': job['job']['status'], 'waited_s': waited, 'status_sequence': statuses,
    'report': {k: job['report'].get(k) for k in ('raw_rows', 'retained_rows', 'positive_sale_rows',
                                                 'date_min', 'date_max', 'elapsed_seconds')},
    'issues_count': len(job['issues']),
    'dashboard_during_import': observed['dashboard_samples'],
    'orders_during_import': observed['order_samples'],
}
assert job['job']['status'] == 'succeeded', job
assert all(sample['unchanged'] for sample in observed['dashboard_samples']), observed
large_batch = job['batch']['id']

# batch database internals
batch_db = RT / 'batches' / large_batch / 'data.sqlite3'
cx = sqlite3.connect(batch_db)
cx.row_factory = sqlite3.Row
tables = [r[0] for r in cx.execute("select name from sqlite_master where type='table'")]
indexes = [r[0] for r in cx.execute("select name from sqlite_master where type='index'")]
meta = json.loads(cx.execute("select value from metadata where key='manifest'").fetchone()[0])
counts = dict(cx.execute('select count(*) total, sum(sale) sale, sum(cross_duplicate) cross_dup from transactions').fetchone())
cx.close()
side_manifest = json.loads((RT / 'batches' / large_batch / 'data_manifest.json').read_text(encoding='utf-8'))
result['large_batch_db'] = {
    'tables': tables, 'has_order_index': 'ix_transactions_order' in indexes,
    'counts': counts, 'manifest_date_range': [meta['date_min'], meta['date_max']],
    'manifest_equals_side_file': meta == side_manifest,
    'manifest_equals_job_report': meta == job['report'],
    'size_bytes': batch_db.stat().st_size,
}

# ------------------------------------------------------------------ activate large batch, verify + exports
status, raw = req(f'/api/imports/batches/{large_batch}/activate', method='POST')
assert status == 200
dash = dashboard()
result['large_batch_dashboard'] = {
    'batch': dash['batch'], 'filters': dash['filters'],
    'totals': {k: dash['totals'][k] for k in ('quantity', 'revenue', 'orders', 'lines')},
    'matches_expected': (dash['totals']['quantity'] == expected_qty
                         and abs(dash['totals']['revenue'] - expected_rev) < 1e-6
                         and dash['totals']['orders'] == expected_orders),
}
assert result['large_batch_dashboard']['matches_expected'], result['large_batch_dashboard']

status, raw = req('/api/export/orders')
orders_csv = raw.decode('utf-8-sig').splitlines()
status2, raw2 = req('/api/export/transactions', timeout=300)
trans_csv = raw2.decode('utf-8-sig').splitlines()
result['large_batch_exports'] = {
    'orders_status': status, 'orders_lines': len(orders_csv),
    'orders_rows_equal_orders': len(orders_csv) - 1 == expected_orders,
    'transactions_status': status2, 'transactions_lines': len(trans_csv),
    'transactions_rows_equal_positive_rows': len(trans_csv) - 1 == job['report']['positive_sale_rows'],
    'bom': raw[:3] == b'\xef\xbb\xbf' and raw2[:3] == b'\xef\xbb\xbf',
    'long_invoice_kept_as_text': any(line.startswith('123456789012345678,') for line in trans_csv),
    'formula_neutralized': any("'=SUM(1,2)" in line for line in trans_csv),
    'negative_invoice_preserved': any(line.startswith("'-700000,") for line in trans_csv),
}
assert result['large_batch_exports']['long_invoice_kept_as_text']
assert result['large_batch_exports']['formula_neutralized']
assert result['large_batch_exports']['orders_rows_equal_orders']
assert result['large_batch_exports']['transactions_rows_equal_positive_rows']

# ------------------------------------------------------------------ switch back
status, raw = req('/api/imports/batches/original/activate', method='POST')
back = jload(raw)
dash = dashboard('?start=2011-11-10&end=2011-12-09')
result['switch_back'] = {
    'previous_batch_id': back['previous_batch_id'], 'batch': dash['batch']['id'],
    'totals': {k: dash['totals'][k] for k in ('quantity', 'revenue', 'orders', 'lines')},
    'baseline_restored': (dash['totals']['quantity'] == 851403
                          and abs(dash['totals']['revenue'] - 1694068.71) < 1e-6
                          and dash['totals']['orders'] == 2913),
}

# ------------------------------------------------------------------ read-only proof
def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


retail = ROOT / '电商运营系统' / 'runtime' / 'retail.sqlite3'
xlsx = ROOT / '20261014_电商数据与页面选型' / 'online_retail_II.xlsx'
result['read_only'] = {
    'retail_sha256': sha256(retail),
    'retail_sha256_expected': '033f4aa2972771fddd95045f6f2de0a63af1d9c744090778a76c8fb803ef61bf',
    'retail_mtime': retail.stat().st_mtime,
    'retail_size': retail.stat().st_size,
    'excel_sha256': sha256(xlsx),
    'excel_sha256_expected': 'bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980',
    'excel_mtime': xlsx.stat().st_mtime,
}
result['read_only']['retail_unchanged'] = result['read_only']['retail_sha256'] == result['read_only']['retail_sha256_expected']
result['read_only']['excel_unchanged'] = result['read_only']['excel_sha256'] == result['read_only']['excel_sha256_expected']
assert result['read_only']['retail_unchanged'] and result['read_only']['excel_unchanged']

# relative-path recording behaviour (production runtime lives inside APP_ROOT)
import sys
sys.path.insert(0, str(ROOT / '电商运营系统'))
from api import config as api_config, db as api_db
production_path = api_config.APP_ROOT / 'runtime' / 'batches' / 'demo' / 'data.sqlite3'
app_db = sqlite3.connect(RT / 'app.sqlite3')
app_db.row_factory = sqlite3.Row
stored_isolated = app_db.execute('select db_path from batches where id=?', (large_batch,)).fetchone()['db_path']
app_db.close()
result['stored_path'] = {
    'isolated_runtime_dir': str(api_config.RUNTIME_DIR),
    'isolated_batch_db_path_stored_by_register': stored_isolated,
    'production_relative_example': api_db.stored_db_path(production_path),
}

# uploads live only under the isolated runtime dir; nothing written into project runtime/batches
project_batches = ROOT / '电商运营系统' / 'runtime' / 'batches'
result['write_scope'] = {
    'isolated_imports_dirs': len(list((RT / 'imports').glob('*'))),
    'isolated_batches': len(list((RT / 'batches').glob('*'))),
    'project_runtime_batches_entries': (sorted(p.name for p in project_batches.glob('*'))
                                        if project_batches.is_dir() else 'absent'),
}

(OUT / 'probe3.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True, indent=1)[:9000])
print('PROBE3 OK')
