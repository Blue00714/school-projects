"""Apply the user-approved layout; business data and model contents stay in place."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

WORK = Path(__file__).resolve().parents[2]
APP = WORK / '电商运营系统'
CHECK = Path(__file__).with_name('20261105_整理核验.json')
ARCHIVE = WORK / '03_测试与验证/历史实现/旧版看板'

def checked(path):
    path = path.resolve()
    if not path.is_relative_to(WORK):
        raise ValueError(f'Outside approved project: {path}')
    return path

def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def data_state():
    from app import db
    counts={}
    with db.connect() as cx:
        for table in ['users','batches','jobs','inventory_scenarios','inventory_items','inventory_movements','inventory_in_transit']:
            counts[table]=cx.execute(f'select count(*) from {table}').fetchone()[0]
        counts['demo_balance']=float(cx.execute("select initial_qty+(select coalesce(sum(case when kind='in' then qty else -qty end),0) from inventory_movements m where m.scenario_id=i.scenario_id and m.stock_code=i.stock_code) from inventory_items i where scenario_id='demo-original' and stock_code='20668'").fetchone()[0])
    return counts

MOVES={
    'api/__init__.py':'backend/app/__init__.py',
    'api/main.py':'backend/app/main.py', 'api/db.py':'backend/app/db.py',
    'api/config.py':'backend/app/core/config.py', 'api/security.py':'backend/app/core/security.py',
    'api/errors.py':'backend/app/core/errors.py', 'api/deps.py':'backend/app/api/deps.py',
    'api/routers':'backend/app/api/routers', 'api/services':'backend/app/services',
    'run.py':'backend/run.py', 'manage.py':'backend/cli.py', 'requirements.txt':'backend/requirements.txt',
    'API契约.md':'docs/API契约.md', '登录配置.ini':'config/local/登录配置.ini',
    'runtime/mysql.env':'config/local/mysql.env', '.mysql/my.ini':'config/local/mysql.ini',
    '.mysql':'vendor/mysql', '.pydeps':'vendor/python-packages',
    '启动系统.ps1':'scripts/启动系统.ps1',
    'scripts/acceptance_mysql.py':'backend/tests/integration/acceptance_mysql.py',
}

def map_module(name):
    special={'api.config':'app.core.config','api.security':'app.core.security','api.errors':'app.core.errors',
             'api.db':'app.db','api.deps':'app.api.deps','api.main':'app.main'}
    for old,new in special.items():
        if name==old or name.startswith(old+'.'):return new+name[len(old):]
    for old,new in [('api.routers','app.api.routers'),('api.services','app.services'),('services','app.services'),('api','app')]:
        if name==old or name.startswith(old+'.'):return new+name[len(old):]
    return name

def imports(text, old_module):
    tree=ast.parse(text)
    lines=text.splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    changes=[]
    for node in ast.walk(tree):
        replacement=None
        if isinstance(node,ast.ImportFrom):
            package=old_module.rsplit('.',1)[0] if '.' in old_module else old_module
            base=importlib.util.resolve_name('.'*node.level+(node.module or ''),package) if node.level else (node.module or '')
            if base in ('api','services','api.services','api.routers'):
                groups={}
                for alias in node.names:
                    full=map_module(base+'.'+alias.name)
                    parent, name=full.rsplit('.',1)
                    groups.setdefault(parent,[]).append(name+(' as '+alias.asname if alias.asname else ''))
                replacement=('\n'+' '*node.col_offset).join('from '+parent+' import '+', '.join(names) for parent,names in groups.items())
            elif map_module(base)!=base or (node.level and base.startswith(('api','services'))):
                replacement='from '+map_module(base)+' import '+', '.join(a.name+(' as '+a.asname if a.asname else '') for a in node.names)
        elif isinstance(node,ast.Import):
            if any(map_module(a.name)!=a.name for a in node.names):
                replacement='import '+', '.join(map_module(a.name)+(' as '+a.asname if a.asname else '') for a in node.names)
        if replacement:
            # Import statements themselves are ASCII, so byte/character columns agree.
            a=offsets[node.lineno-1]+node.col_offset
            b=offsets[node.end_lineno-1]+node.end_col_offset
            changes.append((a,b,replacement))
    for a,b,value in sorted(changes,reverse=True):text=text[:a]+value+text[b:]
    # Dynamic patch/import strings and explanatory module paths also need the new package.
    text=re.sub(r'(?<![\w.])(?:api|services)\.[A-Za-z_][\w.]*',lambda m:map_module(m.group()),text)
    return text

def move(src,dst,record):
    src,dst=checked(src),checked(dst)
    if not src.exists() and dst.exists():
        record.append([str(src.relative_to(WORK)),str(dst.relative_to(WORK))])
        return
    if not src.exists(): raise FileNotFoundError(src)
    if dst.exists(): raise FileExistsError(dst)
    dst.parent.mkdir(parents=True,exist_ok=True)
    print('MOVE',src.relative_to(WORK),'->',dst.relative_to(WORK),flush=True)
    shutil.move(str(src),str(dst))
    record.append([str(src.relative_to(WORK)),str(dst.relative_to(WORK))])

def capture_before():
    sys.path.insert(0,str(APP))
    from api import db
    with db.connect() as cx:
        counts={t:cx.execute(f'select count(*) from {t}').fetchone()[0] for t in ['users','batches','jobs','inventory_scenarios','inventory_items','inventory_movements','inventory_in_transit']}
        counts['demo_balance']=float(cx.execute("select initial_qty+(select coalesce(sum(case when kind='in' then qty else -qty end),0) from inventory_movements m where m.scenario_id=i.scenario_id and m.stock_code=i.stock_code) from inventory_items i where scenario_id='demo-original' and stock_code='20668'").fetchone()[0])
    protected=[WORK/'20261014_电商数据与页面选型/online_retail_II.xlsx', APP/'runtime/retail.sqlite3',APP/'runtime/app.sqlite3']
    protected+=list((APP/'runtime/models').rglob('*'))
    state={'before_counts':counts,'protected':{str(p.relative_to(WORK)):digest(p) for p in protected if p.is_file()},'moves':[]}
    CHECK.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf-8')
    # Keep recovery material inside ignored runtime, not in public documents.
    backup=APP/'runtime/tmp/layout-before.zip'
    backup.parent.mkdir(parents=True,exist_ok=True)
    files=[]
    for folder in ['api','services','scripts']:
        files += [p for p in (APP/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [p for p in APP.iterdir() if p.is_file()]
    files += [APP/'runtime/mysql.env',APP/'.mysql/my.ini',APP/'runtime/initial_admin.txt']
    with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:
            if p.is_file():z.write(p,str(p.relative_to(APP)))
    print('Before-state and private recovery snapshot captured; no secret values printed.')

def execute():
    state=json.loads(CHECK.read_text(encoding='utf-8'))
    record=state['moves']
    source_modules={}
    with zipfile.ZipFile(APP/'runtime/tmp/layout-before.zip') as backup:
        for name in backup.namelist():
            if name.startswith(('api/','services/')) and name.endswith('.py'):
                source_modules[name]=name[:-3].replace('/','.').removesuffix('.__init__')
    for src,dst in MOVES.items():move(APP/src,APP/dst,record)
    for p in sorted((APP/'services').glob('*.py')):
        if p.name=='__init__.py':continue
        move(p,APP/'backend/app/services'/p.name,record)
    for src in source_modules:
        if src.startswith('services/') and src!='services/__init__.py':
            pair=[str((APP/src).relative_to(WORK)),str((APP/'backend/app/services'/Path(src).name).relative_to(WORK))]
            if pair not in record:record.append(pair)
    for p in list((APP/'scripts').iterdir()):
        if p.is_file() and (p.name.startswith('mysql_') or p.name=='migrate_to_mysql.py'):
            move(p,APP/'scripts/mysql'/p.name,record)
    for name in ['server.py','web','启动看板.cmd','启动看板.ps1']:
        move(APP/name,ARCHIVE/name,record)
    move(WORK/'03_测试与验证/系统测试/tests',APP/'backend/tests/services',record)
    move(WORK/'03_测试与验证/系统测试/tests_api',APP/'backend/tests/api',record)
    (APP/'backend/app/core/__init__.py').write_text('',encoding='utf-8')
    (APP/'backend/app/api/__init__.py').write_text('',encoding='utf-8')
    # Apply import rewrites with the ORIGINAL package context, including local imports.
    for src,old_module in source_modules.items():
        if src=='services/__init__.py':continue
        if src.startswith('api/routers/'):dst='backend/app/api/routers/'+src.split('api/routers/',1)[1]
        elif src.startswith('api/services/'):dst='backend/app/services/'+src.split('api/services/',1)[1]
        elif src.startswith('services/'):dst='backend/app/services/'+src.split('services/',1)[1]
        else:dst=MOVES[src]
        p=APP/dst
        p.write_text(imports(p.read_text(encoding='utf-8-sig'),old_module),encoding='utf-8')
    for p in list((APP/'backend').rglob('*.py'))+list((APP/'scripts').rglob('*.py'))+list(ARCHIVE.glob('*.py')):
        if p.is_relative_to(APP/'backend/app'):continue
        p.write_text(imports(p.read_text(encoding='utf-8-sig'),'external'),encoding='utf-8')
    # Newly unified package owns the dependency bootstrap.
    (APP/'backend/app/__init__.py').write_text('''"""Application package; load this checkout's optional local dependencies."""
