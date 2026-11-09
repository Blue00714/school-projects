"""M2 import / batch-snapshot contract tests.

Flow: upload a small clean CSV sample -> asynchronous job succeeds -> the same file is
recognised as a duplicate (no new batch) -> explicit activation switches the current
snapshot and the dashboard/orders follow it -> switching back to ``original`` restores
the historical acceptance numbers.  Bad input is rejected or fails with a readable
report; nothing may be silently wrapped as success.
"""
from __future__ import annotations

import csv
import io
import unittest

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, ORIGINAL_TOTALS, ApiClient,
                        ApiTestCase, jdump)
from fixtures import (SAMPLE_ORDER_KEYS, SAMPLE_TOTALS, missing_column_csv_bytes,
                      sample_csv_bytes, sample_xlsx_bytes)


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


class ImportTests(ApiTestCase):
    upload_batch_id: str = ''

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    # -- helpers -----------------------------------------------------------
    def batch_list(self):
        return self.expect_json(self.admin.get('/api/imports/batches'), 200)

    def active_id(self) -> str:
        return self.batch_list().get('active_batch_id')

    def upload_csv(self, content: bytes, filename: str = 'qa_sample.csv', label: str | None = None):
        fields = {'label': label} if label else None
        response = self.admin.upload('/api/imports', filename, content, 'text/csv', fields)
        return response

    # -- tests -------------------------------------------------------------
    def test_01_rejects_wrong_file_type(self):
        response = self.admin.upload('/api/imports', 'notes.txt', b'not a table',
                                     'text/plain')
        self.expect_error(response, 400, 'bad_request')
        missing = self.admin.request('POST', '/api/imports',
                                     body=b'--x--\r\n',
                                     headers={'Content-Type': 'multipart/form-data; boundary=x'})
        self.expect_error(missing, 400, 'bad_request')

    def test_02_missing_required_column_is_reported(self):
        response = self.upload_csv(missing_column_csv_bytes(), 'qa_missing_price.csv')
        if response.status == 400:
            self.expect_error(response, 400, 'bad_request')
            return
        payload = self.expect_json(response, 202)
        job_id = payload.get('job_id')
        self.assertTrue(job_id, f'202 必须返回 job_id：{payload}')
        job = self.wait_import_job(self.admin, job_id)
        self.assertEqual(job.get('job', {}).get('status'), 'failed',
                         f'缺列文件必须失败，不能包装成成功：{jdump(job)}')
        text = jdump(job)
        self.assertTrue((job.get('job') or {}).get('error'), '失败任务必须给出错误原因')
        self.assertTrue(any(word in text for word in ('Price', '缺', '列')),
                        f'失败原因应说明缺列：{text[:500]}')

    def test_03_upload_small_sample_succeeds_as_inactive_snapshot(self):
        before = self.active_id()
        self.assertEqual(before, 'original', '初始批次应为 original')

        response = self.upload_csv(sample_csv_bytes(), 'qa_sample.csv', 'QA 小样例批次')
        payload = self.expect_json(response, 202)
        self.assertIn(payload.get('status'), ('queued', 'running'))
        job_id = payload.get('job_id')
        self.assertTrue(job_id)

        job = self.wait_import_job(self.admin, job_id)
        header = job.get('job') or {}
        self.assertEqual(header.get('status'), 'succeeded',
                         f'小样例导入必须成功：{jdump(job)}')
        self.assertTrue(header.get('finished_at'))
        report = job.get('report') or {}
        self.assertEqual(report.get('raw_rows'), 4, f'manifest 原始行数不符：{jdump(report)}')
        self.assertEqual(report.get('positive_sale_rows'), 4)
        self.assertEqual(report.get('date_min'), '2011-02-01')
        self.assertEqual(report.get('date_max'), '2011-02-05')
        self.assertIsInstance(job.get('issues'), list)

        batch = job.get('batch') or {}
        self.assertIs(batch.get('is_active'), False, '导入成功后不得自动切换当前批次')
        batch_id = batch.get('id')
        self.assertTrue(batch_id)
        self.assertNotEqual(batch_id, 'original')
        type(self).upload_batch_id = batch_id

        listing = self.batch_list()
        self.assertEqual(listing.get('active_batch_id'), 'original')
        rows = {item.get('id'): item for item in listing.get('batches') or []}
        self.assertIn(batch_id, rows)
        self.assertEqual(rows[batch_id].get('kind'), 'upload')
        self.assertIs(rows[batch_id].get('is_active'), False)

        # The active snapshot is unchanged: the dashboard must still show original data.
        dashboard = self.dashboard(self.admin)
        self.assertEqual(dashboard.get('batch', {}).get('id'), 'original')
        self.assertEqual(dashboard['totals']['quantity'], ORIGINAL_TOTALS['quantity'])

    def test_04_duplicate_file_creates_no_new_batch(self):
        self.assertTrue(self.upload_batch_id, '依赖 test_03 上传的批次')
        count_before = len(self.batch_list().get('batches') or [])
        response = self.upload_csv(sample_csv_bytes(), 'qa_sample.csv', 'QA 重复导入')
        payload = self.expect_json(response, 202)
        job = self.wait_import_job(self.admin, payload['job_id'])
        self.assertEqual(job.get('job', {}).get('status'), 'duplicate',
                         f'同一文件重复导入应为 duplicate：{jdump(job)}')
        batch = job.get('batch') or {}
        self.assertEqual(batch.get('id'), self.upload_batch_id,
                         'duplicate 必须返回既有 batch_id，不新建数据')
        count_after = len(self.batch_list().get('batches') or [])
        self.assertEqual(count_after, count_before, '重复导入不得新增批次')

    def test_05_activate_switches_dashboard_and_orders(self):
        self.assertTrue(self.upload_batch_id, '依赖 test_03 上传的批次')
        response = self.admin.post(f'/api/imports/batches/{self.upload_batch_id}/activate', {})
        payload = self.expect_json(response, 200)
        self.assertEqual(payload.get('previous_batch_id'), 'original')
        self.assertEqual(payload.get('batch', {}).get('id'), self.upload_batch_id)
        self.assertIs(payload.get('batch', {}).get('is_active'), True)
        self.assertEqual(self.active_id(), self.upload_batch_id)

        dashboard = self.dashboard(self.admin)
        totals = dashboard['totals']
        self.assertEqual(dashboard.get('batch', {}).get('id'), self.upload_batch_id)
        self.assertEqual(dashboard.get('batch', {}).get('kind'), 'upload')
        self.assertEqual(totals['quantity'], SAMPLE_TOTALS['quantity'],
                         f'切换后看板应跟随新批次：{jdump(totals)}')
        self.assertAlmostEqual(totals['revenue'], SAMPLE_TOTALS['revenue'], places=2)
        self.assertEqual(totals['orders'], SAMPLE_TOTALS['orders'])
        self.assertEqual(totals['lines'], SAMPLE_TOTALS['lines'])
        self.assertEqual(totals['products'], SAMPLE_TOTALS['products'])
        self.assertEqual(dashboard['filters']['start'], '2011-02-01')
        self.assertEqual(dashboard['filters']['end'], '2011-02-05')

        # Explicit range on the uploaded batch behaves the same way.
        explicit = self.dashboard(self.admin, start='2011-02-01', end='2011-02-05')
        self.assertEqual(explicit['totals']['quantity'], SAMPLE_TOTALS['quantity'])
        self.assertEqual(explicit['totals']['orders'], SAMPLE_TOTALS['orders'])

        orders = self.expect_json(self.admin.get('/api/orders', {
            'start': '2011-02-01', 'end': '2011-02-05', 'page_size': 50}), 200)
        self.assertEqual(orders.get('total'), SAMPLE_TOTALS['orders'], jdump(orders)[:600])
        keys = [row.get('order_key') for row in orders.get('rows') or []]
        self.assertEqual(sorted(keys), sorted(SAMPLE_ORDER_KEYS),
                         f'长数字订单号必须按字符串保留：{keys}')
        for row in orders.get('rows') or []:
            self.assertIsInstance(row.get('order_key'), str)
            for field in ('date', 'first_time', 'last_time', 'customer_id', 'country',
                          'lines', 'quantity', 'revenue', 'product_codes'):
                self.assertIn(field, row, f'订单行缺少字段 {field}：{jdump(row)}')
        self.assertLessEqual(len((orders['rows'])[0].get('product_codes') or []), 5)

        # Order detail lines add up to the order header.
        for order_key in ('900000000001', '900000000002', '900000000003'):
            with self.subTest(order_key=order_key):
                detail = self.expect_json(
                    self.admin.get('/api/orders/' + order_key), 200)
                header = detail.get('order') or {}
                rows = detail.get('rows') or []
                self.assertEqual(header.get('order_key'), order_key)
                self.assertTrue(rows, '订单详情必须包含交易明细行')
                self.assertEqual(len(rows), header.get('lines'))
                self.assertEqual(sum(r['quantity'] for r in rows), header['quantity'])
                self.assertAlmostEqual(sum(r.get('revenue') or 0 for r in rows),
                                       header['revenue'], places=2)
                for row in rows:
                    self.assertEqual(row.get('invoice'), order_key)
                    for field in ('stock_code', 'description', 'unit_price',
                                  'invoice_time', 'sheet', 'source_row'):
                        self.assertIn(field, row, f'明细行缺少字段 {field}')

    def test_06_exports_on_uploaded_batch_keep_text_and_neutralise_formulas(self):
        self.assertEqual(self.active_id(), self.upload_batch_id)

        trend = self.admin.get('/api/export/trend', {'start': '2011-02-01', 'end': '2011-02-05'})
        self.assertEqual(trend.status, 200, trend.text()[:300])
        self.assertIn('text/csv', trend.header('Content-Type'))
        self.assertIn('attachment', trend.header('Content-Disposition'))
        trend_lines = trend.text().lstrip('\ufeff').splitlines()
        self.assertEqual(trend_lines[0].split(','), ['date', 'quantity', 'revenue', 'orders', 'observed'])
        self.assertEqual(len(trend_lines), 6, '2011-02-01..2011-02-05 应为表头+5行')

        orders = self.admin.get('/api/export/orders', {'start': '2011-02-01', 'end': '2011-02-05'})
        self.assertEqual(orders.status, 200, orders.text()[:300])
        rows = list(csv.reader(io.StringIO(orders.text().lstrip('\ufeff'))))
        self.assertEqual(rows[0], ['order_key', 'date', 'first_time', 'customer_id',
                                   'country', 'lines', 'quantity', 'revenue'])
        self.assertEqual(len(rows), 1 + SAMPLE_TOTALS['orders'])
        literals = {row[0] for row in rows[1:]}
        self.assertEqual(literals, set(SAMPLE_ORDER_KEYS))
        for literal in literals:
            self.assertNotIn('e', literal.lower(), f'长编号被转成科学计数法：{literal}')

        detail = self.admin.get('/api/export/transactions',
                                {'start': '2011-02-01', 'end': '2011-02-05'})
        self.assertEqual(detail.status, 200, detail.text()[:300])
        rows = list(csv.reader(io.StringIO(detail.text().lstrip('\ufeff'))))
        self.assertEqual(rows[0], ['invoice', 'stock_code', 'description', 'quantity',
                                   'unit_price', 'revenue', 'invoice_time', 'customer_id',
                                   'country', 'sheet', 'source_row'])
        self.assertEqual(len(rows), 1 + SAMPLE_TOTALS['lines'])
        description_col = rows[0].index('description')
        stock_col = rows[0].index('stock_code')
        descriptions = {row[description_col] for row in rows[1:]}
        self.assertIn("'=SUM(1,2)", descriptions,
                      f'CSV 公式注入必须被中和（前加单引号）：{descriptions}')
        self.assertIn('00999', {row[stock_col] for row in rows[1:]},
                      '商品编号前导零必须保留')

        products = self.expect_json(self.admin.get('/api/products', {'q': '00999'}), 200)
        self.assertIsInstance(products, list)
        matches = [row for row in products if row.get('stock_code') == '00999']
        self.assertTrue(matches, f'按前导零编号搜索应命中：{jdump(products)[:400]}')
        self.assertEqual(matches[0].get('quantity'), 7)

    def test_07_switch_back_to_original_restores_baseline(self):
        self.assertTrue(self.upload_batch_id)
        payload = self.expect_json(
            self.admin.post('/api/imports/batches/original/activate', {}), 200)
        self.assertEqual(payload.get('previous_batch_id'), self.upload_batch_id)
        self.assertEqual(payload.get('batch', {}).get('id'), 'original')
        self.assertEqual(self.active_id(), 'original')

        dashboard = self.dashboard(self.admin, start='2011-11-10', end='2011-12-09')
        totals = dashboard['totals']
        self.assertEqual(dashboard.get('batch', {}).get('id'), 'original')
        self.assertEqual(totals['quantity'], ORIGINAL_TOTALS['quantity'])
        self.assertAlmostEqual(totals['revenue'], ORIGINAL_TOTALS['revenue'], places=2)
        self.assertEqual(totals['orders'], ORIGINAL_TOTALS['orders'])


