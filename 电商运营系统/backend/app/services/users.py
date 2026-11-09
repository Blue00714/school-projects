"""Account, session and audit helpers (OWNER: api-auth).

The single place that knows how a row of ``users`` / ``sessions`` is shaped and which
business rules guard account changes (no self-deactivation, never remove the last
enabled administrator). Routers stay thin: they only map HTTP input to these calls and
the resulting exceptions (``BadRequest`` / ``Conflict`` / ``NotFound``) to the unified
error contract.

Rules enforced here are deliberately server-side: hiding a button on the page is not a
permission check.
"""
from __future__ import annotations

import re
import sqlite3

from app.core import config, security
from app import db
from app.core.errors import BadRequest, Conflict, NotFound

ROLES = ('admin', 'operator')
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200
# Printable, no whitespace or control characters; SQLite compares usernames
# case-insensitively (COLLATE NOCASE), so uniqueness is enforced there.
_USERNAME_RE = re.compile(r'^[^\s\x00-\x1f\x7f]{1,64}$')


def _row(row) -> dict:
    return dict(row) if row is not None else {}


def validate_username(username) -> str:
    name = str(username if username is not None else '').strip()
    if not name:
        raise BadRequest('用户名不能为空')
    if not _USERNAME_RE.match(name):
        raise BadRequest('用户名不能包含空白或控制字符，且最长64个字符')
    return name


def validate_password(password) -> str:
    value = password if isinstance(password, str) else ''
    if len(value) < MIN_PASSWORD_LENGTH:
        raise BadRequest(f'密码至少{MIN_PASSWORD_LENGTH}位')
    if len(value) > MAX_PASSWORD_LENGTH:
        raise BadRequest(f'密码最长{MAX_PASSWORD_LENGTH}位')
    return value


def validate_role(role) -> str:
    value = str(role if role is not None else '').strip()
    if value not in ROLES:
        raise BadRequest('角色必须为 admin 或 operator')
    return value


def validate_display_name(display_name) -> str:
    value = str(display_name if display_name is not None else '').strip()
    if len(value) > 100:
        raise BadRequest('显示名称最长100个字符')
    return value


# --- users -----------------------------------------------------------------

def get_user(user_id) -> dict:
    with db.connect() as cx:
        row = cx.execute('select * from users where id=?', (int(user_id),)).fetchone()
    return _row(row)


def find_by_username(username) -> dict:
    name = str(username if username is not None else '').strip()
    if not name:
        return {}
    with db.connect() as cx:
        row = cx.execute('select * from users where username=?', (name,)).fetchone()
    return _row(row)


def list_users() -> list[dict]:
    with db.connect() as cx:
        rows = cx.execute('select * from users order by role=\'admin\' desc, id').fetchall()
    return [dict(row) for row in rows]


def count_active_admins() -> int:
    with db.connect() as cx:
        return int(cx.execute(
            "select count(*) from users where role='admin' and active=1").fetchone()[0])