import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[2]
PYDEPS = APP_ROOT / 'vendor' / 'python-packages'
if PYDEPS.is_dir() and str(PYDEPS) not in sys.path:
    sys.path.insert(0, str(PYDEPS))
''',encoding='utf-8')
    for path in [APP/'services',APP/'api',APP/'__pycache__']:
        if path.exists():
            checked(path)
            # Only package initializers/caches remain after every source above was mapped.
            remaining=[p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='__init__.py']
            if remaining:raise ValueError(f'Unmapped source files: {remaining}')
            shutil.rmtree(path)
    # Explicitly excluded by the approved framework; recoverable in the private snapshot.
    for name in ['.env.example','技术栈迁移方案.md']:
        checked(APP/name).unlink()
    for name in ['server_stdout.log','server_stderr.log']:
        p=APP/'runtime'/name
        if p.exists():move(p,APP/'runtime/logs'/name,record)
    for src,dst in [('mysql-logs','logs/mysql'),('mysql-tmp','tmp/mysql')]:
        p=APP/'runtime'/src
        if p.exists():move(p,APP/'runtime'/dst,record)
    p=APP/'runtime/mysql.pid'
    if p.exists():move(p,APP/'runtime/tmp/mysql.pid',record)
    # No longer maintain two human-readable copies of the same initial passwords.
    initial=APP/'runtime/initial_admin.txt'
    if initial.exists():
        import configparser
        c=configparser.ConfigParser();c.read(APP/'config/local/登录配置.ini',encoding='utf-8')
        old=initial.read_text(encoding='utf-8')
        for section,label in [('admin','管理员'),('operator','运营')]:
            if re.search(label+r'初始密码[：:]\s*(\S+)',old).group(1)!=c[section]['password']:
                raise ValueError('Credential records differ; do not delete either record')
        initial.unlink()
    CHECK.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Moves complete. Paths/imports must be finalized before restart.')

def finalize():
    def edit(relative, old, new):
        p=APP/relative
        text=p.read_text(encoding='utf-8-sig')
        if old not in text:raise ValueError(f'Missing replacement in {relative}: {old[:75]}')
        p.write_text(text.replace(old,new),encoding='utf-8')
    edit('backend/app/core/config.py', 'APP_ROOT = Path(__file__).resolve().parents[1]', '''APP_ROOT = Path(__file__).resolve().parents[3]
