"""Deterministic fixtures for the API test suite.

* small upload samples (CSV built with the standard library, XLSX via openpyxl which is
  already a project dependency of ``services/data.py``);
* the fixed 30-question AI sample used for M5 acceptance (numbers must be traceable to
  tool/dashboard results);
* helpers that turn a ``/api/dashboard`` payload into an approved number set so an AI
  answer can only repeat numbers that the tools really produced.
"""
from __future__ import annotations

import csv
import io
import re

# --------------------------------------------------------------------- upload data
# Invoices are deliberately long numeric strings: they must stay strings (no float /
# scientific notation) through upload, order listing and CSV export.  Stock code
# ``00999`` checks that leading zeros survive.  One description starts with ``=`` to
# check CSV formula-injection neutralisation on export.
SAMPLE_COLUMNS = ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate',
                  'Price', 'Customer ID', 'Country']

SAMPLE_ROWS = [
    ['900000000001', '00999', 'Alpha widget', 5, '2011-02-01 10:00:00', 2.00, '10001', 'United Kingdom'],
    ['900000000001', 'QA-B', 'Beta widget', 3, '2011-02-01 10:05:00', 1.50, '10001', 'United Kingdom'],
    ['900000000002', '00999', 'Alpha widget', 2, '2011-02-03 09:30:00', 2.50, '10002', 'France'],
    ['900000000003', 'QA-C', '=SUM(1,2)', 4, '2011-02-05 11:15:00', 1.25, '10003', 'Germany'],
]

# Totals of SAMPLE_ROWS under the documented cleaning policy (all rows are clean
# positive sales, no cancelled rows, no cross-sheet copies).
SAMPLE_TOTALS = {
    'quantity': 14,
    'revenue': 24.50,
    'orders': 3,
    'lines': 4,
    'products': 3,
    'date_min': '2011-02-01',
    'date_max': '2011-02-05',
}
SAMPLE_ORDER_KEYS = ['900000000001', '900000000002', '900000000003']


def sample_csv_bytes() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(SAMPLE_COLUMNS)
    writer.writerows(SAMPLE_ROWS)
    return buffer.getvalue().encode('utf-8')


def sample_xlsx_bytes(rows=None) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'First'
    sheet.append(SAMPLE_COLUMNS)
    for row in (rows or SAMPLE_ROWS):
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


LONG_HISTORY_DAYS = 70
LONG_HISTORY_START = '2011-03-01'
LONG_HISTORY_QUANTITY = 10
LONG_HISTORY_PRICE = 1.0


def long_history_csv_bytes(days: int = LONG_HISTORY_DAYS, start: str = LONG_HISTORY_START,
                           quantity: int = LONG_HISTORY_QUANTITY,
                           price: float = LONG_HISTORY_PRICE) -> bytes:
    """One clean invoice per calendar day: enough history for a real 7-day forecast."""
    from datetime import date, timedelta

    first = date.fromisoformat(start)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(SAMPLE_COLUMNS)
    for offset in range(days):
        day = first + timedelta(days=offset)
        writer.writerow([f'91000000{offset:04d}', 'QA-H1', 'History widget', quantity,
                         f'{day.isoformat()} 10:00:00', f'{float(price):.2f}',
                         '20001', 'United Kingdom'])
    return buffer.getvalue().encode('utf-8')


def missing_column_csv_bytes() -> bytes:
    """Same sample with the mandatory ``Price`` column removed."""
    rows = [[row[0], row[1], row[2], row[3], row[4], row[6], row[7]] for row in SAMPLE_ROWS]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow([c for c in SAMPLE_COLUMNS if c != 'Price'])
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')


