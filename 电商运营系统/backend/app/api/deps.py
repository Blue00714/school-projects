"""Shared request dependencies: session authentication, role checks and batch access.

The authentication middleware in ``app.main`` attaches the resolved user to
``request.state.user``; these dependencies also resolve the cookie themselves so the
routers stay testable when mounted directly.
"""
from __future__ import annotations

from fastapi import Depends, Request

from app.services.analytics import Analytics
from app.services.data import Store

from app.core import config, security
from app import db
from app.core.errors import Forbidden, NotFound, Unauthorized


def user_public(row) -> dict:
    return {
        'id': row['id'],
        'username': row['username'],
        'display_name': row['display_name'],
        'role': row['role'],
        'active': bool(row['active']),
        'must_change_password': bool(row['must_change_password']),
    }


def user_from_token(token: str | None):
    if not token:
        return None
    digest = security.token_digest(token)
    with db.connect() as cx:
        row = cx.execute(
            "select u.* from sessions s join users u on u.id=s.user_id "
            "where s.token_hash=? and s.revoked=0 and s.expires_at>? and u.active=1",
            (digest, security.utc_now()),
        ).fetchone()
    return dict(row) if row else None


def current_user(request: Request) -> dict:
    user = getattr(request.state, 'user', None)
    if user is None:
        user = user_from_token(request.cookies.get(config.COOKIE_NAME))
        request.state.user = user
    if not user:
        raise Unauthorized('未登录或登录已失效')
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get('role') != 'admin':
        raise Forbidden('该操作需要管理员权限')
    return user


def require_roles(*roles: str):
    allowed = set(roles)

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user.get('role') not in allowed:
            raise Forbidden('当前账号无权执行该操作')
        return user

    return dependency


def batch_public(batch: dict) -> dict:
    try:
        manifest = batch.get('manifest') or {}
    except AttributeError:
        manifest = {}
    return {
        'id': batch['id'],
        'label': batch['label'],
        'kind': batch['kind'],
        'status': batch['status'],
        'is_active': bool(batch['is_active']),
        'source_file': batch.get('source_file', ''),
        'source_sha256': batch.get('source_sha256', ''),
        'policy_version': batch.get('policy_version', ''),
        'created_at': batch.get('created_at', ''),
        'published_at': batch.get('published_at'),
        'manifest': manifest,
    }


def active_batch() -> dict:
    from app.services.batches import active_batch as _active

    batch = _active()
    if not batch:
        raise NotFound('没有可用的数据批次')
    return batch


def get_batch(batch_id: str | None = None) -> dict:
    from app.services.batches import get_batch as _get

    batch = _get(batch_id) if batch_id else active_batch()
    if not batch:
        raise NotFound('数据批次不存在')
    return batch


def store_for(batch: dict) -> Store:
    """Open the batch snapshot.

    ``batches.db_path`` is a relative SQLite path or (MySQL mode) a ``mysql:<database>``
    target; ``db.resolve_db_path``/``app.services.data.Store`` understand both, and both
    answer ``is_file()`` so the existence check below stays backend-agnostic.
    """
    path = db.resolve_db_path(batch['db_path'])
    if not path.is_file():
        raise NotFound('该数据批次的数据文件不存在')
    return Store(path)


def analytics_for(batch: dict) -> Analytics:
    return Analytics(store_for(batch))
