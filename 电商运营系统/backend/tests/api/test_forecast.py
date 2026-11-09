"""M3 forecast / backtest job contract tests.

Checks the 7-day target (always 7 rows, future actuals are null), the CSV export
contract, a small backtest window, job/batch binding across snapshot switches, and the
"future values never change a historical prediction" leak guard reused from the
existing service tests.
"""
from __future__ import annotations

import csv
import io
import sys
import unittest
from datetime import date, timedelta

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, ORIGINAL_RANGE, SYSTEM_ROOT,
                        ApiClient, ApiTestCase, jdump)
from fixtures import LONG_HISTORY_DAYS, LONG_HISTORY_START, long_history_csv_bytes

if str(SYSTEM_ROOT / 'backend') not in sys.path:
    sys.path.insert(0, str(SYSTEM_ROOT / 'backend'))

EXPORT_COLUMNS = ['origin', 'date', 'h', 'prediction', 'baseline', 'actual', 'abs_error',
                  'model_version', 'data_policy_version', 'batch_id', 'actual_method']


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


def _csv_rows(text: str):
    return list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))


class ForecastTests(ApiTestCase):
    forecast_job_id: str = ''

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    # -- helpers -----------------------------------------------------------
    def create_forecast(self, client=None, body=None) -> dict:
        client = client or self.admin
        payload = self.expect_json(client.post('/api/forecast', body or {}), 202)
        self.assertIn(payload.get('status'), ('queued', 'running'))
        self.assertTrue(payload.get('job_id'))
        self.assertEqual(payload.get('batch_id'), 'original')
        return payload

    def create_backtest(self, body, expect=202) -> dict:
        payload = self.expect_json(self.admin.post('/api/backtest', body), expect)
        if expect == 202:
            self.assertTrue(payload.get('job_id'))
        return payload

    # -- tests -------------------------------------------------------------
    def test_01_forecast_job_returns_seven_null_actual_rows(self):
        created = self.create_forecast()
        job_id = created['job_id']
        type(self).forecast_job_id = job_id

        payload = self.wait_job(self.admin, job_id)
        job = payload.get('job') or {}
        self.assertEqual(job.get('id'), job_id)
        self.assertEqual(job.get('kind'), 'forecast')
        self.assertEqual(job.get('status'), 'succeeded', f'预测任务失败：{jdump(job)}')
        self.assertTrue(job.get('finished_at'))
        self.assertIsNotNone(job.get('elapsed_seconds'))
        self.assertIn('params', job)
        self.assertEqual((job.get('batch') or {}).get('id'), 'original')
        self.assertIn('result', job)
        result = job['result'] or {}

        self.assertEqual(result.get('origin'), '2011-12-09')
        self.assertEqual(result.get('horizon'), 7)
        self.assertEqual(result.get('batch_id'), 'original')
        rows = result.get('rows') or []
        self.assertEqual(len(rows), 7)
        self.assertEqual(result.get('actual_days'), 0)
        self.assertIsNone((result.get('metrics') or {}).get('WAPE'),
                          '未来无实际值时 WAPE 不可计算')

        from app.services.forecasting import ACTIVE_MODEL, DEPLOYMENT_VERSION
        self.assertEqual(result.get('actual_method'), ACTIVE_MODEL)
        self.assertEqual(result.get('model_version'), DEPLOYMENT_VERSION)
        self.assertEqual(result.get('requested_method'), ACTIVE_MODEL)

        rows = result['rows']
        origin = result['origin']
        first = date.fromisoformat(origin)
        expected_dates = [(first + timedelta(days=h)).isoformat() for h in range(1, 8)]
        self.assertEqual([row['date'] for row in rows], expected_dates)
        self.assertEqual([row['h'] for row in rows], list(range(1, 8)))
        for row in rows:
            self.assertIsNone(row.get('actual'), '未来目标不得带实际值')
            self.assertIsNone(row.get('abs_error'))
            self.assertIsInstance(row.get('prediction'), (int, float))
            self.assertIsInstance(row.get('baseline'), (int, float))
            self.assertGreaterEqual(row['prediction'], 0)

        # Succeeded results are persisted: a second read returns the same payload.
        again = self.expect_json(self.admin.get(f'/api/jobs/{job_id}'), 200)['job']
        self.assertEqual(again.get('status'), 'succeeded')
        self.assertEqual([r['prediction'] for r in again['result']['rows']],
                         [r['prediction'] for r in rows],
                         '重启/重复查询不得改变已保存的预测结果')
        saved = self.expect_json(self.admin.get('/api/forecast/latest'), 200)
        self.assertEqual(saved['jobs']['forecast']['id'], job_id)

    def test_02_forecast_csv_export(self):
        self.assertTrue(self.forecast_job_id, '依赖 test_01 的预测任务')
        job = self.expect_json(self.admin.get(f'/api/jobs/{self.forecast_job_id}'), 200)['job']
        response = self.admin.get(f'/api/jobs/{self.forecast_job_id}/export')
        self.assertEqual(response.status, 200, response.text()[:400])
        self.assertIn('text/csv', response.header('Content-Type'))
        self.assertIn('attachment', response.header('Content-Disposition'))
        self.assertTrue(response.body.startswith(b'\xef\xbb\xbf'), 'CSV 必须是 UTF-8 BOM')
        rows = _csv_rows(response.text())
        self.assertEqual(rows[0], EXPORT_COLUMNS)
        self.assertEqual(len(rows), 8, '7 天预测应为表头+7行')
        result = job['result']
        for index, row in enumerate(rows[1:]):
            expected = result['rows'][index]
            self.assertEqual(row[1], expected['date'])
            self.assertEqual(int(row[2]), expected['h'])
            # CSV rounds the numeric columns to 5 decimals.
            self.assertLess(abs(float(row[3]) - expected['prediction']), 1e-4)
            self.assertLess(abs(float(row[4]) - expected['baseline']), 1e-4)
            self.assertEqual(row[5], '', '未来目标 actual 必须为空')
            self.assertEqual(row[7], result['model_version'])
            self.assertTrue(row[8], 'data_policy_version 不能为空')
            self.assertEqual(row[9], 'original')
            self.assertEqual(row[10], result['actual_method'])

    def test_03_single_model_contract(self):
        from app.services.forecasting import ACTIVE_MODEL
        options = self.expect_json(self.admin.get('/api/options'), 200)
        self.assertEqual(list(options['methods']), [ACTIVE_MODEL])
        document = self.expect_json(self.admin.get('/openapi.json'), 200)
        self.assertNotIn('model', document['components']['schemas']['ForecastBody']['properties'])
        for model in ['seasonal_week4', 'lgbm', 'not-a-model']:
            self.expect_error(self.admin.post('/api/forecast', {'model': model}), 400, 'bad_request')
        bad_origin = self.admin.post('/api/forecast', {'origin': '1990-01-01'})
        if bad_origin.status == 400:
            self.expect_error(bad_origin, 400, 'bad_request')
        else:
            payload = self.expect_json(bad_origin, 202)
            job = self.wait_job(self.admin, payload['job_id'])['job']
            self.assertEqual(job.get('status'), 'failed',
                             '不可用的预测起点必须失败，不能包装成成功')

    def test_04_backtest_small_window(self):
        window = {'start': '2011-11-20', 'end': '2011-12-01'}
        created = self.create_backtest(window)
        payload = self.wait_job(self.admin, created['job_id'])
        job = payload.get('job') or {}
        self.assertEqual(job.get('kind'), 'backtest')
        self.assertEqual(job.get('status'), 'succeeded', f'回测任务失败：{jdump(job)[:800]}')
        result = job['result'] or {}
        self.assertEqual(result.get('start'), window['start'])
        self.assertEqual(result.get('end'), window['end'])
        self.assertEqual(result.get('batch_id'), 'original')
        self.assertEqual(result.get('origins_attempted'), 12)
        self.assertEqual(result.get('forecast_count'), 84)
        self.assertEqual(result.get('scored_count'), 84)
        self.assertIsNotNone(result.get('metrics'))
        self.assertIn('WAPE', (result.get('metrics') or {}))
        self.assertIn('MAE', (result.get('metrics') or {}))
        self.assertEqual(set((result.get('by_horizon') or {}).keys()),
                         {str(h) for h in range(1, 8)})
        self.assertTrue(result.get('warnings') is not None)
        origin_results = result.get('origin_results') or []
        self.assertEqual(len(origin_results), 12)
        for origin in origin_results:
            self.assertEqual(len(origin.get('rows') or []), 7)

    def test_05_job_binding_survives_batch_switch(self):
        # 1. import a 70-day synthetic snapshot (needed for a real forecast history)
        uploaded = self.admin.upload('/api/imports', 'qa_history.csv',
                                     long_history_csv_bytes(), 'text/csv')
        created_import = self.expect_json(uploaded, 202)
        import_job = self.wait_import_job(self.admin, created_import['job_id'])
        self.assertEqual(import_job['job']['status'], 'succeeded', jdump(import_job)[:800])
        upload_batch = import_job['batch']['id']

        # 2. forecast created while the original batch is active binds 'original'
        original_job = self.create_forecast()['job_id']

        # 3. switch to the uploaded snapshot and create a second forecast there
        self.expect_json(
            self.admin.post(f'/api/imports/batches/{upload_batch}/activate', {}), 200)
        upload_create = self.expect_json(
            self.admin.post('/api/forecast', {}), 202)
        self.assertEqual(upload_create.get('batch_id'), upload_batch)
        upload_job_id = upload_create['job_id']
        upload_job = self.wait_job(self.admin, upload_job_id)['job']
        self.assertEqual(upload_job['status'], 'succeeded', jdump(upload_job)[:800])
        upload_result = upload_job['result']
        self.assertEqual(upload_result['batch_id'], upload_batch)
        expected_origin = (date.fromisoformat(LONG_HISTORY_START)
                           + timedelta(days=LONG_HISTORY_DAYS - 1)).isoformat()
        self.assertEqual(upload_result['origin'], expected_origin)
        self.assertEqual(len(upload_result['rows']), 7)

        original_result = self.wait_job(self.admin, original_job)['job']['result']
        self.assertEqual(original_result['batch_id'], 'original')

        # 4. switching back must not change either stored result
        self.expect_json(self.admin.post('/api/imports/batches/original/activate', {}), 200)
        reread_upload = self.expect_json(
            self.admin.get(f'/api/jobs/{upload_job_id}'), 200)['job']
        self.assertEqual(reread_upload['status'], 'succeeded')
        self.assertEqual(reread_upload['result']['batch_id'], upload_batch)
        self.assertEqual([r['prediction'] for r in reread_upload['result']['rows']],
                         [r['prediction'] for r in upload_result['rows']],
                         '切换数据快照不得改变已完成任务的输入或结果')
        reread_original = self.expect_json(
            self.admin.get(f'/api/jobs/{original_job}'), 200)['job']
        self.assertEqual(reread_original['result']['batch_id'], 'original')

    def test_06_bad_requests_and_missing_jobs(self):
        self.expect_error(self.admin.post('/api/backtest', {
            'start': '2011-01-01', 'end': '2011-06-30'}), 400, 'bad_request')
        self.expect_error(self.admin.post('/api/backtest', {
            'start': '2011-12-09', 'end': '2011-11-10'}), 400, 'bad_request')
        self.expect_error(self.admin.get('/api/jobs/does-not-exist'), 404, 'not_found')
        self.expect_error(self.admin.get('/api/jobs/does-not-exist/export'), 404, 'not_found')


