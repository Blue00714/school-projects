"""M2 order query contract tests against the original data batch.

Checks that the order list total equals the dashboard's de-duplicated order count, that
order detail lines add up to the order header, that long numeric order keys stay strings
and that filters/paging keep the same scope as the dashboard.
"""
from __future__ import annotations

import re
import unittest

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, ORIGINAL_RANGE, ORIGINAL_TOTALS,
                        ApiClient, ApiTestCase, jdump)

ORDER_KEY_RE = re.compile(r'^\d{5,}$')
PAGE_SIZE = 200


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


class OrdersTests(ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    def range_params(self, **extra):
        params = dict(ORIGINAL_RANGE)
        params.update(extra)
        return params

    def test_01_orders_total_matches_dashboard(self):
        dashboard = self.dashboard(self.admin, **ORIGINAL_RANGE)
        expected_orders = ORIGINAL_TOTALS['orders']
        self.assertEqual(dashboard['totals']['orders'], expected_orders,
                         f'看板订单数基线不符：{jdump(dashboard["totals"])}')

        first = self.expect_json(self.admin.get('/api/orders', self.range_params(
            page=1, page_size=PAGE_SIZE)), 200)
        self.assertEqual(first.get('total'), expected_orders,
                         f'订单列表 total 应等于看板订单数：{jdump({k: first.get(k) for k in ("total", "page", "page_size")})}')
        self.assertEqual(first.get('page'), 1)
        self.assertEqual(first.get('page_size'), PAGE_SIZE)
        self.assertEqual(first.get('filters', {}).get('start'), ORIGINAL_RANGE['start'])
        self.assertEqual(first.get('filters', {}).get('end'), ORIGINAL_RANGE['end'])
        self.assertEqual(first.get('batch', {}).get('id'), 'original')

        quantity = revenue = 0
        seen = 0
        pages = (expected_orders + PAGE_SIZE - 1) // PAGE_SIZE
        for page in range(1, pages + 1):
            payload = self.expect_json(self.admin.get('/api/orders', self.range_params(
                page=page, page_size=PAGE_SIZE)), 200)
            rows = payload.get('rows') or []
            self.assertEqual(payload.get('total'), expected_orders)
            self.assertLessEqual(len(rows), PAGE_SIZE)
            seen += len(rows)
            for row in rows:
                quantity += row['quantity']
                revenue += row['revenue'] or 0
        self.assertEqual(seen, expected_orders, '分页合计行数应等于订单总数')
        self.assertEqual(quantity, ORIGINAL_TOTALS['quantity'],
                         '订单数量合计与看板不一致')
        self.assertAlmostEqual(revenue, ORIGINAL_TOTALS['revenue'], places=2,
                               msg='订单销售额合计与看板不一致')

    def test_02_rows_sorted_and_long_order_keys_are_strings(self):
        payload = self.expect_json(self.admin.get('/api/orders', self.range_params(
            page=1, page_size=PAGE_SIZE)), 200)
        rows = payload.get('rows') or []
        self.assertTrue(rows)
        dates = [row['date'] for row in rows]
        self.assertEqual(dates, sorted(dates, reverse=True), '订单列表应按日期倒序')
        for row in rows:
            key = row.get('order_key')
            self.assertIsInstance(key, str, f'order_key 必须是字符串：{jdump(row)}')
            self.assertRegex(key, ORDER_KEY_RE,
                             f'订单号格式异常（可能被转成数字/科学计数）：{key!r}')
            self.assertIsInstance(row.get('customer_id'), str, '客户编号必须为字符串')
            self.assertIsInstance(row.get('lines'), int)
            self.assertIsInstance(row.get('product_codes'), list)
            self.assertLessEqual(len(row['product_codes']), 5)

        # Ordering must also break ties by order_key deterministically (either direction).
        same_day = [row for row in rows if row['date'] == rows[0]['date']]
        keys = [row['order_key'] for row in same_day]
        self.assertIn(keys, (sorted(keys), sorted(keys, reverse=True)),
                      '同一天内订单应按 order_key 稳定排序')

    def test_03_order_detail_lines_add_up_to_header(self):
        collected = []
        for page in (1, 2, 8):
            payload = self.expect_json(self.admin.get('/api/orders', self.range_params(
                page=page, page_size=PAGE_SIZE)), 200)
            rows = payload.get('rows') or []
            if rows:
                collected.append((page, rows[0]))
                if len(rows) > 5:
                    collected.append((page, rows[5]))
        self.assertTrue(collected, '未取到订单样本')

        for page, row in collected:
            order_key = row['order_key']
            with self.subTest(page=page, order_key=order_key):
                detail = self.expect_json(
                    self.admin.get(f'/api/orders/{order_key}', ORIGINAL_RANGE), 200)
                header = detail.get('order') or {}
                lines = detail.get('rows') or []
                self.assertEqual(header.get('order_key'), order_key)
                self.assertEqual(header.get('date'), row['date'])
                self.assertEqual(header.get('lines'), row['lines'])
                self.assertEqual(header.get('quantity'), row['quantity'])
                self.assertAlmostEqual(header.get('revenue'), row['revenue'], places=2)
                self.assertEqual(len(lines), header['lines'],
                                 f'明细行数应等于订单头 lines：{jdump(header)}')
                self.assertEqual(sum(item['quantity'] for item in lines),
                                 header['quantity'], '明细数量合计应等于订单头数量')
                self.assertAlmostEqual(sum(item.get('revenue') or 0 for item in lines),
                                       header['revenue'], places=2,
                                       msg='明细金额合计应等于订单头金额')
                for item in lines:
                    self.assertEqual(item.get('invoice'), order_key)
                    self.assertIsInstance(item.get('stock_code'), str)
                    self.assertIsInstance(item.get('description'), str)
                    self.assertRegex(item.get('invoice_time'), r'^\d{4}-\d{2}-\d{2} ')

    def test_04_dashboard_and_orders_follow_the_same_filter(self):
        for params in ({'country': 'United Kingdom'}, {'product': '22423'}):
            with self.subTest(params=params):
                dashboard = self.dashboard(self.admin, **self.range_params(**params))
                orders = self.expect_json(self.admin.get('/api/orders', self.range_params(
                    page=1, page_size=PAGE_SIZE, **params)), 200)
                self.assertEqual(orders.get('total'), dashboard['totals']['orders'],
                                 f'筛选 {params} 后订单总数与看板不一致')
                first_page = orders.get('rows') or []
                self.assertLessEqual(len(first_page), dashboard['totals']['orders'])
                if orders['total'] <= PAGE_SIZE:
                    self.assertEqual(sum(row['quantity'] for row in first_page),
                                     dashboard['totals']['quantity'],
                                     f'筛选 {params} 后数量合计与看板不一致')

    def test_05_q_searches_order_keys(self):
        sample = self.expect_json(self.admin.get('/api/orders', self.range_params(
            page=1, page_size=1)), 200)['rows'][0]
        target = sample['order_key']
        payload = self.expect_json(self.admin.get('/api/orders', self.range_params(
            q=target, page=1, page_size=50)), 200)
        self.assertGreaterEqual(payload.get('total') or 0, 1, '按订单号搜索应命中')
        for row in payload.get('rows') or []:
            self.assertIn(target, row['order_key'])

    def test_06_unknown_order_is_404(self):
        self.expect_error(self.admin.get('/api/orders/999999999'), 404, 'not_found')
        self.expect_error(self.admin.get('/api/orders/not-an-order'), 404, 'not_found')


if __name__ == '__main__':
    unittest.main()
