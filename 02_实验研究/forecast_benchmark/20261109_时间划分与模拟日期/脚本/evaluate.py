"""固定训练期拟合一次，逐起点使用此前可见销量评估两个留出时段。"""
from pathlib import Path
import json
import sqlite3
import sys

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[2]
SYSTEM = ROOT / '电商运营系统'
sys.path.insert(0, str(SYSTEM / 'backend'))
from app.services.forecasting import (FEATURES, PARAMS, design, holt_winters,
                                      normalize, robust_history, score)

TRAIN_END = pd.Timestamp('2011-08-15')
VALID_START, VALID_END = pd.Timestamp('2011-08-16'), pd.Timestamp('2011-10-14')
TEST_START, TEST_END = pd.Timestamp('2011-10-15'), pd.Timestamp('2011-12-09')


def main():
    source = SYSTEM / 'runtime' / 'retail.sqlite3'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as cx:
        sales = pd.read_sql_query('select date,sum(quantity) quantity from transactions '
                                  'where sale=1 group by date order by date', cx)
    series = normalize(sales.set_index('date')['quantity'])
    assert series.index.max() == TEST_END
    partitions = {'train': series.loc[:TRAIN_END],
                  'validation': series.loc[VALID_START:VALID_END],
                  'test': series.loc[TEST_START:TEST_END]}
    for name, values in partitions.items():
        values.rename_axis('date').rename('quantity').to_csv(HERE / '结果' / f'{name}_daily.csv')

    train_design = design(partitions['train'])
    fit_data = train_design[(train_design.target_date >= pd.Timestamp('2010-03-01')) &
                            (train_design.target_date <= TRAIN_END) & train_design.y.notna()]
    model = LGBMRegressor(**PARAMS).fit(fit_data[FEATURES], fit_data.y)
    full_design = design(series)
    # After any origin, perturb all future values and verify its model inputs
    # remain identical. Targets intentionally differ and are not model inputs.
    for origin in (pd.Timestamp('2011-08-15'), pd.Timestamp('2011-10-14'),
                   pd.Timestamp('2011-11-20')):
        altered = series.copy()
        altered.loc[altered.index > origin] += 1_000_000
        reference = full_design[full_design.origin == origin].sort_values('h')[FEATURES]
        perturbed = design(altered)
        perturbed = perturbed[perturbed.origin == origin].sort_values('h')[FEATURES]
        np.testing.assert_allclose(reference, perturbed, equal_nan=True, rtol=0, atol=0)
    rows = []
    for partition, start, end in [('validation', VALID_START, VALID_END),
                                  ('test', TEST_START, TEST_END)]:
        for origin in pd.date_range(start - pd.Timedelta(days=1), end - pd.Timedelta(days=7)):
            hist = series.loc[:origin]
            inputs = full_design[full_design.origin == origin].sort_values('h')
            assert len(inputs) == 7
            tree = np.clip(model.predict(inputs[FEATURES]), 0, None)
            clean, caps = robust_history(hist)
            future = pd.date_range(origin + pd.Timedelta(days=1), periods=7)
            prediction = np.minimum(.5 * tree + .5 * holt_winters(clean),
                                    [caps[d] for d in future.dayofweek])
            med = hist.groupby(hist.index.dayofweek).median()
            closed = [int(day) for day, value in med.items() if value < .02 * hist.median()]
            prediction[np.isin(future.dayofweek, closed)] = 0
            baseline = hist.iloc[-28:].groupby(hist.iloc[-28:].index.dayofweek).mean()
            for h, (day, value) in enumerate(zip(future, prediction), 1):
                rows.append({'partition': partition, 'origin': origin.date().isoformat(),
                             'date': day.date().isoformat(), 'h': h,
                             'prediction': float(value), 'baseline': float(baseline[day.dayofweek]),
                             'actual': float(series.loc[day])})
    out = pd.DataFrame(rows)
    out.to_csv(HERE / '结果' / 'rolling_predictions.csv', index=False)
    report = {'simulation_date': '2012-01-01', 'last_observed_date': TEST_END.date().isoformat(),
              'missing_after_last_observation': ['2011-12-10', '2011-12-31'],
              'fit_max_target_date': fit_data.target_date.max().date().isoformat(),
              'fit_rows': int(len(fit_data)), 'partitions': {}}
    for name, g in out.groupby('partition'):
        report['partitions'][name] = {'origins': int(g.origin.nunique()),
                                      'target_start': g.date.min(), 'target_end': g.date.max(),
                                      'model': score(g.actual, g.prediction),
                                      'baseline': score(g.actual, g.baseline)}
    (HERE / '结果' / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
