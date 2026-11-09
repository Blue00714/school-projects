import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '03_测试与验证' / '系统测试' / 'tests_api'))
import apiharness as h

server = h.ServerProcess('probe_inventory').start()
try:
    c = h.ApiClient(server.base)
    print('login', c.login().status)
    created = c.post('/api/inventory/scenarios', {'name': 'p', 'stock_codes': ['QA-ITEM-1']})
    print('create', created.status, created.text()[:300])
    sid = created.json()['scenario']['id']
    put = c.put(f'/api/inventory/scenarios/{sid}/items/QA-ITEM-1',
                {'name': 'n', 'initial_qty': 100, 'lead_time_days': 8,
                 'safety_stock': 30, 'daily_demand': 10})
    print('put', put.status, json.dumps(put.json(), ensure_ascii=False)[:600])
    items = c.get(f'/api/inventory/scenarios/{sid}/items')
    print('items', items.status, json.dumps(items.json(), ensure_ascii=False)[:800])
    repl = c.get(f'/api/inventory/scenarios/{sid}/replenishment')
    print('repl', repl.status, json.dumps(repl.json(), ensure_ascii=False)[:600])
    move = c.post(f'/api/inventory/scenarios/{sid}/movements',
                  {'stock_code': 'QA-ITEM-1', 'kind': 'in', 'qty': 20, 'idempotency_key': 'x'})
    print('move', move.status, json.dumps(move.json(), ensure_ascii=False)[:600])
    again = c.post(f'/api/inventory/scenarios/{sid}/movements',
                   {'stock_code': 'QA-ITEM-1', 'kind': 'in', 'qty': 20, 'idempotency_key': 'x'})
    print('dup', again.status, json.dumps(again.json(), ensure_ascii=False)[:500])
    # orders list shape for approved numbers
    orders = c.get('/api/orders', {'start': '2011-11-10', 'end': '2011-12-09', 'page_size': 2})
    print('orders', orders.status, json.dumps(orders.json(), ensure_ascii=False)[:900])
    trans = c.get('/api/transactions', {'start': '2011-11-10', 'end': '2011-11-10', 'page_size': 1})
    print('trans', trans.status, json.dumps(trans.json(), ensure_ascii=False)[:700])
    ai = c.get('/api/ai/status')
    print('ai status', ai.status, ai.text()[:400])
    dash = c.get('/api/dashboard', {'start': '2011-11-10', 'end': '2011-12-09', 'country': 'United Kingdom'})
    print('uk products', dash.json()['totals']['products'])
finally:
    server.stop()