class ImportXlsxTests(ApiTestCase):
    """The same sample uploaded as .xlsx must go through one cleaning policy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    def test_upload_xlsx_small_sample(self):
        try:
            content = sample_xlsx_bytes()
        except ImportError as exc:  # pragma: no cover - openpyxl is a project dependency
            self.skipTest(f'openpyxl 不可用，跳过 xlsx 导入用例：{exc}')
        response = self.admin.upload('/api/imports', 'qa_sample.xlsx', content,
                                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        payload = self.expect_json(response, 202)
        job = self.wait_import_job(self.admin, payload['job_id'])
        self.assertEqual(job.get('job', {}).get('status'), 'succeeded', jdump(job)[:800])
        batch = job.get('batch') or {}
        self.assertIs(batch.get('is_active'), False)
        self.expect_json(self.admin.post(f"/api/imports/batches/{batch['id']}/activate", {}), 200)
        dashboard = self.dashboard(self.admin, start='2011-02-01', end='2011-02-05')
        self.assertEqual(dashboard['totals']['quantity'], SAMPLE_TOTALS['quantity'])
        self.assertEqual(dashboard['totals']['orders'], SAMPLE_TOTALS['orders'])
        self.assertEqual(dashboard.get('batch', {}).get('id'), batch['id'])


if __name__ == '__main__':
    unittest.main()
