"""Order-level aggregation and CSV export helpers (OWNER: data-api).

All queries run against the *selected batch* database (``deps.store_for(batch)``) and
reuse ``app.services.analytics.filters`` / ``where`` so the dashboard and the order list can
never disagree about the sales policy:

* only ``sale=1`` rows count (positive quantity, not cancelled, not a cross-sheet copy);
* ``amount_units`` is GBP × 10000 per row, so ``sum(amount_units) / 10000.0`` is revenue;
* an order is ``order_key = invoice``; the order key was validated conflict-free when the
  batch was built (see ``app.services.data``).

CSV writers are shared by the analytics and imports routers: UTF-8 BOM, formula-injection
neutralisation (``= + - @`` are prefixed with ``'``) and text identifiers kept as text.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable, Iterator

from fastapi.responses import Response, StreamingResponse
from app.services.analytics import filters as analytics_filters, where as analytics_where
from app.services.data import DataError

# Single-page limits, identical to the legacy /api/transactions contract.
MAX_PAGE_SIZE = 200
MAX_PAGE = 100000
# Order rows expose at most this many distinct product codes.
MAX_PRODUCT_CODES = 5
FORMULA_CHARS = ('=', '+', '-', '@')
CHUNK_BYTES = 64 * 1024


def neutralize(value):
    """Prefix spreadsheet formula triggers; keep text identifiers untouched otherwise."""
    if isinstance(value, str) and value[:1] in FORMULA_CHARS:
        return "'" + value
    return value


def csv_bytes(rows: Iterable, fields: list[str]) -> bytes:
    """Encode rows as UTF-8 CSV with BOM and formula-injection neutralisation."""
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({key: neutralize(row.get(key)) for key in fields})
    return ('\ufeff' + text.getvalue()).encode('utf-8')


def csv_response(payload: bytes, filename: str) -> Response:
    return Response(content=payload, media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })


def csv_stream(fields: list[str], rows: Iterator[dict], filename: str) -> StreamingResponse:
    """Stream a potentially large CSV (whole-batch transaction export) in chunks."""

    def generate():
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        yield ('\ufeff' + buffer.getvalue()).encode('utf-8')
        buffer.seek(0)
        buffer.truncate(0)
        for row in rows:
            writer.writerow({key: neutralize(row.get(key)) for key in fields})
            if buffer.tell() >= CHUNK_BYTES:
                yield buffer.getvalue().encode('utf-8')
                buffer.seek(0)
                buffer.truncate(0)
        if buffer.tell():
            yield buffer.getvalue().encode('utf-8')

    return StreamingResponse(generate(), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })


def _escape_like(term: str) -> str:
    return term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def page_args(params: dict) -> tuple[int, int]:
    """Validate paging exactly like the legacy transactions endpoint (400 on failure)."""
    try:
        page = int(params.get('page', 1))
        size = int(params.get('page_size', 25))
    except (ValueError, TypeError):
        raise DataError('分页参数应为整数') from None
    if not 1 <= page <= MAX_PAGE or not 1 <= size <= MAX_PAGE_SIZE:
        raise DataError('页码至少1，单页1至200行')
    return page, size


def order_filters(store, params: dict) -> tuple[dict, str, list]:
    """Resolve date/product/country/q into (filters, where-clause, arguments)."""
    f = analytics_filters(params or {}, store.manifest())
    clause, args = analytics_where(f)
    q = str((params or {}).get('q') or '').strip()[:100]
    if q:
        term = _escape_like(q)
        clause += " and (order_key like ? escape '\\' or customer_id like ? escape '\\')"
        args = args + [f'%{term}%', f'%{term}%']
    return f, clause, args


ORDER_HEADER_SQL = '''
    select order_key, min(date) date, min(invoice_time) first_time, max(invoice_time) last_time,
           max(customer_id) customer_id, max(country) country, count(*) lines,
           sum(quantity) quantity, sum(amount_units)/10000.0 revenue
    from transactions where {clause}
    group by order_key
    order by date desc, order_key
'''


def list_orders(store, params: dict) -> dict:
    """Paged order list: one row per invoice with the keys documented in API 5.3."""
    f, clause, args = order_filters(store, params)
    page, size = page_args(params)
    with store.connect() as cx:
        total = cx.execute(f'select count(distinct order_key) from transactions where {clause}', args).fetchone()[0]
        rows = [dict(r) for r in cx.execute(
            ORDER_HEADER_SQL.format(clause=clause) + ' limit ? offset ?', args + [size, (page - 1) * size])]
        if rows:
            keys = [row['order_key'] for row in rows]
            marks = ','.join('?' * len(keys))
            codes: dict[str, list] = {}
            for item in cx.execute(
                    f'''select order_key, stock_code, sum(quantity) quantity from transactions
                        where {clause} and order_key in ({marks})
                        group by order_key, stock_code order by order_key, quantity desc, stock_code''',
                    args + keys):
                bucket = codes.setdefault(item['order_key'], [])
                if len(bucket) < MAX_PRODUCT_CODES:
                    bucket.append(item['stock_code'])
            for row in rows:
                row['product_codes'] = codes.get(row['order_key'], [])
    return {'filters': f, 'page': page, 'page_size': size, 'total': total, 'rows': rows}


DETAIL_SQL = '''
    select invoice, stock_code, description, quantity, unit_price,
           case when amount_eligible=1 then amount_units/10000.0 else null end revenue,
           invoice_time, customer_id, country, sheet, source_row
    from transactions where {clause} and order_key=?
    order by invoice_time, invoice, stock_code, sheet, source_row
'''


def order_detail(store, order_key: str, params: dict) -> dict | None:
    """Order header plus its transaction lines, or ``None`` when the order is not in range."""
    _f, clause, args = order_filters(store, params)
    with store.connect() as cx:
        rows = [dict(r) for r in cx.execute(DETAIL_SQL.format(clause=clause), args + [order_key])]
        if not rows:
            return None
        header = cx.execute(
            '''select min(date) date, min(invoice_time) first_time, max(invoice_time) last_time,
                      max(customer_id) customer_id, max(country) country, count(*) lines,
                      sum(quantity) quantity, sum(amount_units)/10000.0 revenue
               from transactions where ''' + clause + ' and order_key=?',
            args + [order_key]).fetchone()
        order = {
            'order_key': order_key,
            'date': header['date'], 'first_time': header['first_time'], 'last_time': header['last_time'],
            'customer_id': header['customer_id'], 'country': header['country'],
            'lines': header['lines'], 'quantity': header['quantity'], 'revenue': header['revenue'],
        }
    return {'order': order, 'rows': rows}


def iter_orders(store, params: dict) -> Iterator[dict]:
    """All matching orders (no paging) for the order CSV export."""
    _f, clause, args = order_filters(store, params)
    with store.connect() as cx:
        cursor = cx.execute(ORDER_HEADER_SQL.format(clause=clause), args)
        while True:
            block = cursor.fetchmany(1000)
            if not block:
                break
            for row in block:
                yield dict(row)


def export_order_fields() -> list[str]:
    return ['order_key', 'date', 'first_time', 'customer_id', 'country', 'lines', 'quantity', 'revenue']


TRANSACTION_SQL = '''
    select invoice, stock_code, description, quantity, unit_price,
           case when amount_eligible=1 then amount_units/10000.0 else null end revenue,
           invoice_time, customer_id, country, sheet, source_row
    from transactions where {clause}
    order by invoice_time desc, invoice, stock_code, sheet, source_row
'''


def iter_transactions(store, params: dict) -> Iterator[dict]:
    """All matching sale lines (streamed) for the transaction CSV export."""
    _f, clause, args = order_filters(store, params)
    with store.connect() as cx:
        cursor = cx.execute(TRANSACTION_SQL.format(clause=clause), args)
        while True:
            block = cursor.fetchmany(2000)
            if not block:
                break
            for row in block:
                yield dict(row)


def export_transaction_fields() -> list[str]:
    return ['invoice', 'stock_code', 'description', 'quantity', 'unit_price', 'revenue',
            'invoice_time', 'customer_id', 'country', 'sheet', 'source_row']
