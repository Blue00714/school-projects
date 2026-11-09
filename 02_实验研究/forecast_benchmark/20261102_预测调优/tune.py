"""Small, time ordered comparison using the canonical read-only SQLite snapshot."""
import json
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2] / '电商运营系统'
sys.path.insert(0, str(ROOT/'backend'))
from app.services.forecasting import forecast, seasonal, holt_winters, score

with sqlite3.connect((ROOT/'runtime/retail.sqlite3').as_uri()+'?mode=ro', uri=True) as cx:
    frame = pd.read_sql_query('select date,sum(quantity) quantity from transactions where sale=1 group by date order by date', cx)
    manifest = json.loads(cx.execute("select value from metadata where key='manifest'").fetchone()[0])
s = frame.set_index(pd.to_datetime(frame.date)).quantity
s = s.reindex(pd.date_range(s.index.min(), s.index.max()), fill_value=0)

cache_path = HERE/'forecast_cache.jsonl'
cache = {}
if cache_path.exists():
    for line in cache_path.read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        cache[item['origin']] = item

def old(o):
    key = o.date().isoformat()
    if key not in cache:
        item = forecast(s, key, model='blend_rule')
        cache[key] = item
        with cache_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(item, ensure_ascii=False)+'\n')
    return np.array([r['prediction'] for r in cache[key]['rows']])

def candidates(o):
    hist = s.loc[:o]
    dates = pd.date_range(o+pd.Timedelta(days=1), periods=7)
    def week_mean(n, weighted=False):
        tail = hist.iloc[-7*n:]
        values = []
        for day in dates.dayofweek:
            y = tail[tail.index.dayofweek == day].values
            values.append(np.average(y, weights=np.arange(1,len(y)+1)) if weighted else y.mean())
        return np.array(values)
    base, hw = seasonal(hist), holt_winters(hist)
    prev, recent = hist.iloc[-28:-14].sum(), hist.iloc[-14:].sum()
    ratio = np.clip(recent/prev, .7, 1.3) if prev else 1.
    blend = old(o)
    # Recover the already fitted tree component from the frozen 50/50 blend.
    # On weekdays forced to zero by the history-only rule, both candidates use zero.
    med = hist.groupby(hist.index.dayofweek).median()
    closed = [int(d) for d,v in med.items() if v < .02*hist.median()]
    tree_rule = np.clip(2*blend-hw,0,None)
    tree_rule[np.isin(dates.dayofweek,closed)] = 0
    return {'blend_rule':blend, 'tree_rule':tree_rule, 'tree75_hw25':.5*blend+.5*tree_rule, 'seasonal_week4':base, 'holt_winters':hw,
            'week2':week_mean(2), 'week8':week_mean(8),
            'hw_week4':.5*hw+.5*base, 'trend_week4':base*ratio,
            'weighted_week4':week_mean(4, True)}

def evaluate(start, end, names, label):
    rows = []
    # Target dates, not origins, are bounded by this partition.
    origins = pd.date_range(pd.Timestamp(start)-pd.Timedelta(days=1), pd.Timestamp(end)-pd.Timedelta(days=7))
    for i,o in enumerate(origins):
        preds = candidates(o)
        for name in names:
            for h,p in enumerate(preds[name],1):
                d = o+pd.Timedelta(days=h)
                rows.append(dict(partition=label,model=name,origin=str(o.date()),date=str(d.date()),h=h,actual=float(s.loc[d]),prediction=float(p)))
        if i%10 == 0: print(label, i+1, '/', len(origins), flush=True)
    df = pd.DataFrame(rows)
    return df, {name:score(g.actual,g.prediction) for name,g in df.groupby('model')}

names = ['blend_rule','tree_rule','tree75_hw25','seasonal_week4','holt_winters','week2','week8','hw_week4','trend_week4','weighted_week4']
val, vm = evaluate('2011-08-16','2011-10-14',names,'validation')
chosen = min(names, key=lambda n:vm[n]['WAPE'])
selection = dict(chosen=chosen,validation=vm,data_policy=manifest['policy_version'],source_sha256=manifest['source_sha256'])
(HERE/'selection.json').write_text(json.dumps(selection,indent=2,ensure_ascii=False),encoding='utf-8')
val.to_csv(HERE/'validation_predictions.csv',index=False)
print('FROZEN CHOICE',chosen, vm[chosen],flush=True)
test, tm = evaluate('2011-10-15','2011-12-09',list(dict.fromkeys([chosen,'blend_rule','seasonal_week4'])),'retrospective_test')
test.to_csv(HERE/'test_predictions.csv',index=False)
tailrows=[]
for o in pd.date_range('2011-12-03','2011-12-09'):
    pred=candidates(o)
    for name in list(dict.fromkeys([chosen,'blend_rule','seasonal_week4'])):
        for h,p in enumerate(pred[name],1):
            d=o+pd.Timedelta(days=h)
            if d in s.index:
                tailrows.append(dict(model=name,origin=str(o.date()),date=str(d.date()),h=h,actual=float(s.loc[d]),prediction=float(p)))
tail=pd.DataFrame(tailrows)
tail.to_csv(HERE/'tail_predictions.csv',index=False)
tail_metrics={n:score(g.actual,g.prediction) for n,g in tail.groupby('model')}
oldtail=tail[tail.model=='blend_rule'].copy()
oldtail['abs_error']=abs(oldtail.prediction-oldtail.actual)
contrib=oldtail.groupby('date').abs_error.sum().sort_values(ascending=False)
summary=dict(**selection,test=tm,tail=tail_metrics,tail_error_by_date=contrib.to_dict(),tail_without_last_day=score(oldtail[oldtail.date!='2011-12-09'].actual,oldtail[oldtail.date!='2011-12-09'].prediction))
(HERE/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
