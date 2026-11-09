"""Local CLI entry point, intentionally independent of FastAPI."""
import argparse
import json
from pathlib import Path
from app.services.data import prepare, Store, DEFAULT_DB, DEFAULT_SOURCE

def main():
    parser=argparse.ArgumentParser(description='电商运营看板与预测本地工具')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare',help='只读导入Excel并建立可追溯缓存')
    p.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--rebuild',action='store_true')
    p=sub.add_parser('serve',help='运行本地看板')
    p.add_argument('--port',type=int,default=8777)
    p.add_argument('--open-browser',action='store_true')
    p=sub.add_parser('forecast',help='独立运行7天预测')
    p.add_argument('--origin')
    p=sub.add_parser('backtest',help='独立运行历史回测')
    p.add_argument('--start',required=True)
    p.add_argument('--end',required=True)
    a=parser.parse_args()
    if a.command=='prepare':
        print(json.dumps(prepare(a.source,rebuild=a.rebuild),ensure_ascii=False,indent=2))
    elif a.command=='serve':
        from run import main as run_server
        run_server(['--port', str(a.port)] + (['--open-browser'] if a.open_browser else []))
    else:
        from app.services.forecasting import forecast, backtest
        s=Store().series()
        if a.command=='forecast':
            result=forecast(s,a.origin,model_dir=Path(__file__).parent/'runtime'/'cli_model')
        else:
            result=backtest(s,a.start,a.end)
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))

if __name__=='__main__':
    main()
