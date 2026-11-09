"""Deterministic operations brief (Markdown) for the AI tab.

The brief is a fixed template filled with numbers produced by the AI read-only tool
layer (:mod:`app.services.assistant`), so it is reproducible and never fabricated:

* data source / batch / covering range / policy version are always printed;
* historical actuals, the 7-day forecast and the simulated inventory are labelled
  separately, each with its limitation;
* the generator is a template (``provider="template"``) and says so; no LLM is used;
* the Markdown is stored in the ``briefs`` table and downloadable as
  ``/api/ai/briefs/{id}.md``.
"""
from __future__ import annotations

import json
import time
import uuid

from app import db
from app.api.deps import active_batch
from app.services import assistant

PROVIDER = 'template'


def _num(value, digits=2):
    if value is None:
        return '—'
    try:
        return f'{float(value):.{digits}f}'
    except (TypeError, ValueError):
        return str(value)


def _int(value):
    if value is None:
        return '—'
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return str(value)


def _pct(value):
    if value is None:
        return '—'
    try:
        return f'{float(value) * 100:+.1f}%'
    except (TypeError, ValueError):
        return str(value)


def _base_params(scope: dict) -> dict:
    return {key: scope[key] for key in ('start', 'end', 'product', 'country') if scope.get(key)}


def _section_metrics(dashboard: dict) -> list[str]:
    data = dashboard.get('data') or {}
    totals = data.get('totals') or {}
    previous = (data.get('changes') or {})
    filters = data.get('filters') or {}
    scope_text = f"{filters.get('start')}~{filters.get('end')}"
    if filters.get('product'):
        scope_text += f"，商品 {filters['product']}"
    if filters.get('country'):
        scope_text += f"，国家 {filters['country']}"
    lines = [
        '## 二、核心指标（历史实际）',
        '',
        f'- 统计范围：{scope_text}',
        f"- 成交量：{_int(totals.get('quantity'))} 件",
        f"- 销售额：£{_num(totals.get('revenue'))}（明细行 {_int(totals.get('lines'))}，"
        f"有效金额行 {_int(totals.get('amount_rows'))}）",
        f"- 订单数：{_int(totals.get('orders'))} 笔（按 invoice 去重）",
        f"- 涉及商品数：{_int(totals.get('products'))}",
        f"- 客单价：£{_num(totals.get('average_order_value'))}",
        '',
        '| 指标 | 环比上一等长区间 |',
        '|---|---|',
        f"| 成交量 | {_pct(previous.get('quantity'))} |",
        f"| 销售额 | {_pct(previous.get('revenue'))} |",
        f"| 订单数 | {_pct(previous.get('orders'))} |",
        '',
        '> 环比为“上一等长区间”对比；若上一区间超出数据覆盖范围则不计算（显示 —）。',
        '',
    ]
    return lines


def _section_ranking(dashboard: dict) -> list[str]:
    data = dashboard.get('data') or {}
    ranking = (data.get('ranking') or [])[:5]
    lines = ['## 三、商品销量前5（历史实际）', '']
    if not ranking:
        lines += ['该范围没有可排名的商品记录（空结果不等于需求为零）。', '']
        return lines
    lines += ['| 名次 | 商品编号 | 商品名称 | 销量(件) | 销售额(£) | 订单数 |',
              '|---|---|---|---|---|---|']
    for index, row in enumerate(ranking, 1):
        lines.append(f"| {index} | {row['stock_code']} | {row['name']} | {_int(row['quantity'])} | "
                     f"{_num(row['revenue'])} | {_int(row['orders'])} |")
    lines += ['', '> 排名按销量降序，销量是件数，不是销售额；销售额不等于利润。', '']
    return lines


