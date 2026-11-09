"""Queued whole-shop forecast / backtest jobs bound to a data batch.

Design notes (see ``API契约.md`` 5.5):

* A job row is written to the state database first (``status='queued'``), then a
  background thread executes the frozen algorithm from ``app.services.forecasting``.
  The response returns immediately (202) and the caller polls
  ``GET /api/jobs/{job_id}``.
* The job stores the ``batch_id`` that was active **when it was created**. Loading the
  batch by id (never "the active batch") keeps a running/stored result stable even
  when the user switches snapshots afterwards.
* Status, timings and the complete result JSON live in the ``jobs`` table, so a
  succeeded result survives a service restart. Interrupted ``queued/running`` rows are
  marked failed by the startup recovery in ``app.main``.
* Every database call opens a short-lived sqlite connection from ``app.db.connect``
  (per-thread, 30 s timeout / 10 s busy_timeout); the worker thread therefore never
  shares a connection with the request threads.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import threading
import time
from datetime import date
from uuid import uuid4

from app.services.analytics import iso_date
from app.services.data import DataError
from app.services.forecasting import ACTIVE_MODEL, DEPLOYMENT_VERSION
from app.services.forecasting import backtest as run_backtest
from app.services.forecasting import forecast as run_forecast

from app import db
from app.api import deps
from app.core.errors import ApiError, BadRequest, NotFound
from app.services.batches import active_batch, get_batch

log = logging.getLogger('ece.jobs')

DEFAULT_MODEL = ACTIVE_MODEL
MAX_ACTIVE_JOBS = 8
EXPORT_FIELDS = [
    'origin', 'date', 'h', 'prediction', 'baseline', 'actual', 'abs_error',
    'model_version', 'data_policy_version', 'batch_id', 'actual_method',
]


# --------------------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------------------
def _method(model) -> str:
    method = DEFAULT_MODEL if model is None else str(model).strip()
    if method != DEFAULT_MODEL:
        raise BadRequest('系统统一使用销量预测模型，无需选择其他模型')
    return method


def _active_batch_or_404() -> dict:
    batch = active_batch()
    if not batch:
        raise NotFound('没有可用的数据批次')
    return batch


def latest_jobs() -> dict:
    """Return saved results for the active batch and deployed model version only."""
    batch = _active_batch_or_404()
    found = {}
    with db.connect() as cx:
        for kind in ('forecast', 'backtest'):
            rows = cx.execute("select id,result_json from jobs where batch_id=? and kind=? and status='succeeded' "
                              'order by created_at desc limit 20', (batch['id'], kind)).fetchall()
            for row in rows:
                result = json.loads(row['result_json'])
                if result.get('model_version') == DEPLOYMENT_VERSION and result.get('requested_method') == DEFAULT_MODEL:
                    found[kind] = row['id']
                    break
    return {'batch_id': batch['id'], 'jobs': {kind: get_job(job_id) for kind, job_id in found.items()}}


def _coverage(batch: dict) -> tuple[str, str]:
    manifest = batch.get('manifest') or {}
    date_min, date_max = str(manifest.get('date_min') or ''), str(manifest.get('date_max') or '')
    if not date_min or not date_max:
        raise BadRequest('当前批次缺少数据覆盖范围（manifest.date_min/date_max），无法执行预测')
    return date_min, date_max


# --------------------------------------------------------------------------------------
# create jobs
# --------------------------------------------------------------------------------------
def create_forecast_job(*, user=None, origin=None, model=None) -> dict:
    """Validate inputs and queue a whole-shop 7-day forecast for the active batch."""
    batch = _active_batch_or_404()
    method = _method(model)
    date_min, date_max = _coverage(batch)
    try:
        origin_date = iso_date(origin if origin not in (None, '') else date_max)
    except DataError as exc:
        raise BadRequest(str(exc)) from None
    if origin_date < date_min or origin_date > date_max:
        raise BadRequest(f'预测起点必须在当前批次数据覆盖范围 {date_min}～{date_max} 内')
    return _enqueue('forecast', {'origin': origin_date, 'model': method}, batch, user)


def create_backtest_job(*, user=None, start=None, end=None, model=None) -> dict:
    """Validate a 1–56 day historical window and queue a backtest for the active batch."""
    batch = _active_batch_or_404()
    method = _method(model)
    if start in (None, '') or end in (None, ''):
        raise BadRequest('回测开始与结束日期不能为空')
    try:
        first, last = iso_date(start), iso_date(end)
    except DataError as exc:
        raise BadRequest(str(exc)) from None
    if first > last:
        raise BadRequest('回测开始日期不能晚于结束日期')
    days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    if not 1 <= days <= 56:
        raise BadRequest('回测窗口须为 1 至 56 个日历日')
    date_min, date_max = _coverage(batch)
    if first < date_min or last > date_max:
        raise BadRequest(f'回测日期必须在当前批次覆盖范围 {date_min}～{date_max} 内')
    return _enqueue('backtest', {'start': first, 'end': last, 'model': method}, batch, user)


def _enqueue(kind: str, params: dict, batch: dict, user) -> dict:
    with db.connect() as cx:
        active = cx.execute("select count(*) from jobs where status in ('queued','running')").fetchone()[0]
        if active >= MAX_ACTIVE_JOBS:
            raise BadRequest(f'已有 {active} 个预测任务在排队或运行，请等当前任务完成后再提交')
        job_id = uuid4().hex
        cx.execute(
            'insert into jobs(id,kind,status,params_json,batch_id,created_by,created_at) values(?,?,?,?,?,?,?)',
            (job_id, kind, 'queued', json.dumps(params, ensure_ascii=False), batch['id'],
             (user or {}).get('id'), db.now_iso()),
        )
    worker = threading.Thread(target=_run_job, args=(job_id,), name=f'ece-{kind}-{job_id[:8]}', daemon=True)
    worker.start()
    return {'job_id': job_id, 'status': 'queued', 'batch_id': batch['id']}


# --------------------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------------------
def _clean(value):
    """Replace non-finite floats with None so the result is valid JSON."""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, 'item') and not isinstance(value, (int, str)):  # numpy scalar
        try:
            return _clean(value.item())
        except (TypeError, ValueError):
            return str(value)
    return value


def _fail(job_id: str, message: str, begun: float) -> None:
    finished, elapsed = db.now_iso(), round(time.monotonic() - begun, 2)
    try:
        with db.connect() as cx:
            cx.execute(
                "update jobs set status='failed', error=?, result_json=null, finished_at=?, elapsed_seconds=? where id=?",
                (str(message)[:1000], finished, elapsed, job_id),
            )
    except Exception:  # a failing status write must at least be logged
        log.exception('could not persist failure for job %s', job_id)


def _run_job(job_id: str) -> None:
    begun = time.monotonic()
    try:
        with db.connect() as cx:
            row = cx.execute('select * from jobs where id=?', (job_id,)).fetchone()
            if not row:
                return
            cx.execute("update jobs set status='running', started_at=? where id=?", (db.now_iso(), job_id))
        params = json.loads(row['params_json'] or '{}')
        batch = get_batch(row['batch_id'])
        if not batch:
            raise ValueError('任务绑定的数据批次不存在或已被移除')
        store = deps.store_for(batch)
        series = store.series()
        manifest = batch.get('manifest') or store.manifest()
        if row['kind'] == 'forecast':
            result = run_forecast(series, params.get('origin'), model=params.get('model', DEFAULT_MODEL))
        elif row['kind'] == 'backtest':
            result = run_backtest(series, params.get('start'), params.get('end'),
                                  model=params.get('model', DEFAULT_MODEL))
        else:
            raise ValueError('未知任务类型')
        result = _clean(result)
        result['batch_id'] = batch['id']
        result['data_policy_version'] = str(manifest.get('policy_version') or '')
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
        finished, elapsed = db.now_iso(), round(time.monotonic() - begun, 2)
        with db.connect() as cx:
            cx.execute(
                "update jobs set status='succeeded', result_json=?, error=null, finished_at=?, elapsed_seconds=? where id=?",
                (payload, finished, elapsed, job_id),
            )
    except (ApiError, DataError) as exc:
        message = getattr(exc, 'message', None) or str(exc)
        log.warning('job %s failed: %s', job_id, message)
        _fail(job_id, message, begun)
    except (ValueError, KeyError, TypeError) as exc:
        message = str(exc) or type(exc).__name__
        log.warning('job %s failed: %s', job_id, message)
        _fail(job_id, message, begun)
    except Exception as exc:  # sqlite/IO/model failures must surface as a failed job
        log.exception('job %s crashed', job_id)
        _fail(job_id, f'{type(exc).__name__}: {exc}', begun)


# --------------------------------------------------------------------------------------
# read / export
# --------------------------------------------------------------------------------------
def job_public(row) -> dict:
    params: dict = {}
    try:
        params = json.loads(row['params_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        params = {}
    batch = get_batch(row['batch_id'])
    job = {
        'id': row['id'],
        'kind': row['kind'],
        'status': row['status'],
        'created_at': row['created_at'],
        'started_at': row['started_at'],
        'finished_at': row['finished_at'],
        'elapsed_seconds': row['elapsed_seconds'],
        'error': row['error'],
        'params': params,
        'batch': deps.batch_public(batch) if batch else None,
    }
    if row['result_json']:
        try:
            job['result'] = json.loads(row['result_json'])
        except json.JSONDecodeError:
            job['result'] = None
    return job


def get_job(job_id: str) -> dict:
    if not isinstance(job_id, str) or not job_id.strip():
        raise NotFound('任务不存在')
    with db.connect() as cx:
        row = cx.execute('select * from jobs where id=?', (job_id.strip(),)).fetchone()
    if not row:
        raise NotFound('任务不存在')
    return job_public(row)


def _cell(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ''
        return format(value, '.10g')
    return str(value)


def _guard(row: dict) -> dict:
    """Neutralise spreadsheet formula injection in text cells."""
    return {
        key: ("'" + value if isinstance(value, str) and value[:1] in ('=', '+', '-', '@') else value)
        for key, value in row.items()
    }


def export_rows(job: dict) -> list[dict]:
    result = job.get('result') or {}
    batch = job.get('batch') or {}
    manifest = batch.get('manifest') or {}
    policy = result.get('data_policy_version') or batch.get('policy_version') or manifest.get('policy_version') or ''
    batch_id = str(result.get('batch_id') or batch.get('id') or '')
    model_version = result.get('model_version') or DEPLOYMENT_VERSION
    entries: list[tuple] = []
    rows = result.get('rows')
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                entries.append((result.get('origin'), result.get('actual_method'), row))
    for part in result.get('origin_results') or []:
        if not isinstance(part, dict):
            continue
        for row in part.get('rows') or []:
            if isinstance(row, dict):
                entries.append((part.get('origin'), part.get('actual_method'), row))
    exported = []
    for origin, method, row in entries:
        exported.append({
            'origin': origin or '',
            'date': row.get('date') or '',
            'h': _cell(row.get('h')),
            'prediction': _cell(row.get('prediction')),
            'baseline': _cell(row.get('baseline')),
            'actual': _cell(row.get('actual')),
            'abs_error': _cell(row.get('abs_error')),
            'model_version': model_version,
            'data_policy_version': policy,
            'batch_id': batch_id,
            'actual_method': method or result.get('actual_method') or '',
        })
    return exported


def export_job(job_id: str) -> tuple[bytes, str]:
    job = get_job(job_id)
    if job.get('status') != 'succeeded' or job.get('result') is None:
        raise BadRequest('任务尚未成功完成，无法导出')
    rows = export_rows(job)
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=EXPORT_FIELDS, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(_guard(row))
    content = ('\ufeff' + text.getvalue()).encode('utf-8')
    filename = f"forecast-{job.get('kind', 'job')}-{job_id[:8]}.csv"
    return content, filename
