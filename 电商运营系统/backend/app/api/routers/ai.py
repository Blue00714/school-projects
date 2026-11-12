"""OWNER: ai-assistant — AI query assistant and operations brief.

Endpoints (all under /api):
  GET  /ai/status          provider configuration (never the key)
  GET  /ai/tools           declared read-only tools and parameter schemas
  POST /ai/ask             {question,scope?} -> answer with tool trace and labels
  POST /ai/ask/stream      {question,scope?} -> NDJSON progress, answer, result
  POST /ai/brief           -> deterministic Markdown brief + download URL
  GET  /ai/briefs/{id}.md  download brief (text/markdown attachment)

Permissions: any logged-in user (admin/operator) may read; nothing here writes data
except the audit tables ``ai_runs`` / ``briefs``.  The language-model API key is only
read server-side and is never echoed by a response, a log line or an export.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import current_user
from app.core.errors import ApiError, BadRequest, NotFound
from app.services import assistant, brief as brief_service

router = APIRouter(tags=['ai'])
log = logging.getLogger('ece.ai')

AI_POLICY = '仅只读；库存写操作必须走页面确认'


@router.get('/ai/status')
def ai_status(user: dict = Depends(current_user)):
    return assistant.status_payload()


@router.get('/ai/tools')
def ai_tools(user: dict = Depends(current_user)):
    return {'tools': assistant.tool_catalog(), 'policy': AI_POLICY}


def _ask_args(body: dict):
    if not isinstance(body, dict):
        raise BadRequest('请求体必须为 JSON 对象')
    allowed = {'question', 'scope'}
    unknown = set(body) - allowed
    if unknown:
        raise BadRequest('请求含未知字段：' + '、'.join(sorted(unknown)))
    if 'question' not in body:
        raise BadRequest('缺少 question')
    return body['question'], body.get('scope')


@router.post('/ai/ask')
def ai_ask(body: dict = Body(...), user: dict = Depends(current_user)):
    return assistant.answer_question(*_ask_args(body), user)


@router.post('/ai/ask/stream')
def ai_ask_stream(body: dict = Body(...), user: dict = Depends(current_user)):
    events = assistant.stream_answer(*_ask_args(body), user)

    def encode():
        try:
            for event in events:
                yield json.dumps(event, ensure_ascii=False, default=str) + '\n'
        except ApiError as exc:
            yield json.dumps({'type': 'error', 'message': assistant._redact(str(exc))}, ensure_ascii=False) + '\n'
        except Exception:
            log.exception('AI stream interrupted')
            yield json.dumps({'type': 'error', 'message': '问答服务发生错误，请重试'}, ensure_ascii=False) + '\n'
        finally:
            events.close()

    return StreamingResponse(encode(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.post('/ai/brief')
def ai_brief(body: dict | None = Body(default=None), user: dict = Depends(current_user)):
    body = body or {}
    if not isinstance(body, dict):
        raise BadRequest('请求体必须为 JSON 对象')
    allowed = {'scope', 'scenario_id'}
    unknown = set(body) - allowed
    if unknown:
        raise BadRequest('请求含未知字段：' + '、'.join(sorted(unknown)))
    payload = brief_service.build_brief(body.get('scope'), body.get('scenario_id'), user)
    return JSONResponse(status_code=201, content=payload)


@router.get('/ai/briefs/{brief_id}.md')
def ai_brief_download(brief_id: str, user: dict = Depends(current_user)):
    row = brief_service.get_brief(brief_id)
    if not row:
        raise NotFound('简报不存在')
    filename = f"brief-{row['id']}.md"
    return Response(
        content=row['markdown'],
        media_type='text/markdown; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