# Test runtimes use isolated configuration instead of loading production credentials.
_config_default = Path(os.environ['ECE_RUNTIME_DIR']) / 'config' if os.environ.get('ECE_RUNTIME_DIR') else APP_ROOT / 'config/local'
LOCAL_CONFIG_DIR = Path(os.environ.get('ECE_CONFIG_DIR', str(_config_default)))
if not LOCAL_CONFIG_DIR.is_absolute():
    LOCAL_CONFIG_DIR = APP_ROOT / LOCAL_CONFIG_DIR''')
    edit('backend/app/core/config.py', "_load_env_file(APP_ROOT / '.env')", "_load_env_file(LOCAL_CONFIG_DIR / 'app.env')")
    edit('backend/app/core/config.py', "WEB_DIR = APP_ROOT / 'web'", "LOG_DIR = RUNTIME_DIR / 'logs'\nTMP_DIR = RUNTIME_DIR / 'tmp'")
    edit('backend/app/core/config.py', "INITIAL_ADMIN_FILE = RUNTIME_DIR / 'initial_admin.txt'", "INITIAL_ADMIN_FILE = LOCAL_CONFIG_DIR / '登录配置.ini'")
    edit('backend/app/core/config.py', "MYSQL_ENV_FILE = RUNTIME_DIR / 'mysql.env'", "MYSQL_ENV_FILE = LOCAL_CONFIG_DIR / 'mysql.env'")
    edit('backend/app/core/config.py', "    BATCH_DIR.mkdir(parents=True, exist_ok=True)", "    BATCH_DIR.mkdir(parents=True, exist_ok=True)\n    LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)\n    LOG_DIR.mkdir(parents=True, exist_ok=True)\n    TMP_DIR.mkdir(parents=True, exist_ok=True)")
    edit('backend/app/services/data.py', 'ROOT = Path(__file__).resolve().parents[1]', 'from app.core.config import APP_ROOT as ROOT')
    edit('backend/run.py', 'def main() -> None:', 'def main(argv=None) -> None:')
    edit('backend/run.py', 'args = parser.parse_args()', 'args = parser.parse_args(argv)')
    edit('backend/cli.py', '''        from server import serve
        if not DEFAULT_DB.exists():
            prepare()
        serve(a.port,open_browser=a.open_browser)''', '''        from run import main as run_server
        run_server(['--port', str(a.port)] + (['--open-browser'] if a.open_browser else []))''')
    # Drop only the legacy page fallback; the built Vue app remains the sole UI.
    edit('backend/app/main.py', "if config.WEB_DIR.is_dir():\n    app.mount('/static', StaticFiles(directory=config.WEB_DIR), name='static')\n", '')
    edit('backend/app/main.py', "    page = config.WEB_DIR / 'index.html'\n    if not page.is_file():\n        return JSONResponse(status_code=503, content={'error': '页面文件缺失', 'code': 'not_ready'})\n    return FileResponse(page, media_type='text/html; charset=utf-8')", "    return JSONResponse(status_code=503, content={'error': '请先构建frontend目录下的Vue页面', 'code': 'not_ready'})")
    edit('backend/app/main.py', "    icon = config.WEB_DIR / 'favicon.ico'", "    icon = config.FRONTEND_DIST / 'favicon.ico'")
    edit('backend/app/db.py', "    if not os.environ.get('ECE_ADMIN_PASSWORD'):\n        config.INITIAL_ADMIN_FILE.write_text(", "    if not os.environ.get('ECE_ADMIN_PASSWORD'):\n        config.INITIAL_ADMIN_FILE.write_text(")
    p=APP/'backend/app/db.py';t=p.read_text(encoding='utf-8')
    start=t.index("    if not os.environ.get('ECE_ADMIN_PASSWORD'):\n        config.INITIAL_ADMIN_FILE.write_text(")
    t=t[:start]+'''    if not os.environ.get('ECE_ADMIN_PASSWORD'):
        import configparser
        accounts = configparser.ConfigParser()
        accounts['system'] = {'url': f'http://{config.HOST}:{config.PORT}/'}
        accounts['admin'] = {'username': admin_name, 'password': admin_pw}
        accounts['operator'] = {'username': operator_name, 'password': operator_pw}
        with config.INITIAL_ADMIN_FILE.open('w', encoding='utf-8') as output:
            output.write('# 本机登录信息；修改本文件不会重置数据库口令。\\n')
            accounts.write(output)
