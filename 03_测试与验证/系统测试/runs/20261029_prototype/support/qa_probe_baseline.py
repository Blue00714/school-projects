import json
import sqlite3
import sys

cx = sqlite3.connect(sys.argv[1])
q = ("select count(*),sum(quantity),sum(amount_units),count(distinct order_key),"
     "count(distinct stock_code) from transactions where sale=1 and date>='2011-11-10' and date<='2011-12-09'")
c, qty, amt, orders, prods = cx.execute(q).fetchone()
print('lines', c, 'qty', qty, 'rev', amt / 10000, 'orders', orders, 'products', prods)
rows = cx.execute("select date,sum(quantity),sum(amount_units) from transactions where sale=1 "
                  "and date>='2011-11-10' and date<='2011-12-09' group by date order by date").fetchall()
print('days', len(rows), 'first', rows[0], 'last', rows[-1])
print('sum qty', sum(r[1] for r in rows), 'sum rev', sum(r[2] for r in rows) / 10000)
prev = cx.execute("select count(*),sum(quantity),sum(amount_units),count(distinct order_key) "
                  "from transactions where sale=1 and date>='2011-10-11' and date<='2011-11-09'").fetchone()
print('prev', prev[0], prev[1], prev[2] / 10000, prev[3])
meta = json.loads(cx.execute("select value from metadata where key='manifest'").fetchone()[0])
print({k: meta[k] for k in ('date_min', 'date_max', 'policy_version', 'raw_rows',
                            'positive_sale_rows', 'retained_rows', 'source_sha256')})
print('countries', cx.execute("select country,sum(quantity) from transactions where sale=1 "
                             "and date>='2011-11-10' and date<='2011-12-09' group by country "
                             "order by sum(quantity) desc limit 5").fetchall())
print('uk', cx.execute("select count(distinct order_key),sum(quantity) from transactions where sale=1 "
                       "and date>='2011-11-10' and date<='2011-12-09' "
                       "and country='United Kingdom'").fetchone())
print('fr', cx.execute("select count(distinct order_key),sum(quantity) from transactions where sale=1 "
                       "and date>='2011-11-10' and date<='2011-12-09' "
                       "and country='France'").fetchone())
print('product22423', cx.execute("select count(*),sum(quantity),count(distinct order_key) from transactions "
                                 "where sale=1 and stock_code='22423' and date>='2011-11-10' "
                                 "and date<='2011-12-09'").fetchone())
print('orders sample', cx.execute("select order_key,date,count(*),sum(quantity) from transactions where sale=1 "
                                  "and date>='2011-11-10' and date<='2011-12-09' "
                                  "group by order_key order by date desc,order_key desc limit 3").fetchall())
print('empty range', cx.execute("select count(*) from transactions where sale=1 and date>='2010-01-01' "
                                "and date<='2010-01-02'").fetchone())
print('2011-12-09', cx.execute("select count(distinct order_key),sum(quantity) from transactions where sale=1 "
                               "and date='2011-12-09'").fetchone())
print('nov orders max day', cx.execute("select date,count(distinct order_key) from transactions where sale=1 "
                                       "and date>='2011-11-01' and date<='2011-11-30' group by date "
                                       "order by count(distinct order_key) desc limit 3").fetchall())
cx.close()
