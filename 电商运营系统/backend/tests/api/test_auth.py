"""M1 authentication/authorisation contract tests.

Covers: health metadata, login success/failure, session cookie flags, logout, 401 for
unauthenticated requests, 403 for operator access to admin-only endpoints, admin user
management (create/duplicate/password length/self-protection/last admin) and password
change (which revokes the user's other sessions).
"""
from __future__ import annotations

import unittest

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, OPERATOR_PASSWORD, OPERATOR_USER,
                        ApiTestCase)


class AuthTests(ApiTestCase):
    """One isolated server; methods are ordered with numeric prefixes."""

    def test_01_health_is_public_and_describes_service(self):
        client = self.new_client()
        payload = self.expect_json(client.get('/api/health'), 200)
        self.assertIs(payload.get('ok'), True)
        self.assertEqual(payload.get('service'), 'ecommerce-ops-fastapi')
        self.assertIn('version', payload)
        self.assertIn('data_ready', payload)
        self.assertIsInstance(payload.get('ai_configured'), bool)
        batch = payload.get('batch')
        self.assertIsInstance(batch, dict, f'健康检查应返回当前批次：{payload}')
        self.assertEqual(batch.get('id'), 'original')
        self.assertEqual(batch.get('kind'), 'original')

    def test_02_login_success_me_and_cookie_flags(self):
        client = self.new_client()
        response = client.post('/api/auth/login',
                               {'username': ADMIN_USER, 'password': ADMIN_PASSWORD})
        payload = self.expect_json(response, 200)
        user = payload.get('user')
        self.assertIsInstance(user, dict)
        self.assertEqual(user.get('username'), ADMIN_USER)
        self.assertEqual(user.get('role'), 'admin')
        self.assertIs(user.get('active'), True)
        for field in ('id', 'display_name', 'must_change_password'):
            self.assertIn(field, user, f'user 对象缺少字段 {field}')
        self.assertNotIn('password', user)
        self.assertNotIn('password_hash', user)

        cookie = response.header('Set-Cookie')
        self.assertIn('ece_session=', cookie, f'登录必须下发 ece_session Cookie：{cookie!r}')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('Path=/', cookie)
        self.assertIn('samesite=lax', cookie.lower())
        self.assertIn('max-age=', cookie.lower())

        me = self.expect_json(client.get('/api/auth/me'), 200)
        self.assertEqual(me.get('user', {}).get('username'), ADMIN_USER)

    def test_03_login_failures_are_401_or_400(self):
        client = self.new_client()
        wrong = client.post('/api/auth/login',
                            {'username': ADMIN_USER, 'password': 'definitely-wrong-pw'})
        payload = self.expect_error(wrong, 401, 'unauthorized')
        self.assertIn('账号或密码错误', payload['error'])
        self.assertNotIn(ADMIN_PASSWORD, wrong.text())

        unknown = client.post('/api/auth/login',
                              {'username': 'no-such-user', 'password': 'whatever-123'})
        self.expect_error(unknown, 401, 'unauthorized')

        missing = client.post('/api/auth/login', {'username': ADMIN_USER})
        self.expect_error(missing, 400, 'bad_request')

        empty = client.post('/api/auth/login', {})
        self.expect_error(empty, 400, 'bad_request')

    def test_04_unauthenticated_requests_are_401(self):
        client = self.new_client()
        for path in ('/api/auth/me', '/api/dashboard', '/api/orders',
                     '/api/products', '/api/transactions', '/api/ai/status',
                     '/api/inventory/scenarios', '/api/imports/batches'):
            with self.subTest(path=path):
                self.expect_error(client.get(path), 401, 'unauthorized')
        self.expect_error(client.post('/api/forecast', {}), 401, 'unauthorized')

    def test_05_operator_cannot_use_admin_endpoints(self):
        operator = self.operator_client()
        self.expect_error(operator.get('/api/users'), 403, 'forbidden')
        self.expect_error(operator.post('/api/users',
                                        {'username': 'x-user', 'password': 'x-user-Pass1',
                                         'role': 'operator'}), 403, 'forbidden')
        self.expect_error(operator.patch('/api/users/1', {'display_name': '越权改名'}),
                          403, 'forbidden')

    def test_06_admin_user_management(self):
        admin = self.admin_client()
        listing = self.expect_json(admin.get('/api/users'), 200)
        users = listing.get('users')
        self.assertIsInstance(users, list)
        names = {user.get('username') for user in users}
        self.assertIn(ADMIN_USER, names)
        self.assertIn(OPERATOR_USER, names)
        for user in users:
            self.assertIn(user.get('role'), ('admin', 'operator'))
            self.assertNotIn('password_hash', user)

        created = self.expect_json(admin.post('/api/users', {
            'username': 'qa-created', 'password': 'qa-created-Pass1',
            'display_name': 'QA 测试账号', 'role': 'operator'}), 201)
        user = created.get('user') or {}
        self.assertEqual(user.get('username'), 'qa-created')
        self.assertEqual(user.get('role'), 'operator')
        created_id = user.get('id')
        self.assertIsInstance(created_id, int)

        self.expect_error(admin.post('/api/users', {
            'username': 'qa-created', 'password': 'qa-created-Pass2',
            'role': 'operator'}), 409, 'conflict')
        self.expect_error(admin.post('/api/users', {
            'username': 'qa-short-pw', 'password': 'short', 'role': 'operator'}),
            400, 'bad_request')
        self.expect_error(admin.post('/api/users', {
            'username': 'qa-bad-role', 'password': 'qa-bad-role-Pass1', 'role': 'root'}),
            400, 'bad_request')

        deactivated = self.expect_json(
            admin.patch(f'/api/users/{created_id}', {'active': False}), 200)
        self.assertIs(deactivated.get('user', {}).get('active'), False)
        self.expect_error(self.new_client().post('/api/auth/login', {
            'username': 'qa-created', 'password': 'qa-created-Pass1'}), 401, 'unauthorized')

        me = self.expect_json(admin.get('/api/auth/me'), 200)['user']
        self.expect_error(admin.patch(f"/api/users/{me['id']}", {'active': False}),
                          400, 'bad_request')
        self.expect_error(admin.patch(f"/api/users/{me['id']}", {'role': 'operator'}),
                          400, 'bad_request')
        self.expect_error(admin.patch('/api/users/999999', {'display_name': 'x'}),
                          404, 'not_found')

        # A second administrator is needed by the password-change test below.
        second = self.expect_json(admin.post('/api/users', {
            'username': 'qa-admin2', 'password': 'qa-admin2-Pass1',
            'display_name': 'QA 第二管理员', 'role': 'admin'}), 201)
        self.assertEqual(second.get('user', {}).get('role'), 'admin')

    def test_07_password_change_revokes_other_sessions(self):
        login_body = {'username': 'qa-admin2', 'password': 'qa-admin2-Pass1'}
        other = self.new_client()
        self.expect_json(other.post('/api/auth/login', login_body), 200)
        current = self.new_client()
        self.expect_json(current.post('/api/auth/login', login_body), 200)

        self.expect_error(current.post('/api/auth/password', {
            'old_password': 'wrong-old-pw', 'new_password': 'qa-admin2-Pass2'}),
            400, 'bad_request')
        self.expect_error(current.post('/api/auth/password', {
            'old_password': 'qa-admin2-Pass1', 'new_password': 'short'}),
            400, 'bad_request')
        self.expect_json(current.post('/api/auth/password', {
            'old_password': 'qa-admin2-Pass1', 'new_password': 'qa-admin2-Pass2'}), 200)

        # Other sessions of that user are revoked, the current session stays valid.
        self.expect_error(other.get('/api/auth/me'), 401, 'unauthorized')
        self.expect_json(current.get('/api/auth/me'), 200)
        fresh = self.new_client()
        self.expect_error(fresh.post('/api/auth/login', {
            'username': 'qa-admin2', 'password': 'qa-admin2-Pass1'}), 401, 'unauthorized')
        self.expect_json(fresh.post('/api/auth/login', {
            'username': 'qa-admin2', 'password': 'qa-admin2-Pass2'}), 200)

    def test_08_logout_invalidates_session(self):
        client = self.new_client('admin')
        self.expect_json(client.get('/api/auth/me'), 200)
        response = client.post('/api/auth/logout', {})
        payload = self.expect_json(response, 200)
        self.assertIs(payload.get('ok'), True)
        self.expect_error(client.get('/api/auth/me'), 401, 'unauthorized')
        self.expect_error(client.get('/api/dashboard'), 401, 'unauthorized')
        self.expect_json(client.post('/api/auth/login', {
            'username': ADMIN_USER, 'password': ADMIN_PASSWORD}), 200)


if __name__ == '__main__':
    unittest.main()