# ------------------------------------------------------------------- AI 30 questions
# ``scope`` is the date range that an honest tool call should use; the test only checks
# numbers for answered questions whose scope is given (a subset check against the real
# dashboard response for the same range).  ``expect`` documents the intended behaviour:
#   clarify -> the question cannot be mapped, a follow-up question is required
#   any     -> answer or clarification are both acceptable, only structure is checked
AI_QUESTIONS = [
    {'id': 1, 'question': '2011-11-10到2011-12-09的全店销量、销售额和订单数分别是多少？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 2, 'question': '2011-11-10到2011-12-09期间英国的销量是多少？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09', 'country': 'United Kingdom'}, 'expect': 'ok'},
    {'id': 3, 'question': '2011-11-10到2011-12-09法国的销售额是多少？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09', 'country': 'France'}, 'expect': 'ok'},
    {'id': 4, 'question': '2011-11-01到2011-11-30德国订单数是多少？',
     'scope': {'start': '2011-11-01', 'end': '2011-11-30', 'country': 'Germany'}, 'expect': 'ok'},
    {'id': 5, 'question': '2011-11-01到2011-11-30销量最高的前5个商品是哪些？',
     'scope': {'start': '2011-11-01', 'end': '2011-11-30'}, 'expect': 'ok'},
    {'id': 6, 'question': '2011-12-01到2011-12-09每天的销量是多少？',
     'scope': {'start': '2011-12-01', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 7, 'question': '2011-10-11到2011-11-09全店销量、销售额、订单数汇总',
     'scope': {'start': '2011-10-11', 'end': '2011-11-09'}, 'expect': 'ok'},
    {'id': 8, 'question': '2011年9月全店销售额是多少？',
     'scope': {'start': '2011-09-01', 'end': '2011-09-30'}, 'expect': 'ok'},
    {'id': 9, 'question': '2011-11-10到2011-12-09的销量和销售额比上一个等长区间变化多少？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 10, 'question': '原始数据覆盖的日期范围和总行数是多少？', 'scope': None, 'expect': 'ok'},
    {'id': 11, 'question': '当前生效的数据批次是哪一个？', 'scope': None, 'expect': 'ok'},
    {'id': 12, 'question': '2011年11月订单数最多的是哪一天？',
     'scope': {'start': '2011-11-01', 'end': '2011-11-30'}, 'expect': 'ok'},
    {'id': 13, 'question': '2011-11-10到2011-12-09有多少条正向销售明细行？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 14, 'question': '2011-11-10到2011-12-09的平均客单价是多少？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 15, 'question': '商品22423在2011年11月的销量是多少？',
     'scope': {'start': '2011-11-01', 'end': '2011-11-30', 'product': '22423'}, 'expect': 'ok'},
    {'id': 16, 'question': '2011年11月名称包含JUMBO的商品销量是多少？',
     'scope': {'start': '2011-11-01', 'end': '2011-11-30'}, 'expect': 'any'},
    {'id': 17, 'question': '预测未来7天的全店销量', 'scope': None, 'expect': 'ok'},
    {'id': 18, 'question': '这次预测的起点和模型版本是什么？', 'scope': None, 'expect': 'any'},
    {'id': 19, 'question': '最近56天回测的WAPE是多少？', 'scope': None, 'expect': 'any'},
    {'id': 20, 'question': '2011-11-10到2011-12-09国家销量排名前5',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 21, 'question': '2011年全年的销量是多少？',
     'scope': {'start': '2011-01-01', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 22, 'question': '2010-01-01到2010-01-02的销量是多少？',
     'scope': {'start': '2010-01-01', 'end': '2010-01-02'}, 'expect': 'ok'},
    {'id': 23, 'question': '这些销售额里面包含利润吗？利润是多少？', 'scope': None, 'expect': 'any'},
    {'id': 24, 'question': '2011-11-10到2011-12-09英国和法国的销量对比',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 25, 'question': '2011-12-09当天的订单数是多少？',
     'scope': {'start': '2011-12-09', 'end': '2011-12-09'}, 'expect': 'ok'},
    {'id': 26, 'question': '当前有哪些模拟库存场景？', 'scope': None, 'expect': 'any'},
    {'id': 27, 'question': 'QA模拟场景里QA-ITEM-1的补货候选量是多少？', 'scope': None, 'expect': 'any'},
    {'id': 28, 'question': '今天天气怎么样？', 'scope': None, 'expect': 'clarify'},
    {'id': 29, 'question': '帮我随便分析一下。', 'scope': None, 'expect': 'clarify'},
    {'id': 30, 'question': '2011-11-10到2011-12-09有哪些天的销量超过一万？',
     'scope': {'start': '2011-11-10', 'end': '2011-12-09'}, 'expect': 'ok'},
]

# Questions whose answer must never invent a profit figure (no cost data exists).
NO_PROFIT_QUESTION_IDS = [23]
# Questions that must trigger a follow-up instead of a fabricated answer.
# #29 is ambiguous but may legitimately fall back to the default 30-day range, so it is
# only checked structurally; #28 cannot be mapped to any read-only tool at all.
MUST_CLARIFY_IDS = [28]


# ------------------------------------------------------- dashboard number approval


def _add_number(out: set[float], value) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float('inf'), float('-inf')):  # NaN/inf
            return
        out.add(round(number, 6))
        out.add(float(round(number)))
        out.add(round(number, 2))
        out.add(round(number, 1))


def _add_dates(out: set[float], value) -> None:
    if not isinstance(value, str):
        return
    parts = value.replace('T', ' ').replace(':', '-').replace(' ', '-').split('-')
    for part in parts:
        if part.isdigit():
            out.add(float(int(part)))


def _walk(out: set[float], node, *, skip_keys=()) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip_keys:
                continue
            if key in ('date', 'start', 'end', 'created_at', 'published_at', 'prepared_at',
                       'filters', 'coverage'):
                _add_dates(out, value)
                continue
            _walk(out, value, skip_keys=skip_keys)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk(out, value, skip_keys=skip_keys)
    else:
        _add_number(out, node)


def dashboard_numbers(payload: dict) -> set[float]:
    """All numbers an honest answer may quote for one dashboard range.

    Covers totals, the previous period, trend, ranking, country and weekday aggregates,
    plus date components (so "2011" / "12" / "09" never look fabricated).
    """
    out: set[float] = set()
    _walk(out, payload.get('totals') or {})
    _walk(out, (payload.get('previous') or {}).get('totals') or {})
    _walk(out, payload.get('changes') or {})
    for item in payload.get('trend') or []:
        _add_dates(out, item.get('date'))
        _walk(out, {k: v for k, v in item.items() if k != 'date'})
    for item in payload.get('ranking') or []:
        _walk(out, {k: v for k, v in item.items() if k not in ('stock_code', 'name')})
    for item in payload.get('countries') or []:
        _walk(out, {k: v for k, v in item.items() if k != 'country'})
    for item in payload.get('weekdays') or []:
        _walk(out, item)
    _walk(out, payload.get('quality') or {})
    for key in ('start', 'end'):
        _add_dates(out, (payload.get('filters') or {}).get(key) or '')
        _add_dates(out, (payload.get('coverage') or {}).get(key) or '')
    return out


def extra_numbers_from_batch(batch: dict) -> set[float]:
    """Manifest/coverage numbers are legitimate tool output too."""
    out: set[float] = set()
    manifest = (batch or {}).get('manifest') or {}
    _walk(out, manifest, skip_keys=('rules',))
    _add_dates(out, manifest.get('date_min') or '')
    _add_dates(out, manifest.get('date_max') or '')
    return out


def number_approved(value: float, approved: set[float]) -> bool:
    """Tolerant membership: exact, rounded, or within half a unit of a known value."""
    if value in approved:
        return True
    tolerance = 0.51
    for candidate in approved:
        if abs(value - candidate) <= tolerance:
            return True
        if candidate and abs(value - candidate) / abs(candidate) <= 1e-9:
            return True
    return False


def all_numbers(value) -> set[float]:
    """Every numeric token visible in a JSON structure (numbers and numeric strings).

    Used to build the "approved" set from real tool responses (dashboard, order page,
    product search, batch manifest).  Strings are scanned so that quoted order keys such
    as ``'581475'`` and dates like ``'2011-12-09'`` are included.
    """
    out: set[float] = set()
    number_re = re.compile(r'-?\d[\d,]*(?:\.\d+)?')

    def walk(node):
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            _add_number(out, node)
        elif isinstance(node, str):
            for token in number_re.findall(node):
                try:
                    out.add(float(token.replace(',', '')))
                except ValueError:  # pragma: no cover - defensive
                    continue

    walk(value)
    return out
