"""M2 imports/batches acceptance probe (isolated server on 127.0.0.1:8782)."""
import hashlib
import http.cookiejar
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

BASE = 'http://127.0.0.1:8782'
OUT = Path(__file__).resolve().parent
COLS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
result = {'steps': []}


def req(path, method='GET', data=None, headers=None, timeout=180):
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


def upload(path: Path, label: str = ''):
    content = Path(path).read_bytes()
    fields = {'label': label} if label else {}
    body, ctype = multipart(fields, {'file': (Path(path).name, content, 'application/octet-stream')})
    status, raw = req('/api/imports', method='POST', data=body, headers={'Content-Type': ctype})
    return status, raw


def wait_job(job_id, timeout=240):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        status, raw = req('/api/imports/jobs/' + job_id)
        payload = jload(raw)
        if payload['job']['status'] not in ('queued', 'running'):
            return payload, round(time.monotonic() - t0, 2)
        time.sleep(0.25)
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
result['login'] = status

# original baseline before any import
base = dashboard('?start=2011-11-10&end=2011-12-09')
result['original_before'] = {'batch': base['batch'], 'totals': {
    k: base['totals'][k] for k in ('quantity', 'revenue', 'orders', 'lines')}}

# ------------------------------------------------------------------ sample workbook
first = [
    ['INV200001', 'A-1001', 'Widget A', 2, datetime(2011, 1, 3, 10, 0), 3.5, 10001, 'United Kingdom'],
    ['INV200001', 'A-1002', 'Widget B', 1, datetime(2011, 1, 3, 10, 5), 10.0, 10001, 'United Kingdom'],
    ['INV200002', 'B-2001', 'Gadget C', 5, datetime(2011, 1, 4, 11, 0), 2.25, 10002, 'France'],
    ['INV200003', 'B-2002', 'Gadget D', 3, datetime(2011, 1, 5, 12, 30), 7.75, 10003, 'Germany'],
    ['INV200004', 'C-3001', 'Thing E', 4, datetime(2011, 1, 6, 9, 15), 1.1, 10004, 'United Kingdom'],
    ['INV200005', 'C-3002', 'Thing F', 2, datetime(2011, 1, 7, 16, 45), 12.99, 10005, 'Spain'],
    ['INV200006', 'A-1001', 'Widget A', 6, datetime(2011, 1, 8, 14, 20), 3.5, 10006, 'United Kingdom'],
    ['INV200007', 'D-4001', 'Item G', 1, datetime(2011, 1, 9, 10, 10), 99.99, 10007, 'France'],
    ['INV200008', 'D-4002', 'Item H', 8, datetime(2011, 1, 10, 11, 11), 0.5, 10008, 'United Kingdom'],
    ['INV200009', 'E-5001', 'Item I', 2, datetime(2011, 1, 11, 13, 13), 45.0, 10009, 'Netherlands'],
]
edge = [
    ['C200010', 'A-1001', 'Widget A', -2, datetime(2011, 1, 12, 10, 0), 3.5, 10010, 'United Kingdom'],
    ['INV200011', 'A-1002', 'Widget B', 0, datetime(2011, 1, 12, 11, 0), 10.0, 10011, 'United Kingdom'],
    ['INV200012', 'B-2001', 'Gadget C', 3, 'not-a-date', 2.25, 10012, 'France'],
    ['INV200013', 'B-2002', 'Gadget D', 2, datetime(2011, 1, 13, 12, 0), None, 10013, 'Germany'],
    ['INV200014', 'C-3001', 'Thing E', -1, datetime(2011, 1, 14, 13, 0), 1.1, 10014, 'United Kingdom'],
]
second = [list(row) for row in first[:3]] + [
    ['INV200015', 'F-6001', 'Item J', 2, datetime(2011, 1, 15, 10, 0), 20.0, 10015, 'United Kingdom'],
    ['INV200016', 'F-6002', 'Item K', 4, datetime(2011, 1, 16, 11, 0), 5.5, 10016, 'France'],
]
sample = OUT / 'sample_upload.xlsx'
wb = Workbook()
ws = wb.active
ws.title = 'SheetA'
ws.append(COLS)
for row in first + edge:
    ws.append(row)
