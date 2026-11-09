"""Provision the local portable MySQL 8 instance used by the app (idempotent).

Run after ``scripts/mysql/mysql_init.ps1`` has extracted the binaries, written ``my.ini``,
initialized the data directory and started ``mysqld``. This script:

1. reads (or generates) the local ``ece`` password and writes ``config/local/mysql.env``;
2. creates the ``ece``@``127.0.0.1`` account with ``mysql_native_password`` (no extra
   cryptography dependency for PyMySQL) and the privileges local development needs;
3. creates the ``ece_app`` (application state) and ``ece_original`` (original data)
   databases;
4. verifies the new account can connect.

It is safe to re-run: the same password is reused and every step is ``IF NOT EXISTS``.

    python scripts/mysql_bootstrap.py [--port 3307] [--reset-password]
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYDEPS = ROOT / 'vendor/python-packages'
for candidate in (str(PYDEPS), str(ROOT / 'backend')):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

try:
    import pymysql
except ImportError:  # pragma: no cover - depends on the machine
    raise SystemExit('PyMySQL missing: pip install pymysql, or keep vendor/python-packages/pymysql in place')

ENV_FILE = ROOT / 'config/local/mysql.env'
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = '3307'
DEFAULT_USER = 'ece'
DEFAULT_APP_DB = 'ece_app'
DEFAULT_ORIGINAL_DB = 'ece_original'
ENV_KEYS = [
    'ECE_DB_BACKEND', 'ECE_MYSQL_HOST', 'ECE_MYSQL_PORT', 'ECE_MYSQL_USER',
    'ECE_MYSQL_PASSWORD', 'ECE_MYSQL_APP_DB', 'ECE_MYSQL_ORIGINAL_DB',
]


def read_env(path: Path) -> dict:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            text = line.strip()
            if not text or text.startswith('#') or '=' not in text:
                continue
            key, value = text.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# 本地便携 MySQL 实例的凭据（scripts/mysql/mysql_init.ps1 生成，勿提交版本库）',
        '# 本文件存在时 api/config.py 默认使用 MySQL；删除后自动回退 SQLite。',
    ]
    for key in ENV_KEYS:
        if key in values:
            lines.append(f'{key}={values[key]}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def connect(host: str, port: int, user: str, password: str, database=None):
    return pymysql.connect(host=host, port=port, user=user, password=password,
                           database=database, charset='utf8mb4', autocommit=True,
                           connect_timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description='Provision the local MySQL account/databases.')
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--user', default=None)
    parser.add_argument('--app-db', default=None)
    parser.add_argument('--original-db', default=None)
    parser.add_argument('--reset-password', action='store_true',
                        help='generate a new ece password even when config/local/mysql.env exists')
    args = parser.parse_args()

    env = read_env(ENV_FILE)
    host = args.host or env.get('ECE_MYSQL_HOST') or DEFAULT_HOST
    port = args.port or int(env.get('ECE_MYSQL_PORT') or DEFAULT_PORT)
    user = args.user or env.get('ECE_MYSQL_USER') or DEFAULT_USER
    app_db = args.app_db or env.get('ECE_MYSQL_APP_DB') or DEFAULT_APP_DB
    original_db = args.original_db or env.get('ECE_MYSQL_ORIGINAL_DB') or DEFAULT_ORIGINAL_DB
    password = '' if args.reset_password else env.get('ECE_MYSQL_PASSWORD', '')
    if not password:
        password = secrets.token_urlsafe(18)

    print(f'[bootstrap] target {host}:{port} user={user} databases={app_db},{original_db}')

    admin = None
    root_error = None
    try:  # a freshly initialized instance has root@localhost with an empty password
        admin = connect(host, port, 'root', '')
        print('[bootstrap] connected as root (empty password, first run)')
    except pymysql.err.MySQLError as exc:
        root_error = exc
    if admin is None:
        try:  # re-run: root password changed, use the existing ece account instead
            admin = connect(host, port, user, password)
            print('[bootstrap] connected as the existing ece account (root unavailable)')
        except pymysql.err.MySQLError as exc:
            raise SystemExit(
                f'cannot connect to MySQL at {host}:{port} as root or {user}: '
                f'{root_error or exc}. Is mysqld running? Start it with scripts\\mysql_start.ps1')

    statements = [
        f"CREATE USER IF NOT EXISTS '{user}'@'127.0.0.1' IDENTIFIED WITH mysql_native_password BY %s",
        f"ALTER USER '{user}'@'127.0.0.1' IDENTIFIED WITH mysql_native_password BY %s",
        f"CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED WITH mysql_native_password BY %s",
        f"ALTER USER '{user}'@'localhost' IDENTIFIED WITH mysql_native_password BY %s",
    ]
    with admin.cursor() as cur:
        for statement in statements:
            cur.execute(statement, (password,))
        for account in ("'{}'@'127.0.0.1'".format(user), f"'{user}'@'localhost'"):
            cur.execute(f'GRANT ALL PRIVILEGES ON *.* TO {account}')
        cur.execute('FLUSH PRIVILEGES')
        for name in (app_db, original_db):
            cur.execute(f'CREATE DATABASE IF NOT EXISTS `{name}` '
                        'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        cur.execute('SHOW DATABASES')
        databases = [row[0] for row in cur.fetchall()]
    admin.close()
    print(f'[bootstrap] grants applied; databases now: '
          f'{", ".join(name for name in databases if name in (app_db, original_db))}')

    values = {
        'ECE_DB_BACKEND': 'mysql',
        'ECE_MYSQL_HOST': host,
        'ECE_MYSQL_PORT': str(port),
        'ECE_MYSQL_USER': user,
        'ECE_MYSQL_PASSWORD': password,
        'ECE_MYSQL_APP_DB': app_db,
        'ECE_MYSQL_ORIGINAL_DB': original_db,
    }
    write_env(ENV_FILE, values)
    print(f'[bootstrap] credentials written to {ENV_FILE} (never commit this file)')

    check = connect(host, port, user, password, database=app_db)
    with check.cursor() as cur:
        cur.execute('select version(), current_user(), database()')
        version, current_user, database = cur.fetchone()
    check.close()
    print(f'[bootstrap] verified as {current_user} on {database}, server {version}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
