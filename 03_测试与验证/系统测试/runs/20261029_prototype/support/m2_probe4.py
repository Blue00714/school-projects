"""M2 edge-contract probe: auth, malformed upload, non-ready batch, legacy .xls error path."""
import http.cookiejar
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = 'http://127.0.0.1:8782'
OUT = Path(__file__).resolve().parent
RT = Path(r'C:\Users\Administrator\11111\数据\学校项目\.tmp\rt_data')
COLS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']
result = {}


def opener_for(login=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    if login:
        opener.open(urllib.request.Request(BASE + '/api/auth/login', method='POST',
                                           data=json.dumps(login).encode(),
                                           headers={'Content-Type': 'application/json'})).read()
    return opener


def call(opener, path, method='GET', data=None, headers=None, timeout=300):
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


admin = opener_for({'username': 'admin', 'password': 'AdminTest123'})
anon = opener_for()

# 1) unauthenticated access to every data endpoint -> 401
auth = {}
for path in ['/api/dashboard', '/api/orders?start=2011-11-10&end=2011-12-09', '/api/products',
             '/api/transactions', '/api/export/orders', '/api/imports/batches',
             '/api/imports/jobs/x', '/api/imports/template.csv']:
    status, raw = call(anon, path)
    auth[path] = {'status': status, 'code': jload(raw).get('code') if status != 200 else None}
    assert status == 401, (path, status)
status, raw = call(anon, '/api/imports', method='POST', data=b'', headers={'Content-Type': 'multipart/form-data'})
auth['POST /api/imports'] = {'status': status, 'code': jload(raw).get('code')}
assert status == 401
result['unauthenticated'] = auth

# 2) POST /api/imports without a file part -> 400
body, ctype = multipart({'label': 'no file'}, {})
status, raw = call(admin, '/api/imports', method='POST', data=body, headers={'Content-Type': ctype})
result['missing_file_part'] = {'status': status, 'body': jload(raw)}
assert status == 400

# 3) same bytes, different file name -> duplicate (hash based, not name based)
sample = (OUT / 'sample_upload.csv').read_bytes()
renamed = OUT / 'renamed_copy.csv'
renamed.write_bytes(sample)
body, ctype = multipart({}, {'file': ('renamed_copy.csv', sample, 'text/csv')})
status, raw = call(admin, '/api/imports', method='POST', data=body, headers={'Content-Type': ctype})
dupe = jload(raw)
result['duplicate_by_hash'] = {'status': status, 'body': dupe, 'is_duplicate': dupe.get('status') == 'duplicate'}
assert dupe['status'] == 'duplicate'

# 4) import a fake .xls (not a real workbook) -> job failed with a clear parse error
fake_xls = OUT / 'fake.xls'
fake_xls.write_bytes(b'this is not a real xls file')
body, ctype = multipart({}, {'file': ('fake.xls', fake_xls.read_bytes(), 'application/vnd.ms-excel')})
status, raw = call(admin, '/api/imports', method='POST', data=body, headers={'Content-Type': ctype})
import json as _json, time as _time
job_body = jload(raw)
t0 = _time.monotonic()
while _time.monotonic() - t0 < 60:
    s, r = call(admin, '/api/imports/jobs/' + job_body['job_id'])
    payload = jload(r)
    if payload['job']['status'] not in ('queued', 'running'):
        break
    _time.sleep(0.2)
result['fake_xls'] = {'upload_http': status, 'status': payload['job']['status'],
                      'error': payload['job']['error'], 'issues': payload['issues'],
                      'batch': payload['batch']}
assert status == 202 and payload['job']['status'] == 'failed'
assert payload['job']['error']

# 5) activating a not-ready batch -> 400
cx = sqlite3.connect(RT / 'app.sqlite3')
cx.execute("insert or replace into batches(id,label,kind,status,db_path,source_file,source_sha256,"
           "policy_version,manifest_json,is_active,created_by,created_at) "
           "values('bnotready','未就绪批次','upload','failed','runtime/batches/x/data.sqlite3','x',"
           "'deadbeef','', '{}',0,NULL,'2026-10-29T00:00:00+00:00')")
cx.commit()
cx.close()
status, raw = call(admin, '/api/imports/batches/bnotready/activate', method='POST')
result['activate_not_ready'] = {'status': status, 'body': jload(raw)}
assert status == 400
# clean the synthetic row so the batch list stays honest
cx = sqlite3.connect(RT / 'app.sqlite3')
cx.execute("delete from batches where id='bnotready'")
cx.commit()
cx.close()

# 6) order detail outside the filter range -> 404
status, raw = call(admin, '/api/orders/581475?start=2011-01-01&end=2011-01-31')
result['detail_out_of_range'] = {'status': status, 'body': jload(raw)}
assert status == 404

# 7) default pagination stays 25 and parameter errors are 400
status, raw = call(admin, '/api/orders')
payload = jload(raw)
result['default_paging'] = {'status': status, 'page': payload['page'], 'page_size': payload['page_size'],
                            'rows': len(payload['rows']), 'total': payload['total'],
                            'default_range_days': 30}
for path, expected in [('/api/orders?page=abc&start=2011-11-10&end=2011-12-09', 400),
                       ('/api/transactions?page_size=0', 400),
                       ('/api/orders?start=2011-01-01&end=2015-01-01', 400)]:
    status, raw = call(admin, path)
    result.setdefault('param_errors', {})[path] = {'status': status, 'code': jload(raw).get('code'),
                                                   'error': jload(raw).get('error')}
    assert status == expected, (path, status)

(OUT / 'probe4.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True, indent=1)[:7000])
print('PROBE4 OK')
