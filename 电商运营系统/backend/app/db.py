"""Database access layer for application state: users, sessions, batches, jobs,
inventory, AI runs and briefs.

Two interchangeable backends are supported and selected by ``ECE_DB_BACKEND``
(``app.core.config.DB_BACKEND``):

* ``sqlite`` (default, tests / fallback) — the original ``runtime/app.sqlite3`` file;
* ``mysql`` — the project-local MySQL 8 instance (``scripts/mysql_start.ps1``), where
  ``runtime/app.sqlite3`` becomes the ``ece_app`` database.

The dialect layer keeps every caller unchanged: SQL is written with ``?``
placeholders, rows support ``row['column']`` (and positional ``row[0]``), and SQLite
errors are re-raised for MySQL so existing ``except sqlite3.IntegrityError`` branches
keep working. Only this module (plus ``app.services.data`` for the analytical databases)
knows which backend is active.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','operator')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS batches(
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('original','upload')),
    status TEXT NOT NULL DEFAULT 'ready',
    db_path TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    source_sha256 TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    published_at TEXT,
    error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_batches_active ON batches(is_active) WHERE is_active=1;
CREATE TABLE IF NOT EXISTS import_jobs(
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    batch_id TEXT,
    report_json TEXT NOT NULL DEFAULT '{}',
    issues_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('forecast','backtest')),
    status TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    batch_id TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    elapsed_seconds REAL
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS inventory_scenarios(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_items(
    scenario_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    initial_qty REAL NOT NULL DEFAULT 0,
    lead_time_days INTEGER NOT NULL DEFAULT 7,
    safety_stock REAL NOT NULL DEFAULT 0,
    daily_demand REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(scenario_id, stock_code)
);
CREATE TABLE IF NOT EXISTS inventory_in_transit(
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    qty REAL NOT NULL CHECK(qty>0),
    expected_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_transit',
    created_at TEXT NOT NULL,
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS inventory_movements(
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('in','out')),
    qty REAL NOT NULL CHECK(qty>0),
    balance_after REAL NOT NULL,
    idempotency_key TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_idem ON inventory_movements(scenario_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_inventory_movements_item ON inventory_movements(scenario_id, stock_code, created_at);
CREATE TABLE IF NOT EXISTS ai_runs(
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    answer_json TEXT NOT NULL DEFAULT '{}',
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    markdown TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# MySQL 8 mirror of SCHEMA. Differences are dialect-only and documented inline:
# * AUTOINCREMENT -> AUTO_INCREMENT, TEXT keys -> VARCHAR(n), TEXT defaults and partial
#   indexes are not supported by MySQL, so they become VARCHAR/MEDIUMTEXT columns and a
#   generated-column unique key (``active_key``) respectively;
# * ``rowid`` is materialised as an AUTO_INCREMENT surrogate column because
#   ``api/services/inventory.py`` orders movements/in-transit rows by ``rowid``;
# * several indexes are declared as standalone ``CREATE INDEX`` statements so
#   ``executescript`` exercises the "index already exists" tolerance on re-init.
SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS users(
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL DEFAULT '',
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    active TINYINT NOT NULL DEFAULT 1,
    must_change_password TINYINT NOT NULL DEFAULT 0,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id),
    UNIQUE KEY ux_users_username(username),
    CONSTRAINT ck_users_role CHECK(role IN ('admin','operator'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS sessions(
    token_hash VARCHAR(128) NOT NULL,
    user_id BIGINT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    revoked TINYINT NOT NULL DEFAULT 0,
    user_agent VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY(token_hash),
    KEY ix_sessions_user(user_id),
    CONSTRAINT fk_sessions_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS batches(
    id VARCHAR(64) NOT NULL,
    label VARCHAR(255) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    db_path VARCHAR(255) NOT NULL,
    source_file VARCHAR(255) NOT NULL DEFAULT '',
    source_sha256 VARCHAR(64) NOT NULL DEFAULT '',
    policy_version VARCHAR(128) NOT NULL DEFAULT '',
    manifest_json MEDIUMTEXT NULL,
    is_active TINYINT NOT NULL DEFAULT 0,
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    published_at VARCHAR(40) NULL,
    error TEXT NULL,
    -- Emulates SQLite's partial unique index: at most one row with is_active=1.
    active_key VARCHAR(64) GENERATED ALWAYS AS (CASE WHEN is_active=1 THEN id ELSE NULL END) STORED,
    PRIMARY KEY(id),
    UNIQUE KEY ux_batches_active(active_key),
    CONSTRAINT ck_batches_kind CHECK(kind IN ('original','upload'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS import_jobs(
    id VARCHAR(64) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    source_sha256 VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    batch_id VARCHAR(64) NULL,
    report_json MEDIUMTEXT NULL,
    issues_json MEDIUMTEXT NULL,
    error TEXT NULL,
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    finished_at VARCHAR(40) NULL,
    PRIMARY KEY(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS jobs(
    id VARCHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    params_json MEDIUMTEXT NOT NULL,
    result_json MEDIUMTEXT NULL,
    error TEXT NULL,
    batch_id VARCHAR(64) NOT NULL,
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    started_at VARCHAR(40) NULL,
    finished_at VARCHAR(40) NULL,
    elapsed_seconds DOUBLE NULL,
    PRIMARY KEY(id),
    CONSTRAINT ck_jobs_kind CHECK(kind IN ('forecast','backtest'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS inventory_scenarios(
    id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    note VARCHAR(1000) NOT NULL DEFAULT '',
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS inventory_items(
    scenario_id VARCHAR(64) NOT NULL,
    stock_code VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL DEFAULT '',
    initial_qty DOUBLE NOT NULL DEFAULT 0,
    lead_time_days INT NOT NULL DEFAULT 7,
    safety_stock DOUBLE NOT NULL DEFAULT 0,
    daily_demand DOUBLE NOT NULL DEFAULT 0,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(scenario_id, stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS inventory_in_transit(
    rowid BIGINT NOT NULL AUTO_INCREMENT,
    id VARCHAR(64) NOT NULL,
    scenario_id VARCHAR(64) NOT NULL,
    stock_code VARCHAR(64) NOT NULL,
    qty DOUBLE NOT NULL,
    expected_date VARCHAR(40) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'in_transit',
    created_at VARCHAR(40) NOT NULL,
    created_by BIGINT NULL,
    PRIMARY KEY(id),
    UNIQUE KEY ux_in_transit_rowid(rowid),
    CONSTRAINT ck_in_transit_qty CHECK(qty>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS inventory_movements(
    rowid BIGINT NOT NULL AUTO_INCREMENT,
    id VARCHAR(64) NOT NULL,
    scenario_id VARCHAR(64) NOT NULL,
    stock_code VARCHAR(64) NOT NULL,
    kind VARCHAR(8) NOT NULL,
    qty DOUBLE NOT NULL,
    balance_after DOUBLE NOT NULL,
    idempotency_key VARCHAR(128) NULL,
    note VARCHAR(1000) NOT NULL DEFAULT '',
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id),
    UNIQUE KEY ux_inventory_rowid(rowid),
    -- MySQL treats NULLs as distinct, so this is equivalent to SQLite's partial index.
    UNIQUE KEY ux_inventory_idem(scenario_id, idempotency_key),
    CONSTRAINT ck_movements_kind CHECK(kind IN ('in','out')),
    CONSTRAINT ck_movements_qty CHECK(qty>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX ix_inventory_movements_item ON inventory_movements(scenario_id, stock_code, created_at);
CREATE TABLE IF NOT EXISTS ai_runs(
    id VARCHAR(64) NOT NULL,
    question TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    provider VARCHAR(64) NOT NULL DEFAULT '',
    answer_json MEDIUMTEXT NULL,
    tool_calls_json MEDIUMTEXT NULL,
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS briefs(
    id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    scope_json MEDIUMTEXT NULL,
    markdown MEDIUMTEXT NOT NULL,
    created_by BIGINT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS settings(
    `key` VARCHAR(128) NOT NULL,
    value MEDIUMTEXT NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE IF NOT EXISTS audit_log(
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NULL,
    action VARCHAR(64) NOT NULL,
    detail VARCHAR(2000) NOT NULL DEFAULT '',
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
"""

