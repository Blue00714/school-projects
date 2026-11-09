"""OWNER: forecast-inventory — queued forecast/backtest jobs bound to a data batch.

Endpoints (all under /api):
  POST /forecast            {origin?} -> 202 {job_id,status:"queued",batch_id}
  POST /backtest            {start,end} -> 202 {job_id,status:"queued",batch_id}
  GET  /jobs/{job_id}       status + result (persisted; still queryable after restart)
  GET  /jobs/{job_id}/export  CSV of rows with method/version/batch columns (BOM, UTF-8)

Business rules live in ``app.services.jobs``; the algorithm itself is the frozen
``app.services.forecasting.forecast/backtest`` and is not modified here.
"""
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_user
from app.services import jobs

router = APIRouter(tags=['forecast'])


class ForecastBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin: str | None = None


class BacktestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: str
    end: str


@router.post('/forecast', status_code=202)
def create_forecast(payload: ForecastBody | None = None, user: dict = Depends(current_user)):
    data = payload.model_dump(exclude_none=True) if payload is not None else {}
    return jobs.create_forecast_job(user=user, origin=data.get('origin'))


@router.post('/backtest', status_code=202)
def create_backtest(payload: BacktestBody, user: dict = Depends(current_user)):
    return jobs.create_backtest_job(user=user, start=payload.start, end=payload.end)


@router.get('/jobs/{job_id}')
def read_job(job_id: str, user: dict = Depends(current_user)):
    return {'job': jobs.get_job(job_id)}


@router.get('/forecast/latest')
def latest_forecast(user: dict = Depends(current_user)):
    return jobs.latest_jobs()


@router.get('/jobs/{job_id}/export')
def export_job(job_id: str, user: dict = Depends(current_user)):
    content, filename = jobs.export_job(job_id)
    return Response(
        content=content,
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
