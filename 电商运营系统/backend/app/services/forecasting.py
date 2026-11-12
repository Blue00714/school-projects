"""Frozen, framework-independent whole-shop daily forecast methods."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = 'daily-v1-hw-fixed-lgbm400'
ACTIVE_MODEL = 'robust_blend'
DEPLOYMENT_VERSION = 'daily-v4-fixed-training-cutoff'
REFERENCE_TRAIN_END = '2011-08-15'
SIMULATION_BATCH_ID = 'simulation_20111101'
SIMULATION_DATE = '2011-11-01'
METHODS = {ACTIVE_MODEL:'销量预测模型', 'blend_rule':'周季节与LightGBM组合＋零销量日规则','lgbm_rule':'LightGBM＋零销量日规则（验证候选）','lgbm':'LightGBM直接7步','holt_winters':'固定参数周季节Holt-Winters','seasonal_week4':'同星期几近4周均值','naive_week':'上周同日'}
LAGS, WINS = [1,2,3,7,14,21,28,56], [7,14,28,56]
HOLIDAYS = pd.to_datetime(['2009-12-25','2009-12-28','2010-01-01','2010-04-02','2010-04-05','2010-05-03','2010-05-31','2010-08-30','2010-12-27','2010-12-28','2011-01-03','2011-04-22','2011-04-25','2011-05-02','2011-05-30','2011-08-29','2011-12-26','2011-12-27'])
FEATURES = ([f'lag_{k}' for k in LAGS] + [f'{a}_{w}' for w in WINS for a in ['mean','std','max']] + ['dow','month','day','week','is_weekend','is_saturday','is_holiday','days_to_christmas','december_run_up','t_index','level_ratio','h'])
PARAMS = dict(objective='regression_l1',n_estimators=400,learning_rate=.05,num_leaves=15,min_child_samples=20,subsample=.8,subsample_freq=1,colsample_bytree=.8,reg_lambda=1,random_state=42,n_jobs=4,verbose=-1)


def score(actual, predicted):
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted,dtype=float)
    if a.shape != p.shape or a.ndim != 1 or not np.isfinite(p).all():
        raise ValueError('实际值与预测值长度应一致，预测值必须有限')
    valid = np.isfinite(a)
    a, p = a[valid], p[valid]
    if not len(a):
        return dict(n=0, MAE=None, RMSE=None, WAPE=None, BIAS=None, BIAS_PCT=None,
                    MEDIAN_AE=None, P90_AE=None)
    err, denom = p-a, float(np.abs(a).sum())
    absolute = np.abs(err)
    median, p90 = np.quantile(absolute, [.5, .9])
    return dict(n=len(a), MAE=float(absolute.mean()), RMSE=float(np.sqrt((err**2).mean())),
                WAPE=float(absolute.sum()/denom) if denom else None, BIAS=float(err.mean()),
                BIAS_PCT=float(err.sum()/denom) if denom else None,
                MEDIAN_AE=float(median), P90_AE=float(p90))


def normalize(series):
    s = pd.Series(series).copy()
    if s.empty:
        raise ValueError('没有可预测的历史数据')
    try:
        s.index = pd.to_datetime(s.index)
        s = s.astype(float)
    except (TypeError, ValueError):
        raise ValueError('历史数据日期或数值无效') from None
    if s.index.tz is not None or s.index.has_duplicates or s.index.isna().any() or not (s.index == s.index.normalize()).all():
        raise ValueError('历史日期必须唯一且为不带时区的日粒度')
    if not np.isfinite(s.values).all() or (s < 0).any():
        raise ValueError('历史销量必须为有限非负数')
    s = s.sort_index()
    return s.reindex(pd.date_range(s.index.min(),s.index.max()),fill_value=0)


def seasonal(hist):
    prof = hist.iloc[-28:].groupby(hist.iloc[-28:].index.dayofweek).mean()
    dates = pd.date_range(hist.index[-1]+pd.Timedelta(days=1),periods=7)
    return np.array([prof[d] for d in dates.dayofweek],dtype=float)


def holt_winters(hist):
    y = hist.values.astype(float)
    level, trend = y[:7].mean(), (y[7:14].mean()-y[:7].mean())/7
    seasons = y[:7]-level
    for t in range(len(y)):
        old_l, old_b = level, trend
        level = .3*(y[t]-seasons[t%7])+.7*(old_l+old_b)
        trend = .05*(level-old_l)+.95*old_b
        seasons[t%7] = .3*(y[t]-old_l-old_b)+.7*seasons[t%7]
    return np.clip([level+h*trend+seasons[(len(y)+h-1)%7] for h in range(1,8)],0,None)


def design(hist):
    # Padding creates seven unseen targets, not seven fabricated observations.
    s = hist.reindex(pd.date_range(hist.index[0],hist.index[-1]+pd.Timedelta(days=7)))
    parts = []
    for h in range(1,8):
        m = pd.DataFrame({'target_date':s.index,'h':h,'y':s.values})
        m['origin'] = s.index-pd.Timedelta(days=h)
        for lag in LAGS:
            m[f'lag_{lag}'] = s.shift(h+lag-1).values
        for win in WINS:
            r = s.shift(h).rolling(win)
            for stat in ['mean','std','max']:
                m[f'{stat}_{win}'] = getattr(r,stat)().values
        parts.append(m)
    d = pd.concat(parts,ignore_index=True)
    t = d['target_date']
    d['dow'],d['month'],d['day'] = t.dt.dayofweek,t.dt.month,t.dt.day
    d['week'] = t.dt.isocalendar().week.astype(int)
    d['is_weekend'],d['is_saturday'] = (d['dow']>=5).astype(int),(d['dow']==5).astype(int)
    d['is_holiday'] = t.isin(HOLIDAYS).astype(int)
    d['days_to_christmas'] = [(pd.Timestamp(f'{x.year}-12-25')-x).days for x in t]
    d['december_run_up'] = ((d['month']==12)&(d['days_to_christmas']<=20)).astype(int)
    d['t_index'] = (t-s.index.min()).dt.days
    d['level_ratio'] = d['mean_7']/d['mean_28'].replace(0,np.nan)
    return d


def version_for(model):
    if model == ACTIVE_MODEL:
        return DEPLOYMENT_VERSION
    return 'daily-v2-lgbm400-rule' if model == 'lgbm_rule' else VERSION


def robust_history(hist):
    """Fit the fixed weekday cap using visible history only; never alter actuals."""
    recent = hist.iloc[-182:]
    caps = {}
    for dow, group in recent.groupby(recent.index.dayofweek):
        median = float(group.median())
        mad = float((group-median).abs().median())
        caps[int(dow)] = max(1.5*median, median+3*mad)
    upper = pd.Series([caps[d] for d in hist.index.dayofweek], index=hist.index)
    return hist.clip(upper=upper), caps


def forecast(series, origin=None, horizon=7, model=ACTIVE_MODEL, model_dir:Path|None=None,
             train_end=None, *, _fixed_fit=None):
    if horizon != 7 or model not in METHODS:
        raise ValueError('只支持未来7天和已列出的方法')
    s = normalize(series)
    try:
        o = pd.Timestamp(origin) if origin else s.index[-1]
        if o.tz is not None or o != o.normalize():
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('预测起点必须为有效日历日期') from None
    if pd.isna(o) or o < s.index[0] or o > s.index[-1]:
        raise ValueError('预测起点必须在数据覆盖范围内')
    hist = s.loc[:o].copy()
    fit_end = min(o, pd.Timestamp(train_end)) if train_end else o
    if len(hist)<28:
        raise ValueError('预测至少需要28天历史数据')
    base = seasonal(hist)
    method, train_rows, closed = model, 0, []
    warnings = ['无记录日按0构造连续记录销量，不能据此认定不营业或市场需求为0。']
    if model in ['lgbm','blend_rule','lgbm_rule',ACTIVE_MODEL]:
        if len(hist)<84:
            method='seasonal_week4'
            warnings.append('历史不足84天，已明确回退至同星期几近4周均值。')
        else:
            try:
                from lightgbm import LGBMRegressor
                d = design(hist)
                x = d[d['origin']==o].sort_values('h')
                can_share_fit = _fixed_fit is not None and fit_end == train_end
                if can_share_fit and fit_end in _fixed_fit:
                    mdl, train_rows = _fixed_fit[fit_end]
                else:
                    tr = d[(d['target_date']<=fit_end)&(d['target_date']>=pd.Timestamp('2010-03-01'))&d['y'].notna()]
                    train_rows = len(tr)
                    if train_rows < 200:
                        raise ValueError('固定训练起始日期后的训练样本不足200行')
                    mdl = LGBMRegressor(**PARAMS).fit(tr[FEATURES],tr['y'])
                    if can_share_fit:
                        _fixed_fit[fit_end] = (mdl, train_rows)
                tree = np.clip(mdl.predict(x[FEATURES]),0,None)
                if len(tree)!=7 or not np.isfinite(tree).all():
                    raise ValueError('模型未返回7条有效结果')
                if model_dir is not None:
                    folder = Path(model_dir)
                    folder.mkdir(parents=True,exist_ok=True)
                    # Python handles Unicode Windows paths; keep native text format.
                    (folder/'lightgbm.txt').write_text(mdl.booster_.model_to_string(),encoding='utf-8')
                    (folder/'metadata.json').write_text(json.dumps({'version':version_for(model),'origin':o.date().isoformat(),'training_end':fit_end.date().isoformat(),'parameters':PARAMS,'features':FEATURES,'robust_rule':({'lookback_days':182,'mad_multiplier':3,'median_floor_multiplier':1.5} if model==ACTIVE_MODEL else None),'history_sha256':sha256(hist.to_csv().encode()).hexdigest()},ensure_ascii=False,indent=2),encoding='utf-8')
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                method='seasonal_week4'
                warnings.append(f'组合/树模型不可用，已回退4周均值：{type(exc).__name__}: {exc}')
    if method=='seasonal_week4':
        pred=base.copy()
    elif method=='naive_week':
        pred=hist.iloc[-7:].values.copy()
    elif method=='holt_winters':
        pred=holt_winters(hist)
    elif method=='lgbm':
        pred=tree
    else:
        if method == ACTIVE_MODEL:
            clean, caps = robust_history(hist)
            pred = .5*tree+.5*holt_winters(clean)
            dates = pd.date_range(o+pd.Timedelta(days=1), periods=7)
            pred = np.minimum(pred, [caps[d] for d in dates.dayofweek])
            warnings.append('已按起点以前的同星期历史波动约束异常峰值的延续；实际销量与大单仍完整保留，突发大单可能低估。')
        else:
            pred=tree.copy() if method=='lgbm_rule' else .5*tree+.5*holt_winters(hist)
        med=hist.groupby(hist.index.dayofweek).median()
        closed=[int(day) for day,value in med.items() if value<.02*hist.median()]
        future=pd.date_range(o+pd.Timedelta(days=1),periods=7)
        pred[np.isin(future.dayofweek,closed)]=0
        warnings.append('零销量星期规则由可见历史计算，是候选规则，未确认真实营业安排。')
    rows=[]
    for h,day in enumerate(pd.date_range(o+pd.Timedelta(days=1),periods=7),1):
        actual=float(s.loc[day]) if day in s.index else None
        rows.append({'date':day.date().isoformat(),'h':h,'prediction':float(pred[h-1]),'baseline':float(base[h-1]),'actual':actual,'abs_error':abs(float(pred[h-1])-actual) if actual is not None else None})
    if rows[-1]['date']>'2011-12-31':
        warnings.append('当前日历假日表仅覆盖2009至2011年，后续年份的假日特征未验证。')
    acts=[r['actual'] for r in rows]
    n=sum(a is not None for a in acts)
    return {'origin':o.date().isoformat(),'horizon':7,'requested_method':model,'actual_method':method,'method_label':METHODS[method],'model_version':version_for(model),'history_start':hist.index[0].date().isoformat(),'history_end':o.date().isoformat(),'training_end':fit_end.date().isoformat(),'history_days':len(hist),'training_rows':train_rows,'closed_weekdays':closed,'rows':rows,'metrics':score(acts,pred),'baseline_metrics':score(acts,base),'complete_actual':n==7,'actual_days':n,'warnings':warnings}


def backtest(series,start,end,model=ACTIVE_MODEL,train_end=None):
    try:
        a,b=pd.Timestamp(start),pd.Timestamp(end)
        if a!=a.normalize() or b!=b.normalize() or a.tz is not None or b.tz is not None:
            raise ValueError()
    except (TypeError,ValueError):
        raise ValueError('回测日期无效') from None
    if pd.isna(a) or pd.isna(b) or b<a or (b-a).days>55:
        raise ValueError('回测起点区间须为1至56个日历日')
    s=normalize(series)
    if a<s.index[0]+pd.Timedelta(days=27) or b>s.index[-1]:
        raise ValueError('回测起点超出覆盖范围或历史不足28天')
    # Origins at or after the fixed cutoff have exactly the same training targets
    # and features. Each origin still computes inputs from its own visible history.
    cutoff = pd.Timestamp(train_end) if train_end else None
    fixed_fit = {} if cutoff is not None else None
    results = [forecast(s, x.date().isoformat(), model=model, train_end=cutoff, _fixed_fit=fixed_fit)
               for x in pd.date_range(a, b)]
    rows=[dict(r,origin=result['origin']) for result in results for r in result['rows']]
    scored=[r for r in rows if r['actual'] is not None]
    def pair(items,key='prediction'):
        return score([r['actual'] for r in items],[r[key] for r in items])
    complete_origins = [result['rows'] for result in results if result['complete_actual']]
    complete = [r for window in complete_origins for r in window]
    def weekly_pair(key):
        return score([sum(r['actual'] for r in window) for window in complete_origins],
                     [sum(r[key] for r in window) for window in complete_origins])
    worst={}
    for row in scored:
        if row['date'] not in worst or row['abs_error']>worst[row['date']]['abs_error']:
            worst[row['date']]=row
    warnings=['重叠起点的预测不是独立样本，不以条数代表独立样本量。','完整窗口和末端部分目标分别统计；未开展显著性检验。']
    fallback=sum(x['actual_method']!=model for x in results)
    if fallback:
        warnings.append(f'{fallback}个起点使用了明确标记的备用方法，请查看分起点实际方法。')
    return {
        'start': a.date().isoformat(), 'end': b.date().isoformat(),
        'requested_method': model, 'model_version': version_for(model),
        'origins_attempted': len(results), 'origins_with_actual': sum(x['actual_days'] > 0 for x in results),
        'complete_origins': len(complete_origins), 'forecast_count': len(rows), 'scored_count': len(scored),
        'scored_unique_days': len(worst), 'metrics': pair(scored), 'baseline_metrics': pair(scored, 'baseline'),
        'complete_window_metrics': pair(complete), 'complete_window_baseline_metrics': pair(complete, 'baseline'),
        'weekly_total_metrics': weekly_pair('prediction'), 'weekly_total_baseline_metrics': weekly_pair('baseline'),
        'by_horizon': {str(h): pair([r for r in scored if r['h'] == h]) for h in range(1, 8)},
        'worst_cases': sorted(worst.values(), key=lambda r: r['abs_error'], reverse=True)[:5],
        'origin_results': results, 'warnings': warnings,
    }