MYSQL_PREFIX = 'mysql:'
_NAME_RE = re.compile(r'^[A-Za-z0-9_]{1,64}$')
_KNOWN_DATABASES: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# --------------------------------------------------------------------------- backend


def db_backend() -> str:
    return 'mysql' if str(config.DB_BACKEND).strip().lower() == 'mysql' else 'sqlite'


def is_mysql() -> bool:
    """True when the applications state database is MySQL."""
    return db_backend() == 'mysql'


class MySQLTarget(str):
    """A ``mysql:<database>`` store target (batch path) with a Path-like probe.

    Batch rows store their target as text. In SQLite mode that is a relative file path;
    in MySQL mode it is ``mysql:<database>``. ``is_file()`` keeps ``api/deps.store_for``
    and ``api/services/inventory._batch_names`` unchanged while still answering
    "does this snapshot exist?".
    """

    def __new__(cls, database: str):
        return super().__new__(cls, MYSQL_PREFIX + str(database))

    @property
    def database(self) -> str:
        return self[len(MYSQL_PREFIX):]

    def is_file(self) -> bool:  # noqa: D401 - mirrors pathlib.Path.is_file
        return mysql_database_exists(self.database)

    def exists(self) -> bool:
        return mysql_database_exists(self.database)

    def stat(self):
        """Path-like stat so ``api/routers/system._data_ready`` can key its cache.

        A database has no file mtime/size; the readiness probe only uses them as a cache
        key, so stable zeroes are enough (the probe still opens a real Store).
        """
        return _MySQLStat()


