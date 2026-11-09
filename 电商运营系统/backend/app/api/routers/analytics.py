"""OWNER: data-api — dashboard, products, orders, transactions and CSV exports.

Endpoints (all under /api):
  GET /products                 product search with sales totals (legacy array contract)
  GET /orders                   order-level aggregation (filters + paging)
  GET /orders/{order_key}       order header and its transaction lines
  GET /dashboard                existing dashboard contract + batch info
  GET /transactions             existing transaction-list contract
  GET /export/trend             trend CSV (legacy contract)
  GET /export/orders            order CSV
  GET /export/transactions      transaction CSV

Every endpoint resolves the *selected* batch through ``deps.get_batch()`` and builds
its reader with ``deps.analytics_for`` / ``deps.store_for``, so a batch switch moves the
dashboard, order list, detail view and exports together. Parameter errors are reported as
400 through ``app.services.data.DataError``; missing resources use ``app.core.errors.NotFound``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api import deps
from app.api.deps import current_user
from app.core.errors import NotFound
from app.services import orders as order_service

router = APIRouter(tags=['analytics'])


def _current_batch() -> dict:
    """The explicitly selected data batch (never a request-supplied id)."""
    return deps.get_batch()


def _batch_brief(batch: dict) -> dict:
    return {'id': batch['id'], 'label': batch['label'], 'kind': batch['kind']}


def _filter_params(start: str | None, end: str | None, product: str | None,
                   country: str | None) -> dict:
    params = {'start': start, 'end': end, 'product': product, 'country': country}
    return {key: value for key, value in params.items() if value is not None}


@router.get('/products')
def products(q: str = '', batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    """Legacy contract: a bare array of product summaries (at most 50 rows)."""
    return deps.analytics_for(batch).search_products(q)


@router.get('/dashboard')
def dashboard(start: str | None = Query(None), end: str | None = Query(None),
             product: str | None = Query(None), country: str | None = Query(None),
             batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    result = deps.analytics_for(batch).dashboard(_filter_params(start, end, product, country))
    result['batch'] = _batch_brief(batch)
    return result


@router.get('/transactions')
def transactions(start: str | None = Query(None), end: str | None = Query(None),
                 product: str | None = Query(None), country: str | None = Query(None),
                 page: str = Query('1'), page_size: str = Query('25'),
                 batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    params = dict(_filter_params(start, end, product, country), page=page, page_size=page_size)
    result = deps.analytics_for(batch).transactions(params)
    result['batch'] = _batch_brief(batch)
    return result


@router.get('/orders')
def orders(start: str | None = Query(None), end: str | None = Query(None),
           product: str | None = Query(None), country: str | None = Query(None),
           q: str = Query(''), page: str = Query('1'), page_size: str = Query('25'),
           batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    params = dict(_filter_params(start, end, product, country), q=q, page=page, page_size=page_size)
    result = order_service.list_orders(deps.store_for(batch), params)
    result['batch'] = _batch_brief(batch)
    return result


@router.get('/orders/{order_key}')
def order_detail(order_key: str, start: str | None = Query(None), end: str | None = Query(None),
                 product: str | None = Query(None), country: str | None = Query(None),
                 batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    result = order_service.order_detail(deps.store_for(batch), order_key,
                                        _filter_params(start, end, product, country))
    if result is None:
        raise NotFound('订单不存在或不在所选筛选范围内')
    result['batch'] = _batch_brief(batch)
    return result


@router.get('/export/trend')
def export_trend(start: str | None = Query(None), end: str | None = Query(None),
                 product: str | None = Query(None), country: str | None = Query(None),
                 batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    """Legacy CSV contract: ``date,quantity,revenue,orders,observed`` -> sales-trend.csv."""
    trend = deps.analytics_for(batch).dashboard(_filter_params(start, end, product, country))['trend']
    payload = order_service.csv_bytes(trend, ['date', 'quantity', 'revenue', 'orders', 'observed'])
    return order_service.csv_response(payload, 'sales-trend.csv')


@router.get('/export/orders')
def export_orders(start: str | None = Query(None), end: str | None = Query(None),
                  product: str | None = Query(None), country: str | None = Query(None),
                  q: str = Query(''),
                  batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    params = dict(_filter_params(start, end, product, country), q=q)
    rows = list(order_service.iter_orders(deps.store_for(batch), params))
    payload = order_service.csv_bytes(rows, order_service.export_order_fields())
    return order_service.csv_response(payload, 'orders.csv')


@router.get('/export/transactions')
def export_transactions(start: str | None = Query(None), end: str | None = Query(None),
                        product: str | None = Query(None), country: str | None = Query(None),
                        batch: dict = Depends(_current_batch), _user: dict = Depends(current_user)):
    rows = order_service.iter_transactions(deps.store_for(batch), _filter_params(start, end, product, country))
    return order_service.csv_stream(order_service.export_transaction_fields(), rows, 'transactions.csv')
