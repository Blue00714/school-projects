"""Loopback-only development adapter; services do not depend on HTTP."""
from __future__ import annotations

import csv
from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
from pathlib import Path
from threading import Lock, Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.services.analytics import Analytics
from app.services.data import Store
from app.services.forecasting import forecast, backtest, METHODS, VERSION

ROOT=Path(__file__).resolve().parent


class Application:
    def __init__(self,db=None):
        self.store=Store(db) if db else Store()
        self.analytics=Analytics(self.store)
        self.series=self.store.series()
        self.jobs={}
        self.lock=Lock()
        self.worker_lock=Lock()
        self.cache={}

    def submit(self,kind,params):
        if kind not in ['forecast','backtest']:
            raise ValueError('未知任务类型')
        method=params.get('model','blend_rule')
        if method not in METHODS:
            raise ValueError('未知预测方法')
        if kind=='forecast':
            origin=params.get('origin') or self.store.manifest()['date_max']
            # Cheap input validation before creating a task; full checks in service.
            from app.services.analytics import iso_date
            params={'origin':iso_date(origin),'model':method}
        else:
            from app.services.analytics import iso_date
            params={'start':iso_date(params.get('start')),'end':iso_date(params.get('end')),'model':method}
        key=sha256(json.dumps([kind,params,self.store.manifest()['source_sha256'],self.store.manifest()['policy_version'],VERSION],sort_keys=True).encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                return self.jobs[self.cache[key]].copy()
            queued=sum(x['status'] in ['queued','running'] for x in self.jobs.values())
            if queued>=4:
                raise ValueError('已有4个预测任务等待处理，请稍后再试')
            task_id=uuid4().hex
            job={'id':task_id,'kind':kind,'status':'queued','created_at':datetime.now().isoformat(timespec='seconds'),'params':params}
            self.jobs[task_id]=job
            self.cache[key]=task_id
        def execute():
            with self.worker_lock:
                with self.lock:
                    job['status']='running'
                begun=time.monotonic()
                try:
                    if kind=='forecast':
                        result=forecast(self.series,**params,model_dir=ROOT/'runtime'/'models'/key)
                    else:
                        result=backtest(self.series,**params)
                    result['data_policy_version']=self.store.manifest()['policy_version']
                    result['data_source_sha256']=self.store.manifest()['source_sha256']
                    result['task_id']=task_id
                    payload=json.dumps(result,ensure_ascii=False,allow_nan=False)
                    with self.store.connect() as cx:
                        cx.execute('insert into forecast_runs values (?,?,?,?,?)',(task_id,job['created_at'],params.get('origin',params.get('start')),params['model'],payload))
                    with self.lock:
                        job.update(status='succeeded',elapsed_seconds=round(time.monotonic()-begun,2),result=result)
                except Exception as exc:
                    with self.lock:
                        job.update(status='failed',error=str(exc),elapsed_seconds=round(time.monotonic()-begun,2))
                        self.cache.pop(key,None)
        Thread(target=execute,daemon=True).start()
        return {k:v for k,v in job.items() if k!='result'}

    def job(self,task_id):
        with self.lock:
            if task_id in self.jobs:
                return self.jobs[task_id].copy()
        with self.store.connect() as cx:
            row=cx.execute('select * from forecast_runs where id=?',(task_id,)).fetchone()
        if not row:
            raise KeyError('找不到该任务')
        return {'id':task_id,'status':'succeeded','created_at':row['created_at'],'result':json.loads(row['payload'])}


def csv_bytes(rows,fields):
    text=io.StringIO(newline='')
    writer=csv.DictWriter(text,fieldnames=fields,extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        clean={k:("'"+v if isinstance(v,str) and v[:1] in '=+-@' else v) for k,v in row.items()}
        writer.writerow(clean)
    return ('\ufeff'+text.getvalue()).encode('utf-8')


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            print(f'{datetime.now().isoformat(timespec="seconds")} {fmt % args}',flush=True)

        def reply(self,status,body,ctype='application/json; charset=utf-8',filename=None):
            if not isinstance(body,bytes):
                body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                url=urlsplit(self.path)
                p={k:v[-1] for k,v in parse_qs(url.query).items()}
                path=url.path
                if path in ['/','/index.html','/app.js','/style.css']:
                    name='index.html' if path=='/' else path[1:]
                    ctype={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name]
                    return self.reply(200,(ROOT/'web'/name).read_bytes(),ctype)
                if path=='/api/health':
                    return self.reply(200,{'ok':True,'data_ready':True})
                if path=='/api/options':
                    result=app.analytics.options()
                    result['methods']=METHODS
                    return self.reply(200,result)
                if path=='/api/products':
                    return self.reply(200,app.analytics.search_products(p.get('q','')))
                if path=='/api/dashboard':
                    return self.reply(200,app.analytics.dashboard(p))
                if path=='/api/transactions':
                    return self.reply(200,app.analytics.transactions(p))
                if path=='/api/export/trend':
                    rows=app.analytics.dashboard(p)['trend']
                    return self.reply(200,csv_bytes(rows,['date','quantity','revenue','orders','observed']),'text/csv; charset=utf-8','sales-trend.csv')
                if path.startswith('/api/jobs/'):
                    parts=path.split('/')
                    job=app.job(parts[3])
                    if len(parts)==5 and parts[4]=='export':
                        if job['status']!='succeeded':
                            raise ValueError('任务尚未成功完成')
                        result=job['result']
                        rows=result.get('rows')
                        if rows is None:
                            rows=[dict(r,origin=x['origin']) for x in result['origin_results'] for r in x['rows']]
                        rows=[dict(r,origin=r.get('origin',result.get('origin')),model_version=result['model_version'],data_policy_version=result['data_policy_version']) for r in rows]
                        return self.reply(200,csv_bytes(rows,['origin','date','h','prediction','baseline','actual','abs_error','model_version','data_policy_version']),'text/csv; charset=utf-8','forecast-results.csv')
                    if len(parts)!=4:
                        raise KeyError('路径不存在')
                    return self.reply(200,job)
                raise KeyError('路径不存在')
            except (ValueError,TypeError) as exc:
                self.reply(400,{'error':str(exc)})
            except KeyError as exc:
                self.reply(404,{'error':str(exc)})
            except Exception as exc:
                self.log_message('request failed: %s',repr(exc))
                self.reply(500,{'error':'查询失败，请检查本地服务日志'})

        def do_POST(self):
            try:
                origin=self.headers.get('Origin')
                host=self.headers.get('Host')
                if origin and origin!=f'http://{host}':
                    return self.reply(403,{'error':'仅接受本地同源页面的任务提交'})
                if self.headers.get_content_type()!='application/json':
                    raise ValueError('请求必须为application/json')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<32768:
                    raise ValueError('请求大小无效')
                params=json.loads(self.rfile.read(length))
                if not isinstance(params,dict):
                    raise ValueError('请求参数必须为对象')
                path=urlsplit(self.path).path
                if path not in ['/api/forecast','/api/backtest']:
                    return self.reply(404,{'error':'路径不存在'})
                self.reply(202,app.submit(path.split('/')[-1],params))
            except (ValueError,TypeError) as exc:
                self.reply(400,{'error':str(exc)})

    return Handler


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def serve(port=8777,db=None,open_browser=False):
    app=Application(db)
    httpd=LocalHTTPServer(('127.0.0.1',port),make_handler(app))
    print(f'Local: http://127.0.0.1:{httpd.server_port}',flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{httpd.server_port}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
