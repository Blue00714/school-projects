"""Pure queries shared by a local page and future API adapters."""
from __future__ import annotations

from datetime import date, timedelta
from app.services.data import DataError, Store


def iso_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise DataError('日期必须为YYYY-MM-DD') from None


def filters(params, manifest):
    end = iso_date(params.get('end') or manifest['date_max'])
    start = iso_date(params.get('start') or max(manifest['date_min'], (date.fromisoformat(end)-timedelta(days=29)).isoformat()))
    if start > end:
        raise DataError('开始日期不能晚于结束日期')
    if (date.fromisoformat(end)-date.fromisoformat(start)).days > 1460:
        raise DataError('单次查询最多覆盖4年')
    return {'start': start, 'end': end, 'product': str(params.get('product') or '').strip(), 'country': str(params.get('country') or '').strip()}


def where(f, sales=True):
    pieces = ['date>=?', 'date<=?']
    args = [f['start'], f['end']]
    if sales:
        pieces.append('sale=1')
    else:
        pieces.append('cross_duplicate=0')
    if f['product']:
        pieces.append('stock_code=?')
        args.append(f['product'])
    if f['country']:
        pieces.append('country=?')
        args.append(f['country'])
    return ' AND '.join(pieces), args


class Analytics:
    def __init__(self, store: Store):
        self.store = store

    def options(self):
        meta = self.store.manifest()
        with self.store.connect() as cx:
            countries = [r[0] for r in cx.execute("select distinct country from transactions where sale=1 and country<>'' order by country")]
            products = [dict(r) for r in cx.execute('select stock_code, max(description) name, sum(quantity) quantity from transactions where sale=1 group by stock_code order by quantity desc limit 500')]
        return {'manifest':meta, 'countries':countries, 'products':products}

    def search_products(self, query=''):
        term = str(query).strip()[:100]
        # Escape wildcard characters; the search is text, not a SQL expression.
        term = term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        with self.store.connect() as cx:
            rows = cx.execute("select stock_code,max(description) name,sum(quantity) quantity from transactions where sale=1 and (stock_code like ? escape '\\' or description like ? escape '\\') group by stock_code order by quantity desc limit 50", (f'%{term}%', f'%{term}%')).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _totals(cx, f):
        w, args = where(f)
        row = cx.execute(f'''select count(*) lines,coalesce(sum(quantity),0) quantity,
            coalesce(sum(amount_units),0) amount_units,count(distinct order_key) orders,
            count(distinct stock_code) products,coalesce(sum(amount_eligible),0) amount_rows
            from transactions where {w}''', args).fetchone()
        out = dict(row)
        out['revenue'] = out.pop('amount_units') / 10000
        out['average_order_value'] = out['revenue']/out['orders'] if out['orders'] else None
        return out

    def dashboard(self, params=None):
        meta = self.store.manifest()
        f = filters(params or {}, meta)
        span = (date.fromisoformat(f['end'])-date.fromisoformat(f['start'])).days+1
        previous = dict(f, start=(date.fromisoformat(f['start'])-timedelta(days=span)).isoformat(), end=(date.fromisoformat(f['start'])-timedelta(days=1)).isoformat())
        # Only dialect difference in the whole module: weekday number of an ISO date.
        # SQLite: strftime('%w') = 0..6 with Sunday=0; MySQL: DAYOFWEEK() = 1..7 with Sunday=1.
        weekday_expr = '(DAYOFWEEK(date)-1)' if getattr(self.store, 'backend', 'sqlite') == 'mysql' \
            else "cast(strftime('%w',date) as integer)"
        with self.store.connect() as cx:
            totals, prev = self._totals(cx, f), self._totals(cx, previous)
            w, args = where(f)
            observed = {r['date']:dict(r) for r in cx.execute(f'''select date,sum(quantity) quantity,sum(amount_units)/10000.0 revenue,count(distinct order_key) orders,count(*) lines from transactions where {w} group by date order by date''', args)}
            ranking = [dict(r) for r in cx.execute(f'''select stock_code,max(description) name,sum(quantity) quantity,sum(amount_units)/10000.0 revenue,count(distinct order_key) orders from transactions where {w} group by stock_code order by quantity desc,stock_code limit 20''', args)]
            countries = [dict(r) for r in cx.execute(f'''select country,sum(quantity) quantity,sum(amount_units)/10000.0 revenue,count(distinct order_key) orders from transactions where {w} group by country order by quantity desc limit 10''', args)]
            weekdays = [dict(r) for r in cx.execute(f'''select {weekday_expr} weekday,sum(quantity) quantity,count(distinct date) observed_days from transactions where {w} group by weekday''', args)]
            qw, qa = where(f, sales=False)
            quality = dict(cx.execute(f'''select count(*) retained_rows,coalesce(sum(cancelled),0) cancelled_rows,
                coalesce(sum(case when quantity<=0 then 1 else 0 end),0) nonpositive_rows,
                coalesce(sum(within_duplicate_candidate),0) duplicate_candidates,
                coalesce(sum(case when sale=1 and amount_eligible=0 then 1 else 0 end),0) invalid_price_rows
                from transactions where {qw}''', qa).fetchone())
        trend = []
        for offset in range(span):
            day = (date.fromisoformat(f['start'])+timedelta(days=offset)).isoformat()
            inside = meta['date_min'] <= day <= meta['date_max']
            item = observed.get(day)
            trend.append(item if item else {'date':day, 'quantity':0 if inside else None,'revenue':0 if inside else None,'orders':0 if inside else None,'lines':0,'observed':False})
            trend[-1]['observed'] = bool(item)
        previous_complete = previous['start'] >= meta['date_min'] and previous['end'] <= meta['date_max']
        changes = {key: (totals[key]/prev[key]-1) if previous_complete and prev[key] else None for key in ['quantity','revenue','orders']}
        warnings = []
        if not totals['lines']:
            warnings.append('所选范围没有符合口径的交易记录，不等于市场需求为零。')
        if not previous_complete:
            warnings.append('上一等长区间超出数据覆盖范围，不计算增长率。')
        if f['start'] < meta['date_min'] or f['end'] > meta['date_max']:
            warnings.append('部分查询日期超出数据覆盖范围，缺失区间显示为空。')
        if quality['invalid_price_rows']:
            warnings.append('部分正向销量没有有效单价，销售额覆盖行数少于销量。')
        return {'filters':f,'coverage':{'start':meta['date_min'],'end':meta['date_max']},'totals':totals,'previous':{'filters':previous,'totals':prev,'complete':previous_complete},'changes':changes,'trend':trend,'ranking':ranking,'countries':countries,'weekdays':weekdays,'quality':quality,'warnings':warnings,'policy_version':meta['policy_version']}

    def transactions(self, params=None):
        params = params or {}
        f = filters(params, self.store.manifest())
        try:
            page = int(params.get('page',1))
            size = int(params.get('page_size',25))
        except (ValueError, TypeError):
            raise DataError('分页参数应为整数') from None
        if not 1 <= page <= 100000 or not 1 <= size <= 200:
            raise DataError('页码至少1，单页1至200行')
        w, args = where(f)
        with self.store.connect() as cx:
            count = cx.execute(f'select count(*) from transactions where {w}',args).fetchone()[0]
            rows = [dict(r) for r in cx.execute(f'''select invoice,stock_code,description,quantity,unit_price,
                case when amount_eligible=1 then amount_units/10000.0 else null end revenue,
                invoice_time,country,sheet,source_row,within_duplicate_candidate from transactions where {w}
                order by invoice_time desc,invoice,stock_code,sheet,source_row limit ? offset ?''',args+[size,(page-1)*size])]
        return {'filters':f,'page':page,'page_size':size,'total':count,'rows':rows}
