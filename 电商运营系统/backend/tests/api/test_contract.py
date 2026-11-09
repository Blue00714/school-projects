"""Cross-cutting contract tests: error codes, same-origin guard, OpenAPI consistency.

* error bodies are always ``{"error": "...", "code": "..."}`` with the documented status
  codes (validation must be 400, never FastAPI's default 422);
* non-GET requests from a foreign Origin are rejected with 403 before any work happens;
* ``/openapi.json`` lists exactly the routes FastAPI actually registered, and every
  endpoint promised by ``API契约.md`` section 5 is present;
* the single-page frontend is served by the FastAPI app itself.
"""
from __future__ import annotations

import re
import unittest

from apiharness import ADMIN_PASSWORD, ADMIN_USER, ApiClient, ApiTestCase, jdump

# (method, path) promised by API契约.md section 5. Path parameters are normalised to
# ``{}`` before comparison, parameter *names* are implementation details.
CONTRACT_ENDPOINTS = [
    ('GET', '/api/health'),
    ('POST', '/api/auth/login'),
    ('POST', '/api/auth/logout'),
    ('GET', '/api/auth/me'),
    ('POST', '/api/auth/password'),
    ('GET', '/api/options'),
    ('GET', '/api/users'),
    ('POST', '/api/users'),
    ('PATCH', '/api/users/{id}'),
    ('GET', '/api/products'),
    ('GET', '/api/orders'),
    ('GET', '/api/orders/{order_key}'),
    ('GET', '/api/dashboard'),
    ('GET', '/api/transactions'),
    ('GET', '/api/export/trend'),
    ('GET', '/api/export/orders'),
    ('GET', '/api/export/transactions'),
    ('POST', '/api/imports'),
    ('GET', '/api/imports/jobs/{job_id}'),
    ('GET', '/api/imports/batches'),
    ('POST', '/api/imports/batches/{id}/activate'),
    ('GET', '/api/imports/template.csv'),
    ('POST', '/api/forecast'),
    ('GET', '/api/forecast/latest'),
    ('POST', '/api/backtest'),
    ('GET', '/api/jobs/{job_id}'),
    ('GET', '/api/jobs/{job_id}/export'),
    ('GET', '/api/inventory/scenarios'),
    ('POST', '/api/inventory/demo'),
    ('POST', '/api/inventory/scenarios'),
    ('GET', '/api/inventory/scenarios/{sid}/items'),
    ('PUT', '/api/inventory/scenarios/{sid}/items/{stock_code}'),
    ('POST', '/api/inventory/scenarios/{sid}/movements'),
    ('GET', '/api/inventory/scenarios/{sid}/movements'),
    ('GET', '/api/inventory/scenarios/{sid}/replenishment'),
    ('POST', '/api/inventory/scenarios/{sid}/replenishment/confirm'),
    ('GET', '/api/inventory/scenarios/{sid}/in-transit'),
    ('POST', '/api/inventory/scenarios/{sid}/in-transit/{iid}/receive'),
    ('GET', '/api/ai/status'),
    ('GET', '/api/ai/tools'),
    ('POST', '/api/ai/ask'),
    ('POST', '/api/ai/brief'),
    ('GET', '/api/ai/briefs/{id}.md'),
]
METHOD_ORDER = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS')
PARAM_RE = re.compile(r'\{[^}]+\}')


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


def _normalise(path: str) -> str:
    return PARAM_RE.sub('{}', path)


def _endpoint_set(pairs) -> set[tuple[str, str]]:
    return {(method.upper(), _normalise(path)) for method, path in pairs
            if method.upper() not in ('HEAD', 'OPTIONS')}


