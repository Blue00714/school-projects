"""Shared harness for the FastAPI prototype API test suite.

Design rules (see ``API契约.md`` section 6):

* Only the standard library is used: ``unittest``, ``urllib``/``http.cookiejar``,
  ``subprocess``, ``shutil``, ``socket``. No pytest/httpx/requests.
* Every test class starts its own ``python run.py --port <free port>`` subprocess with a
  private temporary runtime directory (``ECE_RUNTIME_DIR`` / ``ECE_APP_DB``) and a
  private copy of ``runtime/retail.sqlite3`` as ``ECE_ORIGINAL_DB``. The official
  ``电商运营系统/runtime`` database is never opened for writing by the tests.
* Temporary data lives under ``<project>/.tmp/qa_api/<class>-<token>/``; the copied
  285 MB database is deleted on teardown (keep it with ``QA_KEEP_TMP=1``), the server
  log stays for debugging.
* The port is always allocated by the OS, so several runs never share a fixed port.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

SYSTEM_ROOT = Path(__file__).resolve().parents[3]
ROOT = SYSTEM_ROOT.parent
EVIDENCE_DIR = ROOT / '03_测试与验证' / '系统测试' / 'runs' / '20261029_prototype'
TMP_ROOT = SYSTEM_ROOT / 'runtime/tmp/qa_api'
OFFICIAL_DB = SYSTEM_ROOT / 'runtime' / 'retail.sqlite3'

# Fixed test credentials (>= 8 chars) injected through ECE_* environment variables.
ADMIN_USER = 'admin'
ADMIN_PASSWORD = 'qa-admin-Passw0rd'
OPERATOR_USER = 'operator'
OPERATOR_PASSWORD = 'qa-operator-Passw0rd'

# The historical full-shop window used by the acceptance numbers (inclusive).
ORIGINAL_RANGE = {'start': '2011-11-10', 'end': '2011-12-09'}
ORIGINAL_TOTALS = {
    'quantity': 851403,
    'revenue': 1694068.71,
    'orders': 2913,
    'lines': 86861,
}

READY_TIMEOUT_SECONDS = float(os.environ.get('QA_READY_TIMEOUT', '180'))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get('QA_REQUEST_TIMEOUT', '120'))
JOB_TIMEOUT_SECONDS = float(os.environ.get('QA_JOB_TIMEOUT', '300'))


# --------------------------------------------------------------------------- ports


def free_port() -> int:
    """Ask the OS for an unused TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


# ------------------------------------------------------------------------ responses


class ApiResponse:
    """Small wrapper around ``urllib`` responses and ``HTTPError`` responses."""

    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = int(status)
        self.headers = headers
        self.body = body or b''
        self.url = url

    # -- helpers -----------------------------------------------------------
    def header(self, name: str, default: str = '') -> str:
        try:
            return self.headers.get(name, default) or default
        except AttributeError:
            return default

    def text(self) -> str:
        try:
            return self.body.decode('utf-8')
        except UnicodeDecodeError:
            return self.body.decode('utf-8', 'replace')

    def json(self):
        try:
            return json.loads(self.text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AssertionError(
                f'{self.url} 返回非 JSON（status={self.status}）: {self.text()[:300]!r}'
            ) from exc

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f'<ApiResponse {self.status} {self.url} {self.text()[:120]!r}>'


# ------------------------------------------------------------------------- clients


def multipart_body(fields: dict, files: list[tuple[str, str, bytes, str]]):
    """Encode a multipart/form-data body with only the standard library."""
    boundary = '----qa' + secrets.token_hex(10)
    chunks: list[bytes] = []
    for name, value in (fields or {}).items():
        chunks.append(f'--{boundary}\r\n'.encode('ascii'))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
        chunks.append(str(value).encode('utf-8'))
        chunks.append(b'\r\n')
    for field_name, file_name, content, content_type in files:
        chunks.append(f'--{boundary}\r\n'.encode('ascii'))
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_name}"\r\n'.encode('utf-8'))
        chunks.append(f'Content-Type: {content_type}\r\n\r\n'.encode('ascii'))
        chunks.append(content)
        chunks.append(b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode('ascii'))
    return b''.join(chunks), f'multipart/form-data; boundary={boundary}'


