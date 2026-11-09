"""M4 simulated inventory contract tests.

The fixed acceptance sample: initial 100, +20 in, -30 out -> balance 90; repeating the
same out movement is idempotent (still 90); an out of 91 is rejected; with lead-time
demand 80, safety stock 30, balance 90 and effective in-transit 10 the replenishment
candidate is 10.  Every inventory response must carry ``simulated: true``.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from apiharness import ADMIN_PASSWORD, ADMIN_USER, ApiClient, ApiTestCase, jdump

STOCK = 'QA-ITEM-1'
SCENARIO_NAME = 'QA模拟场景'


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


class InventoryTests(ApiTestCase):
    scenario_id: str = ''
    in_transit_id: str = ''

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    # -- helpers -----------------------------------------------------------
    def items(self):
        payload = self.expect_json(
            self.admin.get(f'/api/inventory/scenarios/{self.scenario_id}/items'), 200)
        return payload

    def item(self, stock_code: str = STOCK) -> dict:
        payload = self.items()
        for row in payload.get('items') or []:
            if row.get('stock_code') == stock_code:
                return row
        self.fail(f'商品 {stock_code} 不在场景清单中：{jdump(payload)[:600]}')

    def replenishment(self) -> dict:
        return self.expect_json(
            self.admin.get(f'/api/inventory/scenarios/{self.scenario_id}/replenishment'), 200)

    def movements(self) -> dict:
        return self.expect_json(
            self.admin.get(f'/api/inventory/scenarios/{self.scenario_id}/movements'), 200)

    # -- tests -------------------------------------------------------------
    def test_01_scenario_create_and_list(self):
        before = self.expect_json(self.admin.get('/api/inventory/scenarios'), 200)
        self.assertIs(before.get('simulated'), True)
        self.assertIsInstance(before.get('scenarios'), list)

        created = self.expect_json(self.admin.post('/api/inventory/scenarios', {
            'name': SCENARIO_NAME, 'note': '自动化测试场景（模拟数据）',
            'stock_codes': [STOCK, 'QA-ITEM-2']}), 201)
        scenario = created.get('scenario') or {}
        self.assertTrue(scenario.get('id'))
        self.assertEqual(scenario.get('name'), SCENARIO_NAME)
        self.assertEqual(scenario.get('item_count'), 2)
        type(self).scenario_id = scenario['id']

        listing = self.expect_json(self.admin.get('/api/inventory/scenarios'), 200)
        self.assertIs(listing.get('simulated'), True)
        ids = [row.get('id') for row in listing.get('scenarios') or []]
        self.assertIn(self.scenario_id, ids)

        payload = self.items()
        self.assertIs(payload.get('simulated'), True)
        self.assertEqual((payload.get('scenario') or {}).get('id'), self.scenario_id)
        rows = {row['stock_code']: row for row in payload['items']}
        self.assertEqual(set(rows), {STOCK, 'QA-ITEM-2'})
        for row in rows.values():
            self.assertEqual(row.get('balance'), 0)
            self.assertIn('replenishment_candidate', row)
            self.assertIs(bool(row.get('replenishment_candidate')), False,
                          '全 0 参数时不应有补货候选')

    def test_02_item_parameters_and_replenishment_formula(self):
        updated = self.expect_json(self.admin.put(
            f'/api/inventory/scenarios/{self.scenario_id}/items/{STOCK}', {
                'name': '测试商品', 'initial_qty': 100, 'lead_time_days': 8,
                'safety_stock': 30, 'daily_demand': 10}), 200)
        item = updated.get('item') or {}
        if updated.get('simulated') is not None:
            self.assertIs(updated.get('simulated'), True)
        self.assertEqual(item.get('initial_qty'), 100)
        self.assertEqual(item.get('lead_time_days'), 8)
        self.assertEqual(item.get('safety_stock'), 30)
        self.assertEqual(item.get('daily_demand'), 10)
        self.assertEqual(item.get('balance'), 100)
        self.assertAlmostEqual(item.get('in_transit_effective') or 0, 0)
        # candidate = max(0, 80 + 30 - 100 - 0) = 10 > 0 -> flagged as a candidate
        self.assertAlmostEqual(item.get('replenishment_candidate') or 0, 10)

        # ``lead_time_demand`` is part of the replenishment response (per contract).
        replenishment = self.replenishment()
        self.assertIs(replenishment.get('simulated'), True)
        self.assertIn('max(0', replenishment.get('formula') or '',
                      f'补货公式必须可核对：{replenishment.get("formula")!r}')
        rows = {row['stock_code']: row for row in replenishment.get('items') or []}
        self.assertIn(STOCK, rows)
        row = rows[STOCK]
        self.assertEqual(row.get('current_qty'), 100)
        self.assertEqual(row.get('lead_time_demand'), 80)
        self.assertAlmostEqual(row.get('effective_in_transit') or 0, 0)
        self.assertEqual(row.get('safety_stock'), 30)
        # candidate = max(0, 80 + 30 - 100 - 0) = 10
        self.assertAlmostEqual(row.get('candidate'), 10)

    def test_03_movements_balance_and_idempotency(self):
        inbound = self.expect_json(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/movements', {
                'stock_code': STOCK, 'kind': 'in', 'qty': 20,
                'idempotency_key': 'qa-in-1', 'note': '测试入库'}), 201)
        self.assertEqual(inbound['item']['balance'], 120)
        self.assertEqual(inbound['movement']['kind'], 'in')
        self.assertEqual(inbound['movement']['qty'], 20)
        self.assertEqual(inbound['movement']['balance_after'], 120)

        outbound = self.expect_json(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/movements', {
                'stock_code': STOCK, 'kind': 'out', 'qty': 30,
                'idempotency_key': 'qa-out-1', 'note': '测试出库'}), 201)
        self.assertEqual(outbound['item']['balance'], 90)
        self.assertEqual(outbound['movement']['balance_after'], 90)

        duplicate = self.expect_json(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/movements', {
                'stock_code': STOCK, 'kind': 'out', 'qty': 30,
                'idempotency_key': 'qa-out-1'}), 200)
        self.assertIs(duplicate.get('duplicate'), True,
                      f'同一幂等键必须返回 duplicate，不重复记账：{jdump(duplicate)}')
        self.assertEqual(duplicate['item']['balance'], 90, '重复提交后余额仍应为 90')
        self.assertEqual(duplicate['movement']['id'], outbound['movement']['id'])

        insufficient = self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/movements', {
                'stock_code': STOCK, 'kind': 'out', 'qty': 91,
                'idempotency_key': 'qa-out-2'})
        self.expect_error(insufficient, 400, 'bad_request')
        self.assertEqual(self.item()['balance'], 90, '被拒绝的出库不得改变余额')

        ledger = self.movements()
        self.assertIs(ledger.get('simulated'), True)
        self.assertEqual(len(ledger.get('movements') or []), 2,
                         'in + out 两条流水；重复提交不新增流水')
        kinds = [row['kind'] for row in ledger['movements']]
        self.assertEqual(sorted(kinds), ['in', 'out'])

    def test_04_in_transit_confirmation_and_receiving(self):
        expected_date = (date.today() + timedelta(days=3)).isoformat()
        confirmed = self.expect_json(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/replenishment/confirm', {
                'stock_code': STOCK, 'qty': 10, 'expected_date': expected_date,
                'idempotency_key': 'qa-confirm-1'}), 201)
        in_transit = confirmed.get('in_transit') or {}
        self.assertTrue(in_transit.get('id'))
        self.assertEqual(in_transit.get('qty'), 10)
        self.assertEqual(in_transit.get('expected_date'), expected_date)
        self.assertIn(in_transit.get('status'), ('in_transit', 'pending'))
        type(self).in_transit_id = in_transit['id']
        self.assertEqual(confirmed['item']['balance'], 90,
                         '确认补货只登记在途，不直接加库存')
        self.assertAlmostEqual(confirmed['item']['in_transit_effective'] or 0, 10)
        self.assertAlmostEqual(confirmed['item'].get('in_transit_total') or 0, 10)

        replenishment = self.replenishment()
        row = {r['stock_code']: r for r in replenishment['items']}[STOCK]
        # candidate = max(0, 80 + 30 - 90 - 10) = 10
        self.assertAlmostEqual(row.get('effective_in_transit'), 10)
        self.assertAlmostEqual(row.get('candidate'), 10)

        pending = self.expect_json(
            self.admin.get(f'/api/inventory/scenarios/{self.scenario_id}/in-transit'), 200)
        self.assertIs(pending.get('simulated'), True)
        entries = {row['id']: row for row in pending.get('items') or []}
        self.assertIn(self.in_transit_id, entries)
        self.assertEqual(entries[self.in_transit_id].get('status'), 'in_transit')

        received = self.expect_json(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/in-transit/{self.in_transit_id}/receive',
            {}), 200)
        self.assertEqual(received['item']['balance'], 100, '到货后库存 90 + 10 = 100')
        self.assertEqual(received['in_transit']['status'], 'received')
        self.assertAlmostEqual(received['item'].get('in_transit_effective') or 0, 0)
        self.assertEqual(received['movement']['kind'], 'in')
        self.assertEqual(received['movement']['qty'], 10)

        ledger = self.movements()
        self.assertEqual(len(ledger['movements']), 3, '到货入库需新增一条入库流水')
        self.assertEqual(ledger['movements'][0]['kind'], 'in')

        replenishment = self.replenishment()
        row = {r['stock_code']: r for r in replenishment['items']}[STOCK]
        # candidate = max(0, 80 + 30 - 100 - 0) = 10
        self.assertAlmostEqual(row.get('candidate'), 10)
        self.assertAlmostEqual(row.get('effective_in_transit'), 0)

    def test_05_validation_and_missing_resources(self):
        self.expect_error(self.admin.put(
            f'/api/inventory/scenarios/{self.scenario_id}/items/{STOCK}',
            {'lead_time_days': 366}), 400, 'bad_request')
        self.expect_error(self.admin.put(
            f'/api/inventory/scenarios/{self.scenario_id}/items/{STOCK}',
            {'initial_qty': -1}), 400, 'bad_request')
        for body in ({'stock_code': STOCK, 'kind': 'in', 'qty': 0},
                     {'stock_code': STOCK, 'kind': 'transfer', 'qty': 1},
                     {'kind': 'in', 'qty': 5}):
            with self.subTest(body=body):
                self.expect_error(self.admin.post(
                    f'/api/inventory/scenarios/{self.scenario_id}/movements', body),
                    400, 'bad_request')
        self.expect_error(self.admin.get('/api/inventory/scenarios/no-such-scenario/items'),
                          404, 'not_found')
        self.expect_error(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/in-transit/no-such-id/receive', {}),
            404, 'not_found')
        self.expect_error(self.admin.post(
            f'/api/inventory/scenarios/{self.scenario_id}/replenishment/confirm', {
                'stock_code': STOCK, 'qty': 0,
                'expected_date': date.today().isoformat()}), 400, 'bad_request')


    def test_06_demo_data_is_complete_idempotent_and_operable(self):
        first = self.expect_json(self.admin.post('/api/inventory/demo', {}), 200)
        sid = first['scenario']['id']
        before = self.expect_json(self.admin.get(f'/api/inventory/scenarios/{sid}/items'), 200)
        self.assertEqual(len(before['items']), 12)
        self.assertTrue(all(row['name'] and row['daily_demand'] > 0 for row in before['items']))
        self.assertTrue(any(row['replenishment_candidate'] > 0 for row in before['items']))
        self.assertTrue(any(row['in_transit_effective'] > 0 and row['replenishment_candidate'] == 0 for row in before['items']))
        ledger = self.expect_json(self.admin.get(f'/api/inventory/scenarios/{sid}/movements'), 200)
        self.assertEqual(len(ledger['movements']), 27)
        repeat = self.expect_json(self.admin.post('/api/inventory/demo', {}), 200)
        self.assertTrue(repeat['duplicate'])
        again = self.expect_json(self.admin.get(f'/api/inventory/scenarios/{sid}/movements'), 200)
        self.assertEqual(len(again['movements']), 27)
        row = next(row for row in before['items'] if row['replenishment_candidate'] > 0)
        qty = row['replenishment_candidate']
        created = self.expect_json(self.admin.post(f'/api/inventory/scenarios/{sid}/replenishment/confirm', {
            'stock_code': row['stock_code'], 'qty': qty,
            'expected_date': (date.today()+timedelta(days=1)).isoformat()}), 201)
        self.assertEqual(created['item']['balance'], row['balance'])
        self.assertEqual(created['item']['replenishment_candidate'], 0)
        received = self.expect_json(self.admin.post(f'/api/inventory/scenarios/{sid}/in-transit/{created["in_transit"]["id"]}/receive', {}), 200)
        self.assertEqual(received['item']['balance'], row['balance'] + qty)
        restored = self.expect_json(self.admin.post('/api/inventory/demo', {}), 200)
        current = self.expect_json(self.admin.get(f'/api/inventory/scenarios/{restored["scenario"]["id"]}/items'), 200)
        self.assertEqual(next(x for x in current['items'] if x['stock_code'] == row['stock_code'])['balance'], row['balance']+qty)


if __name__ == '__main__':
    unittest.main()