class ContractTests(ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    # -- OpenAPI -----------------------------------------------------------
    def test_01_openapi_matches_registered_routes(self):
        response = self.new_client().get('/openapi.json')
        document = self.expect_json(response, 200)
        documented = set()
        for path, operations in (document.get('paths') or {}).items():
            for method in operations:
                if method.upper() in METHOD_ORDER:
                    documented.add((method.upper(), _normalise(path)))
        documented = {(m, p) for m, p in documented if p.startswith('/api')}

        registered = set()
        for path, methods in self.server.dump_routes():
            if not path.startswith('/api'):
                continue
            for method in methods:
                if method.upper() not in ('HEAD', 'OPTIONS'):
                    registered.add((method.upper(), _normalise(path)))

        self.assertEqual(sorted(registered), sorted(documented),
                         'openapi.json 与 FastAPI 实际注册的路由不一致\n'
                         f'仅在文档：{sorted(documented - registered)}\n'
                         f'仅在路由：{sorted(registered - documented)}')
        self.assertGreaterEqual(len(registered), len(_endpoint_set(CONTRACT_ENDPOINTS)),
                                f'契约端点数量少于预期：实际注册 {len(registered)} 个')

    def test_02_contract_endpoints_are_all_implemented(self):
        document = self.expect_json(self.admin.get('/openapi.json'), 200)
        documented = set()
        for path, operations in (document.get('paths') or {}).items():
            for method in operations:
                if method.upper() in METHOD_ORDER:
                    documented.add((method.upper(), _normalise(path)))
        expected = _endpoint_set(CONTRACT_ENDPOINTS)
        missing = sorted(expected - documented)
        self.assertFalse(missing, f'契约声明但未实现的端点：{missing}')
        extra = sorted({item for item in documented if item[1].startswith('/api')}
                       - expected)
        if extra:  # informational: any extra endpoint must be added to the contract first
            print(f'[contract] 契约之外的已实现端点（需回到契约确认）：{extra}')

    def test_03_openapi_metadata_and_docs(self):
        document = self.expect_json(self.new_client().get('/openapi.json'), 200)
        self.assertIn(document.get('openapi', ''), ('3.0.2', '3.1.0'),
                      f'OpenAPI 版本异常：{document.get("openapi")!r}')
        info = document.get('info') or {}
        self.assertIn('智能电商', info.get('title') or '')
        self.assertEqual(info.get('version'), '0.2.0-prototype')
        for path in ('/docs', '/redoc'):
            response = self.new_client().get(path)
            self.assertEqual(response.status, 200, f'{path} 应可访问：{response.status}')
            self.assertIn('text/html', response.header('Content-Type'))

    # -- error contract ----------------------------------------------------
    def test_04_validation_errors_are_400_with_stable_code(self):
        cases = [
            ('GET', '/api/dashboard', {'start': '2011-12-09', 'end': '2011-11-10'}),
            ('GET', '/api/dashboard', {'start': '2007-01-01', 'end': '2011-12-09'}),
            ('GET', '/api/transactions', {'page': -1}),
            ('GET', '/api/transactions', {'page_size': 500}),
        ]
        for method, path, params in cases:
            with self.subTest(path=path, params=params):
                response = self.admin.get(path, params)
                self.assertNotEqual(response.status, 422,
                                    f'参数错误必须映射为 400，不能是 FastAPI 默认 422：{path}')
                payload = response.json()
                self.assertIn(payload.get('code'),
                              ('bad_request', 'not_found'),
                              f'错误码不在契约枚举内：{jdump(payload)}')
                if response.status == 400:
                    self.assertEqual(payload.get('code'), 'bad_request')

        bad_bodies = [
            ('/api/auth/login', {}),
            ('/api/forecast', {'model': 'not-a-model'}),
            ('/api/backtest', {'start': '2011-01-01', 'end': '2011-06-30'}),
        ]
        for path, body in bad_bodies:
            with self.subTest(path=path):
                response = (self.new_client().post(path, body) if path.startswith('/api/auth')
                            else self.admin.post(path, body))
                self.assertEqual(response.status, 400, f'{path} 应返回 400：{response.text()[:300]}')
                payload = response.json()
                self.assertEqual(payload.get('code'), 'bad_request')
                self.assertTrue(payload.get('error'))

    def test_05_http_status_codes_are_documented(self):
        anonymous = self.new_client()
        self.expect_error(anonymous.get('/api/dashboard'), 401, 'unauthorized')
        self.expect_error(anonymous.post('/api/imports/batches/original/activate', {}),
                          401, 'unauthorized')
        # Unknown paths hide behind auth when not logged in (401 instead of route 404),
        # authenticated clients get the documented 404.
        unknown = anonymous.get('/api/definitely-not-a-route')
        self.assertIn(unknown.status, (401, 404),
                      f'未登录未知路径应为 401 或 404：{unknown.status}')
        self.expect_error(self.admin.get('/api/definitely-not-a-route'), 404, 'not_found')
        self.expect_error(self.admin.get('/api/orders/999999999'), 404, 'not_found')
        operator = self.new_client('operator')
        self.expect_error(operator.get('/api/users'), 403, 'forbidden')
        for response in (anonymous.get('/api/dashboard'),
                         operator.get('/api/users'),
                         self.admin.get('/api/definitely-not-a-route')):
            payload = response.json()
            self.assertEqual(set(payload) & {'error', 'code'}, {'error', 'code'})
            self.assertIsInstance(payload['error'], str)
            self.assertIsInstance(payload['code'], str)

    # -- same-origin guard -------------------------------------------------
    def test_06_cross_origin_writes_are_403(self):
        evil = 'https://evil.example'
        login = self.new_client().post('/api/auth/login', {
            'username': ADMIN_USER, 'password': ADMIN_PASSWORD}, headers={'Origin': evil})
        self.expect_error(login, 403, 'forbidden')

        write = self.admin.post('/api/forecast', {}, headers={'Origin': evil})
        self.expect_error(write, 403, 'forbidden')
        write = self.admin.post('/api/inventory/scenarios', {'name': '跨源场景'},
                                headers={'Origin': evil})
        self.expect_error(write, 403, 'forbidden')

        # Same-origin writes keep working (read endpoints ignore Origin by design).
        same_origin = f'http://127.0.0.1:{self.server.port}'
        ok = self.new_client().post('/api/auth/login', {
            'username': ADMIN_USER, 'password': ADMIN_PASSWORD}, headers={'Origin': same_origin})
        self.expect_json(ok, 200)
        reads = self.admin.get('/api/dashboard', {'start': '2011-11-10', 'end': '2011-11-10'},
                               headers={'Origin': evil})
        self.assertEqual(reads.status, 200, 'GET 请求不做同源限制')

    # -- static page -------------------------------------------------------
    def test_07_frontend_is_served_by_fastapi(self):
        root = self.new_client().get('/')
        self.assertEqual(root.status, 200, root.text()[:300])
        self.assertIn('text/html', root.header('Content-Type'))
        self.assertIn('<', root.text())
        for asset in ('/static/app.js', '/static/style.css'):
            response = self.new_client().get(asset)
            self.assertEqual(response.status, 200, f'{asset} 应可访问：{response.status}')
        for forbidden in ('cdn.', 'https://unpkg', 'https://cdn'):
            self.assertNotIn(forbidden, root.text().lower(),
                             '页面必须离线可用，不得引用外部 CDN')


if __name__ == '__main__':
    unittest.main()
