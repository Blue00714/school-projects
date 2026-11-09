"""OWNER: api-auth — login, logout, current user, password change.

Endpoints (all under /api):
  POST /auth/login    {username,password} -> user + HttpOnly session cookie
  POST /auth/logout   -> clears the session
  GET  /auth/me       -> current user
  POST /auth/password {old_password,new_password} -> change own password

The raw session token only ever lives in the cookie: the ``sessions`` table stores its
SHA-256 digest (``security.token_digest``). ``/auth/login`` and ``/auth/logout`` are
exempt from the login middleware so that a stale cookie can still be cleaned up.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.core import config, security
from app.api import deps
from app.core.errors import BadRequest, Unauthorized
from app.services import users as user_service

router = APIRouter(tags=['auth'])

# One message for every login failure: a disabled account, an unknown username and a
# wrong password must be indistinguishable to the caller.
LOGIN_FAILED = '账号或密码错误'

# Verified against a throw-away hash so an unknown username costs the same time as a
# known one (no account-enumeration timing signal).
_DUMMY_HASH = security.hash_password(secrets.token_hex(16))


class LoginBody(BaseModel):
    username: str = Field(default='')
    password: str = Field(default='')


class PasswordBody(BaseModel):
    old_password: str = Field(default='')
    new_password: str = Field(default='')


def _public(row) -> dict:
    return deps.user_public(row)


def _cookie_max_age() -> int:
    return max(1, int(float(config.SESSION_TTL_HOURS) * 3600))


@router.post('/auth/login')
def login(payload: LoginBody, request: Request, response: Response):
    # Missing fields are a malformed request (400), not a credential failure (401);
    # the interface contract reports parameter validation as 400.
    username, password = (payload.username or '').strip(), payload.password or ''
    if not username or not password:
        raise BadRequest('用户名和密码不能为空')
    account = user_service.find_by_username(username)
    stored_hash = account['password_hash'] if account else _DUMMY_HASH
    password_ok = security.verify_password(password, stored_hash)
    if not account or not password_ok or not account['active']:
        raise Unauthorized(LOGIN_FAILED)

    token = security.new_session_token()
    user_service.create_session(account['id'], token,
                                user_agent=request.headers.get('user-agent', ''))
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=_cookie_max_age(),
        httponly=True,
        samesite='lax',
        path='/',
        secure=False,
    )
    return {'user': _public(account)}


@router.post('/auth/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(config.COOKIE_NAME)
    if token:
        user_service.revoke_session(token)
    response.delete_cookie(config.COOKIE_NAME, path='/', httponly=True, samesite='lax')
    return {'ok': True}


@router.get('/auth/me')
def me(user: dict = Depends(deps.current_user)):
    return {'user': _public(user)}


@router.post('/auth/password')
def change_password(payload: PasswordBody, request: Request,
                    user: dict = Depends(deps.current_user)):
    # The current session survives the change; every other session of this account dies.
    user_service.change_password(
        user['id'], payload.old_password, payload.new_password,
        keep_token=request.cookies.get(config.COOKIE_NAME), actor_id=user['id'])
    return {'ok': True}