class FutureLeakTests(unittest.TestCase):
    """Direct-code guard: changing future actuals must not change a historical forecast.

    Reuses the existing service-level idea (``tests/test_services.py``): only data up to
    the forecast origin may be used by training, feature building or zero-sales
    candidate rules.
    """

    @staticmethod
    def _series(days: int = 160):
        import numpy as np
        import pandas as pd

        index = pd.date_range('2011-01-01', periods=days)
        values = np.array([0 if stamp.dayofweek == 5 else 100 + stamp.dayofweek * 7 + (pos % 9)
                           for pos, stamp in enumerate(index)], dtype=float)
        return pd.Series(values, index=index)

    def test_changing_the_future_does_not_change_predictions(self):
        import numpy as np

        from app.services.forecasting import METHODS, forecast

        series = self._series()
        origin = series.index[119]
        altered = series.copy()
        altered.loc[altered.index > origin] = np.arange(len(altered) - 120) * 10000 + 500000
        for method in METHODS:
            with self.subTest(method=method):
                base = forecast(series, str(origin.date()), model=method)
                changed = forecast(altered, str(origin.date()), model=method)
                self.assertEqual(base['actual_method'], changed['actual_method'])
                self.assertEqual(base['closed_weekdays'], changed['closed_weekdays'])
                self.assertEqual(base['origin'], str(origin.date()))
                self.assertEqual(base['history_days'], 120)
                np.testing.assert_allclose(
                    [row['prediction'] for row in base['rows']],
                    [row['prediction'] for row in changed['rows']],
                    rtol=0, atol=1e-9)
                # ``forecast`` may attach actuals when history beyond the origin exists
                # (used by backtest); only the predictions must be immune to the future.


if __name__ == '__main__':
    unittest.main()
