"""Populate the expressly requested demo through authenticated local APIs."""
import http.cookiejar
import json
from pathlib import Path
import re
import time
from urllib.request import build_opener,HTTPCookieProcessor,Request

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]/'电商运营系统'
import configparser
credentials=configparser.ConfigParser()
credentials.read(ROOT/'config/local/登录配置.ini',encoding='utf-8')
username=credentials['admin']['username']
password=credentials['admin']['password']
client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
def api(path,body=None):
    req=Request('http://127.0.0.1:8777/api'+path,data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json'})
    with client.open(req,timeout=60) as response:return json.load(response)

api('/auth/login',{'username':username,'password':password})
options=api('/options')
assert list(options['methods'])==['robust_blend']
demo=api('/inventory/demo',{})
sid=demo['scenario']['id']
items=api(f'/inventory/scenarios/{sid}/items')['items']
ledger=api(f'/inventory/scenarios/{sid}/movements')['movements']
transit=api(f'/inventory/scenarios/{sid}/in-transit')['items']
repeat=api('/inventory/demo',{})
assert repeat['duplicate'] and repeat['scenario']['id']==sid
assert len(api(f'/inventory/scenarios/{sid}/movements')['movements'])==len(ledger)
assert len(items)==12 and len(ledger)>=27 and all(x['name'] for x in items)
assert any(x['balance']<x['safety_stock'] for x in items)
assert any(x['replenishment_candidate']==0 and x['in_transit_effective']>0 for x in items)
assert any(x['in_transit_total']>x['in_transit_effective'] for x in items)
jobs={}
for kind,body in [('forecast',{'origin':'2011-12-09'}),('backtest',{'start':'2011-11-05','end':'2011-12-02'})]:
    job=api('/'+kind,body)
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        data=api('/jobs/'+job['job_id'])['job']
        if data['status'] in ('succeeded','failed'):break
        time.sleep(.5)
    assert data['status']=='succeeded',data.get('error')
    jobs[kind]=data
saved=api('/forecast/latest')
assert saved['jobs']['forecast']['id']==jobs['forecast']['id']
assert saved['jobs']['backtest']['id']==jobs['backtest']['id']
summary={'scenario':demo['scenario'],'item_count':len(items),'movement_count':len(ledger),'transit_count':len(transit),
         'needs_replenishment':sum(x['replenishment_candidate']>0 for x in items),
         'replenishment_quantity':sum(x['replenishment_candidate'] for x in items),'items':items,'transit':transit,
         'jobs':{kind:{'id':x['id'],'result':x['result']} for kind,x in jobs.items()}}
(HERE/'live_results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('items','transit','jobs')},ensure_ascii=False))
print('Forecast/backtest saved, both recoverable through latest API. Demo repeated without duplicate movements.')
