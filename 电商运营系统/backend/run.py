"""Single official entry point: ``python run.py`` (FastAPI + uvicorn).

The legacy ``server.py`` (standard-library http.server adapter) is kept only for
historical comparison and is not started by this script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> None:
    from app.core import config

    parser = argparse.ArgumentParser(description='智能电商运营管理系统（FastAPI 原型）')
    parser.add_argument('--host', default=config.HOST)
    parser.add_argument('--port', type=int, default=config.PORT)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--reload', action='store_true', help='开发模式自动重载')
    args = parser.parse_args(argv)

    import uvicorn

    if args.open_browser:
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(f'http://{args.host}:{args.port}')).start()

    if args.reload:
        uvicorn.run('app.main:app', host=args.host, port=args.port, reload=True, log_level='info')
    else:
        from app.main import app

        uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
