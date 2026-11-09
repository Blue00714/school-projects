"""Unified error contract: ``{"error": "...", "code": "..."}``.

The legacy standard-library adapter returned ``{"error": str}`` with 400 for invalid
parameters and 404 for unknown paths; that shape is preserved and extended with a
stable ``code`` field. Validation errors are reported as 400 (not FastAPI's default
422) to stay compatible with the existing interface document.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.data import DataError

log = logging.getLogger('ece.api')


class ApiError(Exception):
    status_code = 400
    code = 'bad_request'

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None, detail=None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.detail = detail


class BadRequest(ApiError):
    status_code, code = 400, 'bad_request'


class Unauthorized(ApiError):
    status_code, code = 401, 'unauthorized'


class Forbidden(ApiError):
    status_code, code = 403, 'forbidden'


class NotFound(ApiError):
    status_code, code = 404, 'not_found'


class Conflict(ApiError):
    status_code, code = 409, 'conflict'


class Unprocessable(ApiError):
    status_code, code = 422, 'unprocessable'


class UpstreamError(ApiError):
    status_code, code = 502, 'upstream_failed'


def _payload(message: str, code: str, detail=None) -> dict:
    body = {'error': message, 'code': code}
    if detail is not None:
        body['detail'] = detail
    return body


def register(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=_payload(exc.message, exc.code, exc.detail))

    @app.exception_handler(DataError)
    async def _data_error(_request: Request, exc: DataError):
        return JSONResponse(status_code=400, content=_payload(str(exc), 'bad_request'))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = '.'.join(str(x) for x in first.get('loc', ()) if x not in ('body', 'query', 'path'))
        message = f'参数无效：{loc} {first.get("msg", "")}'.strip()
        return JSONResponse(status_code=400, content=_payload(message, 'bad_request'))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return JSONResponse(status_code=404, content=_payload('路径不存在', 'not_found'))
        if exc.status_code == 405:
            return JSONResponse(status_code=405, content=_payload('方法不允许', 'method_not_allowed'))
        return JSONResponse(status_code=exc.status_code, content=_payload(str(exc.detail), 'http_error'))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception('unhandled error on %s %s', request.method, request.url.path)
        return JSONResponse(status_code=500, content=_payload('服务内部错误，请查看服务端日志', 'internal_error'))