class _MySQLStat:
    st_mtime_ns = 0
    st_size = 0
    st_mode = 0o40755

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return 'MySQLStat()'


def is_mysql_target(value) -> bool:
    return isinstance(value, str) and value.startswith(MYSQL_PREFIX)


def check_database_name(name: str) -> str:
    name = str(name or '').strip()
    if not _NAME_RE.match(name):
        raise ValueError(f'非法的 MySQL 数据库名：{name!r}')
    return name


def batch_database(batch_id: str) -> str:
    """Database name that stores one uploaded data batch (``ece_batch_<id>``)."""
    suffix = re.sub(r'[^A-Za-z0-9_]', '_', str(batch_id))
    return check_database_name(f'ece_batch_{suffix}'[:64])


def resolve_db_path(value) -> Path:
    """Resolve a stored batch path; relative values are relative to APP_ROOT.

    MySQL targets are returned unchanged: they have no filesystem path but answer
    ``is_file()``/``exists()`` like one.
    """
    if is_mysql_target(value):
        return MySQLTarget(str(value)[len(MYSQL_PREFIX):])
    path = Path(str(value))
    return path if path.is_absolute() else config.APP_ROOT / path


def stored_db_path(path: Path) -> str:
    """Store paths relative to APP_ROOT when possible (portable project copy)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(config.APP_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def stored_target(value) -> str:
    """Value written to ``batches.db_path`` for either backend."""
    if is_mysql_target(value):
        return str(value)
    return stored_db_path(Path(value))


def original_target():
    """Store target of the original data snapshot (``batch_id='original'``)."""
    return MySQLTarget(config.MYSQL_ORIGINAL_DB) if is_mysql() else Path(config.ORIGINAL_DB)


def target_ready(value) -> bool:
    if is_mysql_target(value):
        return mysql_database_exists(MySQLTarget(str(value)[len(MYSQL_PREFIX):]).database)
    return Path(str(value)).is_file()


# --------------------------------------------------------------------------- mysql helpers


def _mysql_settings() -> dict:
    if not config.MYSQL_PASSWORD:
        raise RuntimeError(
            'MySQL 口令未配置：请先运行 scripts\\mysql\\mysql_init.ps1 生成 config\\local\\mysql.env，'
            '或设置 ECE_MYSQL_PASSWORD')
    return {
        'host': config.MYSQL_HOST,
        'port': int(config.MYSQL_PORT),
        'user': config.MYSQL_USER,
        'password': config.MYSQL_PASSWORD,
        'charset': 'utf8mb4',
        'connect_timeout': 10,
        'max_allowed_packet': 256 * 1024 * 1024,
    }


def _pymysql():
    try:  # vendored in vendor/python-packages for the restricted environment
        import pymysql  # noqa: PLC0415 - optional dependency resolved at call time
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise RuntimeError(
            'MySQL 模式需要 PyMySQL：pip install pymysql（本机可用 vendor/python-packages 内的副本）') from exc
    return pymysql


def mysql_raw_connect(database: str | None = None, *, autocommit: bool = True):
    """Plain PyMySQL connection for scripts/admin work (no dialect translation)."""
    pymysql = _pymysql()
    settings = _mysql_settings()
    settings['database'] = database if database else None
    if database is None:
        settings.pop('database')
    conn = pymysql.connect(autocommit=autocommit, **settings)
    return conn


def mysql_database_exists(database: str) -> bool:
    database = str(database)
    if database in _KNOWN_DATABASES:
        return True
    pymysql = _pymysql()
    conn = mysql_raw_connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW DATABASES LIKE %s', (database.replace('_', '\\_'),))
            found = cur.fetchone() is not None
    finally:
        conn.close()
    if found:
        _KNOWN_DATABASES.add(database)
    return found


def ensure_database(database: str) -> str:
    database = check_database_name(database)
    if database in _KNOWN_DATABASES:
        return database
    conn = mysql_raw_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'CREATE DATABASE IF NOT EXISTS `{database}` '
                'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
    finally:
        conn.close()
    _KNOWN_DATABASES.add(database)
    return database


def drop_database(database: str) -> None:
    database = check_database_name(database)
    conn = mysql_raw_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS `{database}`')
    finally:
        conn.close()
    _KNOWN_DATABASES.discard(database)


def list_databases(prefix: str = '') -> list[str]:
    conn = mysql_raw_connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW DATABASES')
            names = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return sorted(name for name in names if name.startswith(prefix))


# --------------------------------------------------------------------------- dialect


class Row(dict):
    """Row that supports ``row['col']`` like ``sqlite3.Row`` (plus ``row[0]``)."""

    __slots__ = ('_values',)

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self):
        return list(super().keys())

    def __repr__(self) -> str:
        return f'Row({dict(self)!r})'


def _adapt(value):
    """Convert numpy/pandas scalars to plain Python and NaN to NULL."""
    if value is None or isinstance(value, (str, bytes, bool, int)):
        return value
    if isinstance(value, float):
        return None if value != value else value  # NaN -> NULL
    item = getattr(value, 'item', None)
    if callable(item):
        try:
            return _adapt(item())
        except (ValueError, TypeError):
            return value
    return value


adapt_value = _adapt  # public alias used by app.services.data when bulk-loading MySQL


def _adapt_params(params):
    if params is None:
        return None
    if isinstance(params, (list, tuple)):
        return tuple(_adapt(value) for value in params)
    return tuple(_adapt(value) for value in params)


_MYSQL_FIXUPS = (
    # SQLite escapes LIKE wildcards with a backslash; MySQL needs it doubled in SQL text.
    ("escape '\\'", "escape '\\\\'"),
)
# ``LINES`` is reserved in MySQL (LOAD DATA ... LINES TERMINATED BY) but the prototype's
# analytical SQL uses it as a column alias. Quote it instead of touching every caller.
_MYSQL_RESERVED_ALIASES = (
    (re.compile(r'\bcount\(\*\)\s+lines\b', re.IGNORECASE), 'count(*) `lines`'),
)


def to_mysql_sql(sql: str) -> str:
    """Translate SQLite-style SQL to MySQL: ``?`` -> ``%s``, literal ``%`` escaped.

    Placeholders are only replaced outside quoted literals so a ``?`` inside a JSON
    blob or a comment is left alone. Backslashes are literal in SQLite SQL, so they do
    not escape the closing quote while scanning.
    """
    out: list[str] = []
    quote = ''
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            out.append(char)
            if char == quote:
                quote = ''
        elif char in ("'", '"', '`'):
            quote = char
            out.append(char)
        elif char == '?':
            out.append('%s')
        elif char == '%':
            out.append('%%')
        else:
            out.append(char)
        index += 1
    translated = ''.join(out)
    for old, new in _MYSQL_FIXUPS:
        translated = translated.replace(old, new)
    for pattern, replacement in _MYSQL_RESERVED_ALIASES:
        translated = pattern.sub(replacement, translated)
    return translated


def _strip_sql_comments(script: str) -> str:
    """Remove ``--`` and ``/* */`` comments outside quoted literals.

    Needed before splitting a script on semicolons: an apostrophe inside a comment
    (e.g. "SQLite's") would otherwise look like the start of a string literal and
    swallow the next statement terminator.
    """
    out: list[str] = []
    quote = ''
    index = 0
    while index < len(script):
        char = script[index]
        if quote:
            out.append(char)
            if char == quote:
                quote = ''
            index += 1
            continue
        if char in ("'", '"', '`'):
            quote = char
            out.append(char)
            index += 1
            continue
        if char == '-' and index + 1 < len(script) and script[index + 1] == '-':
            while index < len(script) and script[index] != '\n':
                index += 1
            continue
        if char == '/' and index + 1 < len(script) and script[index + 1] == '*':
            index += 2
            while index + 1 < len(script) and not (script[index] == '*' and script[index + 1] == '/'):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def split_statements(script: str) -> list[str]:
    """Split an SQL script on top-level semicolons (quotes respected, comments dropped)."""
    script = _strip_sql_comments(str(script))
    statements: list[str] = []
    buffer: list[str] = []
    quote = ''
    index = 0
    while index < len(script):
        char = script[index]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ''
        elif char in ("'", '"', '`'):
            quote = char
            buffer.append(char)
        elif char == ';':
            statement = ''.join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(char)
        index += 1
    tail = ''.join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


_INDEX_ERRNOS = {1061, 1050, 1060, 1091, 1826}  # duplicate key/table/column, drop missing
_CREATE_INDEX_PREFIXES = ('create index', 'create unique index', 'create fulltext index',
                          'alter table')


def _is_index_statement(sql: str) -> bool:
    head = sql.lstrip().lower()
    return head.startswith(_CREATE_INDEX_PREFIXES)


class _EmptyCursor:
    """Cursor returned for tolerated DDL (index already exists)."""

    rowcount = 0
    lastrowid = None
    description = None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())

    def close(self) -> None:
        pass


class MySQLCursor:
    """``sqlite3.Cursor``-compatible wrapper around a PyMySQL cursor."""

    def __init__(self, raw):
        self.raw = raw
        self._columns = [column[0] for column in raw.description] if raw.description else []

    def _wrap(self, values):
        if values is None:
            return None
        return Row(self._columns, values)

    def fetchone(self):
        return self._wrap(self.raw.fetchone())

    def fetchall(self):
        return [self._wrap(values) for values in self.raw.fetchall()]

    def __iter__(self):
        for values in self.raw:
            yield self._wrap(values)

    @property
    def rowcount(self):
        return self.raw.rowcount

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    @property
    def description(self):
        return self.raw.description

    def close(self) -> None:
        self.raw.close()


class MySQLConnection:
    """``sqlite3.Connection``-compatible wrapper (execute/executemany/executescript)."""

    backend = 'mysql'

    def __init__(self, database: str, conn):
        self.database = database
        self._conn = conn

    # -- statement execution -------------------------------------------------
    def _translate(self, sql: str) -> str:
        text = str(sql).strip()
        if text.upper() == 'BEGIN IMMEDIATE':
            # SQLite's "take the write lock now" has no direct MySQL equivalent;
            # autocommit is off, so an explicit transaction start is equivalent here.
            return 'START TRANSACTION'
        return to_mysql_sql(text)

    def execute(self, sql: str, params=()):
        text = self._translate(sql)
        try:
            cursor = self._conn.cursor()
            if params is None or (isinstance(params, (list, tuple)) and not params):
                cursor.execute(text)
            else:
                cursor.execute(text, _adapt_params(params))
        except Exception as exc:  # noqa: BLE001 - re-raised as a sqlite3 error below
            if _is_index_statement(text) and _errno(exc) in _INDEX_ERRNOS:
                return _EmptyCursor()
            raise _translate_error(exc) from None
        return MySQLCursor(cursor)

    def executemany(self, sql: str, seq):
        text = self._translate(sql)
        rows = [tuple(_adapt(value) for value in row) for row in seq]
        if not rows:
            return _EmptyCursor()
        try:
            cursor = self._conn.cursor()
            cursor.executemany(text, rows)
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc) from None
        return MySQLCursor(cursor)

    def executescript(self, script: str):
        for statement in split_statements(str(script)):
            self.execute(statement)

    # -- transaction control -------------------------------------------------
    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:  # noqa: BLE001 - rollback must never mask the real error
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def cursor(self):
        return MySQLCursor(self._conn.cursor())

    # context-manager parity with ``with sqlite3.connect(...) as cx``
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def _errno(exc) -> int:
    args = getattr(exc, 'args', ())
    return args[0] if args and isinstance(args[0], int) else 0


def _translate_error(exc):
    """Re-raise a PyMySQL error as the sqlite3 error the callers already catch."""
    pymysql = _pymysql()
    errno = _errno(exc)
    message = str(exc.args[1]) if len(getattr(exc, 'args', ())) > 1 else str(exc)
    if isinstance(exc, pymysql.err.IntegrityError) or errno in (1048, 1062, 1364, 1451, 1452, 3819):
        return sqlite3.IntegrityError(f'[MySQL {errno}] {message}')
    if isinstance(exc, pymysql.err.OperationalError) or errno in (1040, 1044, 1045, 1049, 1205, 1213):
        return sqlite3.OperationalError(f'[MySQL {errno}] {message}')
    if isinstance(exc, pymysql.err.ProgrammingError) or errno in (1064, 1146, 1054):
        return sqlite3.OperationalError(f'[MySQL {errno}] {message}')
    if isinstance(exc, pymysql.err.InterfaceError) or errno in (2003, 2006, 2013):
        return sqlite3.OperationalError(f'[MySQL {errno}] {message}')
    return sqlite3.DatabaseError(f'[MySQL {errno}] {message}')


def _open_mysql(database: str) -> MySQLConnection:
    pymysql = _pymysql()
    settings = _mysql_settings()
    conn = pymysql.connect(database=database, autocommit=False, **settings)
    connection = MySQLConnection(database, conn)
    # READ COMMITTED matches the single-writer SQLite prototype: a request that just
    # waited on a unique-key insert must see the winner's committed row.
    connection.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
    return connection


# --------------------------------------------------------------------------- connect


def _resolve_target(path, database=None):
    """Return ``('mysql', name)`` or ``('sqlite', Path)``."""
    if database:
        return 'mysql', check_database_name(database)
    if path is None:
        if is_mysql():
            return 'mysql', check_database_name(config.MYSQL_APP_DB)
        return 'sqlite', Path(config.APP_DB)
    if is_mysql_target(path):
        return 'mysql', check_database_name(str(path)[len(MYSQL_PREFIX):])
    text = str(path)
    if is_mysql() and isinstance(path, str) and ':' not in text and '\\' not in text \
            and '/' not in text and not text.endswith(('.sqlite3', '.db', '.sqlite')):
        return 'mysql', check_database_name(text)
    return 'sqlite', Path(path)


def resolve_target(path=None, database=None):
    return _resolve_target(path, database)


@contextmanager
def connect(path=None, database=None):
    """Yield a connection to the app state DB (default) or a batch database.

    ``path`` accepts a SQLite file path, a ``mysql:<database>`` target, or (MySQL
    backend only) a bare database name. ``database`` selects a MySQL database
    explicitly.
    """
    kind, target = _resolve_target(path, database)
    if kind == 'mysql':
        ensure_database(target)
        cx = _open_mysql(target)
        try:
            yield cx
            cx.commit()
        except Exception:
            cx.rollback()
            raise
        finally:
            cx.close()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(target, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys=ON')
    cx.execute('PRAGMA busy_timeout=10000')
    try:
        yield cx
        cx.commit()
    except Exception:
        cx.rollback()
        raise
    finally:
        cx.close()


# --------------------------------------------------------------------------- settings


def get_setting(key: str, default=None):
    with connect() as cx:
        # backticks work on both backends; ``key`` is reserved in MySQL
        row = cx.execute('select value from settings where `key`=?', (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except json.JSONDecodeError:
        return row['value']


def set_setting(key: str, value) -> None:
    with connect() as cx:
        if is_mysql():
            cx.execute('insert into settings(`key`,value,updated_at) values(?,?,?) '
                       'on duplicate key update value=values(value),updated_at=values(updated_at)',
                       (key, json.dumps(value, ensure_ascii=False), now_iso()))
        else:
            cx.execute('insert into settings(key,value,updated_at) values(?,?,?) '
                       'on conflict(key) do update set value=excluded.value,updated_at=excluded.updated_at',
                       (key, json.dumps(value, ensure_ascii=False), now_iso()))


def audit(user_id, action: str, detail='') -> None:
    with connect() as cx:
        cx.execute('insert into audit_log(user_id,action,detail,created_at) values(?,?,?,?)',
                   (user_id, action, str(detail)[:2000], now_iso()))


def init_app_db():
    """Create the state schema, register the original data batch and seed accounts."""
    config.ensure_dirs()
    with connect() as cx:
        cx.executescript(SCHEMA_MYSQL if is_mysql() else SCHEMA)
    from app.services import batches as batch_service  # local import avoids a cycle
    batch_service.ensure_original_batch()
    _seed_users()
    return MySQLTarget(config.MYSQL_APP_DB) if is_mysql() else config.APP_DB


def _seed_users() -> None:
    """Create the first administrator/operator once; no fixed public password."""
    import secrets as _secrets

    from app.core import security

    with connect() as cx:
        existing = cx.execute('select count(*) from users').fetchone()[0]
        if existing:
            return
        admin_name = (os.environ.get('ECE_ADMIN_USER') or 'admin').strip()
        operator_name = (os.environ.get('ECE_OPERATOR_USER') or 'operator').strip()
        admin_pw = os.environ.get('ECE_ADMIN_PASSWORD') or _secrets.token_urlsafe(12)
        operator_pw = os.environ.get('ECE_OPERATOR_PASSWORD') or _secrets.token_urlsafe(12)
        stamp = now_iso()
        cx.execute("insert into users(username,display_name,password_hash,role,active,must_change_password,created_at,updated_at) "
                   "values(?,?,?,'admin',1,1,?,?)",
                   (admin_name, '系统管理员', security.hash_password(admin_pw), stamp, stamp))
        cx.execute("insert into users(username,display_name,password_hash,role,active,must_change_password,created_at,updated_at) "
                   "values(?,?,?,'operator',1,1,?,?)",
                   (operator_name, '运营人员', security.hash_password(operator_pw), stamp, stamp))
    if not os.environ.get('ECE_ADMIN_PASSWORD'):
        import configparser
        accounts = configparser.ConfigParser()
        accounts['system'] = {'url': f'http://{config.HOST}:{config.PORT}/'}
        accounts['admin'] = {'username': admin_name, 'password': admin_pw}
        accounts['operator'] = {'username': operator_name, 'password': operator_pw}
        with config.INITIAL_ADMIN_FILE.open('w', encoding='utf-8') as output:
            output.write('# 本机登录信息；修改本文件不会重置数据库口令。\n')
            accounts.write(output)