class ApiClient:
    """Cookie-aware JSON client. Same-origin write guard only fires when Origin is sent."""

    def __init__(self, base_url: str, timeout: float = REQUEST_TIMEOUT_SECONDS):
        self.base = base_url.rstrip('/')
        self.timeout = timeout
        self.jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))
        self.user = None

    # -- low level ---------------------------------------------------------
    def request(self, method: str, path: str, *, json_body=None, body: bytes | None = None,
                headers: dict | None = None) -> ApiResponse:
        url = self.base + path
        send_headers = {'Accept': 'application/json, text/csv, text/markdown, */*',
                        'User-Agent': 'ece-qa/1.0'}
        payload = body
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False).encode('utf-8')
            send_headers['Content-Type'] = 'application/json; charset=utf-8'
        if headers:
            send_headers.update(headers)
        req = Request(url, data=payload, headers=send_headers, method=method.upper())
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return ApiResponse(resp.status, resp.headers, resp.read(), url)
        except HTTPError as exc:
            return ApiResponse(exc.code, exc.headers, exc.read(), url)

    def get(self, path: str, params: dict | None = None, *, headers=None) -> ApiResponse:
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            path = f'{path}?{urlencode(clean)}'
        return self.request('GET', path, headers=headers)

    def post(self, path: str, json_body=None, *, headers=None) -> ApiResponse:
        return self.request('POST', path, json_body=json_body, headers=headers)

    def put(self, path: str, json_body=None) -> ApiResponse:
        return self.request('PUT', path, json_body=json_body)

    def patch(self, path: str, json_body=None) -> ApiResponse:
        return self.request('PATCH', path, json_body=json_body)

    def upload(self, path: str, filename: str, content: bytes,
               content_type: str = 'application/octet-stream', fields: dict | None = None) -> ApiResponse:
        body, ctype = multipart_body(fields or {}, [('file', filename, content, content_type)])
        return self.request('POST', path, body=body, headers={'Content-Type': ctype})

    # -- convenience -------------------------------------------------------
    def login(self, username: str = ADMIN_USER, password: str = ADMIN_PASSWORD) -> ApiResponse:
        response = self.post('/api/auth/login', {'username': username, 'password': password})
        if response.status == 200:
            self.user = (response.json().get('user') or {})
        return response

    def logout(self) -> ApiResponse:
        response = self.post('/api/auth/logout', {})
        self.user = None
        return response


# ------------------------------------------------------------------ server process


