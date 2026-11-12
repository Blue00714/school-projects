from contextlib import redirect_stdout
from datetime import datetime,timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import sys

SYSTEM_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SYSTEM_ROOT / 'backend'))
sys.path.insert(0, str(SYSTEM_ROOT.parent / '03_测试与验证/历史实现/旧版看板'))
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd
from openpyxl import Workbook

from app.services.data import prepare, Store, DataError, COLS, file_hash
from app.services.analytics import Analytics
from app.services.forecasting import forecast, backtest, score, holt_winters, design
from server import csv_bytes,Application

TEMP_ROOT=SYSTEM_ROOT/'runtime/tmp/service_tests'
TEMP_ROOT.mkdir(parents=True,exist_ok=True)

class WorkspaceScratch:
    """Retain tiny test fixtures for review; avoid restricted Windows mkdtemp ACLs."""
    def __init__(self):
        self.name=str(TEMP_ROOT/uuid4().hex)
        Path(self.name).mkdir()
    def __enter__(self):return self.name
    def __exit__(self,*_):pass
    def cleanup(self):pass


def series(n=160):
    idx=pd.date_range('2011-01-01',periods=n)
    vals=np.array([0 if x.dayofweek==5 else 100+x.dayofweek*7+(i%9) for i,x in enumerate(idx)],dtype=float)
    return pd.Series(vals,index=idx)


