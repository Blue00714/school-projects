"""OWNER: data-api — asynchronous Excel/CSV import, batch listing and switching.

Endpoints (all under /api):
  POST /imports                     multipart file -> 202 {job_id,status}; same sha256 -> duplicate
  GET  /imports/jobs/{job_id}       import status, quality summary, issues, batch
  GET  /imports/batches             batch list with active flag
  POST /imports/batches/{id}/activate  explicit snapshot switch (never automatic)
  GET  /imports/template.csv        downloadable example header/rows

Uploads are read-only copies; the original Excel and ``runtime/retail.sqlite3`` are never
modified. A successful import creates a new batch but does **not** activate it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app import db
from app.api import deps
from app.api.deps import current_user
from app.core.errors import BadRequest, NotFound
from app.services import batches as batch_service, importer, orders as order_service

router = APIRouter(tags=['imports'])

TEMPLATE_FIELDS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price',
                   'Customer ID', 'Country']
TEMPLATE_ROWS = [
    {'Invoice': '100001', 'StockCode': 'A-1001', 'Description': '示例商品A', 'Quantity': 2,
     'InvoiceDate': '2011-01-01 10:00:00', 'Price': 3.5, 'Customer ID': '10001',
     'Country': 'United Kingdom'},
    {'Invoice': '100002', 'StockCode': 'B-2002', 'Description': '示例商品B', 'Quantity': 1,
     'InvoiceDate': '2011-01-02 11:30:00', 'Price': 12.99, 'Customer ID': '10002',
     'Country': 'France'},
]


def _clean_file_name(raw: str | None) -> tuple[str, str]:
    name = (raw or '').strip().replace('\\', '/').split('/')[-1]
    if not name:
        raise BadRequest('缺少上传文件名')
    suffix = Path(name).suffix.lower()
    if suffix not in importer.ALLOWED_SUFFIXES:
        raise BadRequest('仅支持 .xlsx/.xls/.csv 文件（单文件不超过 20MB）')
    return name, suffix


async def _read_limited(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await upload.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > importer.MAX_BYTES:
            raise BadRequest('文件超过 20MB 上限，请拆分后再导入')
        chunks.append(block)
    if not total:
        raise BadRequest('上传文件为空')
    return b''.join(chunks)


@router.post('/imports', status_code=202)
async def create_import(file: UploadFile = File(...), label: str = Form(''),
                        user: dict = Depends(current_user)):
    """Store the upload and queue a background import; duplicates are reported at once."""
    file_name, _suffix = _clean_file_name(file.filename)
    content = await _read_limited(file)
    return importer.submit(file_name=file_name, content=content, label=label, user_id=user['id'])


@router.get('/imports/jobs/{job_id}')
def import_job(job_id: str, _user: dict = Depends(current_user)):
    job = importer.get_job(job_id)
    if job is None:
        raise NotFound('导入任务不存在')
    batch = None
    if job.get('batch_id'):
        row = batch_service.get_batch(job['batch_id'])
        if row:
            batch = {'id': row['id'], 'label': row['label'], 'is_active': bool(row['is_active'])}
    return {
        'job': {
            'id': job['id'],
            'file_name': job['file_name'],
            'status': job['status'],
            'created_at': job['created_at'],
            'finished_at': job['finished_at'],
            'error': job['error'],
        },
        'report': job['report'],
        'issues': job['issues'],
        'batch': batch,
    }


@router.get('/imports/batches')
def list_batches(_user: dict = Depends(current_user)):
    rows = batch_service.list_batches()
    active = next((row['id'] for row in rows if row['is_active']), None)
    return {'batches': [deps.batch_public(row) for row in rows], 'active_batch_id': active}


@router.post('/imports/batches/{batch_id}/activate')
def activate_batch(batch_id: str, user: dict = Depends(current_user)):
    row = batch_service.get_batch(batch_id)
    if not row:
        raise NotFound('数据批次不存在')
    if row['status'] != 'ready':
        raise BadRequest('该批次尚未就绪，不能切换')
    previous = None
    with db.connect() as cx:
        active = cx.execute('select id from batches where is_active=1').fetchone()
        previous = active['id'] if active else None
    try:
        updated = batch_service.set_active(batch_id, user['id'])
    except LookupError:
        raise NotFound('数据批次不存在') from None
    return {'batch': deps.batch_public(updated), 'previous_batch_id': previous}


@router.get('/imports/template.csv')
def import_template(_user: dict = Depends(current_user)):
    """Example header plus two rows; column names match the required source columns."""
    payload = order_service.csv_bytes(TEMPLATE_ROWS, TEMPLATE_FIELDS)
    return order_service.csv_response(payload, 'import-template.csv')
