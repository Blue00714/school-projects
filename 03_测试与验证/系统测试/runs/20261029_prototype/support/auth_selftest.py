"""Self-test for the api-auth module (M1): auth, sessions, users, settings, health, options.

Standard library only (urllib + http.cookiejar), matching the project's test convention.
Run against a live isolated server:

    python .tmp/auth_selftest.py --base http://127.0.0.1:8781 --app-db <runtime>/app.sqlite3 \
        --initial-admin <runtime>/initial_admin.txt
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = [], []


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(('PASS ' if condition else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))
    return condition


class Client:
    """One browser-like session: own cookie jar, never shared between accounts."""

    def __init__(self, base):
        self.base = base.rstrip('/')
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, payload=None):
        data = None
        headers = {'Accept': 'application/json'}
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=120) as resp:
                raw = resp.read()
                return resp.status, dict(resp.headers), _json(raw), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, dict(exc.headers), _json(raw), raw

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, payload):
        return self.request('POST', path, payload)

    def patch(self, path, payload):
        return self.request('PATCH', path, payload)

    def put(self, path, payload):
        return self.request('PUT', path, payload)

    def token(self):
        return next((c.value for c in self.jar if c.name == 'ece_session'), '')


def raw_get(base, path, cookie):
    """GET with an explicitly crafted Cookie header (replay / forged token tests)."""
    req = urllib.request.Request(base.rstrip('/') + path, method='GET',
                                 headers={'Accept': 'application/json', 'Cookie': cookie})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), _json(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), _json(exc.read())


def _json(raw):
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def read_accounts(path):
    text = pathlib.Path(path).read_text(encoding='utf-8')
    values = {}
    for line in text.splitlines():
        if '：' not in line:
            continue
        key, value = line.split('：', 1)
        values[key.strip()] = value.strip()
    return {
        'admin_user': values.get('管理员账号', 'admin'),
        'admin_password': values.get('管理员初始密码', ''),
        'operator_user': values.get('运营账号', 'operator'),
        'operator_password': values.get('运营初始密码', ''),
    }


def wait_health(client, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, body, _ = client.get('/api/health')
            if status == 200:
                return body
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise SystemExit('server did not become healthy in time')


def wait_file(path, timeout=90):
    """First startup writes the one-time credentials file; wait for it before reading."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pathlib.Path(path).is_file():
            return
        time.sleep(0.3)
    raise SystemExit(f'missing credentials file: {path}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8781')
    parser.add_argument('--app-db', required=True)
    parser.add_argument('--initial-admin', required=True)
    parser.add_argument('--unit-only', action='store_true')
    args = parser.parse_args()

    wait_file(args.initial_admin)
    accounts = read_accounts(args.initial_admin)
    app_db = pathlib.Path(args.app_db)
    admin = accounts['admin_user']
    admin_pw = accounts['admin_password']
    operator = accounts['operator_user']
    operator_pw = accounts['operator_password']

    if not args.unit_only:
        run_http_suite(args.base, app_db, admin, admin_pw, operator, operator_pw)
    run_unit_suite(app_db)

    print(f'\n===== {len(PASS)} passed, {len(FAIL)} failed =====')
    if FAIL:
        print('failed: ' + ', '.join(FAIL))
        sys.exit(1)


