"""OWNER: ai-assistant — AI query assistant and operations brief.

Endpoints (all under /api):
  GET  /ai/status          provider configuration (never the key)
  GET  /ai/tools           declared read-only tools and parameter schemas
  POST /ai/ask             {question,scope?} -> answer with tool trace and labels
  POST /ai/brief           -> deterministic Markdown brief + download URL
  GET  /ai/briefs/{id}.md  download brief (text/markdown attachment)

Permissions: any logged-in user (admin/operator) may read; nothing here writes data
except the audit tables ``ai_runs`` / ``briefs``.  The language-model API key is only
read server-side and is never echoed by a response, a log line or an export.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse

from app.api.deps import current_user
from app.core.errors import BadRequest, NotFound
from app.services import assistant, brief as brief_service

router = APIRouter(tags=['ai'])

AI_POLICY = '仅只读；库存写操作必须走页面确认'


@router.get('/ai/status')
def ai_status(user: dict = Depends(current_user)):
    return assistant.status_payload()


@router.get('/ai/tools')
def ai_tools(user: dict = Depends(current_user)):
    return {'tools': assistant.tool_catalog(), 'policy': AI_POLICY}


@router.post('/ai/ask')
def ai_ask(body: dict = Body(...), user: dict = Depends(current_user)):
    if not isinstance(body, dict):
        raise BadRequest('请求体必须为 JSON 对象')
    allowed = {'question', 'scope'}
    unknown = set(body) - allowed
    if unknown:
        raise BadRequest('请求含未知字段：' + '、'.join(sorted(unknown)))
    if 'question' not in body:
        raise BadRequest('缺少 question')
    return assistant.answer_question(body.get('question'), body.get('scope'), user)


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
