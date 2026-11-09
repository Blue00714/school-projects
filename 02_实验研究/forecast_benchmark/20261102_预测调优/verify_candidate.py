import json
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]/'电商运营系统'
sys.path.insert(0,str(ROOT/'backend'))
from app.services.forecasting import forecast, backtest
with sqlite3.connect((ROOT/'runtime/retail.sqlite3').as_uri()+'?mode=ro',uri=True) as cx:
    f=pd.read_sql_query('select date,sum(quantity) quantity from transactions where sale=1 group by date order by date',cx)
s=f.set_index(pd.to_datetime(f.date)).quantity
s=s.reindex(pd.date_range(s.index.min(),s.index.max()),fill_value=0)
expected=pd.read_csv(HERE/'validation_predictions.csv')
for o in ['2011-08-15','2011-09-01','2011-10-07']:
    r=forecast(s,o,model='lgbm_rule')
    e=expected[(expected.model=='tree_rule')&(expected.origin==o)].prediction
    np.testing.assert_allclose([x['prediction'] for x in r['rows']],e,atol=1e-8)
    changed=s.copy()
    changed.loc[changed.index>pd.Timestamp(o)]=999999.
    r2=forecast(changed,o,model='lgbm_rule')
    np.testing.assert_allclose([x['prediction'] for x in r['rows']],[x['prediction'] for x in r2['rows']])
    assert r['model_version']=='daily-v2-lgbm400-rule'
r=backtest(s,'2011-11-05','2011-12-02',model='blend_rule')
assert r['complete_origins']==28 and r['scored_count']==196
(HERE/'default_window.json').write_text(json.dumps({k:v for k,v in r.items() if k!='origin_results'},ensure_ascii=False,indent=2),encoding='utf-8')
print('3 candidate-reconstruction checks, 3 future-leakage checks, version and 28-complete-origin checks passed')