def run_http_suite(base, app_db, admin, admin_pw, operator, operator_pw):
    anon = Client(base)

    # --- 1. health without login ------------------------------------------
    health = wait_health(anon)
    check('health 200 and login-free', health.get('ok') is True)
    check('health fields complete',
          {'ok', 'service', 'version', 'data_ready', 'batch', 'ai_configured'} <= set(health),
          ','.join(sorted(health)))
    check('health service/version', health.get('service') == 'ecommerce-ops-fastapi'
          and isinstance(health.get('version'), str) and health['version'])
    check('health data_ready true (Store constructible)', health.get('data_ready') is True)
    check('health batch public', isinstance(health.get('batch'), dict)
          and health['batch'].get('id') == 'original'
          and {'id', 'label', 'kind'} <= set(health['batch']))
    check('health ai_configured is bool', isinstance(health.get('ai_configured'), bool))

    # --- 2. unauthenticated protected endpoints ---------------------------
    status, _, body, _ = anon.get('/api/auth/me')
    check('GET /api/auth/me without login -> 401', status == 401
          and body.get('code') == 'unauthorized', f'{status} {body}')
    status, _, body, _ = anon.get('/api/options')
    check('GET /api/options without login -> 401', status == 401
          and body.get('code') == 'unauthorized', f'{status} {body}')

    # --- 3. login failures are uniform ------------------------------------
    for label, user, pw in [
        ('wrong password', admin, 'definitely-wrong-123'),
        ('unknown user', 'no-such-account', 'whatever-123'),
    ]:
        status, headers, body, _ = anon.post('/api/auth/login', {'username': user, 'password': pw})
        check(f'login {label} -> 401 unified message',
              status == 401 and body.get('error') == '账号或密码错误'
              and body.get('code') == 'unauthorized' and 'set-cookie' not in {
                  k.lower() for k in headers},
              f'{status} {body}')

    # --- 4. admin login + cookie attributes -------------------------------
    admin_client = Client(base)
    status, headers, body, _ = admin_client.post('/api/auth/login',
                                                 {'username': admin, 'password': admin_pw})
    check('admin login 200', status == 200 and body.get('user', {}).get('username') == admin,
          f'{status} {body}')
    user = body.get('user', {})
    check('login user object fields',
          {'id', 'username', 'display_name', 'role', 'active', 'must_change_password'} <= set(user),
          ','.join(sorted(user)))
    check('login user role admin and no password hash',
          user.get('role') == 'admin' and 'password_hash' not in user)
    set_cookie = next((v for k, v in headers.items() if k.lower() == 'set-cookie'), '')
    lowered = set_cookie.lower()
    check('Set-Cookie ece_session present', set_cookie.startswith('ece_session='), set_cookie[:80])
    check('cookie HttpOnly/Path//SameSite=Lax',
          'httponly' in lowered and 'path=/' in lowered and 'samesite=lax' in lowered,
          set_cookie[:160])
    check('cookie Max-Age=43200 (12h)', 'max-age=43200' in lowered, set_cookie[:160])
    check('cookie value not empty', len(set_cookie.split(';')[0].split('=', 1)[1]) > 20)

    # cookie domain/path behaviour: /api/auth/me accepts the cookie
    status, _, body, _ = admin_client.get('/api/auth/me')
    check('GET /api/auth/me 200', status == 200 and body.get('user', {}).get('id') == user.get('id'),
          f'{status} {body}')
    check('seeded admin must_change_password true',
          body.get('user', {}).get('must_change_password') is True)

    # --- 5. options contract + cache --------------------------------------
    started = time.perf_counter()
    status, _, options, _ = admin_client.get('/api/options')
    first_ms = (time.perf_counter() - started) * 1000
    check('GET /api/options 200', status == 200, f'{status}')
    check('options existing contract keys',
          {'manifest', 'countries', 'products', 'methods'} <= set(options))
    check('options batch + forecast extras',
          options.get('batch', {}).get('id') == 'original'
          and options.get('forecast') == {'scope': 'whole-shop', 'horizon': 7},
          json.dumps({k: options.get(k) for k in ('batch', 'forecast')}, ensure_ascii=False)[:200])
    check('options products <=500 with stock_code/name/quantity',
          isinstance(options.get('products'), list) and 0 < len(options['products']) <= 500
          and {'stock_code', 'name', 'quantity'} <= set(options['products'][0]),
          str(len(options.get('products', []))))
    check('options countries non-empty list', isinstance(options.get('countries'), list)
          and len(options['countries']) > 0)
    check('options methods = 5 forecasting methods',
          isinstance(options.get('methods'), dict) and len(options['methods']) == 5)
    check('options manifest has policy_version/date range',
          {'policy_version', 'date_min', 'date_max'} <= set(options.get('manifest', {})))
    started = time.perf_counter()
    status2, _, options2, _ = admin_client.get('/api/options')
    second_ms = (time.perf_counter() - started) * 1000
    check('options second call served from batch cache',
          status2 == 200 and options2 == options and second_ms < max(first_ms, 50),
          f'first={first_ms:.0f}ms second={second_ms:.0f}ms')
    print(f'INFO options first={first_ms:.0f}ms cached={second_ms:.0f}ms')

    # --- 6. operator cannot administer ------------------------------------
    op_client = Client(base)
    status, _, body, _ = op_client.post('/api/auth/login',
                                        {'username': operator, 'password': operator_pw})
    check('operator login 200', status == 200 and body.get('user', {}).get('role') == 'operator',
          f'{status} {body}')
    for method, path, payload in [
        ('GET', '/api/users', None),
        ('GET', '/api/settings', None),
        ('POST', '/api/users', {'username': 'x-operator-attempt', 'password': 'x-1234567'}),
        ('PATCH', f'/api/users/{user.get("id")}', {'active': True}),
        ('PUT', '/api/settings', {'ai': {'model': 'hijack'}}),
    ]:
        status, _, body, _ = op_client.request(method, path, payload)
        check(f'operator {method} {path} -> 403',
              status == 403 and body.get('code') == 'forbidden', f'{status} {body}')

    # --- 7. admin user administration -------------------------------------
    status, _, body, _ = admin_client.get('/api/users')
    check('admin GET /api/users 200', status == 200 and len(body.get('users', [])) >= 2,
          f'{status} {len(body.get("users", []))} users')
    check('users list has no password_hash',
          all('password_hash' not in u for u in body.get('users', [])))

    status, _, created, _ = admin_client.post('/api/users', {
        'username': 'qa_user', 'password': 'qa-pass-1234',
        'display_name': 'QA测试账号', 'role': 'operator'})
    qa = created.get('user', {})
    check('admin POST /api/users -> 201',
          status == 201 and qa.get('username') == 'qa_user' and qa.get('role') == 'operator'
          and qa.get('display_name') == 'QA测试账号' and qa.get('active') is True,
          f'{status} {created}')
    check('created user has no password_hash', 'password_hash' not in qa)

    status, _, body, _ = admin_client.post('/api/users', {
        'username': 'qa_user', 'password': 'another-1234'})
    check('duplicate username -> 409 conflict',
          status == 409 and body.get('code') == 'conflict', f'{status} {body}')
    status, _, body, _ = admin_client.post('/api/users', {
        'username': 'qa_short', 'password': 'short'})
    check('password shorter than 8 -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')
    status, _, body, _ = admin_client.post('/api/users', {
        'username': 'qa_badrole', 'password': 'long-enough-1', 'role': 'root'})
    check('invalid role -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')

    # new operator can log in and is still blocked from admin APIs
    qa_client = Client(base)
    status, _, body, _ = qa_client.post('/api/auth/login',
                                       {'username': 'qa_user', 'password': 'qa-pass-1234'})
    check('created user can log in', status == 200 and body.get('user', {}).get('username') == 'qa_user',
          f'{status} {body}')
    status, _, body, _ = qa_client.get('/api/users')
    check('created operator GET /api/users -> 403', status == 403, f'{status} {body}')

    # admin deactivates the account -> login refused with the unified message
    status, _, body, _ = admin_client.patch(f'/api/users/{qa.get("id")}', {'active': False})
    check('admin PATCH deactivate 200', status == 200 and body.get('user', {}).get('active') is False,
          f'{status} {body}')
    status, _, body, _ = Client(base).post('/api/auth/login',
                                           {'username': 'qa_user', 'password': 'qa-pass-1234'})
    check('disabled account login -> 401 unified message',
          status == 401 and body.get('error') == '账号或密码错误', f'{status} {body}')
    status, _, body, _ = qa_client.get('/api/auth/me')
    check('disabled account existing session -> 401', status == 401, f'{status} {body}')

    # self-protection rules
    status, _, body, _ = admin_client.patch(f'/api/users/{user.get("id")}', {'active': False})
    check('cannot deactivate self -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')
    status, _, body, _ = admin_client.patch(f'/api/users/{user.get("id")}', {'role': 'operator'})
    check('cannot demote self -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')
    status, _, body, _ = admin_client.patch('/api/users/999999', {'active': False})
    check('PATCH unknown user -> 404', status == 404 and body.get('code') == 'not_found',
          f'{status} {body}')

    # admin resets a password: old hash replaced, login uses the new one
    status, _, body, _ = admin_client.patch(f'/api/users/{qa.get("id")}',
                                            {'active': True, 'password': 'reset-pass-999'})
    check('admin PATCH password+active 200',
          status == 200 and body.get('user', {}).get('active') is True, f'{status} {body}')
    status, _, body, _ = Client(base).post('/api/auth/login',
                                           {'username': 'qa_user', 'password': 'reset-pass-999'})
    check('login with admin-reset password 200', status == 200, f'{status} {body}')

    # --- 8. self-service password change ----------------------------------
    op_second = Client(base)
    status, _, _, _ = op_second.post('/api/auth/login',
                                     {'username': operator, 'password': operator_pw})
    check('operator second session login 200', status == 200)
    status, _, body, _ = op_client.post('/api/auth/password',
                                        {'old_password': 'wrong-old-123', 'new_password': 'brand-new-123'})
    check('password change wrong old -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')
    status, _, body, _ = op_client.post('/api/auth/password',
                                        {'old_password': operator_pw, 'new_password': 'short'})
    check('password change new too short -> 400', status == 400, f'{status} {body}')
    status, _, body, _ = op_client.post('/api/auth/password',
                                        {'old_password': operator_pw, 'new_password': 'operator-new-4321'})
    check('password change 200 {ok:true}', status == 200 and body.get('ok') is True,
          f'{status} {body}')
    status, _, _, _ = op_client.get('/api/auth/me')
    check('current session kept after password change', status == 200, str(status))
    status, _, _, _ = op_second.get('/api/auth/me')
    check('other sessions revoked after password change', status == 401, str(status))
    status, _, body, _ = Client(base).post('/api/auth/login',
                                           {'username': operator, 'password': operator_pw})
    check('old password no longer works', status == 401 and body.get('error') == '账号或密码错误',
          f'{status} {body}')
    status, _, body, _ = Client(base).post('/api/auth/login',
                                           {'username': operator, 'password': 'operator-new-4321'})
    check('new password works', status == 200, f'{status} {body}')
    op_pw_latest = 'operator-new-4321'

    # --- 9. logout --------------------------------------------------------
    temp_client = Client(base)
    temp_client.post('/api/auth/login', {'username': admin, 'password': admin_pw})
    status, headers, body, _ = temp_client.post('/api/auth/logout', {})
    check('logout 200 {ok:true}', status == 200 and body.get('ok') is True, f'{status} {body}')
    clear_cookie = next((v for k, v in headers.items() if k.lower() == 'set-cookie'), '')
    check('logout clears cookie', 'ece_session=' in clear_cookie
          and ('max-age=0' in clear_cookie.lower() or 'expires=' in clear_cookie.lower()),
          clear_cookie[:160])
    status, _, body, _ = temp_client.get('/api/auth/me')
    check('after logout /api/auth/me -> 401', status == 401, f'{status} {body}')

    # logout must revoke the token server-side, not merely drop the browser cookie
    replay_client = Client(base)
    replay_client.post('/api/auth/login', {'username': admin, 'password': admin_pw})
    replayed_token = replay_client.token()
    replay_client.post('/api/auth/logout', {})
    status, _, body = raw_get(base, '/api/auth/me', f'ece_session={replayed_token}')
    check('revoked token replayed after logout -> 401', status == 401, f'{status} {body}')
    status, _, body = raw_get(base, '/api/auth/me', 'ece_session=forged-token-value')
    check('unknown session token -> 401', status == 401, f'{status} {body}')

    # --- 10. settings -----------------------------------------------------
    status, _, body, _ = admin_client.get('/api/settings')
    check('admin GET /api/settings 200',
          status == 200 and {'ai', 'session_ttl_hours', 'batch'} <= set(body), f'{status}')
    check('settings ai public shape',
          {'configured', 'provider', 'base_url', 'model', 'timeout_seconds', 'key_present'}
          <= set(body.get('ai', {})) and 'api_key' not in body.get('ai', {}))
    check('settings session_ttl_hours 12', body.get('session_ttl_hours') == 12.0)
    check('settings batch original', body.get('batch', {}).get('id') == 'original')

    status, _, body, _ = admin_client.put('/api/settings', {
        'ai': {'base_url': 'https://llm.example.com/v1', 'model': 'demo-model',
               'timeout_seconds': 45}})
    check('PUT /api/settings 200 with stored values',
          status == 200 and body.get('ai', {}).get('base_url') == 'https://llm.example.com/v1'
          and body['ai'].get('model') == 'demo-model'
          and body['ai'].get('timeout_seconds') == 45.0, f'{status} {body.get("ai")}')
    check('settings never returns API key',
          'api_key' not in json.dumps(body.get('ai', {}))
          and 'key' not in {k.lower() for k in body.get('ai', {})})
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'api_key': 'sk-secret-value'}})
    check('PUT api_key rejected -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'timeout_seconds': 0}})
    check('PUT invalid timeout -> 400', status == 400, f'{status} {body}')
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'base_url': 'ftp://x'}})
    check('PUT invalid base_url -> 400', status == 400, f'{status} {body}')
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'unknown_key': 'x'}})
    check('PUT unknown ai key -> 400', status == 400, f'{status} {body}')
    status, _, body, _ = admin_client.get('/api/settings')
    check('stored settings persisted for later GET',
          status == 200 and body.get('ai', {}).get('model') == 'demo-model'
          and body['ai'].get('base_url') == 'https://llm.example.com/v1', json.dumps(body.get('ai')))
    status, _, body, _ = admin_client.put('/api/settings', {})
    check('PUT /api/settings empty body 200 keeps stored values',
          status == 200 and body.get('ai', {}).get('model') == 'demo-model', f'{status}')
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'model': None}})
    check('PUT null clears stored override (falls back to env)',
          status == 200 and body.get('ai', {}).get('model') == '', json.dumps(body.get('ai')))
    status, _, body, _ = admin_client.put('/api/settings', {'ai': {'model': 'demo-model'}})
    status, _, body, _ = admin_client.put('/api/settings', {'api_key': 'sk-top-level'})
    check('PUT top-level unknown field -> 400', status == 400 and body.get('code') == 'bad_request',
          f'{status} {body}')

    # --- 11. session token is stored hashed -------------------------------
    with sqlite3.connect(app_db) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute('select token_hash, revoked from sessions').fetchall()
        audit_actions = [r[0] for r in cx.execute('select action from audit_log').fetchall()]
    check('sessions rows present', len(rows) > 0, f'{len(rows)} sessions')
    check('session token stored as sha256 hex (64 chars, no raw token)',
          all(len(r['token_hash']) == 64 and all(c in '0123456789abcdef' for c in r['token_hash'])
              for r in rows))
    raw_tokens = [c.value for c in admin_client.jar]
    check('raw token not stored in sessions table',
          all(t not in {r['token_hash'] for r in rows} for t in raw_tokens))
    check('audit log records user.create/user.update/auth.password_change/settings',
          {'user.create', 'user.update', 'auth.password_change', 'settings.ai.update'}
          <= set(audit_actions), ','.join(sorted(set(audit_actions))))

    # --- 12. options cache follows the active batch -----------------------
    switch_batch(app_db, 'cache-test-batch')
    try:
        status, _, switched, _ = admin_client.get('/api/options')
        check('options cache invalidated after batch switch',
              status == 200 and switched.get('batch', {}).get('id') == 'cache-test-batch'
              and switched.get('manifest') == options.get('manifest'), f'{status}')
    finally:
        restore_batch(app_db)
    status, _, restored, _ = admin_client.get('/api/options')
    check('options back to original batch after restore',
          status == 200 and restored.get('batch', {}).get('id') == 'original', str(status))

    # --- 13. health is still login-free after all writes ------------------
    status, _, body, _ = anon.get('/api/health')
    check('health still login-free 200', status == 200 and body.get('data_ready') is True,
          f'{status}')

    if op_pw_latest == 'operator-new-4321':
        print('INFO operator password was changed during this run (isolated runtime only)')


