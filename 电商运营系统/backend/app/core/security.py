"""Password hashing and session-token helpers.

Uses only the standard library: PBKDF2-HMAC-SHA256 with a per-password salt and a
constant-time comparison. No plain-text password is stored or logged.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

ROUNDS = 240_000
ALGORITHM = 'pbkdf2_sha256'


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError('密码至少8位')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ROUNDS).hex()
    return f'{ALGORITHM}${ROUNDS}${salt}${digest}'


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, digest = str(stored).split('$')
        if algorithm != ALGORITHM:
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(candidate, digest)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def session_expiry(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=float(hours))).isoformat(timespec='seconds')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