class ServerProcess:
    """Owns one isolated ``python run.py`` subprocess and its temporary runtime."""

    def __init__(self, label: str, extra_env: dict | None = None):
        self.label = label
        self.extra_env = dict(extra_env or {})
        self.dir = TMP_ROOT / f'{self._slug(label)}-{secrets.token_hex(4)}'
        self.runtime_dir = self.dir / 'runtime'
        self.original_db = self.runtime_dir / 'retail.sqlite3'
        self.app_db = self.runtime_dir / 'app.sqlite3'
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self.log_path = self.dir / 'server.log'
        self._log_handle = None
        self.base = ''

    @staticmethod
    def _slug(label: str) -> str:
        keep = [c if (c.isalnum() or c in '-_.') else '_' for c in label]
        return ''.join(keep)[-60:] or 'server'

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> 'ServerProcess':
        if not OFFICIAL_DB.is_file():
            raise RuntimeError(f'缺少原始分析缓存：{OFFICIAL_DB}')
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OFFICIAL_DB, self.original_db)
        manifest_side = OFFICIAL_DB.parent / 'data_manifest.json'
        if manifest_side.is_file():
            shutil.copy2(manifest_side, self.runtime_dir / 'data_manifest.json')
        self.port = free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        env = os.environ.copy()
        for key in [k for k in env if k.startswith('ECE_')]:
            env.pop(key, None)
        env.update({
            'ECE_RUNTIME_DIR': str(self.runtime_dir),
            'ECE_ORIGINAL_DB': str(self.original_db),
            'ECE_APP_DB': str(self.app_db),
            'ECE_ADMIN_PASSWORD': ADMIN_PASSWORD,
            'ECE_OPERATOR_PASSWORD': OPERATOR_PASSWORD,
            'ECE_SECRET_KEY': 'qa-api-test-fixed-secret-key-0123456789',
            'ECE_HOST': '127.0.0.1',
            'ECE_SESSION_TTL_HOURS': '12',
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUTF8': '1',
        })
        env.update({k: str(v) for k, v in self.extra_env.items()})
        self._log_handle = self.log_path.open('wb')
        self.proc = subprocess.Popen(
            [sys.executable, 'run.py', '--port', str(self.port)],
            cwd=str(SYSTEM_ROOT / 'backend'), env=env, stdout=self._log_handle,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise
        return self

    def _wait_ready(self, timeout: float = READY_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f'服务进程提前退出（code={self.proc.returncode}）：\n{self.log_tail()}')
            try:
                with urlopen(self.base + '/api/health', timeout=5) as resp:
                    resp.read()
                    return
            except HTTPError:
                # The server is listening; the health route itself may be missing while
                # a module is still being implemented. Individual tests then fail honestly.
                return
            except (URLError, ConnectionError, OSError):
                time.sleep(0.25)
        raise RuntimeError(f'服务在 {timeout:.0f}s 内未就绪：\n{self.log_tail()}')

    def log_tail(self, lines: int = 40) -> str:
        try:
            text = self.log_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return '<no server log>'
        return '\n'.join(text.splitlines()[-lines:])

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                    pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:  # pragma: no cover - defensive
                pass
            self._log_handle = None
        if not os.environ.get('QA_KEEP_TMP'):
            # Keep the small server log for debugging, drop the 285 MB database copy.
            for path in (self.original_db, self.runtime_dir / 'data_manifest.json'):
                try:
                    path.unlink()
                except OSError:
                    pass

    # -- route introspection ----------------------------------------------
    def dump_routes(self) -> list[list[str]]:
        """Import ``app.main`` in a throwaway subprocess and list real routes.

        This compares ``/openapi.json`` with the routes that FastAPI actually registered
        instead of trusting the contract document alone.
        """
        snippet = (
            'import json\n'
            'from app.main import app\n'
            'print("QA_ROUTES=" + json.dumps([\n'
            '    [getattr(r, "path", ""), sorted(getattr(r, "methods", None) or [])]\n'
            '    for r in app.routes], ensure_ascii=False))\n'
        )
        env = os.environ.copy()
        for key in [k for k in env if k.startswith('ECE_')]:
            env.pop(key, None)
        env.update({
            'ECE_RUNTIME_DIR': str(self.runtime_dir),
            'ECE_ORIGINAL_DB': str(self.original_db),
            'ECE_APP_DB': str(self.app_db),
            'ECE_ADMIN_PASSWORD': ADMIN_PASSWORD,
            'ECE_OPERATOR_PASSWORD': OPERATOR_PASSWORD,
            'ECE_SECRET_KEY': 'qa-api-test-fixed-secret-key-0123456789',
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUTF8': '1',
        })
        env.update({k: str(v) for k, v in self.extra_env.items()})
        completed = subprocess.run(
            [sys.executable, '-X', 'utf8', '-c', snippet],
            cwd=str(SYSTEM_ROOT / 'backend'), env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180,
        )
        for line in completed.stdout.splitlines():
            if line.startswith('QA_ROUTES='):
                return json.loads(line[len('QA_ROUTES='):])
        raise AssertionError(
            f'无法读取实际路由（code={completed.returncode}）：\n'
            f'{completed.stdout[-1000:]}\n{completed.stderr[-2000:]}')


# ------------------------------------------------------------------ base test case


class ApiTestCase(unittest.TestCase):
    """Base class: one isolated live server for the whole test class."""

    extra_env: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.server = ServerProcess(f'{cls.__module__}.{cls.__name__}', cls.extra_env).start()
        cls.base = cls.server.base

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    # -- client factories --------------------------------------------------
    def new_client(self, login: str | None = None) -> ApiClient:
        client = ApiClient(self.base)
        if login == 'admin':
            response = client.login(ADMIN_USER, ADMIN_PASSWORD)
            self.assertEqual(response.status, 200, f'管理员登录失败：{response.text()[:300]}')
        elif login == 'operator':
            response = client.login(OPERATOR_USER, OPERATOR_PASSWORD)
            self.assertEqual(response.status, 200, f'运营账号登录失败：{response.text()[:300]}')
        return client

    def admin_client(self) -> ApiClient:
        return self.new_client('admin')

    def operator_client(self) -> ApiClient:
        return self.new_client('operator')

    # -- assertions --------------------------------------------------------
    def expect_json(self, response: ApiResponse, status: int = 200) -> dict:
        self.assertEqual(
            response.status, status,
            f'{response.url} 期望 HTTP {status}，实际 {response.status}：{response.text()[:500]}')
        return response.json()

    def expect_error(self, response: ApiResponse, status: int, code: str | None = None) -> dict:
        payload = self.expect_json(response, status)
        self.assertIn('error', payload, f'错误体缺少 error：{payload}')
        self.assertIsInstance(payload['error'], str)
        self.assertTrue(payload['error'].strip(), '错误信息不能为空')
        if code is not None:
            self.assertEqual(payload.get('code'), code,
                             f'错误码期望 {code}，实际 {payload.get("code")}：{payload}')
        return payload

    # -- polling -----------------------------------------------------------
    def wait_import_job(self, client: ApiClient, job_id: str,
                        timeout: float = JOB_TIMEOUT_SECONDS) -> dict:
        return self._wait_job(client, f'/api/imports/jobs/{job_id}', timeout)

    def wait_job(self, client: ApiClient, job_id: str,
                 timeout: float = JOB_TIMEOUT_SECONDS) -> dict:
        return self._wait_job(client, f'/api/jobs/{job_id}', timeout)

    def _wait_job(self, client: ApiClient, path: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        terminal = {'succeeded', 'failed', 'duplicate'}
        last = None
        while time.monotonic() < deadline:
            response = client.get(path)
            if response.status != 200:
                self.fail(f'轮询 {path} 返回 {response.status}：{response.text()[:400]}')
            last = response.json()
            status = (last.get('job') or {}).get('status')
            if status in terminal:
                return last
            time.sleep(0.4)
        self.fail(f'任务 {path} 在 {timeout:.0f}s 内未结束，最后状态：{last}')

    # -- convenience -------------------------------------------------------
    def dashboard(self, client: ApiClient, **params) -> dict:
        return self.expect_json(client.get('/api/dashboard', params), 200)

    def active_batch(self, client: ApiClient) -> dict:
        return self.expect_json(client.get('/api/imports/batches'), 200)


# --------------------------------------------------------------------- misc utils


PARAM_RE = re.compile(r'\{[^}]+\}')


def normalise_path(path: str) -> str:
    """Normalise path parameters: ``/api/orders/{order_key}`` -> ``/api/orders/{}``."""
    return PARAM_RE.sub('{}', path or '')


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def official_file_state() -> dict:
    """Hash/size of the official runtime databases (integrity evidence)."""
    state = {}
    for path in (OFFICIAL_DB, SYSTEM_ROOT / 'runtime' / 'app.sqlite3'):
        if path.is_file():
            stat = path.stat()
            state[str(path)] = {'sha256': _sha256_file(path), 'size': stat.st_size}
    return state


def environment_record() -> dict:
    """Environment record attached to the acceptance JSON and run logs."""
    record = {'python': sys.version.split()[0], 'platform': platform.platform(),
              'implementation': platform.python_implementation(),
              'executable': sys.executable}
    try:  # adds the vendored ``vendor/python-packages`` directory to sys.path on this machine
        if str(SYSTEM_ROOT / 'backend') not in sys.path:
            sys.path.insert(0, str(SYSTEM_ROOT / 'backend'))
        import app  # noqa: F401
    except Exception:  # noqa: BLE001 - report what is importable
        pass
    for name in ('pandas', 'numpy', 'lightgbm', 'openpyxl', 'fastapi', 'uvicorn'):
        try:
            module = __import__(name)
            record[name] = getattr(module, '__version__', 'unknown')
        except Exception:  # noqa: BLE001 - optional packages are simply reported missing
            record[name] = '<missing>'
    return record


def jdump(value, limit: int = 1200) -> str:
    """Compact JSON-ish dump used in assertion messages."""
    text = json.dumps(value, ensure_ascii=False, indent=None, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


def extract_numbers(text: str) -> list[float]:
    """Extract numeric tokens from free text (thousand separators handled)."""
    import re

    values: list[float] = []
    for token in re.findall(r'-?\d[\d,]*(?:\.\d+)?', text):
        cleaned = token.replace(',', '')
        try:
            values.append(float(cleaned))
        except ValueError:  # pragma: no cover - defensive
            continue
    return values


def flatten_text(value) -> str:
    """Flatten any JSON structure into one string (keys are ignored)."""
    parts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif node is None:
            pass
        else:
            parts.append(str(node))

    walk(value)
    return '\n'.join(parts)