class ForecastTests(unittest.TestCase):
    def test_future_changes_never_change_prediction_or_rule(self):
        a=series();b=a.copy();origin=a.index[119]
        b.loc[b.index>origin]=np.arange(len(b)-120)*10000+500000
        for method in ['seasonal_week4','holt_winters','lgbm','blend_rule']:
            with self.subTest(method=method):
                x,y=forecast(a,str(origin.date()),model=method),forecast(b,str(origin.date()),model=method)
                np.testing.assert_allclose([r['prediction'] for r in x['rows']],[r['prediction'] for r in y['rows']],rtol=0,atol=1e-9)
                self.assertEqual(x['closed_weekdays'],y['closed_weekdays'])
                self.assertEqual(x['actual_method'],method)
                self.assertEqual(x['history_days'],120)

    def test_fixed_training_cutoff_does_not_expand_into_test_period(self):
        idx = pd.date_range('2011-01-01', periods=300)
        data = pd.Series([100 + i % 31 for i in range(300)], index=idx, dtype=float)
        early = forecast(data, '2011-09-01', train_end='2011-05-15')
        later = forecast(data, '2011-10-01', train_end='2011-05-15')
        rolling = forecast(data, '2011-10-01')
        self.assertEqual(early['training_end'], '2011-05-15')
        self.assertEqual(later['training_end'], '2011-05-15')
        self.assertEqual(early['training_rows'], later['training_rows'])
        self.assertGreater(rolling['training_rows'], later['training_rows'])
        self.assertEqual(later['actual_method'], 'robust_blend')

    def test_predict_beyond_data_always_seven_rows(self):
        result=forecast(series(),model='blend_rule')
        self.assertEqual(result['actual_method'],'blend_rule')
        self.assertEqual(len(result['rows']),7)
        self.assertEqual(result['actual_days'],0)
        self.assertIsNone(result['metrics']['WAPE'])
        self.assertTrue(all(r['actual'] is None for r in result['rows']))
        self.assertTrue(all(np.isfinite(r['prediction']) for r in result['rows']))
        json.dumps(result,allow_nan=False)

    def test_overlap_accounting_and_full_windows(self):
        s=series();result=backtest(s,str(s.index[-56].date()),str(s.index[-1].date()),model='seasonal_week4')
        self.assertEqual((result['origins_attempted'],result['origins_with_actual'],result['complete_origins']),(56,55,49))
        self.assertEqual((result['forecast_count'],result['scored_count']),(392,364))
        self.assertEqual(result['complete_window_metrics']['n'],343)
        self.assertEqual(sum(x['n'] for x in result['by_horizon'].values()),364)
        self.assertEqual(len({x['date'] for x in result['worst_cases']}),len(result['worst_cases']))
        self.assertGreaterEqual(result['worst_cases'][0]['abs_error'],result['worst_cases'][-1]['abs_error'])

    def test_scores_zero_empty_partial(self):
        m=score([0,0],[1,3]);self.assertIsNone(m['WAPE']);self.assertEqual(m['MAE'],2)
        self.assertEqual(score([None,None],[1,2])['n'],0)
        m=score([10,None,30],[11,100,27]);self.assertEqual(m['n'],2);self.assertAlmostEqual(m['WAPE'],.1)
        self.assertEqual(m['MEDIAN_AE'], 2)
        self.assertAlmostEqual(m['P90_AE'], 2.8)
        with self.assertRaises(ValueError):score([1],[2,3])
        with self.assertRaises(ValueError):score([1],[np.inf])

    def test_backtest_reuses_fixed_fit_without_changing_predictions(self):
        data = series(200)
        cutoff = '2011-05-01'
        start, end = '2011-06-10', '2011-06-12'
        expected = [forecast(data, day.date().isoformat(), train_end=cutoff)['rows']
                    for day in pd.date_range(start, end)]
        result = backtest(data, start, end, train_end=cutoff)
        self.assertEqual([origin['rows'] for origin in result['origin_results']], expected)
        self.assertEqual(result['scored_unique_days'], 9)
        self.assertEqual(result['weekly_total_metrics']['n'], 3)

    def test_invalid_history_and_dates(self):
        for s in [series(27),pd.Series([-1.],index=[pd.Timestamp('2011-01-01')]),pd.Series([1,2],index=[pd.Timestamp('2011-01-01')]*2)]:
            with self.subTest(size=len(s)),self.assertRaises(ValueError):forecast(s)
        for origin in ['2010-12-01','2012-01-01','not-a-date','2011-03-01 10:00']:
            with self.subTest(origin=origin),self.assertRaises(ValueError):forecast(series(),origin)
        with self.assertRaises(ValueError):backtest(series(),'2011-02-01','2011-04-01')

    def test_fallback_is_named(self):
        result=forecast(series(60),model='blend_rule')
        self.assertEqual(result['actual_method'],'seasonal_week4')
        self.assertTrue(any('回退' in v for v in result['warnings']))
        with patch.dict('sys.modules',{'lightgbm':None}):
            result=forecast(series(),model='lgbm')
        self.assertEqual(result['actual_method'],'seasonal_week4')
        self.assertTrue(any('ImportError' in v or 'ModuleNotFoundError' in v for v in result['warnings']))

    def test_candidate_zero_weekday_and_features(self):
        r=forecast(series(),model='blend_rule')
        self.assertEqual(r['closed_weekdays'],[5])
        for row in r['rows']:
            if pd.Timestamp(row['date']).dayofweek==5:self.assertEqual(row['prediction'],0)
        s=series();d=design(s)
        origin=s.index[-1]
        x=d[(d['origin']==origin)&(d['h']==7)].iloc[0]
        self.assertEqual(x['lag_1'],s.iloc[-1])
        self.assertAlmostEqual(x['mean_7'],s.iloc[-7:].mean())
        self.assertTrue(pd.isna(x['y']))

    def test_native_model_saved(self):
        with WorkspaceScratch() as folder:
            r=forecast(series(),model='lgbm',model_dir=Path(folder))
            self.assertEqual(r['actual_method'],'lgbm')
            self.assertTrue((Path(folder)/'lightgbm.txt').is_file())
            meta=json.loads((Path(folder)/'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['origin'],r['origin'])
            self.assertEqual(len(meta['features']),32)


class DataAndAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=WorkspaceScratch()
        self.path=Path(self.tmp.name)
        self.source=self.path/'sample.xlsx';self.db=self.path/'data.sqlite3'
        a=['100','A','Alpha',2,datetime(2011,1,1,10),3,1,'United Kingdom']
        b=['101','B','Beta',5,datetime(2011,1,2,10),4,2,'France']
        rows=[a,a.copy(),b,['C102','A','Alpha',-1,datetime(2011,1,3),3,1,'United Kingdom'],['103','A','Alpha',1,datetime(2011,1,4),None,1,'United Kingdom'],['104','Z','=SUM(1,2)',3,datetime(2011,1,5),0,1,'United Kingdom']]
        wb=Workbook();ws=wb.active;ws.title='First';ws.append(COLS)
        for row in rows:ws.append(row)
        ws=wb.create_sheet('Second');ws.append(COLS)
        for row in [a,a.copy(),b,['105','C','Gamma',7,datetime(2011,1,6),2,3,'France']]:ws.append(row)
        wb.save(self.source);wb.close()
        self.before=file_hash(self.source)
        with redirect_stdout(StringIO()):self.meta=prepare(self.source,self.db)
        self.store=Store(self.db);self.analytics=Analytics(self.store)
        self.params={'start':'2011-01-01','end':'2011-01-06'}

    def tearDown(self):self.tmp.cleanup()

    def test_cross_sheet_multiplicity_and_source_immutability(self):
        self.assertEqual(self.meta['raw_rows'],10)
        self.assertEqual(self.meta['cross_sheet_copies_excluded'],3)
        self.assertEqual(self.meta['within_sheet_duplicate_candidates_retained'],1)
        self.assertEqual(self.meta['positive_sale_rows'],6)
        self.assertEqual(file_hash(self.source),self.before)
        self.assertEqual(prepare(self.source,self.db)['prepared_at'],self.meta['prepared_at'])

    def test_totals_match_details_and_rankings(self):
        d=self.analytics.dashboard(self.params);t=d['totals']
        self.assertEqual(t['quantity'],20)
        self.assertEqual(t['revenue'],46)
        self.assertEqual(t['orders'],5)
        self.assertEqual(t['amount_rows'],5)
        rows=self.analytics.transactions(dict(self.params,page_size=200))['rows']
        self.assertEqual(sum(r['quantity'] for r in rows),t['quantity'])
        self.assertEqual(sum(r['revenue'] or 0 for r in rows),t['revenue'])
        self.assertEqual(sum(r['quantity'] for r in d['ranking']),t['quantity'])
        self.assertEqual(d['quality']['cancelled_rows'],1)
        self.assertEqual(d['quality']['invalid_price_rows'],1)

    def test_filters_empty_missing_dates_and_page(self):
        d=self.analytics.dashboard(dict(self.params,country='France'))
        self.assertEqual(d['totals']['quantity'],12)
        d=self.analytics.dashboard(dict(self.params,product='A'))
        self.assertEqual(d['totals']['quantity'],5)
        self.assertFalse(d['trend'][1]['observed']);self.assertEqual(d['trend'][1]['quantity'],0)
        d=self.analytics.dashboard({'start':'2010-12-31','end':'2011-01-01'})
        self.assertIsNone(d['trend'][0]['quantity'])
        self.assertIsNone(d['changes']['quantity'])
        d=self.analytics.dashboard(dict(self.params,product="A' OR 1=1--"))
        self.assertEqual(d['totals']['lines'],0)
        self.assertTrue(d['warnings'])
        self.assertEqual(self.analytics.transactions(dict(self.params,page=2,page_size=2))['rows'][0]['invoice'],'103')
        with self.assertRaises(DataError):self.analytics.transactions(dict(self.params,page=-1))
        with self.assertRaises(DataError):self.analytics.dashboard({'start':'2011-01-07','end':'2011-01-01'})

    def test_series_retains_calendar_and_sum(self):
        s=self.store.series();self.assertEqual(len(s),6)
        self.assertEqual(s.sum(),20);self.assertEqual(s.loc['2011-01-03'],0)

    def test_same_invoice_time_variation_is_one_order(self):
        from openpyxl import load_workbook
        wb=load_workbook(self.source)
        wb['First'].append(['100','A','Alpha',3,datetime(2011,1,1,10,1),3,1,'United Kingdom'])
        wb.save(self.source);wb.close()
        with redirect_stdout(StringIO()):prepare(self.source,self.db,rebuild=True)
        result=self.analytics.dashboard(self.params)
        self.assertEqual(result['totals']['orders'],5)
        self.assertEqual(result['totals']['quantity'],23)

    def test_order_identity_conflict_requires_review(self):
        from openpyxl import load_workbook
        wb=load_workbook(self.source)
        wb['First'].append(['100','A','Alpha',3,datetime(2011,1,2,10),3,99,'France'])
        wb.save(self.source);wb.close()
        with redirect_stdout(StringIO()),self.assertRaises(DataError):prepare(self.source,self.db,rebuild=True)

    def test_http_queries_errors_and_static_scope(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import urlopen,Request
        from urllib.error import HTTPError
        from server import make_handler
        app=Application(self.db)
        httpd=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app))
        thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(httpd.server_port)
        try:
            with urlopen(base+'/api/dashboard?start=2011-01-01&end=2011-01-06') as response:
                self.assertEqual(json.load(response)['totals']['quantity'],20)
            for path,status in [('/../services/data.py',404),('/api/transactions?page=-1',400),('/api/jobs/not-a-job',404)]:
                with self.subTest(path=path),self.assertRaises(HTTPError) as caught:urlopen(base+path)
                self.assertEqual(caught.exception.code,status)
            req=Request(base+'/api/forecast',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://unrelated.example'},method='POST')
            with self.assertRaises(HTTPError) as caught:urlopen(req)
            self.assertEqual(caught.exception.code,403)
            with urlopen(base+'/api/export/trend?start=2011-01-01&end=2011-01-06') as response:
                self.assertEqual(len(response.read().decode('utf-8-sig').splitlines()),7)
        finally:
            httpd.shutdown();httpd.server_close();thread.join()

    def test_csv_formula_neutralized(self):
        text=csv_bytes([{'name':'=SUM(1,2)','n':-3}],['name','n']).decode('utf-8-sig')
        self.assertIn("'=SUM",text);self.assertIn('-3',text)


if __name__=='__main__':unittest.main()
