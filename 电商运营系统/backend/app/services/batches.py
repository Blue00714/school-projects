"""Data-batch (snapshot) registry.

Each batch is an independent analytical database:

* ``original`` – the existing read-only analytical cache ``runtime/retail.sqlite3``
  (MySQL mode: the ``ece_original`` database);
* ``upload``   – a database created under ``runtime/batches/<batch_id>/`` (MySQL mode:
  the ``ece_batch_<batch_id>`` database) by the import pipeline using the same
  preparation policy as the original data.

Forecast jobs bind the batch id that existed when they were created, so switching the
active batch later never changes a running or stored result.

``db_path`` stores a relative SQLite path in SQLite mode and ``mysql:<database>`` in
MySQL mode; ``app.db.resolve_db_path`` (and therefore ``app.api.deps.store_for``) accepts
both.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.core import config
from app import db

ORIGINAL_BATCH_ID = 'original'


def read_manifest(db_path) -> dict:
    """Manifest of a batch: side file -> SQLite metadata -> MySQL metadata."""
    if db.is_mysql_target(db_path):
        if not db.target_ready(db_path):
            return {}
        try:
            with db.connect(db_path) as cx:
                row = cx.execute("select value from metadata where `key`='manifest'").fetchone()
            return json.loads(row[0]) if row else {}
        except (sqlite3.Error, TypeError, json.JSONDecodeError):
            return {}
    db_path = Path(db_path)
    side = db_path.parent / 'data_manifest.json'
    if side.is_file():
        try:
            return json.loads(side.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        with db.connect(db_path) as cx:
            row = cx.execute("select value from metadata where `key`='manifest'").fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error, TypeError, json.JSONDecodeError):
        return {}


def _row(row) -> dict:
    if row is None:
        return {}
    data = dict(row)
    raw = data.pop('manifest_json', '{}') or '{}'
    try:
        data['manifest'] = json.loads(raw)
    except json.JSONDecodeError:
        data['manifest'] = {}
    return data


def get_batch(batch_id: str) -> dict:
    with db.connect() as cx:
        row = cx.execute('select * from batches where id=?', (batch_id,)).fetchone()
    return _row(row)


def active_batch() -> dict:
    with db.connect() as cx:
        row = cx.execute('select * from batches where is_active=1').fetchone()
    return _row(row)


def list_batches() -> list[dict]:
    with db.connect() as cx:
        rows = cx.execute('select * from batches order by is_active desc, created_at desc').fetchall()
    return [_row(row) for row in rows]


def ensure_original_batch() -> dict:
    """Register the existing analytical cache as the ``original`` batch once."""
    target = db.original_target()
    if not db.target_ready(target):
        return {}
    manifest = read_manifest(target)
    with db.connect() as cx:
        existing = cx.execute('select * from batches where id=?', (ORIGINAL_BATCH_ID,)).fetchone()
        if existing:
            if not cx.execute('select 1 from batches where is_active=1').fetchone():
                cx.execute('update batches set is_active=1 where id=?', (ORIGINAL_BATCH_ID,))
            row = cx.execute('select * from batches where id=?', (ORIGINAL_BATCH_ID,)).fetchone()
            return _row(row)
        has_active = cx.execute('select count(*) from batches where is_active=1').fetchone()[0]
        created = manifest.get('prepared_at') or db.now_iso()
        label = f"原始数据 {manifest.get('date_min', '')}~{manifest.get('date_max', '')}".strip()
        cx.execute(
            'insert into batches(id,label,kind,status,db_path,source_file,source_sha256,policy_version,'
            'manifest_json,is_active,created_by,created_at,published_at) '
            'values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ORIGINAL_BATCH_ID, label or '原始数据', 'original', 'ready',
             db.stored_target(target),
             manifest.get('source_file', 'online_retail_II.xlsx'), manifest.get('source_sha256', ''),
             manifest.get('policy_version', ''), json.dumps(manifest, ensure_ascii=False),
             0 if has_active else 1, None, created, created))
        row = cx.execute('select * from batches where id=?', (ORIGINAL_BATCH_ID,)).fetchone()
    return _row(row)


def set_active(batch_id: str, user_id: int | None = None) -> dict:
    """Explicitly switch the current data snapshot; only one batch is active."""
    with db.connect() as cx:
        exists = cx.execute('select 1 from batches where id=?', (batch_id,)).fetchone()
        if not exists:
            raise LookupError('数据批次不存在')
        cx.execute('update batches set is_active=0 where is_active=1')
        cx.execute('update batches set is_active=1 where id=?', (batch_id,))
        row = cx.execute('select * from batches where id=?', (batch_id,)).fetchone()
        cx.execute('insert into audit_log(user_id,action,detail,created_at) values(?,?,?,?)',
                   (user_id, 'batch.activate', batch_id, db.now_iso()))
    return _row(row)


def register_batch(*, batch_id: str, label: str, kind: str, db_path, source_file: str,
                   source_sha256: str, policy_version: str, manifest: dict,
                   status: str = 'ready', created_by: int | None = None,
                   publish: bool = False) -> dict:
    """Register a batch; ``db_path`` is a SQLite path or a ``mysql:<database>`` target."""
    with db.connect() as cx:
        cx.execute(
            'insert into batches(id,label,kind,status,db_path,source_file,source_sha256,policy_version,'
            'manifest_json,is_active,created_by,created_at,published_at) '
            'values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (batch_id, label, kind, status, db.stored_target(db_path), source_file,
             source_sha256, policy_version, json.dumps(manifest, ensure_ascii=False),
             0, created_by, db.now_iso(), db.now_iso() if status == 'ready' else None))
        row = cx.execute('select * from batches where id=?', (batch_id,)).fetchone()
    if publish:
        return set_active(batch_id, created_by)
    return _row(row)


def mark_batch_failed(batch_id: str, error: str) -> None:
    with db.connect() as cx:
        cx.execute("update batches set status='failed', error=? where id=?", (str(error)[:1000], batch_id))


def find_batch_by_hash(source_sha256: str) -> dict:
    with db.connect() as cx:
        row = cx.execute(
            "select * from batches where source_sha256=? and status='ready' order by created_at desc limit 1",
            (source_sha256,)).fetchone()
    return _row(row)