def _section_forecast(forecast_result: dict) -> list[str]:
    lines = ['## 四、全店未来7天预测（预测，非历史实际）', '']
    if not forecast_result.get('ok'):
        lines += [f"预测未生成：{forecast_result.get('summary', '工具不可用')}", '']
        lines += ['> 预测局限：模型只能作为备货参考，存在误差；无记录日按0构造连续销量，'
                  '不能据此认定不营业或需求为零。', '']
        return lines
    data = forecast_result.get('data') or {}
    rows = data.get('rows') or []
    total = sum(float(row.get('prediction') or 0) for row in rows)
    lines += [
        f"- 预测起点：{data.get('origin')}；预测区间：{rows[0]['date']}~{rows[-1]['date']}" if rows else '- 预测区间：—',
        f"- 方法：{data.get('actual_method')}（{data.get('method_label')}）；模型版本 {data.get('model_version')}",
        f"- 7天预测合计：{_int(total)} 件",
        '',
        '| 日期 | 第h天 | 预测销量(件) | 基线(件) |',
        '|---|---|---|---|',
    ]
    for row in rows:
        lines.append(f"| {row['date']} | {row['h']} | {_num(row['prediction'], 1)} | "
                     f"{_num(row.get('baseline'), 1)} |")
    lines += ['', '预测局限：']
    for warning in (data.get('warnings') or [])[:4]:
        lines.append(f'- {warning}')
    lines += [
        '- 预测是模型输出，不是已发生事实；不能当作承诺或经营收益。',
        '- 历史实际与预测必须分开阅读；本表不含实际值。',
        '',
    ]
    return lines


def _section_inventory(overview: dict) -> list[str]:
    lines = ['## 五、模拟库存概览（模拟，非真实库存）', '']
    scenario = overview.get('scenario')
    if not scenario:
        if overview.get('error'):
            lines += [f"指定场景不可用：{overview['error']}。"]
        lines += ['尚未建立模拟库存场景（模拟库存不是真实库存，不参与财务口径）。',
                  '- 模拟库存标识：simulated=true（模拟，非真实库存）']
        ready = overview.get('service_module_ready')
        if not ready:
            lines.append('库存服务模块未就绪，本节仅提示状态，不输出任何库存数字。')
        lines += ['', '> 补货候选公式：max(0, 提前期内需求 + 安全库存 − 余额 − 有效在途量)，全部为人工模拟参数。', '']
        return lines
    items = overview.get('items') or []
    candidates = overview.get('candidates') or []
    lines += [
        f"- 场景：{scenario.get('name')}（ID {scenario.get('id')}，商品 {len(items)} 个）",
        f"- 场景说明：{scenario.get('note') or '无'}",
        '- 模拟库存标识：simulated=true（模拟，非真实库存）',
        '',
        '| 商品编号 | 名称 | 余额 | 提前期需求 | 安全库存 | 有效在途 | 补货候选 |',
        '|---|---|---|---|---|---|---|',
    ]
    for item in candidates or items[:5]:
        lines.append(f"| {item['stock_code']} | {item['name']} | {_num(item['balance'], 0)} | "
                     f"{_num(item['lead_time_demand'], 0)} | {_num(item['safety_stock'], 0)} | "
                     f"{_num(item['effective_in_transit'], 0)} | {_num(item['replenishment_candidate'], 0)} |")
    lines += [
        '',
        '> 模拟库存标识：以上数字来自模拟参数与模拟流水，**不是真实库存**；补货候选只是公式结果，'
        '写操作必须在“模拟库存”页面确认后执行。',
        '',
    ]
    return lines


def _section_sources(batch: dict, scope: dict, warnings: list[str], tool_calls: list[dict]) -> list[str]:
    meta = batch.get('manifest') or {}
    lines = [
        '## 六、数据来源与口径',
        '',
        f"- 当前数据批次：{batch['id']}（{batch['label']}，{batch['kind']}）",
        f"- 数据覆盖范围：{meta.get('date_min')}~{meta.get('date_max')}",
        f"- 来源文件：{meta.get('source_file', '未登记')}；文件名哈希用于批次识别，不在此展示",
        f"- 口径版本：{meta.get('policy_version', '未登记')}",
        f"- 原始行数 {_int(meta.get('raw_rows'))}，保留行 {_int(meta.get('retained_rows'))}，"
        f"正向销售行 {_int(meta.get('positive_sale_rows'))}",
        '- 正向销量口径：日期/编号有效、数量大于0、非取消、非跨表副本；'
        '销售额=数量×英镑单价（内部1/10000整数存储）；订单数按 invoice 去重。',
        '- 无记录日补0只是构造连续记录，不代表已确认营业或市场需求为0。',
        '- 金额单位 GBP；**销售额不等于利润**，数据没有成本/采购价字段，无法计算利润或毛利。',
        '',
        '## 七、生成方式与已知限制',
        '',
        '- 本简报由确定性模板生成：provider=template，**未调用大模型**；所有数字来自服务端只读工具。',
        '- 历史实际、未来预测、模拟库存三类数据已分别标注，不得混用。',
        '- 数据为公开镜像数据，官方原文件逐字节比对未完成；本阶段为课程原型，不是生产交付。',
    ]
    for warning in warnings[:8]:
        lines.append(f'- 工具提示：{warning}')
    if not tool_calls:
        lines.append('- 本次没有任何工具成功执行，请检查数据批次与参数。')
    lines += ['', '---', '> 生成方式：确定性模板（local-rule-planner 工具层）；模拟库存与预测均已标注；'
              '如发现数字与页面不一致，以页面接口的当前批次结果为准并记录问题。', '']
    return lines


