import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TESTS = ROOT / '03_测试与验证' / '系统测试' / 'tests_api'
sys.path.insert(0, str(TESTS))
import apiharness as h

server = h.ServerProcess('smoke').start()
try:
    client = h.ApiClient(server.base)
    health = client.get('/api/health')
    print('health status:', health.status, health.text()[:200])
    root = client.get('/')
    print('root status:', root.status, root.header('Content-Type'))
    doc = client.get('/openapi.json')
    print('openapi status:', doc.status)
    if doc.status == 200:
        print('api paths:', len(doc.json().get('paths') or {}))
    routes = server.dump_routes()
    api_routes = [r for r in routes if r[0].startswith('/api')]
    print('registered api routes:', len(api_routes))
    for r in api_routes[:8]:
        print('  ', r)
finally:
    server.stop()
print('server log:', server.log_tail(5))
