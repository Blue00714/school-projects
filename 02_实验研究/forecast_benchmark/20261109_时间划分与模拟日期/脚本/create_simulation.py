"""从本机原始缓存建立截至 2011-10-31 的独立演示批次。"""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[2]
SYSTEM = ROOT / '电商运营系统'
sys.path.insert(0, str(SYSTEM / 'backend'))
sys.path.insert(0, str(SYSTEM / 'vendor/python-packages'))
from app.services.batches import get_batch, register_batch
from app.services.forecasting import SIMULATION_BATCH_ID

CUTOFF = '2011-10-31'


def main():
    if get_batch(SIMULATION_BATCH_ID):
        raise RuntimeError('演示批次已存在；请勿覆盖已有数据')
    source = SYSTEM / 'runtime/retail.sqlite3'
    folder = SYSTEM / 'runtime/batches' / SIMULATION_BATCH_ID
    target = folder / 'data.sqlite3'
    if target.exists():
        raise RuntimeError('演示批次数据库已存在；请先检查，不能覆盖')
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        # Only the newly created snapshot is changed; preserve the source database.
        dst.execute('delete from transactions where date is null or date > ?', (CUTOFF,))
        dst.execute('delete from forecast_runs')
        row = dst.execute('''select count(*), sum(cross_duplicate),
            sum(case when cross_duplicate=0 then 1 else 0 end), sum(sale),
            sum(case when cross_duplicate=0 then within_duplicate_candidate else 0 end),
            sum(case when cross_duplicate=0 then cancelled else 0 end),
            sum(case when cross_duplicate=0 and quantity<=0 then 1 else 0 end),
            sum(case when cross_duplicate=0 and quantity is null then 1 else 0 end),
            sum(case when sale=1 and amount_eligible=0 then 1 else 0 end),
            sum(case when sale=1 and unit_price=0 then 1 else 0 end), min(date), max(date)
            from transactions''').fetchone()
        (raw, cross, retained, positive, duplicate, cancelled, nonpositive, invalid_qty,
         invalid_price, zero_price, date_min, date_max) = row
        assert date_max == CUTOFF
        metadata = json.loads(dst.execute("select value from metadata where key='manifest'").fetchone()[0])
        metadata.update(source_kind='historical_simulation', simulation_date='2011-11-01',
                        simulation_cutoff=CUTOFF, prepared_at=datetime.now().isoformat(timespec='seconds'),
                        sheets=[], raw_rows=raw, cross_sheet_copies_excluded=cross, retained_rows=retained,
                        positive_sale_rows=positive, within_sheet_duplicate_candidates_retained=duplicate,
                        cancelled_rows=cancelled, nonpositive_quantity_rows=nonpositive, invalid_dates=0,
                        quantity_invalid_rows=invalid_qty, sale_rows_without_valid_price=invalid_price,
                        zero_price_sale_rows=zero_price, date_min=date_min, date_max=date_max,
                        invoice_with_multiple_times=None, order_identity_conflicts=None,
                        elapsed_seconds=None)
        dst.execute("update metadata set value=? where key='manifest'", (json.dumps(metadata, ensure_ascii=False),))
        dst.commit()
    batch = register_batch(batch_id=SIMULATION_BATCH_ID, label='模拟 2011-11-01 · 截至 2011-10-31',
                           kind='upload', db_path=target, source_file=metadata['source_file'],
                           source_sha256='', policy_version=metadata['policy_version'], manifest=metadata)
    print('已登记独立批次', batch['id'], date_min, date_max, '记录', raw)


if __name__ == '__main__':
    main()
