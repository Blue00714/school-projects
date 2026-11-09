"""Runtime configuration for the FastAPI prototype.

No secret is hard-coded. The session secret is generated once into
``runtime/app_secret.key``; the initial administrator password is generated once and
written to ``config/local/登录配置.ini`` (clearly marked as one-time setup material).
Values can be overridden with environment variables or ``config/local/app.env``.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3]
# Test runtimes use isolated configuration instead of loading production credentials.
_config_default = Path(os.environ['ECE_RUNTIME_DIR']) / 'config' if os.environ.get('ECE_RUNTIME_DIR') else APP_ROOT / 'config/local'
LOCAL_CONFIG_DIR = Path(os.environ.get('ECE_CONFIG_DIR', str(_config_default)))
if not LOCAL_CONFIG_DIR.is_absolute():
    LOCAL_CONFIG_DIR = APP_ROOT / LOCAL_CONFIG_DIR


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding variables already in the environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_dotenv() -> None:
    _load_env_file(LOCAL_CONFIG_DIR / 'app.env')


_load_dotenv()


def _path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name)
    if not raw:
        return default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else APP_ROOT / candidate


RUNTIME_DIR = _path('ECE_RUNTIME_DIR', APP_ROOT / 'runtime')
BATCH_DIR = RUNTIME_DIR / 'batches'
LOG_DIR = RUNTIME_DIR / 'logs'
TMP_DIR = RUNTIME_DIR / 'tmp'
FRONTEND_DIR = APP_ROOT / 'frontend'
FRONTEND_DIST = _path('ECE_FRONTEND_DIST', FRONTEND_DIR / 'dist')
ORIGINAL_DB = _path('ECE_ORIGINAL_DB', RUNTIME_DIR / 'retail.sqlite3')
APP_DB = _path('ECE_APP_DB', RUNTIME_DIR / 'app.sqlite3')
SECRET_FILE = RUNTIME_DIR / 'app_secret.key'
INITIAL_ADMIN_FILE = LOCAL_CONFIG_DIR / '登录配置.ini'
MYSQL_ENV_FILE = LOCAL_CONFIG_DIR / 'mysql.env'

# A portable local MySQL instance (scripts/mysql/mysql_init.ps1) writes config/local/mysql.env.
# When that file exists the application defaults to MySQL; tests keep SQLite unless
# ECE_DB_BACKEND is set explicitly.
if MYSQL_ENV_FILE.is_file():
    _load_env_file(MYSQL_ENV_FILE)
    os.environ.setdefault('ECE_DB_BACKEND', 'mysql')

DB_BACKEND = os.environ.get('ECE_DB_BACKEND', 'sqlite').strip().lower() or 'sqlite'
MYSQL_HOST = os.environ.get('ECE_MYSQL_HOST', '127.0.0.1')
MYSQL_PORT = int(os.environ.get('ECE_MYSQL_PORT', '3307'))
MYSQL_USER = os.environ.get('ECE_MYSQL_USER', 'ece')
MYSQL_PASSWORD = os.environ.get('ECE_MYSQL_PASSWORD', '')
MYSQL_APP_DB = os.environ.get('ECE_MYSQL_APP_DB', 'ece_app')
MYSQL_ORIGINAL_DB = os.environ.get('ECE_MYSQL_ORIGINAL_DB', 'ece_original')

COOKIE_NAME = 'ece_session'
SESSION_TTL_HOURS = float(os.environ.get('ECE_SESSION_TTL_HOURS', '12'))
PORT = int(os.environ.get('ECE_PORT', '8777'))
HOST = os.environ.get('ECE_HOST', '127.0.0.1')

AI_BASE_URL = os.environ.get('ECE_AI_BASE_URL', '').strip()
AI_API_KEY = os.environ.get('ECE_AI_API_KEY', '').strip()
AI_MODEL = os.environ.get('ECE_AI_MODEL', '').strip()
AI_TIMEOUT_SECONDS = float(os.environ.get('ECE_AI_TIMEOUT', '60'))

APP_VERSION = '0.2.0-prototype'
SERVICE_NAME = 'ecommerce-ops-fastapi'
SECRET_KEY_ENV = 'ECE_SECRET_KEY'


def ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def secret_key() -> bytes:
    """Session secret; generated once, never returned by an API."""
    ensure_dirs()
    env_value = os.environ.get(SECRET_KEY_ENV, '').strip()
    if env_value:
        return env_value.encode('utf-8')
    if SECRET_FILE.is_file():
        value = SECRET_FILE.read_bytes().strip()
        if value:
            return value
    value = secrets.token_hex(32).encode('ascii')
    SECRET_FILE.write_bytes(value)
    return value


def ai_configured() -> bool:
    return bool(AI_BASE_URL and AI_API_KEY and AI_MODEL)


def public_ai_config() -> dict:
    """Configuration safe to expose to the page: never includes the API key."""
    return {
        'configured': ai_configured(),
        'provider': 'openai-compatible' if AI_BASE_URL else '',
        'base_url': AI_BASE_URL,
        'model': AI_MODEL,
        'timeout_seconds': AI_TIMEOUT_SECONDS,
        'key_present': bool(AI_API_KEY),
    }