ws2 = wb.create_sheet('SheetB')
ws2.append(COLS)
for row in second:
    ws2.append(row)
wb.save(sample)
wb.close()

# expected numbers, derived independently from the source rows above
def parse_date(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


all_rows = [(name, row) for name, rows in (('SheetA', first + edge), ('SheetB', second)) for row in rows]
seen, retained = set(), []
cross = 0
for _sheet, row in all_rows:
    key = tuple(str(v) if not isinstance(v, datetime) else v.isoformat() for v in row)
    if key in seen:
        cross += 1
        continue
    seen.add(key)
    retained.append(row)
sale = [row for row in retained
        if parse_date(row[4]) is not None and float(row[3]) > 0
        and not str(row[0]).upper().startswith('C')]
revenue = sum(round(float(row[3]) * float(row[5]) * 10000) / 10000 for row in sale
              if row[5] is not None and float(row[5]) >= 0)
expected = {
    'raw_rows': len(all_rows), 'cross_sheet_copies_excluded': cross,
    'retained_rows': len(retained), 'positive_sale_rows': len(sale),
    'quantity': int(sum(float(row[3]) for row in sale)),
    'revenue': round(revenue, 2),
    'orders': len({row[0] for row in sale}),
    'cancelled_rows': sum(1 for row in retained if str(row[0]).upper().startswith('C')),
    'invalid_dates': sum(1 for row in retained if parse_date(row[4]) is None),
    'nonpositive_quantity_rows': sum(1 for row in retained if float(row[3]) <= 0),
    'sale_rows_without_valid_price': sum(1 for row in sale if row[5] is None or float(row[5]) < 0),
}
expected_manifest = {key: expected[key] for key in (
    'raw_rows', 'cross_sheet_copies_excluded', 'retained_rows', 'positive_sale_rows',
    'cancelled_rows', 'invalid_dates', 'nonpositive_quantity_rows', 'sale_rows_without_valid_price')}
result['expected'] = expected
result['expected_manifest'] = expected_manifest

# ------------------------------------------------------------------ import xlsx
status, raw = upload(sample, label='M2 样例导入')
body = jload(raw)
result['import_upload_status'] = status
assert status == 202, (status, raw[:300])
assert body['status'] == 'queued', body
job, waited = wait_job(body['job_id'])
result['import1'] = {
    'job_id': body['job_id'], 'http': status, 'status': job['job']['status'],
    'waited_s': waited, 'job_keys': sorted(job['job'].keys()),
    'batch': job['batch'], 'report': {k: job['report'].get(k) for k in expected_manifest},
    'report_matches_expected': all(job['report'].get(k) == v for k, v in expected_manifest.items()),
    'issues_count': len(job['issues']),
    'issue_levels': {level: sum(1 for i in job['issues'] if i['level'] == level) for level in ('error', 'warning', 'info')},
    'issue_fields': sorted({i['field'] for i in job['issues']}),
    'sample_issues': job['issues'][:3],
    'report_date_range': [job['report'].get('date_min'), job['report'].get('date_max')],
    'report_policy': job['report'].get('policy_version'),
    'report_source_file': job['report'].get('source_file'),
    'report_source_kind': job['report'].get('source_kind'),
    'report_source_sha256_matches': job['report'].get('source_sha256') == hashlib.sha256(sample.read_bytes()).hexdigest(),
}
assert job['job']['status'] == 'succeeded', job
assert result['import1']['report_matches_expected'], (expected_manifest, job['report'])

xlsx_batch = job['batch']['id']
status, raw = req('/api/imports/batches')
batches = jload(raw)
result['batches_after_import'] = {
    'active_batch_id': batches['active_batch_id'], 'ids': [b['id'] for b in batches['batches']],
    'new_batch': next(b for b in batches['batches'] if b['id'] == xlsx_batch),
    'no_auto_switch': batches['active_batch_id'] == 'original',
}
assert batches['active_batch_id'] == 'original'

# batch database must exist under the isolated runtime and be recorded in app.sqlite3
batch_dir = Path(r'C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_data\batches') / xlsx_batch
result['batch_files'] = {
    'dir_exists': batch_dir.is_dir(),
    'files': sorted(p.name for p in batch_dir.glob('*')),
    'db_bytes': (batch_dir / 'data.sqlite3').stat().st_size if (batch_dir / 'data.sqlite3').is_file() else 0,
}
app_db = r'C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_data\app.sqlite3'
cx = sqlite3.connect(app_db)
cx.row_factory = sqlite3.Row
row = cx.execute('select * from batches where id=?', (xlsx_batch,)).fetchone()
result['app_db_row'] = {'db_path': row['db_path'], 'kind': row['kind'], 'status': row['status'],
                        'is_active': row['is_active'], 'label': row['label'],
                        'source_sha256': row['source_sha256'][:16] + '...'}
cx.close()

# duplicate import of the same bytes
status, raw = upload(sample, label='M2 样例导入(重复)')
dupe = jload(raw)
result['import_duplicate'] = {'http': status, 'body': dupe,
                              'same_batch': dupe.get('batch_id') == xlsx_batch}
assert status == 202 and dupe['status'] == 'duplicate' and dupe['batch_id'] == xlsx_batch
dupe_job, _ = wait_job(dupe['job_id'])
result['import_duplicate']['job'] = {'status': dupe_job['job']['status'], 'batch': dupe_job['batch'],
                                     'report_keys': sorted(dupe_job['report'].keys())}
status, raw = req('/api/imports/batches')
result['batch_count_after_duplicate'] = len(jload(raw)['batches'])
assert result['batch_count_after_duplicate'] == 2, result['batch_count_after_duplicate']

# ------------------------------------------------------------------ activate new batch
status, raw = req(f'/api/imports/batches/{xlsx_batch}/activate', method='POST')
activated = jload(raw)
result['activate'] = {'http': status, 'batch_id': activated['batch']['id'],
                      'previous_batch_id': activated['previous_batch_id'],
                      'is_active': activated['batch']['is_active']}
assert status == 200 and activated['previous_batch_id'] == 'original'

new_dash = dashboard()
new_orders_status, new_orders_raw = req('/api/orders?page=1&page_size=200')
new_orders = jload(new_orders_raw)
result['dashboard_new_batch'] = {
    'batch': new_dash['batch'], 'filters': new_dash['filters'],
    'totals': {k: new_dash['totals'][k] for k in ('quantity', 'revenue', 'orders', 'lines')},
    'matches_expected': (new_dash['totals']['quantity'] == expected['quantity']
                         and abs(new_dash['totals']['revenue'] - expected['revenue']) < 1e-6
                         and new_dash['totals']['orders'] == expected['orders']),
    'order_total': new_orders['total'], 'order_total_matches': new_orders['total'] == expected['orders'],
    'order_rows': len(new_orders['rows']), 'batch_field': new_orders['batch'],
}
assert result['dashboard_new_batch']['matches_expected'], result['dashboard_new_batch']
assert new_orders['total'] == expected['orders']

# old batch still queryable/consistent through its own export? (switch back first)
status, raw = req('/api/imports/batches/original/activate', method='POST')
back = jload(raw)
after = dashboard('?start=2011-11-10&end=2011-12-09')
result['switch_back'] = {
    'http': status, 'previous_batch_id': back['previous_batch_id'], 'batch': after['batch'],
    'totals': {k: after['totals'][k] for k in ('quantity', 'revenue', 'orders', 'lines')},
    'matches_original_baseline': (after['totals']['quantity'] == 851403
                                  and abs(after['totals']['revenue'] - 1694068.71) < 1e-6
                                  and after['totals']['orders'] == 2913),
}
assert result['switch_back']['matches_original_baseline']

# ------------------------------------------------------------------ CSV import
csv_rows = [
    ['300001', 'G-7001', 'Csv Item A', 2, '2011-02-01 10:00:00', 4.5, '20001', 'Italy'],
    ['300001', 'G-7002', 'Csv Item B', 3, '2011-02-01 10:10:00', 2.0, '20001', 'Italy'],
    ['300002', 'G-7003', 'Csv Item C', 1, '2011-02-02 11:00:00', 15.75, '20002', 'France'],
    ['300003', 'G-7004', 'Csv Item D', 4, '2011-02-03 12:00:00', 1.25, '20003', 'Spain'],
    ['C300004', 'G-7005', 'Csv Cancelled', -3, '2011-02-04 13:00:00', 9.0, '20004', 'Germany'],
    ['300006', 'G-7006', 'Csv Bad Date', 2, '2020-13-45', 6.0, '20005', 'France'],
]
csv_path = OUT / 'sample_upload.csv'
lines = [','.join(COLS)]
for row in csv_rows:
    lines.append(','.join(str(value) if value is not None else '' for value in row))
csv_path.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8-sig')

status, raw = upload(csv_path, label='M2 CSV 样例')
csv_body = jload(raw)
csv_job, csv_waited = wait_job(csv_body['job_id'])
result['import_csv'] = {
    'http': status, 'batch_id': csv_job['batch'], 'status': csv_job['job']['status'],
    'waited_s': csv_waited, 'report': {k: csv_job['report'].get(k) for k in
                                       ('raw_rows', 'retained_rows', 'positive_sale_rows', 'cancelled_rows',
                                        'invalid_dates', 'date_min', 'date_max')},
    'issues_count': len(csv_job['issues']),
}
assert status == 202 and csv_job['job']['status'] == 'succeeded', csv_job
assert csv_job['report']['raw_rows'] == 6
assert csv_job['report']['positive_sale_rows'] == 4, csv_job['report']
csv_batch = csv_job['batch']['id']

# duplicate CSV
status, raw = upload(csv_path)
csv_dupe = jload(raw)
result['import_csv_duplicate'] = {'http': status, 'body': csv_dupe,
                                  'same_batch': csv_dupe.get('batch_id') == csv_batch}
assert csv_dupe['status'] == 'duplicate' and csv_dupe['batch_id'] == csv_batch

# ------------------------------------------------------------------ failed imports
bad_csv = OUT / 'bad_columns.csv'
bad_csv.write_text('Invoice,StockCode,Quantity\r\nX1,A,2\r\n', encoding='utf-8')
status, raw = upload(bad_csv)
bad_body = jload(raw)
bad_job, bad_waited = wait_job(bad_body['job_id'])
result['import_failed_columns'] = {
    'http': status, 'status': bad_job['job']['status'], 'error': bad_job['job']['error'],
    'issues': bad_job['issues'], 'batch': bad_job['batch'],
}
assert status == 202 and bad_job['job']['status'] == 'failed'
assert '缺少必要字段' in (bad_job['job']['error'] or '')
assert bad_job['batch'] is None

bad_ext = OUT / 'note.txt'
bad_ext.write_text('not a data file', encoding='utf-8')
status, raw = upload(bad_ext)
result['import_bad_extension'] = {'http': status, 'body': jload(raw)}
assert status == 400

empty_file = OUT / 'empty.csv'
empty_file.write_bytes(b'')
status, raw = upload(empty_file)
result['import_empty_file'] = {'http': status, 'body': jload(raw)}
assert status == 400

# oversize upload (>20MB) must be rejected with 400 and leave no job
big = b'Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country\r\n' + b'X' * (21 * 1024 * 1024)
body_bytes, ctype = multipart({}, {'file': ('big.csv', big, 'text/csv')})
status, raw = req('/api/imports', method='POST', data=body_bytes, headers={'Content-Type': ctype}, timeout=300)
result['import_oversize'] = {'http': status, 'body': jload(raw)}
assert status == 400, status

# ------------------------------------------------------------------ final state
status, raw = req('/api/imports/batches')
final = jload(raw)
result['final_batches'] = {
    'active_batch_id': final['active_batch_id'],
    'ids': [b['id'] for b in final['batches']],
    'count': len(final['batches']),
}
assert final['active_batch_id'] == 'original'
assert len(final['batches']) == 3  # original + xlsx + csv

(OUT / 'probe2.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'sample_issues'}, ensure_ascii=True, indent=1)[:9000])
print('PROBE2 OK')