def switch_batch(app_db, new_id):
    with sqlite3.connect(app_db, timeout=30) as cx:
        cx.row_factory = sqlite3.Row
        row = dict(cx.execute("select * from batches where id='original'").fetchone())
        cx.execute('update batches set is_active=0 where is_active=1')
        cx.execute(
            'insert into batches(id,label,kind,status,db_path,source_file,source_sha256,'
            'policy_version,manifest_json,is_active,created_by,created_at,published_at) '
            'values(?,?,?,?,?,?,?,?,?,1,NULL,?,?)',
            (new_id, '缓存失效测试批次', 'upload', 'ready', row['db_path'],
             row['source_file'], row['source_sha256'], row['policy_version'],
             row['manifest_json'], row['created_at'], row['created_at']))
        cx.commit()


def restore_batch(app_db):
    with sqlite3.connect(app_db, timeout=30) as cx:
        cx.execute("update batches set is_active=0 where id='cache-test-batch'")
        cx.execute("delete from batches where id='cache-test-batch'")
        cx.execute("update batches set is_active=1 where id='original'")
        cx.commit()


def run_unit_suite(app_db):
    """Rules that are unreachable through HTTP when the only active admin is the caller."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / '电商运营系统'))
    import os

    os.environ.setdefault('ECE_APP_DB', str(app_db))
    from api.errors import BadRequest
    from api.services import users as user_service

    with sqlite3.connect(app_db) as cx:
        cx.row_factory = sqlite3.Row
        admin_row = cx.execute("select * from users where role='admin' order by id limit 1").fetchone()
        admin_id = admin_row['id']
        admins = cx.execute("select count(*) from users where role='admin' and active=1").fetchone()[0]

    if admins != 1:
        check('last-admin guard (unit) skipped: not a single active admin', False, f'{admins}')
        return
    try:
        user_service.update_user(admin_id, actor_id=None, active=False)
        check('last enabled admin cannot be removed (unit) -> 400', False, 'no exception')
    except BadRequest as exc:
        check('last enabled admin cannot be removed (unit) -> 400',
              '管理员' in str(exc), str(exc))
    except Exception as exc:  # noqa: BLE001 - report the actual failure type
        check('last enabled admin cannot be removed (unit) -> 400', False,
              f'{type(exc).__name__}: {exc}')
    current = user_service.get_user(admin_id)
    check('admin account still active after blocked change', bool(current.get('active')))


if __name__ == '__main__':
    main()