def create_user(*, username, password, display_name='', role='operator',
                created_by=None) -> dict:
    name = validate_username(username)
    secret = validate_password(password)
    role_value = validate_role(role)
    label = validate_display_name(display_name) or name
    stamp = security.utc_now()
    try:
        with db.connect() as cx:
            cursor = cx.execute(
                'insert into users(username,display_name,password_hash,role,active,'
                'must_change_password,created_at,updated_at) values(?,?,?,?,1,0,?,?)',
                (name, label, security.hash_password(secret), role_value, stamp, stamp))
            row = cx.execute('select * from users where id=?', (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise Conflict('用户名已存在') from None
    db.audit(created_by, 'user.create', f'id={row["id"]} username={name} role={role_value}')
    return dict(row)


def update_user(user_id, *, actor_id=None, active=None, role=None, password=None,
                display_name=None) -> dict:
    """Patch any subset of ``active`` / ``role`` / ``password`` / ``display_name``.

    The guards (self-deactivation, self-demotion, last enabled administrator) are
    checked against the row inside the same transaction that writes the change.
    """
    user_id = int(user_id)
    with db.connect() as cx:
        row = cx.execute('select * from users where id=?', (user_id,)).fetchone()
        if row is None:
            raise NotFound('账号不存在')

        new_active = bool(row['active']) if active is None else bool(active)
        new_role = row['role'] if role is None else validate_role(role)
        is_self = actor_id is not None and int(actor_id) == user_id

        if is_self and bool(row['active']) and not new_active:
            raise BadRequest('不能停用当前登录的账号')
        if is_self and row['role'] == 'admin' and new_role != 'admin':
            raise BadRequest('不能取消自己的管理员角色')

        was_active_admin = row['role'] == 'admin' and bool(row['active'])
        will_be_active_admin = new_role == 'admin' and new_active
        if was_active_admin and not will_be_active_admin:
            admins = int(cx.execute(
                "select count(*) from users where role='admin' and active=1").fetchone()[0])
            if admins <= 1:
                raise BadRequest('系统必须保留至少一个启用状态的管理员')

        stamp = security.utc_now()
        sets = ['active=?', 'role=?', 'updated_at=?']
        args = [1 if new_active else 0, new_role, stamp]
        changes = []
        if active is not None and bool(row['active']) != new_active:
            changes.append(f'active={1 if new_active else 0}')
        if role is not None and row['role'] != new_role:
            changes.append(f'role={new_role}')
        if password is not None:
            sets.append('password_hash=?')
            args.append(security.hash_password(validate_password(password)))
            sets.append('must_change_password=0')
            changes.append('password=changed')
        if display_name is not None:
            sets.append('display_name=?')
            args.append(validate_display_name(display_name))
            changes.append('display_name')
        cx.execute(f'update users set {", ".join(sets)} where id=?', args + [user_id])
        updated = cx.execute('select * from users where id=?', (user_id,)).fetchone()
    if changes:
        db.audit(actor_id, 'user.update', f'id={user_id} username={row["username"]} '
                                         + ' '.join(changes))
    return dict(updated)


def change_password(user_id, old_password, new_password, *, keep_token=None,
                    actor_id=None) -> None:
    """Self-service password change: verify the old one, keep only the current session."""
    user_id = int(user_id)
    new_secret = validate_password(new_password)
    with db.connect() as cx:
        row = cx.execute('select * from users where id=?', (user_id,)).fetchone()
        if row is None:
            raise NotFound('账号不存在')
        if not security.verify_password(str(old_password or ''), row['password_hash']):
            raise BadRequest('旧密码不正确')
        cx.execute('update users set password_hash=?, must_change_password=0, updated_at=? '
                   'where id=?',
                   (security.hash_password(new_secret), security.utc_now(), user_id))
        revoked = revoke_sessions(cx, user_id, keep_token=keep_token)
    db.audit(actor_id if actor_id is not None else user_id, 'auth.password_change',
             f'id={user_id} revoked_other_sessions={revoked}')


# --- sessions --------------------------------------------------------------

def create_session(user_id, token: str, *, hours=None, user_agent='') -> None:
    ttl = config.SESSION_TTL_HOURS if hours is None else float(hours)
    with db.connect() as cx:
        cx.execute('insert into sessions(token_hash,user_id,created_at,expires_at,revoked,'
                   'user_agent) values(?,?,?,?,0,?)',
                   (security.token_digest(token), int(user_id), security.utc_now(),
                    security.session_expiry(ttl), str(user_agent or '')[:200]))


def revoke_session(token) -> bool:
    """Revoke one raw session token; returns whether a live session was revoked."""
    if not token:
        return False
    with db.connect() as cx:
        cursor = cx.execute('update sessions set revoked=1 where token_hash=? and revoked=0',
                            (security.token_digest(token),))
        return cursor.rowcount > 0


def revoke_sessions(cx, user_id, *, keep_token=None) -> int:
    """Revoke every session of a user (optionally keeping the current one)."""
    if keep_token:
        cursor = cx.execute('update sessions set revoked=1 where user_id=? and revoked=0 '
                            'and token_hash<>?',
                            (int(user_id), security.token_digest(keep_token)))
    else:
        cursor = cx.execute('update sessions set revoked=1 where user_id=? and revoked=0',
                            (int(user_id),))
    return int(cursor.rowcount or 0)
