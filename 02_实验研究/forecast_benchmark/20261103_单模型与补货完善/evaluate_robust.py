"""Validation-only selection of a single robust deployment model."""
import json
import sys
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]/'电商运营系统'
sys.path.insert(0,str(ROOT/'backend'))
from app.services.forecasting import forecast, holt_winters, score
with sqlite3.connect((ROOT/'runtime/retail.sqlite3').as_uri()+'?mode=ro',uri=True) as cx:
    f=pd.read_sql_query('select date,sum(quantity) quantity from transactions where sale=1 group by date order by date',cx)
s=f.set_index(pd.to_datetime(f.date)).quantity
s=s.reindex(pd.date_range(s.index.min(),s.index.max()),fill_value=0)
cached={r['origin']:r for r in map(json.loads,(HERE.parent/'20261102_预测调优/forecast_cache.jsonl').read_text(encoding='utf-8').splitlines())}

def variants(o):
    hist=s.loc[:o]
    key=str(o.date())
    old=cached[key] if key in cached else forecast(s,key,model='blend_rule')
    blend=np.array([r['prediction'] for r in old['rows']])
    hw=holt_winters(hist)
    tree=np.clip(2*blend-hw,0,None)
    future=pd.date_range(o+pd.Timedelta(days=1),periods=7)
    closed=old['closed_weekdays']
    result={'original':blend}
    for k in [3,4,6]:
        caps={}
        recent=hist.iloc[-182:]
        for dow,g in recent.groupby(recent.index.dayofweek):
            med=float(g.median()); mad=float((g-med).abs().median())
            caps[int(dow)]=max(1.5*med,med+k*mad)
        clean=hist.clip(upper=pd.Series([caps[d] for d in hist.index.dayofweek],index=hist.index))
        p=.5*tree+.5*holt_winters(clean)
        p=np.minimum(p,[caps[d] for d in future.dayofweek])
        p[np.isin(future.dayofweek,closed)]=0
        result[f'robust{k}']=p
    return result

def evaluate(start,end,names,label):
    rows=[]
    for o in pd.date_range(pd.Timestamp(start)-pd.Timedelta(days=1),pd.Timestamp(end)-pd.Timedelta(days=7)):
        for model,p in variants(o).items():
            if model not in names: continue
            for h,v in enumerate(p,1):
                d=o+pd.Timedelta(days=h)
                rows.append(dict(partition=label,origin=str(o.date()),date=str(d.date()),h=h,model=model,prediction=float(v),actual=float(s.loc[d])))
    df=pd.DataFrame(rows)
    return df,{n:score(g.actual,g.prediction) for n,g in df.groupby('model')}

v,vm=evaluate('2011-08-16','2011-10-14',['original','robust3','robust4','robust6'],'validation')
winner=min(vm,key=lambda x:vm[x]['WAPE'])
(HERE/'selection.json').write_text(json.dumps(dict(chosen=winner,validation=vm),indent=2),encoding='utf-8')
v.to_csv(HERE/'validation.csv',index=False)
t,tm=evaluate('2011-10-15','2011-12-09',list(dict.fromkeys(['original',winner])),'retrospective')
t.to_csv(HERE/'retrospective.csv',index=False)
end=variants(pd.Timestamp('2011-12-09'))
out=dict(chosen=winner,validation=vm,retrospective=tm,latest_forecast={n:p.tolist() for n,p in end.items()},latest_raw_sums={n:float(p.sum()) for n,p in end.items()})
tail=[]
for o in pd.date_range('2011-12-03','2011-12-09'):
    for n,p in variants(o).items():
        if n not in ['original',winner]: continue
        for h,x in enumerate(p,1):
            d=o+pd.Timedelta(days=h)
            if d in s.index: tail.append(dict(model=n,origin=str(o.date()),date=str(d.date()),prediction=float(x),actual=float(s.loc[d])))
tail=pd.DataFrame(tail)
tail.to_csv(HERE/'tail_diagnostic.csv',index=False)
out['tail_diagnostic']={n:score(g.actual,g.prediction) for n,g in tail.groupby('model')}
(HERE/'results.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))
