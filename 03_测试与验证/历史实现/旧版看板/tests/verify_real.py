"""Acceptance checks against the retained real workbook cache and live local UI adapter."""
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[3] / "电商运营系统"
EVIDENCE_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.data import Store, file_hash, DEFAULT_SOURCE
from app.services.analytics import Analytics

BASE='http://127.0.0.1:8777'

def get(path):
    with urlopen(BASE+path,timeout=30) as r:
        return json.load(r)

def task(kind,params):
    req=Request(BASE+'/api/'+kind,data=json.dumps(params).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urlopen(req,timeout=30) as r:
        job=json.load(r)
    begin=time.monotonic()
    while job['status'] in ['queued','running']:
        if time.monotonic()-begin>300:
            raise TimeoutError(job['id'])
        time.sleep(.5)
        job=get('/api/jobs/'+job['id'])
    assert job['status']=='succeeded',job
    return job

def main():
    store=Store();analytics=Analytics(store);meta=store.manifest()
    assert file_hash(DEFAULT_SOURCE)==meta['source_sha256']
    with store.connect() as c:
        conflicts=[dict(r) for r in c.execute('''select invoice,count(distinct invoice_time) times,
            min(invoice_time) first,max(invoice_time) last,count(distinct customer_id) customers,
            count(distinct country) countries from transactions where sale=1 group by invoice
            having times>1 order by times desc limit 12''')]
        raw=c.execute('''select invoice,invoice_time,quantity,unit_price,amount_eligible,stock_code,country
            from transactions where sale=1 and date between '2011-11-10' and '2011-12-09' ''').fetchall()
    # Independent Decimal arithmetic over line records, instead of reusing SQL sums.
    quantity=sum(Decimal(str(r['quantity'])) for r in raw)
    revenue=sum((Decimal(str(r['quantity']))*Decimal(str(r['unit_price']))).quantize(Decimal('.0001')) for r in raw if r['amount_eligible'])
    orders=len({r['invoice'] for r in raw})
    d=analytics.dashboard({'start':'2011-11-10','end':'2011-12-09'})
    assert d['totals']['quantity']==float(quantity)
    assert abs(d['totals']['revenue']-float(revenue))<.00001
    assert d['totals']['orders']==orders
    timings=[]
    for _ in range(5):
        begin=time.monotonic();live=get('/api/dashboard?start=2011-11-10&end=2011-12-09');timings.append(time.monotonic()-begin)
    assert live['totals']==d['totals']
    def concurrent_query(_):
        begin=time.monotonic()
        assert get('/api/dashboard?start=2011-11-10&end=2011-12-09')['totals']==d['totals']
        return round(time.monotonic()-begin,4)
    with ThreadPoolExecutor(max_workers=5) as pool:
        concurrent_seconds=list(pool.map(concurrent_query,range(5)))
    assert get('/api/transactions?start=2011-11-10&end=2011-12-09&page_size=25')['total']==len(raw)
    future=task('forecast',{'origin':meta['date_max'],'model':'blend_rule'})
    assert future['result']['actual_method']=='blend_rule'
    assert len(future['result']['rows'])==7 and future['result']['actual_days']==0
    assert future['result']['metrics']['WAPE'] is None
    repeat=task('forecast',{'origin':meta['date_max'],'model':'blend_rule'})
    assert repeat['id']==future['id']
    historical=task('forecast',{'origin':'2011-11-01','model':'blend_rule'})
    assert historical['result']['complete_actual'] and historical['result']['metrics']['n']==7
    rolling=task('backtest',{'start':'2011-10-15','end':'2011-12-09','model':'blend_rule'})
    r=rolling['result']
    assert (r['origins_attempted'],r['origins_with_actual'],r['complete_origins'])==(56,55,49)
    assert (r['forecast_count'],r['scored_count'])==(392,364)
    assert r['complete_window_metrics']['n']==343
    assert len(r['worst_cases'])==5
    assert len({x['date'] for x in r['worst_cases']})==5
    with urlopen(BASE+'/api/jobs/'+rolling['id']+'/export') as response:
        csv=response.read().decode('utf-8-sig')
        assert len(csv.splitlines())==393
    for path in ['/api/dashboard?start=2011-12-10&end=2011-01-01','/api/transactions?page=-1']:
        try:
            get(path);raise AssertionError('invalid request was accepted')
        except HTTPError as e:
            assert e.code==400
    result={'data_manifest':meta,'verified_window':d['filters'],'independent_line_totals':{'quantity':float(quantity),'revenue':float(revenue),'orders':orders,'lines':len(raw)},'dashboard_seconds':timings,'five_concurrent_requests_seconds':concurrent_seconds,'invoice_time_conflict_examples':conflicts,'future_task_id':future['id'],'historical_task_id':historical['id'],'backtest_task_id':rolling['id'],'backtest':{k:r[k] for k in ['origins_attempted','origins_with_actual','complete_origins','forecast_count','scored_count','metrics','baseline_metrics','complete_window_metrics','complete_window_baseline_metrics','model_version','data_policy_version']},'all_assertions_passed':True}
    (EVIDENCE_ROOT/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
