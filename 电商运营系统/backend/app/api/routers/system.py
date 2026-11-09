"""OWNER: api-auth — health, metadata/options, user administration, settings.

Endpoints (all under /api):
  GET   /health            (no login) service/data readiness
  GET   /options           countries, products, forecast methods, batch
  GET   /users             (admin) list accounts
  POST  /users             (admin) create account
  PATCH /users/{user_id}   (admin) activate/deactivate, role, reset password
  GET   /settings          (admin) non-secret configuration
  PUT   /settings          (admin) AI base_url/model/timeout (key stays in env)

``/options`` is the expensive call (it scans the whole batch); its result is cached in
process memory per active batch id and recomputed when the active batch changes.
``/health`` is deliberately login-free so the launcher can probe it.
"""
from __future__ import annotations

import json
import logging
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from app.services.forecasting import ACTIVE_MODEL, DEPLOYMENT_VERSION

from app.core import config
from app import db
from app.api import deps
from app.core.errors import BadRequest, NotFound
from app.services import users as user_service

log = logging.getLogger('ece.system')

router = APIRouter(tags=['system'])

# Batch-scoped caches. The options payload is keyed by the active batch id only
# (switching the batch invalidates it); the readiness probe also keys on file stat so a
# batch database replaced in place is re-checked.
_OPTIONS_LOCK = threading.Lock()
_OPTIONS_CACHE: dict = {'batch_id': None, 'payload': None}
_READY_LOCK = threading.Lock()
_READY_CACHE: dict = {'key': None, 'value': False}

AI_SETTING_KEYS = ('base_url', 'model', 'timeout_seconds')
AI_SETTING_KEY = 'ai'


class CreateUserBody(BaseModel):
    username: str = Field(default='')
    password: str = Field(default='')
    display_name: str | None = None
    role: str = 'operator'


class UpdateUserBody(BaseModel):
    active: bool | None = None
    role: str | None = None
    password: str | None = None
    display_name: str | None = None


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')

    ai: dict = Field(default_factory=dict)


# --- shared helpers --------------------------------------------------------

def _current_batch() -> dict | None:
    try:
        return deps.active_batch()
    except NotFound:
        return None


def _batch_payload() -> dict | None:
    batch = _current_batch()
    return deps.batch_public(batch) if batch else None


def _data_ready(batch: dict | None) -> bool:
    """Current batch database exists *and* a Store can be opened on it (cached)."""
    if not batch:
        return False
    try:
        path = db.resolve_db_path(batch.get('db_path') or '')
    except (KeyError, TypeError, ValueError):
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (batch.get('id'), str(path), stat.st_mtime_ns, stat.st_size)
    with _READY_LOCK:
        if _READY_CACHE['key'] == key:
            return bool(_READY_CACHE['value'])
    ready = False
    try:
        from app.services.data import Store

        Store(path).manifest()
        ready = True
    except Exception as exc:  # a readiness probe reports, it does not crash the endpoint
        log.warning('data readiness probe failed for batch %s: %s', batch.get('id'), exc)
    with _READY_LOCK:
        _READY_CACHE.update(key=key, value=ready)
    return ready


def _stored_ai_settings() -> dict:
    value = db.get_setting(AI_SETTING_KEY, {}) or {}
    return dict(value) if isinstance(value, dict) else {}


def effective_ai_config() -> dict:
    """``config.public_ai_config()`` overlaid with the non-secret values stored in
    ``settings``. The API key itself only ever comes from the environment."""
    base = config.public_ai_config()
    stored = _stored_ai_settings()
    for key in AI_SETTING_KEYS:
        if key in stored:
            base[key] = stored[key]
    base['provider'] = 'openai-compatible' if base.get('base_url') else ''
    base['configured'] = bool(base.get('base_url') and base.get('model')
                              and base.get('key_present'))
    return base


def _settings_payload() -> dict:
    return {
        'ai': effective_ai_config(),
        'session_ttl_hours': config.SESSION_TTL_HOURS,
        'batch': _batch_payload(),
    }


# --- health / options ------------------------------------------------------

@router.get('/health')
def health():
    batch = _current_batch()
    return {
        'ok': True,
        'service': config.SERVICE_NAME,
        'version': config.APP_VERSION,
        'data_ready': _data_ready(batch),
        'batch': deps.batch_public(batch) if batch else None,
        'ai_configured': bool(effective_ai_config()['configured']),
    }


@router.get('/options')
def options(_user: dict = Depends(deps.current_user)):
    batch = deps.active_batch()
    batch_id = batch.get('id')
    with _OPTIONS_LOCK:
        cached = _OPTIONS_CACHE['payload']
        if cached is not None and _OPTIONS_CACHE['batch_id'] == batch_id:
            return cached

    data = deps.analytics_for(batch).options()
    payload = {
        'manifest': data['manifest'],
        'countries': data['countries'],
        'products': data['products'],
        'methods': {ACTIVE_MODEL: '销量预测模型'},
        'batch': deps.batch_public(batch),
        'forecast': {'scope': 'whole-shop', 'horizon': 7, 'model': ACTIVE_MODEL, 'version': DEPLOYMENT_VERSION},
    }
    with _OPTIONS_LOCK:
        _OPTIONS_CACHE.update(batch_id=batch_id, payload=payload)
    return payload


# --- user administration (admin only) --------------------------------------

@router.get('/users')
def list_users(_admin: dict = Depends(deps.require_admin)):
    return {'users': [deps.user_public(row) for row in user_service.list_users()]}


@router.post('/users', status_code=201)
def create_user(payload: CreateUserBody, admin: dict = Depends(deps.require_admin)):
    user = user_service.create_user(
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name or '',
        role=payload.role,
        created_by=admin['id'],
    )
    return {'user': deps.user_public(user)}


@router.patch('/users/{user_id}')
def update_user(user_id: int, payload: UpdateUserBody,
                admin: dict = Depends(deps.require_admin)):
    user = user_service.update_user(
        user_id,
        actor_id=admin['id'],
        active=payload.active,
        role=payload.role,
        password=payload.password,
        display_name=payload.display_name,
    )
    return {'user': deps.user_public(user)}


# --- settings (admin only) -------------------------------------------------

@router.get('/settings')
def get_settings(_admin: dict = Depends(deps.require_admin)):
    return _settings_payload()


@router.put('/settings')
def put_settings(payload: SettingsBody, admin: dict = Depends(deps.require_admin)):
    stored = _stored_ai_settings()
    for key, value in (payload.ai or {}).items():
        if key not in AI_SETTING_KEYS:
            raise BadRequest('AI设置仅允许 base_url、model、timeout_seconds；'
                             'API密钥只能通过环境变量配置，接口不接受密钥')
        if value is None:  # explicit null clears the override and falls back to env
            stored.pop(key, None)
            continue
        if key == 'base_url':
            text = str(value).strip()
            if text and not text.lower().startswith(('http://', 'https://')):
                raise BadRequest('base_url 必须以 http:// 或 https:// 开头')
            stored[key] = text
        elif key == 'model':
            stored[key] = str(value).strip()
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise BadRequest('timeout_seconds 必须为数字') from None
            if not 1 <= number <= 600:
                raise BadRequest('timeout_seconds 需在1至600秒之间')
            stored[key] = number
    db.set_setting(AI_SETTING_KEY, stored)
    db.audit(admin['id'], 'settings.ai.update',
             json.dumps(stored, ensure_ascii=False))
    return _settings_payload()
