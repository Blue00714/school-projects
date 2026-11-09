"""Simulated, item-level inventory scenarios (never real stock).

Scope (see ``API契约.md`` 5.6):

* A scenario is a pure **simulation**: quantities are manual inputs. Historical
  transactions are *never* deducted into stock automatically.
* ``balance = initial_qty + Σ in − Σ out``; the movement row and the derived balance are
  written in one sqlite transaction (``BEGIN IMMEDIATE`` serialises concurrent writers).
* ``inventory_movements(scenario_id, idempotency_key)`` is unique, so re-submitting the
  same outbound movement returns the original row with ``duplicate: true`` instead of
  booking twice.
* ``lead_time_demand = lead_time_days × daily_demand``;
  ``effective_in_transit`` = sum of ``status='in_transit'`` rows whose ``expected_date``
  is not later than ``today + lead_time_days``;
  ``candidate = max(0, lead_time_demand + safety_stock − balance − effective_in_transit)``.
* Confirming replenishment only registers an in-transit row; receiving it writes a real
  ``kind='in'`` movement and flips the row to ``received`` in the same transaction.

Every public payload carries ``simulated: true``.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from uuid import uuid4
from threading import Lock

from app.services.analytics import iso_date
from app.services.data import DataError

from app import db
from app.core.errors import BadRequest, NotFound
from app.services.batches import active_batch

SIMULATED = True
FORMULA = 'max(0, 提前期内场景需求 + 安全库存 - 当前库存 - 有效在途量)'
DEFAULT_LEAD_TIME = 7
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
MAX_ITEMS_PER_SCENARIO = 500
_DEMO_LOCK = Lock()


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _r(value) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return float(value)


def _num(value) -> str:
    """Compact human number for validation messages."""
    return format(_r(value), '.10g')


def _text(value, field: str, *, required: bool = False, max_length: int = 200) -> str | None:
    if value is None:
        if required:
            raise BadRequest(f'{field} 不能为空')
        return None
    if not isinstance(value, str):
        raise BadRequest(f'{field} 必须为文本')
    value = value.strip()
    if required and not value:
        raise BadRequest(f'{field} 不能为空')
    if len(value) > max_length:
        raise BadRequest(f'{field} 过长（最多 {max_length} 个字符）')
    return value


def _number(value, field: str, *, minimum: float = 0.0, integer: bool = False,
            maximum: float | None = None):
    if value is None:
        raise BadRequest(f'{field} 不能为空')
    if isinstance(value, bool):
        raise BadRequest(f'{field} 必须为数字')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f'{field} 必须为数字') from None
    if not math.isfinite(number):
        raise BadRequest(f'{field} 必须为有限数字')
    if integer and number != int(number):
        raise BadRequest(f'{field} 必须为整数')
    if number < minimum:
        raise BadRequest(f'{field} 不能小于 {_num(minimum)}')
    if maximum is not None and number > maximum:
        raise BadRequest(f'{field} 不能大于 {_num(maximum)}')
    return int(number) if integer else number


def _begin_immediate(cx) -> None:
    """Take the sqlite write lock up front so balance reads cannot interleave."""
    try:
        cx.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError:
        # A transaction is already open on this connection (defensive; db.connect opens none).
        pass


def _scenario_row(cx, scenario_id: str):
    row = cx.execute('select * from inventory_scenarios where id=?', (scenario_id,)).fetchone()
    if not row:
        raise NotFound('模拟库存场景不存在')
    return row


def _scenario_public(row, item_count: int) -> dict:
    return {
        'id': row['id'],
        'name': row['name'],
        'note': row['note'] or '',
        'created_at': row['created_at'],
        'item_count': int(item_count),
    }


def _balance(cx, scenario_id: str, stock_code: str) -> float | None:
    item = cx.execute('select initial_qty from inventory_items where scenario_id=? and stock_code=?',
                      (scenario_id, stock_code)).fetchone()
    if not item:
        return None
    delta = cx.execute(
        "select coalesce(sum(case when kind='in' then qty else -qty end),0) from inventory_movements "
        "where scenario_id=? and stock_code=?", (scenario_id, stock_code)).fetchone()[0]
    return _r(float(item['initial_qty']) + float(delta))


def _items_public(cx, scenario_id: str) -> list[dict]:
    """All items of a scenario with derived balance / transit / replenishment figures."""
    items = cx.execute('select * from inventory_items where scenario_id=? order by stock_code',
                       (scenario_id,)).fetchall()
    deltas = {
        row['stock_code']: float(row['delta'])
        for row in cx.execute(
            "select stock_code, coalesce(sum(case when kind='in' then qty else -qty end),0) delta "
            "from inventory_movements where scenario_id=? group by stock_code", (scenario_id,))
    }
    transit: dict[str, list[tuple[float, str]]] = {}
    for row in cx.execute(
            "select stock_code,qty,expected_date from inventory_in_transit "
            "where scenario_id=? and status='in_transit'", (scenario_id,)):
        transit.setdefault(row['stock_code'], []).append((float(row['qty']), str(row['expected_date'])))
    today = date.today()
    result = []
    for item in items:
        stock_code = item['stock_code']
        lead_time = int(item['lead_time_days'])
        daily_demand = float(item['daily_demand'])
        safety_stock = float(item['safety_stock'])
        balance = _r(float(item['initial_qty']) + deltas.get(stock_code, 0.0))
        limit_date = today + timedelta(days=lead_time)
        total = effective = 0.0
        for qty, expected in transit.get(stock_code, []):
            total += qty
            try:
                expected_date = date.fromisoformat(expected[:10])
            except ValueError:
                continue
            if expected_date <= limit_date:
                effective += qty
        lead_time_demand = lead_time * daily_demand
        candidate = max(0.0, lead_time_demand + safety_stock - balance - effective)
        result.append({
            'stock_code': stock_code,
            'name': item['name'] or '',
            'initial_qty': _r(item['initial_qty']),
            'lead_time_days': lead_time,
            'safety_stock': _r(safety_stock),
            'daily_demand': _r(daily_demand),
            'balance': balance,
            'in_transit_effective': _r(effective),
            'in_transit_total': _r(total),
            'replenishment_candidate': _r(candidate),
        })
    return result


def _item_public(cx, scenario_id: str, stock_code: str) -> dict | None:
    for item in _items_public(cx, scenario_id):
        if item['stock_code'] == stock_code:
            return item
    return None


def _movement_public(row) -> dict:
    return {
        'id': row['id'],
        'stock_code': row['stock_code'],
        'kind': row['kind'],
        'qty': _r(row['qty']),
        'balance_after': _r(row['balance_after']),
        'idempotency_key': row['idempotency_key'],
        'note': row['note'] or '',
        'created_at': row['created_at'],
    }


def _transit_public(row) -> dict:
    return {
        'id': row['id'],
        'stock_code': row['stock_code'],
        'qty': _r(row['qty']),
        'expected_date': row['expected_date'],
        'status': row['status'],
        'created_at': row['created_at'],
    }


def _batch_names(codes: list[str]) -> dict[str, str]:
    """Resolve display names for stock codes from the *current* batch (best effort)."""
    batch = active_batch()
    if not batch or not codes:
        return {}
    path = db.resolve_db_path(batch['db_path'])
    if not path.is_file():
        return {}
    placeholders = ','.join('?' for _ in codes)
    try:
        with db.connect(path) as cx:
            rows = cx.execute(
                f'select stock_code, max(description) name from transactions '
                f'where sale=1 and stock_code in ({placeholders}) group by stock_code', codes).fetchall()
    except sqlite3.Error:
        return {}
    return {row['stock_code']: (row['name'] or '')[:200] for row in rows}


# --------------------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------------------
def list_scenarios() -> list[dict]:
    with db.connect() as cx:
        rows = cx.execute(
            'select s.*, (select count(*) from inventory_items i where i.scenario_id=s.id) item_count '
            'from inventory_scenarios s order by s.created_at desc, s.id').fetchall()
    return [_scenario_public(row, row['item_count']) for row in rows]


def create_scenario(*, name, note=None, stock_codes=None, user=None) -> dict:
    name = _text(name, '场景名称', required=True, max_length=100)
    note = _text(note, '备注', max_length=500) or ''
    raw_codes = stock_codes or []
    if not isinstance(raw_codes, list):
        raise BadRequest('stock_codes 必须为数组')
    codes: list[str] = []
    seen: set[str] = set()
    for raw in raw_codes:
        code = _text(raw, 'stock_code', required=True, max_length=50)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if len(codes) > MAX_ITEMS_PER_SCENARIO:
        raise BadRequest(f'单个场景最多 {MAX_ITEMS_PER_SCENARIO} 个商品')
    if codes and not active_batch():
        raise NotFound('没有可用的数据批次，无法从当前批次创建商品清单')
    names = _batch_names(codes)
    scenario_id = uuid4().hex
    stamp = db.now_iso()
    with db.connect() as cx:
        cx.execute('insert into inventory_scenarios(id,name,note,created_by,created_at) values(?,?,?,?,?)',
                   (scenario_id, name, note, (user or {}).get('id'), stamp))
        for code in codes:
            cx.execute(
                'insert into inventory_items(scenario_id,stock_code,name,initial_qty,lead_time_days,'
                'safety_stock,daily_demand,updated_at) values(?,?,?,?,?,?,?,?)',
                (scenario_id, code, names.get(code, ''), 0.0, DEFAULT_LEAD_TIME, 0.0, 0.0, stamp))
        row = _scenario_row(cx, scenario_id)
    return _scenario_public(row, len(codes))


def ensure_demo_scenario(*, user=None) -> dict:
    """Create one persistent demo per batch; reloading never overwrites user edits."""
    from app.api.deps import store_for
    batch = active_batch()
    if not batch:
        raise NotFound('请先导入交易数据')
    scenario_id = 'demo-' + batch['id']
    with _DEMO_LOCK:
        with db.connect() as cx:
            existing = cx.execute('select * from inventory_scenarios where id=?', (scenario_id,)).fetchone()
            if existing:
                count = cx.execute('select count(*) from inventory_items where scenario_id=?', (scenario_id,)).fetchone()[0]
                return {'scenario': _scenario_public(existing, count), 'duplicate': True, 'simulated': True}
        store = store_for(batch)
        manifest = store.manifest()
        end = date.fromisoformat(manifest['date_max'])
        start = max(date.fromisoformat(manifest['date_min']), end-timedelta(days=27))
        days = (end-start).days+1
        with store.connect() as cx:
            products = cx.execute(
                'select stock_code,max(description) name,sum(quantity) quantity from transactions '
                'where sale=1 and date>=? and date<=? group by stock_code '
                'having count(distinct date)>=? order by sum(quantity) desc,stock_code limit 12',
                (str(start), str(end), min(8, days))).fetchall()
        if not products:
            raise BadRequest('该批次没有足够的连续销售商品来建立演示，请使用原始数据批次')
        note = (f'模拟演示；商品名称来自批次 {batch["id"]}。日需求参考{start}至{end}的日均记录销量；'
                '初始库存、出入库及采购在途为模拟设定，未由历史订单自动扣减。重复载入保留当前操作结果。')
        stamp = db.now_iso()
        today = date.today()
        actor = (user or {}).get('id')
        with db.connect() as cx:
            _begin_immediate(cx)
            cx.execute('insert into inventory_scenarios(id,name,note,created_by,created_at) values(?,?,?,?,?)',
                       (scenario_id, '补货运营演示 · 模拟', note, actor, stamp))
            for i, product in enumerate(products):
                code = product['stock_code']
                demand = float(max(1, math.ceil(float(product['quantity'])/days)))
                case = i % 4
                initial = demand * [1, 12, 1, 2][case]
                lead = [7, 5, 5, 7][case]
                cx.execute('insert into inventory_items(scenario_id,stock_code,name,initial_qty,lead_time_days,'
                           'safety_stock,daily_demand,updated_at) values(?,?,?,?,?,?,?,?)',
                           (scenario_id, code, product['name'] or code, initial, lead, demand*(3 if case == 0 else 2), demand, stamp))
                balance = initial
                for kind, qty, offset, desc in [('in', demand*2, -2, '模拟常规到货'), ('out', demand, -1, '模拟销售出库')]:
                    balance += qty if kind == 'in' else -qty
                    created = f'{today+timedelta(days=offset)}T09:00:00+08:00'
                    cx.execute('insert into inventory_movements(id,scenario_id,stock_code,kind,qty,balance_after,'
                               'idempotency_key,note,created_by,created_at) values(?,?,?,?,?,?,?,?,?,?)',
                               (uuid4().hex, scenario_id, code, kind, qty, balance, f'demo-{code}-{kind}', desc, actor, created))
                if case in (1, 2, 3):
                    received = case == 1
                    qty = demand * (3 if received else (5 if case == 2 else 6))
                    expected = today+timedelta(days=(-1 if received else (2 if case == 2 else 14)))
                    tid = uuid4().hex
                    cx.execute('insert into inventory_in_transit(id,scenario_id,stock_code,qty,expected_date,status,'
                               'created_at,created_by) values(?,?,?,?,?,?,?,?)',
                               (tid, scenario_id, code, qty, str(expected), 'received' if received else 'in_transit', stamp, actor))
                    if received:
                        cx.execute('insert into inventory_movements(id,scenario_id,stock_code,kind,qty,balance_after,'
                                   'idempotency_key,note,created_by,created_at) values(?,?,?,?,?,?,?,?,?,?)',
                                   (uuid4().hex, scenario_id, code, 'in', qty, balance+qty, f'demo-{code}-received',
                                    f'模拟在途到货 {tid}', actor, f'{today-timedelta(days=1)}T10:00:00+08:00'))
            row = _scenario_row(cx, scenario_id)
        return {'scenario': _scenario_public(row, len(products)), 'duplicate': False, 'simulated': True}


def get_scenario(scenario_id: str) -> dict:
    with db.connect() as cx:
        row = _scenario_row(cx, scenario_id)
        count = cx.execute('select count(*) from inventory_items where scenario_id=?', (scenario_id,)).fetchone()[0]
    return _scenario_public(row, count)


def items_payload(scenario_id: str) -> dict:
    with db.connect() as cx:
        row = _scenario_row(cx, scenario_id)
        items = _items_public(cx, scenario_id)
    return {'scenario': _scenario_public(row, len(items)), 'items': items, 'simulated': SIMULATED}


def update_item(scenario_id: str, stock_code: str, changes: dict) -> dict:
    """Update item parameters; unknown stock codes are created (upsert).

    Scenario items are manual what-if inputs, so a code does not have to exist in the
    current data batch (e.g. ``C001``); a missing item is created with zero quantities and
    the code itself as the default display name.
    """
    changes = changes or {}
    stock_code = _text(stock_code, 'stock_code', required=True, max_length=50)
    with db.connect() as cx:
        _begin_immediate(cx)
        _scenario_row(cx, scenario_id)
        item = cx.execute('select * from inventory_items where scenario_id=? and stock_code=?',
                          (scenario_id, stock_code)).fetchone()
        updates: dict = {}
        if changes.get('name') is not None:
            updates['name'] = _text(changes.get('name'), 'name', max_length=200) or ''
        if changes.get('initial_qty') is not None:
            updates['initial_qty'] = _number(changes.get('initial_qty'), 'initial_qty', minimum=0.0)
        if changes.get('lead_time_days') is not None:
            updates['lead_time_days'] = _number(changes.get('lead_time_days'), 'lead_time_days',
                                                minimum=0, maximum=365, integer=True)
        if changes.get('safety_stock') is not None:
            updates['safety_stock'] = _number(changes.get('safety_stock'), 'safety_stock', minimum=0.0)
        if changes.get('daily_demand') is not None:
            updates['daily_demand'] = _number(changes.get('daily_demand'), 'daily_demand', minimum=0.0)
        if item is None:
            cx.execute(
                'insert into inventory_items(scenario_id,stock_code,name,initial_qty,lead_time_days,'
                'safety_stock,daily_demand,updated_at) values(?,?,?,?,?,?,?,?)',
                (scenario_id, stock_code, stock_code, 0.0, DEFAULT_LEAD_TIME, 0.0, 0.0, db.now_iso()))
        if updates:
            assignments = ', '.join(f'{field}=?' for field in updates)
            cx.execute(f'update inventory_items set {assignments}, updated_at=? '
                       f'where scenario_id=? and stock_code=?',
                       [*updates.values(), db.now_iso(), scenario_id, stock_code])
        return {'item': _item_public(cx, scenario_id, stock_code), 'simulated': SIMULATED}


# --------------------------------------------------------------------------------------
# movements
# --------------------------------------------------------------------------------------
def create_movement(scenario_id: str, payload: dict, *, user=None) -> dict:
    payload = payload or {}
    stock_code = _text(payload.get('stock_code'), 'stock_code', required=True, max_length=50)
    kind = payload.get('kind')
    if kind not in ('in', 'out'):
        raise BadRequest('kind 必须为 in 或 out')
    qty = _number(payload.get('qty'), 'qty', minimum=0.0)
    if qty <= 0:
        raise BadRequest('数量必须大于 0')
    idempotency_key = _text(payload.get('idempotency_key'), 'idempotency_key', max_length=100) or None
    note = _text(payload.get('note'), 'note', max_length=500) or ''
    with db.connect() as cx:
        _begin_immediate(cx)
        _scenario_row(cx, scenario_id)
        if idempotency_key:
            existing = cx.execute(
                'select * from inventory_movements where scenario_id=? and idempotency_key=?',
                (scenario_id, idempotency_key)).fetchone()
            if existing:
                return {'duplicate': True, 'movement': _movement_public(existing),
                        'item': _item_public(cx, scenario_id, existing['stock_code']),
                        'simulated': SIMULATED}
        balance = _balance(cx, scenario_id, stock_code)
        if balance is None:
            raise NotFound(f'该场景中不存在商品 {stock_code}')
        if kind == 'out' and qty > balance + 1e-9:
            raise BadRequest(f'出库数量超过当前余额：当前余额 {_num(balance)}，请求出库 {_num(qty)}')
        balance_after = _r(balance + qty if kind == 'in' else balance - qty)
        movement_id = uuid4().hex
        try:
            cx.execute(
                'insert into inventory_movements(id,scenario_id,stock_code,kind,qty,balance_after,'
                'idempotency_key,note,created_by,created_at) values(?,?,?,?,?,?,?,?,?,?)',
                (movement_id, scenario_id, stock_code, kind, qty, balance_after,
                 idempotency_key, note, (user or {}).get('id'), db.now_iso()))
        except sqlite3.IntegrityError:
            # Concurrent submission of the same idempotency key: return the winner.
            existing = cx.execute(
                'select * from inventory_movements where scenario_id=? and idempotency_key=?',
                (scenario_id, idempotency_key)).fetchone()
            if existing:
                return {'duplicate': True, 'movement': _movement_public(existing),
                        'item': _item_public(cx, scenario_id, existing['stock_code']),
                        'simulated': SIMULATED}
            raise BadRequest('重复的幂等键，但找不到已记账流水，请改用新的 idempotency_key') from None
        row = cx.execute('select * from inventory_movements where id=?', (movement_id,)).fetchone()
        return {'duplicate': False, 'movement': _movement_public(row),
                'item': _item_public(cx, scenario_id, stock_code), 'simulated': SIMULATED}


def list_movements(scenario_id: str, limit=DEFAULT_LIMIT) -> dict:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise BadRequest('limit 必须为整数') from None
    if not 1 <= value <= MAX_LIMIT:
        raise BadRequest(f'limit 须在 1—{MAX_LIMIT} 之间')
    with db.connect() as cx:
        _scenario_row(cx, scenario_id)
        rows = cx.execute(
            'select * from inventory_movements where scenario_id=? '
            'order by created_at desc, rowid desc limit ?', (scenario_id, value)).fetchall()
    return {'movements': [_movement_public(row) for row in rows], 'limit': value, 'simulated': SIMULATED}


# --------------------------------------------------------------------------------------
# replenishment / in-transit
# --------------------------------------------------------------------------------------
def replenishment(scenario_id: str) -> dict:
    with db.connect() as cx:
        _scenario_row(cx, scenario_id)
        items = _items_public(cx, scenario_id)
    rows = [{
        'stock_code': item['stock_code'],
        'name': item['name'],
        'current_qty': item['balance'],
        'lead_time_demand': _r(item['lead_time_days'] * item['daily_demand']),
        'effective_in_transit': item['in_transit_effective'],
        'safety_stock': item['safety_stock'],
        'candidate': item['replenishment_candidate'],
    } for item in items]
    return {'formula': FORMULA, 'items': rows, 'simulated': SIMULATED}


def confirm_replenishment(scenario_id: str, payload: dict, *, user=None) -> dict:
    payload = payload or {}
    stock_code = _text(payload.get('stock_code'), 'stock_code', required=True, max_length=50)
    qty = _number(payload.get('qty'), 'qty', minimum=0.0)
    if qty <= 0:
        raise BadRequest('数量必须大于 0')
    try:
        expected_date = iso_date(payload.get('expected_date'))
    except DataError as exc:
        raise BadRequest(str(exc)) from None
    # ``idempotency_key`` is accepted for client correlation; the fixed schema has no
    # unique index on inventory_in_transit, so this endpoint does not de-duplicate.
    _text(payload.get('idempotency_key'), 'idempotency_key', max_length=100)
    with db.connect() as cx:
        _begin_immediate(cx)
        _scenario_row(cx, scenario_id)
        if _balance(cx, scenario_id, stock_code) is None:
            raise NotFound(f'该场景中不存在商品 {stock_code}')
        transit_id = uuid4().hex
        cx.execute(
            'insert into inventory_in_transit(id,scenario_id,stock_code,qty,expected_date,status,'
            'created_at,created_by) values(?,?,?,?,?,?,?,?)',
            (transit_id, scenario_id, stock_code, qty, expected_date, 'in_transit',
             db.now_iso(), (user or {}).get('id')))
        row = cx.execute('select * from inventory_in_transit where id=?', (transit_id,)).fetchone()
        return {'in_transit': _transit_public(row),
                'item': _item_public(cx, scenario_id, stock_code), 'simulated': SIMULATED}


def list_in_transit(scenario_id: str) -> dict:
    with db.connect() as cx:
        _scenario_row(cx, scenario_id)
        rows = cx.execute(
            'select * from inventory_in_transit where scenario_id=? order by created_at desc, rowid desc',
            (scenario_id,)).fetchall()
    return {'items': [_transit_public(row) for row in rows], 'simulated': SIMULATED}


def receive_in_transit(scenario_id: str, transit_id: str, *, user=None) -> dict:
    with db.connect() as cx:
        _begin_immediate(cx)
        _scenario_row(cx, scenario_id)
        row = cx.execute('select * from inventory_in_transit where scenario_id=? and id=?',
                         (scenario_id, transit_id)).fetchone()
        if not row:
            raise NotFound('在途记录不存在')
        if row['status'] != 'in_transit':
            raise BadRequest('该在途记录已入库，不能重复入库')
        stock_code = row['stock_code']
        balance = _balance(cx, scenario_id, stock_code)
        if balance is None:
            raise NotFound(f'该场景中不存在商品 {stock_code}')
        qty = float(row['qty'])
        balance_after = _r(balance + qty)
        movement_id = uuid4().hex
        cx.execute(
            'insert into inventory_movements(id,scenario_id,stock_code,kind,qty,balance_after,'
            'idempotency_key,note,created_by,created_at) values(?,?,?,?,?,?,?,?,?,?)',
            (movement_id, scenario_id, stock_code, 'in', qty, balance_after, None,
             f'在途到货 {transit_id}', (user or {}).get('id'), db.now_iso()))
        cx.execute("update inventory_in_transit set status='received' where id=?", (transit_id,))
        movement = cx.execute('select * from inventory_movements where id=?', (movement_id,)).fetchone()
        received = cx.execute('select * from inventory_in_transit where id=?', (transit_id,)).fetchone()
        return {'movement': _movement_public(movement), 'in_transit': _transit_public(received),
                'item': _item_public(cx, scenario_id, stock_code), 'simulated': SIMULATED}
