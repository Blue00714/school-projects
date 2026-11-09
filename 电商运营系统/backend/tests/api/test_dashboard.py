"""M2 dashboard contract tests with the historical acceptance numbers.

The 2011-11-10..2011-12-09 full-shop window (inclusive, original policy) is the fixed
baseline: 851,403 pieces / 1,694,068.71 GBP / 2,913 orders / 86,861 positive sale lines.
The trend intentionally keeps calendar days: the four Sundays without recorded sales
appear as observed=false with quantity 0, they are not "closed days".
"""
from __future__ import annotations

import csv
import io
import unittest
from datetime import date

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, ORIGINAL_RANGE, ORIGINAL_TOTALS,
                        ApiClient, ApiTestCase, jdump)

POLICY_VERSION = 'cross-sheet-multiset-v1-sales-v2-invoice-order'
COVERAGE = {'start': '2009-12-01', 'end': '2011-12-09'}
EXPECTED_DAYS = 30
EXPECTED_OBSERVED_DAYS = 26  # four Sundays carry no recorded sales


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


def _csv_rows(text: str):
    return list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))


class DashboardTests(ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    def test_01_original_window_totals_match_historical_baseline(self):
        payload = self.dashboard(self.admin, **ORIGINAL_RANGE)
        totals = payload['totals']
        self.assertEqual(totals['quantity'], ORIGINAL_TOTALS['quantity'])
        self.assertAlmostEqual(totals['revenue'], ORIGINAL_TOTALS['revenue'], places=2)
        self.assertEqual(totals['orders'], ORIGINAL_TOTALS['orders'])
        self.assertEqual(totals['lines'], ORIGINAL_TOTALS['lines'])
        self.assertEqual(totals['products'], 2930)
        self.assertGreater(totals['amount_rows'], 0)
        self.assertLessEqual(totals['amount_rows'], totals['lines'])
        self.assertAlmostEqual(totals['average_order_value'],
                               ORIGINAL_TOTALS['revenue'] / ORIGINAL_TOTALS['orders'],
                               places=2)

        self.assertEqual(payload.get('filters', {}).get('start'), ORIGINAL_RANGE['start'])
        self.assertEqual(payload.get('filters', {}).get('end'), ORIGINAL_RANGE['end'])
        self.assertEqual(payload.get('coverage'), COVERAGE)
        self.assertEqual(payload.get('policy_version'), POLICY_VERSION)
        self.assertEqual(payload.get('batch', {}).get('id'), 'original')
        self.assertEqual(payload.get('batch', {}).get('kind'), 'original')

        for field in ('previous', 'changes', 'trend', 'ranking', 'countries',
                      'weekdays', 'quality', 'warnings'):
            self.assertIn(field, payload, f'看板缺少既有字段 {field}')

    def test_02_trend_keeps_calendar_days_and_sums_to_totals(self):
        payload = self.dashboard(self.admin, **ORIGINAL_RANGE)
        trend = payload['trend']
        self.assertEqual(len(trend), EXPECTED_DAYS)
        self.assertEqual(trend[0]['date'], ORIGINAL_RANGE['start'])
        self.assertEqual(trend[-1]['date'], ORIGINAL_RANGE['end'])
        self.assertEqual(sum(1 for row in trend if row.get('observed')), EXPECTED_OBSERVED_DAYS)
        missing = [row for row in trend if not row.get('observed')]
        self.assertEqual(len(missing), EXPECTED_DAYS - EXPECTED_OBSERVED_DAYS)
        for row in missing:
            self.assertEqual(row.get('quantity'), 0, '覆盖内无记录日补0，不是空值')
            self.assertEqual(row.get('revenue'), 0)
            self.assertEqual(row.get('orders'), 0)
        self.assertEqual(sum(row['quantity'] for row in trend), ORIGINAL_TOTALS['quantity'])
        self.assertAlmostEqual(sum(row['revenue'] for row in trend),
                               ORIGINAL_TOTALS['revenue'], places=2)

    def test_03_previous_period_and_changes(self):
        payload = self.dashboard(self.admin, **ORIGINAL_RANGE)
        previous = payload['previous']
        self.assertEqual(previous['filters']['start'], '2011-10-11')
        self.assertEqual(previous['filters']['end'], '2011-11-09')
        self.assertIs(previous['complete'], True)
        self.assertEqual(previous['totals']['quantity'], 655756)
        self.assertAlmostEqual(previous['totals']['revenue'], 1229356.50, places=2)
        self.assertEqual(previous['totals']['orders'], 2278)

        changes = payload['changes']
        for key in ('quantity', 'revenue', 'orders'):
            expected = (payload['totals'][key] / previous['totals'][key]) - 1
            self.assertIsNotNone(changes[key])
            self.assertAlmostEqual(changes[key], expected, places=9,
                                   msg=f'{key} 变化率应为同长度前期对比')

    def test_04_ranking_countries_weekdays_quality(self):
        payload = self.dashboard(self.admin, **ORIGINAL_RANGE)
        totals = payload['totals']

        ranking = payload['ranking']
        self.assertLessEqual(len(ranking), 20)
        # Ranking is a top-20 list, so it cannot add up to the whole shop total.
        self.assertLessEqual(sum(row['quantity'] for row in ranking), totals['quantity'])
        quantities = [row['quantity'] for row in ranking]
        self.assertEqual(quantities, sorted(quantities, reverse=True),
                         '商品排名应按销量倒序')
        for row in ranking:
            self.assertIsInstance(row['stock_code'], str)
            self.assertIn('name', row)
            self.assertLessEqual(row['quantity'], totals['quantity'])

        countries = payload['countries']
        self.assertLessEqual(len(countries), 10)
        self.assertLessEqual(sum(row['quantity'] for row in countries), totals['quantity'])
        top = {row['country']: row['quantity'] for row in countries}
        self.assertEqual(top.get('United Kingdom'), 744481)

        weekdays = payload['weekdays']
        # ``weekdays`` groups real sale rows only, so a weekday without any recorded
        # sales (a historical closed day) is not invented here; zero-record calendar
        # days appear in ``trend`` with quantity 0 instead.
        self.assertGreaterEqual(len(weekdays), 1)
        self.assertLessEqual(len(weekdays), 7)
        weekday_values = [row['weekday'] for row in weekdays]
        self.assertEqual(len(weekday_values), len(set(weekday_values)),
                         'weekday 分组应唯一')
        self.assertTrue(all(0 <= value <= 6 for value in weekday_values),
                        f'weekday 取值应在 0..6：{weekday_values}')
        observed_weekdays = {
            (date.fromisoformat(row['date']).weekday() + 1) % 7  # Python Mon=0 -> SQLite Sun=0
            for row in payload['trend'] if row.get('observed')}
        self.assertEqual(set(weekday_values), observed_weekdays,
                         'weekdays 必须与 trend 中实际观测到的星期几一致')
        self.assertEqual(sum(row['quantity'] for row in weekdays), totals['quantity'])

        quality = payload['quality']
        for key in ('retained_rows', 'cancelled_rows', 'nonpositive_rows',
                    'duplicate_candidates', 'invalid_price_rows'):
            self.assertIn(key, quality, f'质量统计缺少 {key}')
        self.assertIsInstance(payload['warnings'], list)

    def test_05_default_range_is_last_30_days(self):
        payload = self.dashboard(self.admin)
        self.assertEqual(payload['filters']['end'], COVERAGE['end'])
        self.assertEqual(payload['filters']['start'], '2011-11-10')
        self.assertEqual(payload['totals']['quantity'], ORIGINAL_TOTALS['quantity'])

    def test_06_trend_csv_export(self):
        response = self.admin.get('/api/export/trend', ORIGINAL_RANGE)
        self.assertEqual(response.status, 200, response.text()[:300])
        self.assertTrue(response.body.startswith(b'\xef\xbb\xbf'), 'CSV 必须是 UTF-8 BOM')
        self.assertIn('text/csv', response.header('Content-Type'))
        self.assertIn('charset=utf-8', response.header('Content-Type').lower())
        disposition = response.header('Content-Disposition')
        self.assertIn('attachment', disposition)
        self.assertIn('sales-trend.csv', disposition)

        rows = _csv_rows(response.text())
        self.assertEqual(rows[0], ['date', 'quantity', 'revenue', 'orders', 'observed'])
        self.assertEqual(len(rows), 1 + EXPECTED_DAYS)
        self.assertEqual(rows[1][0], ORIGINAL_RANGE['start'])
        self.assertEqual(rows[-1][0], ORIGINAL_RANGE['end'])
        quantity = sum(int(row[1]) for row in rows[1:])
        revenue = sum(float(row[2]) for row in rows[1:])
        self.assertEqual(quantity, ORIGINAL_TOTALS['quantity'])
        self.assertAlmostEqual(revenue, ORIGINAL_TOTALS['revenue'], places=2)
        observed_values = {row[4] for row in rows[1:]}
        self.assertLessEqual(len(observed_values), 2, f'observed 列应为布尔值：{observed_values}')

    def test_07_orders_csv_export_matches_order_list(self):
        response = self.admin.get('/api/export/orders', ORIGINAL_RANGE)
        self.assertEqual(response.status, 200, response.text()[:300])
        self.assertIn('text/csv', response.header('Content-Type'))
        rows = _csv_rows(response.text())
        self.assertEqual(rows[0], ['order_key', 'date', 'first_time', 'customer_id',
                                   'country', 'lines', 'quantity', 'revenue'])
        self.assertEqual(len(rows), 1 + ORIGINAL_TOTALS['orders'])
        keys = [row[0] for row in rows[1:]]
        self.assertTrue(all(key.isdigit() and len(key) >= 5 for key in keys),
                        f'订单号必须是保留文本的长编号：{keys[:5]}')
        self.assertNotIn('e', ''.join(keys).lower(), '长编号不得变成科学计数法')
        self.assertEqual(sum(int(row[5]) for row in rows[1:]), ORIGINAL_TOTALS['lines'])
        self.assertEqual(sum(int(row[6]) for row in rows[1:]), ORIGINAL_TOTALS['quantity'])
        self.assertAlmostEqual(sum(float(row[7]) for row in rows[1:]),
                               ORIGINAL_TOTALS['revenue'], places=2)

    def test_08_transactions_csv_export(self):
        response = self.admin.get('/api/export/transactions', ORIGINAL_RANGE)
        self.assertEqual(response.status, 200, response.text()[:300])
        self.assertTrue(response.body.startswith(b'\xef\xbb\xbf'))
        rows = _csv_rows(response.text())
        self.assertEqual(rows[0], ['invoice', 'stock_code', 'description', 'quantity',
                                   'unit_price', 'revenue', 'invoice_time', 'customer_id',
                                   'country', 'sheet', 'source_row'])
        self.assertEqual(len(rows), 1 + ORIGINAL_TOTALS['lines'])
        header = rows[0]
        invoice_col = header.index('invoice')
        quantity_col = header.index('quantity')
        for row in rows[1:6]:
            self.assertTrue(row[invoice_col].isdigit(), f'发票号应为字符串：{row[invoice_col]!r}')
        self.assertEqual(sum(int(row[quantity_col]) for row in rows[1:]),
                         ORIGINAL_TOTALS['quantity'])

    def test_09_invalid_filters_are_400(self):
        self.expect_error(self.admin.get('/api/dashboard', {
            'start': '2011-12-09', 'end': '2011-11-10'}), 400, 'bad_request')
        self.expect_error(self.admin.get('/api/dashboard', {
            'start': '2007-01-01', 'end': '2011-12-09'}), 400, 'bad_request')
        self.expect_error(self.admin.get('/api/dashboard', {
            'start': 'not-a-date', 'end': '2011-12-09'}), 400, 'bad_request')
        self.expect_error(self.admin.get('/api/transactions', {
            'start': '2011-11-10', 'end': '2011-12-09', 'page': -1}), 400, 'bad_request')
        self.expect_error(self.admin.get('/api/transactions', {
            'start': '2011-11-10', 'end': '2011-12-09', 'page_size': 201}), 400, 'bad_request')

    def test_10_empty_range_is_empty_not_fabricated(self):
        payload = self.dashboard(self.admin, start='2008-01-01', end='2008-01-02')
        totals = payload['totals']
        self.assertEqual(totals['quantity'], 0)
        self.assertEqual(totals['orders'], 0)
        self.assertEqual(totals['revenue'], 0)
        trend = payload['trend']
        self.assertEqual(len(trend), 2)
        for row in trend:
            self.assertIsNone(row['quantity'], '覆盖外未知日期应显示为空而不是0')
            self.assertIsNone(row['revenue'])
            self.assertIs(row['observed'], False)
        self.assertIsNone(payload['changes']['quantity'])
        self.assertTrue(payload['warnings'], '空范围必须给出提示')

    def test_11_products_and_transactions_follow_the_batch(self):
        products = self.expect_json(self.admin.get('/api/products', {'q': '22423'}), 200)
        self.assertIsInstance(products, list)
        self.assertTrue(any(row['stock_code'] == '22423' for row in products),
                        f'按编号检索应命中 22423：{jdump(products)[:400]}')
        self.assertLessEqual(len(products), 50)

        transactions = self.expect_json(self.admin.get('/api/transactions', {
            **ORIGINAL_RANGE, 'page': 1, 'page_size': 5}), 200)
        self.assertEqual(transactions['page'], 1)
        self.assertEqual(transactions['page_size'], 5)
        self.assertEqual(transactions['total'], ORIGINAL_TOTALS['lines'])
        self.assertEqual(len(transactions['rows']), 5)
        self.assertEqual(transactions.get('batch', {}).get('id'), 'original')
        for row in transactions['rows']:
            # Legacy transaction row contract: no customer_id column here, but the CSV
            # export (test_08) does carry it.
            for field in ('invoice', 'stock_code', 'description', 'quantity', 'unit_price',
                          'revenue', 'invoice_time', 'country', 'sheet', 'source_row'):
                self.assertIn(field, row)


if __name__ == '__main__':
    unittest.main()
