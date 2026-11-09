import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '电商运营系统'))
from api.main import app  # noqa: E402

for route in app.routes:
    path = getattr(route, 'path', '')
    methods = sorted(getattr(route, 'methods', None) or [])
    flag = getattr(route, 'include_in_schema', None)
    if path.startswith('/api'):
        print(f'{path:55s} {",".join(methods):12s} schema={flag}')
