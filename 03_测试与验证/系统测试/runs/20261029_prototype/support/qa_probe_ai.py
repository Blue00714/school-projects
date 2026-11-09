import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '03_测试与验证' / '系统测试' / 'tests_api'))
import apiharness as h

server = h.ServerProcess('probe_ai').start()
try:
    c = h.ApiClient(server.base)
    c.login()
    questions = [
        '2011-11-10到2011-12-09的销量是多少？',
        '2011-11-10到2011-12-09全店汇总',
        '2011-11-10到2011-12-09的销售额是多少？',
        '2011-11-10到2011-12-09的订单数是多少？',
        '帮我看看2011-11-10到2011-12-09的整体经营情况，销量、销售额、订单数',
        '2011-11-10到2011-12-09全店销量、销售额、订单数',
    ]
    for q in questions:
        r = c.post('/api/ai/ask', {'question': q, 'scope': h.ORIGINAL_RANGE}).json()
        calls = [(t.get('name'), str(t.get('summary'))[:120]) for t in r.get('tool_calls') or []]
        print('Q:', q)
        print('   status', r.get('status'), 'calls', [c0[0] for c0 in calls])
        print('   answer', (r.get('answer') or '')[:220].replace('\n', ' | '))
finally:
    server.stop()