'''
    p.write_text(t,encoding='utf-8')
    # Database tooling now sits two levels below the application root.
    edit('scripts/mysql/mysql_common.ps1', '$script:EceRoot = Split-Path -Parent $PSScriptRoot', '$script:EceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)')
    for old,new in [("'.mysql'","'vendor\\mysql'"),("'mysql-logs'","'logs\\mysql'"),("Join-Path $script:EceRuntime 'mysql.env'","Join-Path $script:EceRoot 'config\\local\\mysql.env'"),("Join-Path $script:EceRuntime 'mysql.pid'","Join-Path $script:EceRuntime 'tmp\\mysql.pid'"),("Join-Path $script:EceMysqlHome 'my.ini'","Join-Path $script:EceRoot 'config\\local\\mysql.ini'")]:
        edit('scripts/mysql/mysql_common.ps1',old,new)
    edit('scripts/mysql/mysql_start.ps1', 'Write-Host "[mysql-start] starting mysqld', '''$mysqlTemp = Join-Path $root 'runtime\\tmp\\mysql'
New-Item -ItemType Directory -Path $mysqlTemp -Force | Out-Null
$env:TEMP = $mysqlTemp
$env:TMP = $mysqlTemp
Write-Host "[mysql-start] starting mysqld''')
    edit('scripts/mysql/mysql_init.ps1', "New-Item -ItemType Directory -Path $logDir -Force | Out-Null", "New-Item -ItemType Directory -Path $logDir -Force | Out-Null\nNew-Item -ItemType Directory -Path (Split-Path -Parent $iniFile) -Force | Out-Null")
    for relative in ['scripts/mysql/migrate_to_mysql.py','scripts/mysql/mysql_bootstrap.py']:
        edit(relative,'ROOT = Path(__file__).resolve().parents[1]','ROOT = Path(__file__).resolve().parents[2]')
        edit(relative,"ROOT / '.pydeps'", "ROOT / 'vendor/python-packages'")
        edit(relative,'str(ROOT))','str(ROOT / \'backend\'))')
    edit('scripts/mysql/mysql_bootstrap.py', "ENV_FILE = ROOT / 'runtime' / 'mysql.env'", "ENV_FILE = ROOT / 'config/local/mysql.env'")
    # Preserve the ANSI option-file encoding required by this MySQL build on Windows.
    p=APP/'config/local/mysql.ini';t=p.read_bytes().decode('gbk')
    for name,value in [('basedir',APP/'vendor/mysql'),('datadir',APP/'runtime/mysql-data'),('log-error',APP/'runtime/logs/mysql/error.log')]:
        t=re.sub(r'^'+re.escape(name)+r'=.*$',lambda m:f'{name}={value.as_posix()}',t,flags=re.M)
    t=t.replace('[mysqld]','[mysqld]\ntmpdir='+ (APP/'runtime/tmp/mysql').as_posix())
    p.write_bytes(t.encode('gbk'))
    # Test infrastructure keeps evidence outside the application source tree.
    edit('backend/tests/api/apiharness.py', "ROOT = Path(__file__).resolve().parents[3]\nSYSTEM_ROOT = ROOT / '电商运营系统'", "SYSTEM_ROOT = Path(__file__).resolve().parents[3]\nROOT = SYSTEM_ROOT.parent")
    edit('backend/tests/api/apiharness.py', "TMP_ROOT = ROOT / '.tmp' / 'qa_api'", "TMP_ROOT = SYSTEM_ROOT / 'runtime/tmp/qa_api'")
    edit('backend/tests/api/apiharness.py', 'cwd=str(SYSTEM_ROOT)', "cwd=str(SYSTEM_ROOT / 'backend')")
    for relative in ['backend/tests/api/apiharness.py','backend/tests/api/test_forecast.py']:
        edit(relative,'str(SYSTEM_ROOT)',"str(SYSTEM_ROOT / 'backend')")
    for relative in ['backend/tests/api/run_api_tests.py','backend/tests/api/build_acceptance.py']:
        edit(relative,'ROOT = HERE.parents[2]','ROOT = HERE.parents[3]')
    edit('backend/tests/services/test_services.py', 'SYSTEM_ROOT = Path(__file__).resolve().parents[3] / "电商运营系统"', 'SYSTEM_ROOT = Path(__file__).resolve().parents[3]')
    edit('backend/tests/services/test_services.py', 'sys.path.insert(0, str(SYSTEM_ROOT))', '''sys.path.insert(0, str(SYSTEM_ROOT / 'backend'))
sys.path.insert(0, str(SYSTEM_ROOT.parent / '03_测试与验证/历史实现/旧版看板'))''')
    edit('backend/tests/services/test_services.py', "TEMP_ROOT=Path(__file__).resolve().parents[1]/'test_tmp'", "TEMP_ROOT=SYSTEM_ROOT/'runtime/tmp/service_tests'")
    edit('backend/tests/integration/acceptance_mysql.py','SYSTEM_ROOT = HERE.parent', 'SYSTEM_ROOT = HERE.parents[2]')
    edit('backend/tests/integration/acceptance_mysql.py', "TESTS_API = ROOT / '03_测试与验证' / '系统测试' / 'tests_api'", "TESTS_API = SYSTEM_ROOT / 'backend/tests/api'")
    edit('backend/tests/integration/acceptance_mysql.py', "SYSTEM_ROOT / '.pydeps'", "SYSTEM_ROOT / 'vendor/python-packages'")
    edit('backend/tests/integration/acceptance_mysql.py','str(SYSTEM_ROOT))',"str(SYSTEM_ROOT / 'backend'))")
    # The old unauthenticated HTTP verifier belongs with its adapter, not current tests.
    move(APP/'backend/tests/services/verify_real.py', ARCHIVE/'tests/verify_real.py', [])
    (APP/'config/local/app.env').write_text('ECE_HOST=127.0.0.1\nECE_PORT=8777\nECE_SESSION_TTL_HOURS=12\nECE_AI_TIMEOUT=60\n',encoding='utf-8') if not (APP/'config/local/app.env').exists() else None
    (APP/'启动系统.cmd').write_text('''@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\启动系统.ps1" %*
''',encoding='utf-8')
    (APP/'scripts/启动系统.ps1').write_text('''param([int]$Port = 8777, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$mysqlConfig = Join-Path $projectRoot 'config\\local\\mysql.env'
if (Test-Path -LiteralPath $mysqlConfig) {
    & (Join-Path $PSScriptRoot 'mysql\\mysql_start.ps1')
}
$env:PYTHONUTF8 = '1'
$arguments = @((Join-Path $projectRoot 'backend\\run.py'), '--port', "$Port")
if (-not $NoBrowser) { $arguments += '--open-browser' }
Push-Location $projectRoot
try { & python @arguments } finally { Pop-Location }
''',encoding='utf-8-sig')
    (APP/'.gitignore').write_text('''config/local/
runtime/
vendor/
frontend/node_modules/
frontend/dist/
__pycache__/
*.pyc
''',encoding='utf-8')
    print('Finalized package imports, production/test config isolation, and startup/database paths.')

