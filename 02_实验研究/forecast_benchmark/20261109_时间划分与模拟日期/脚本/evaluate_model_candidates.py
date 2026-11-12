"""Compare model adjustments on the same visible history at each forecast origin."""
import json
import sqlite3
from time import perf_counter

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from evaluate_asof import HERE, SYSTEM, TEST_END, TRAIN_END, VALID_START, VALID_END, TEST_START
from app.services.forecasting import (
    FEATURES, PARAMS, backtest, design, forecast, holt_winters, normalize,
    robust_history, score, seasonal,
)


def evaluate(series, features, start, end, cutoff):
    training = features[features.target_date.between('2010-03-01', cutoff)]
    model = LGBMRegressor(**PARAMS).fit(training[FEATURES], training.y)
    origins = pd.date_range(pd.Timestamp(start) - pd.Timedelta(days=1),
                            pd.Timestamp(end) - pd.Timedelta(days=7))
    actual = []
    predictions = {}
    for origin in origins:
        hist = series.loc[:origin]
        inputs = features[features.origin == origin].sort_values('h')
        weekdays = inputs.target_date.dt.dayofweek
        tree = np.maximum(model.predict(inputs[FEATURES]), 0)
        clean, caps = robust_history(hist)
        hw = holt_winters(clean)
        cap = [caps[day] for day in weekdays]
        weekday_median = hist.groupby(hist.index.dayofweek).median()
        closed = weekdays.isin(weekday_median[weekday_median < .02 * hist.median()].index)
        blend = np.minimum(.5 * tree + .5 * hw, cap)
        heavier_tree = np.minimum(.75 * tree + .25 * hw, cap)
        blend[closed] = 0
        heavier_tree[closed] = 0
        baseline = seasonal(hist)
        recent = hist.iloc[-28:]
        median = recent.groupby(recent.index.dayofweek).median().loc[weekdays].to_numpy()
        actual.extend(inputs.y)
        candidates = {
            'current': blend,
            'four_week_mean': baseline,
            'uplift_10pct': 1.1 * blend,
            'uplift_20pct': 1.2 * blend,
            'mix_25pct_four_week_median': .75 * blend + .25 * median,
            'tree_weight_75pct': heavier_tree,
            'mix_25pct_four_week_mean': .75 * blend + .25 * baseline,
            'mix_50pct_four_week_mean': .5 * blend + .5 * baseline,
        }
        for name, values in candidates.items():
            predictions.setdefault(name, []).extend(values)
    weekly_actual = np.asarray(actual).reshape(-1, 7).sum(axis=1)
    return {
        'target_start': str(pd.Timestamp(start).date()),
        'target_end': str(pd.Timestamp(end).date()),
        'training_end': str(pd.Timestamp(cutoff).date()),
        'origins': len(origins),
        'metrics': {name: score(actual, values) for name, values in predictions.items()},
        'weekly_total_wape': {
            name: score(weekly_actual, np.asarray(values).reshape(-1, 7).sum(axis=1))['WAPE']
            for name, values in predictions.items()
        },
    }


def measure_reuse(series):
    start, end = '2011-09-27', '2011-10-24'
    cutoff = str(TRAIN_END.date())
    began = perf_counter()
    independent = [forecast(series, str(origin.date()), train_end=cutoff)['rows']
                   for origin in pd.date_range(start, end)]
    independent_seconds = perf_counter() - began
    began = perf_counter()
    optimized = backtest(series, start, end, train_end=cutoff)
    reused_seconds = perf_counter() - began
    assert independent == [result['rows'] for result in optimized['origin_results']]
    return {
        'origin_start': start, 'origin_end': end, 'forecast_rows': optimized['forecast_count'],
        'separate_fit_seconds': independent_seconds, 'shared_fit_seconds': reused_seconds,
        'predictions_identical': True,
        'daily': optimized['complete_window_metrics'],
        'daily_baseline': optimized['complete_window_baseline_metrics'],
        'weekly_total': optimized['weekly_total_metrics'],
        'weekly_total_baseline': optimized['weekly_total_baseline_metrics'],
    }


def main():
    source = SYSTEM / 'runtime' / 'batches' / 'simulation_20111101' / 'data.sqlite3'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as connection:
        daily = pd.read_sql_query(
            'select date,sum(quantity) quantity from transactions where sale=1 group by date order by date',
            connection,
        )
    series = normalize(daily.set_index('date').quantity)
    assert series.index.max() == TEST_END
    features = design(series)
    windows = [
        ('february', '2011-02-01', '2011-02-28', '2011-01-31'),
        ('april', '2011-04-01', '2011-04-30', '2011-03-31'),
        ('june', '2011-06-01', '2011-06-30', '2011-05-31'),
        ('july', '2011-07-01', '2011-07-31', '2011-06-30'),
        ('validation', VALID_START, VALID_END, TRAIN_END),
        ('observed_test', TEST_START, TEST_END, TRAIN_END),
    ]
    report = {
        'source': '电商运营系统/runtime/batches/simulation_20111101/data.sqlite3',
        'last_observed_date': str(TEST_END.date()),
        'windows': {name: evaluate(series, features, start, end, cutoff)
                    for name, start, end, cutoff in windows},
        'backtest_timing': measure_reuse(series),
    }
    path = HERE / '结果' / 'model_candidates_summary.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for name, window in report['windows'].items():
        print(name, ' · '.join(f"{key} {values['WAPE']:.2%}" for key, values in window['metrics'].items()))
    timing = report['backtest_timing']
    print(f"28 origins: {timing['separate_fit_seconds']:.2f}s -> {timing['shared_fit_seconds']:.2f}s")


if __name__ == '__main__':
    main()
