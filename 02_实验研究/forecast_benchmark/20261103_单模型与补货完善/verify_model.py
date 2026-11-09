import json
import sys
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]/'电商运营系统'
sys.path.insert(0,str(ROOT/'backend'))
from app.services.forecasting import forecast, ACTIVE_MODEL, DEPLOYMENT_VERSION
with sqlite3.connect((ROOT/'runtime/retail.sqlite3').as_uri()+'?mode=ro',uri=True) as cx:
    f=pd.read_sql_query('select date,sum(quantity) quantity from transactions where sale=1 group by date order by date',cx)
s=f.set_index(pd.to_datetime(f.date)).quantity
s=s.reindex(pd.date_range(s.index.min(),s.index.max()),fill_value=0)
expected=pd.read_csv(HERE/'validation.csv')
for o in ['2011-08-15','2011-09-01','2011-10-07']:
    result=forecast(s,o)
    actual=np.array([r['prediction'] for r in result['rows']])
    prediction=expected[(expected.model=='robust3')&(expected.origin==o)].prediction
    np.testing.assert_allclose(actual,prediction,atol=1e-8)
    changed=s.copy(); changed.loc[changed.index>pd.Timestamp(o)]=999999
    np.testing.assert_allclose(actual,[r['prediction'] for r in forecast(changed,o)['rows']],atol=1e-8)
    assert result['actual_method']==ACTIVE_MODEL and result['model_version']==DEPLOYMENT_VERSION
latest=forecast(s,'2011-12-09',model_dir=HERE/'verified_model')
meta=json.loads((HERE/'verified_model/metadata.json').read_text(encoding='utf-8'))
assert meta['version']==DEPLOYMENT_VERSION and meta['robust_rule']['mad_multiplier']==3
assert all(r['actual'] is None for r in latest['rows'])
print('PASS: 3 offline/production equivalence checks; 3 future-leakage checks; version, native model metadata, 7 unknown actuals')