def references():
    for relative in ['backend/tests/api/run_api_tests.py','backend/tests/api/build_acceptance.py']:
        p=APP/relative;t=p.read_text(encoding='utf-8')
        t=t.replace("/ 'runs' / '20261029_prototype'", "/ 'runs' / datetime.now().strftime('%Y%m%d_regression')")
        t=t.replace('03_测试与验证/系统测试/tests_api/', '电商运营系统/backend/tests/api/')
        t=t.replace("'daily-v1-hw-fixed-lgbm400'", "'daily-v3-robust-blend-mad3'")
        p.write_text(t,encoding='utf-8')
    p=APP/'backend/tests/api/README.md';t=p.read_text(encoding='utf-8')
    t=t.replace('03_测试与验证/系统测试/tests_api/','电商运营系统/backend/tests/api/')
    t=t.replace('runtime/initial_admin.txt','config/local/登录配置.ini')
    p.write_text(t,encoding='utf-8')
    p=APP/'docs/API契约.md';t=p.read_text(encoding='utf-8')
    for old,new in [('python run.py','python backend/run.py'),('api/main.py','backend/app/main.py'),('api/routers/','backend/app/api/routers/'),('services/forecasting.py','backend/app/services/forecasting.py'),('runtime/initial_admin.txt','config/local/登录配置.ini'),('runtime/mysql.env','config/local/mysql.env'),('scripts/mysql_start.ps1','scripts/mysql/mysql_start.ps1')]:t=t.replace(old,new)
    t=t.replace('- 文件 `web/index.html|app.js|style.css`，由 FastAPI 提供 `/` 与 `/static/*`；禁止 CDN/外部资源（离线可用），原生 HTML/CSS/JS，无构建步骤。','- Vue源码位于frontend/src，构建产物frontend/dist由FastAPI提供，不回退到旧版原生看板。')
    t=t.replace('旧 `web/` 页面仅在无 dist 时回退。','无dist时明确提示先构建前端。')
    p.write_text(t,encoding='utf-8')
    # Update navigational links, leaving historical prose/numbers and logs intact.
    mapping={APP/'API契约.md':APP/'docs/API契约.md',APP/'登录配置.ini':APP/'config/local/登录配置.ini',
             APP/'技术栈迁移方案.md':APP/'README.md',APP/'启动系统.ps1':APP/'scripts/启动系统.ps1',
             APP/'services':APP/'backend/app/services',APP/'api':APP/'backend/app',
             WORK/'03_测试与验证/系统测试/tests_api':APP/'backend/tests/api',
             WORK/'03_测试与验证/系统测试/tests':APP/'backend/tests/services'}
    import os
    candidates=[WORK/'01_交付内容/README.md',WORK/'01_交付内容/原型说明.md',WORK/'01_交付内容/演示步骤.md',
                WORK/'03_测试与验证/README.md',WORK/'20261014_电商数据与页面选型/先看这里.md',
                WORK/'20261014_电商数据与页面选型/第一阶段任务与报告提纲.md']
    candidates+=list((WORK/'02_实验研究').rglob('README.md'))
    candidates+=list((WORK/'03_测试与验证/系统测试/runs').rglob('README.md'))
    for p in candidates:
        if not p.is_file():continue
        t=p.read_text(encoding='utf-8')
        def link(match):
            target=match[1]
            if target.startswith(('http:','https:','#')):return match[0]
            old=(p.parent/target.split('#')[0]).resolve()
            for src,dst in mapping.items():
                if old==src or old.is_relative_to(src):
                    resolved=dst/old.relative_to(src)
                    suffix='#'+target.split('#',1)[1] if '#' in target else ''
                    return ']('+os.path.relpath(resolved,p.parent).replace('\\','/')+suffix+')'
            return match[0]
        t=re.sub(r'\]\(([^)]+)\)',link,t)
        p.write_text(t,encoding='utf-8')
    print('Updated active entry points and reusable test/document references.')

if __name__=='__main__':
    if sys.argv[1]=='before':capture_before()
    elif sys.argv[1]=='move':execute()
    elif sys.argv[1]=='finalize':finalize()
    elif sys.argv[1]=='references':references()
    elif sys.argv[1]=='verify':
        sys.path.insert(0,str(APP/'backend'))
        state=json.loads(CHECK.read_text(encoding='utf-8'))
        state['after_counts']=data_state()
        assert state['after_counts']==state['before_counts'],(state['before_counts'],state['after_counts'])
        assert all(digest(WORK/p)==expected for p,expected in state['protected'].items())
        state['protected_bytes_unchanged']=True
        CHECK.write_text(json.dumps(state,indent=2,ensure_ascii=False),encoding='utf-8')
        print('PASS: business counts, demo stock, raw data and saved model bytes preserved.')