def build_brief(scope=None, scenario_id=None, user=None) -> dict:
    """Create, store and return one deterministic Markdown operations brief."""
    started = time.monotonic()
    scope_data = assistant.validate_scope(scope)
    if scenario_id is not None:
        scope_data['scenario_id'] = assistant.validate_scope({'scenario_id': scenario_id})['scenario_id']
    batch = active_batch()
    scope_data = assistant.scope_for_batch(scope_data, batch)
    base = _base_params(scope_data)
    tool_calls: list[dict] = []
    warnings: list[str] = []

    dashboard_result = assistant.execute_tool('dashboard_summary', base, batch)
    tool_calls.append({'name': 'dashboard_summary', 'params': dashboard_result['params'],
                       'ok': dashboard_result['ok'],
                       'summary': assistant._redact(dashboard_result['summary'])})
    warnings.extend(assistant._redact(w) for w in (dashboard_result.get('warnings') or []))

    forecast_params = {}
    forecast_result = assistant.execute_tool('forecast_7d', forecast_params, batch)
    tool_calls.append({'name': 'forecast_7d', 'params': forecast_result['params'],
                       'ok': forecast_result['ok'],
                       'summary': assistant._redact(forecast_result['summary'])})
    warnings.extend(assistant._redact(w) for w in (forecast_result.get('warnings') or []))

    overview = assistant.inventory_overview(scenario_id=scope_data.get('scenario_id'))
    range_ = (dashboard_result.get('range') if dashboard_result.get('ok')
              else assistant.default_range(batch)) or assistant.default_range(batch)
    title = f"运营简报 {range_.get('start')}~{range_.get('end')}"
    created_at = db.now_iso()
    brief_id = uuid.uuid4().hex

    lines = [
        f'# {title}',
        '',
        f"- 生成时间：{created_at}",
        f"- 数据批次：{batch['id']}（{batch['label']}）",
        f"- 数据范围：{range_.get('start')}~{range_.get('end')}",
        f"- 生成方式：确定性模板 provider={PROVIDER}，未调用大模型；工具调用 {len(tool_calls)} 次",
        '- 数据类别标识：历史实际 / 预测（非实际） / 模拟库存（非真实库存） 分节标注',
        '',
    ]
    if dashboard_result.get('ok'):
        lines += _section_metrics(dashboard_result)
        lines += _section_ranking(dashboard_result)
    else:
        lines += ['## 二、核心指标（历史实际）', '',
                  f"核心指标未生成：{assistant._redact(dashboard_result['summary'])}", '']
    lines += _section_forecast(forecast_result)
    lines += _section_inventory(overview)
    lines += _section_sources(batch, scope_data, warnings, tool_calls)
    markdown = '\n'.join(lines)
    markdown = assistant._redact(markdown)

    scope_json = json.dumps({'scope': scope_data, 'range': range_, 'provider': PROVIDER,
                             'batch': {'id': batch['id'], 'label': batch['label']},
                             'tool_calls': tool_calls, 'simulated_inventory': True},
                            ensure_ascii=False)
    with db.connect() as cx:
        cx.execute('insert into briefs(id,title,scope_json,markdown,created_by,created_at) '
                   'values(?,?,?,?,?,?)',
                   (brief_id, title, scope_json, markdown, (user or {}).get('id'), created_at))
    return {
        'brief': {'id': brief_id, 'title': title, 'created_at': created_at,
                  'url': f'/api/ai/briefs/{brief_id}.md'},
        'markdown': markdown,
        'provider': PROVIDER,
        'range': range_,
        'batch': {'id': batch['id'], 'label': batch['label']},
        'tool_calls': tool_calls,
        'elapsed_seconds': round(time.monotonic() - started, 3),
    }


def get_brief(brief_id: str) -> dict | None:
    if not brief_id or len(brief_id) > 64:
        return None
    with db.connect() as cx:
        row = cx.execute('select id,title,scope_json,markdown,created_at from briefs where id=?',
                         (brief_id,)).fetchone()
    return dict(row) if row else None
