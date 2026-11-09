# -*- coding: utf-8 -*-
"""Online Retail II 日销量 7 天预测：走前回测（walk-forward）基准对比。

口径（先声明后执行，不看过测试结果再改）：
  * 序列    ：每日总销量 = 正数量、非取消（Invoice 不以 C 开头）明细的数量求和；无记录日补 0
  * 区间    ：2009-12-01 ~ 2011-12-09，共 739 天
  * 切分    ：训练 2009-12-01~2011-08-15；验证 2011-08-16~2011-10-14；测试 2011-10-15~2011-12-09
  * 预测任务：7 天多步；测试期每个日历日为一个预测起点，共 56 个起点，每次只用起点及以前的数据重训
  * 指标    ：MAE / RMSE / WAPE / 偏差，按“全部(起点,步长)预测合并”和“分步长”两种口径报告
  * 输出    ：不写死结论；所有数字由本脚本计算

只读原始 Excel，不改动任何原始文件。首次运行读取 43MB xlsx 并缓存日序列 CSV。
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
PKG = ROOT.parents[1] / "20261014_电商数据与页面选型"
XLSX = PKG / "online_retail_II.xlsx"
DATA = ROOT / "data"
RES = ROOT / "results"
FIG = ROOT / "figures"
for d in (DATA, RES, FIG):
    d.mkdir(exist_ok=True)

H = 7
SPLIT = {
    "train": ("2009-12-01", "2011-08-15"),
    "valid": ("2011-08-16", "2011-10-14"),
    "test": ("2011-10-15", "2011-12-09"),
}
TRAIN_ROW_START = pd.Timestamp("2010-03-01")

# 英国银行假日（2009-12 至 2011-12），仅作日历特征，不额外抓取
HOLIDAYS = pd.to_datetime([
    "2009-12-25", "2009-12-28", "2010-01-01",
    "2010-04-02", "2010-04-05", "2010-05-03", "2010-05-31", "2010-08-30",
    "2010-12-27", "2010-12-28",
    "2011-01-03", "2011-04-22", "2011-04-25", "2011-05-02", "2011-05-30",
    "2011-08-29", "2011-12-26", "2011-12-27",
])


# ---------------------------------------------------------------- 数据
def load_daily(rebuild: bool = False) -> pd.Series:
    """返回连续日粒度的全店总销量序列（缺失日=0）。"""
    cache = DATA / "daily_qty.csv"
    if cache.exists() and not rebuild:
        s = pd.read_csv(cache, parse_dates=["date"]).set_index("date")["qty"]
        return s.astype(float)

    t0 = time.time()
    frames = []
    for sheet in ("Year 2009-2010", "Year 2010-2011"):
        df = pd.read_excel(XLSX, sheet_name=sheet,
                           usecols=["Invoice", "Quantity", "InvoiceDate"])
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw["InvoiceDate"] = pd.to_datetime(raw["InvoiceDate"])
    cancel = raw["Invoice"].astype(str).str.startswith("C")
    pos = raw[(raw["Quantity"] > 0) & (~cancel)].copy()
    pos["date"] = pos["InvoiceDate"].dt.normalize()

    daily = pos.groupby("date")["Quantity"].sum()
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full, fill_value=0).astype(float)
    daily.index.name = "date"
    daily.rename("qty").to_frame().to_csv(cache)
    print(f"[data] 读取 {len(raw)} 行 -> {len(daily)} 天，用时 {time.time()-t0:.1f}s")
    return daily


# ---------------------------------------------------------------- 特征
LAGS = [1, 2, 3, 7, 14, 21, 28, 56]
WINS = [7, 14, 28, 56]


def build_design(s: pd.Series) -> pd.DataFrame:
    """构造 (目标日, 步长) 长表；所有特征只用预测起点及以前的信息。"""
    parts = []
    for h in range(1, H + 1):
        m = pd.DataFrame({"target_date": s.index, "h": h, "y": s.values})
        m["origin"] = s.index - pd.Timedelta(days=h)
        for k in LAGS:
            m[f"lag_{k}"] = s.shift(h + k - 1).values  # lag_1 = 起点当天，均为可见历史
        base = s.shift(h)  # 起点当天的值 = s[t-h]，可见
        for w in WINS:
            r = base.rolling(w)
            m[f"mean_{w}"] = r.mean().values
            m[f"std_{w}"] = r.std().values
            m[f"max_{w}"] = r.max().values
        parts.append(m)

    d = pd.concat(parts, ignore_index=True)
    t = d["target_date"]
    d["dow"] = t.dt.dayofweek
    d["month"] = t.dt.month
    d["day"] = t.dt.day
    d["week"] = t.dt.isocalendar().week.astype(int)
    d["is_weekend"] = (d["dow"] >= 5).astype(int)
    d["is_saturday"] = (d["dow"] == 5).astype(int)
    d["is_holiday"] = t.isin(HOLIDAYS).astype(int)
    d["days_to_christmas"] = [(pd.Timestamp(f"{y}-12-25") - x).days for x, y in zip(t, t.dt.year)]
    d["december_run_up"] = ((d["month"] == 12) & (d["days_to_christmas"] <= 20)).astype(int)
    d["t_index"] = (t - s.index.min()).dt.days
    d["level_ratio"] = d["mean_7"] / d["mean_28"].replace(0, np.nan)
    return d


FEATURES = ([f"lag_{k}" for k in LAGS]
            + [f"{a}_{w}" for w in WINS for a in ("mean", "std", "max")]
            + ["dow", "month", "day", "week", "is_weekend", "is_saturday",
               "is_holiday", "days_to_christmas", "december_run_up",
               "t_index", "level_ratio", "h"])


# ---------------------------------------------------------------- 模型
def f_naive_last(s: pd.Series) -> np.ndarray:
    return np.repeat(s.iloc[-1], H)


def f_naive_week(s: pd.Series) -> np.ndarray:
    """上周同日：h 步预测取 7 天前同星期几的值（s[t-7], s[t-6], ...）。"""
    return s.iloc[-7:].values.astype(float)


def f_ma7(s: pd.Series) -> np.ndarray:
    return np.repeat(s.iloc[-7:].mean(), H)


def f_ma28(s: pd.Series) -> np.ndarray:
    return np.repeat(s.iloc[-28:].mean(), H)


def f_seasonal_week4(s: pd.Series) -> np.ndarray:
    """同一星期几在最近 4 周的平均值。"""
    tail = s.iloc[-28:]
    dow = tail.index.dayofweek
    prof = tail.groupby(dow).mean()
    fut = pd.date_range(s.index[-1] + pd.Timedelta(days=1), periods=H, freq="D")
    return np.array([prof.get(d, s.iloc[-28:].mean()) for d in fut.dayofweek], dtype=float)


def _try_statsmodels():
    try:
        import statsmodels  # noqa: F401
        from statsmodels.tsa.holtwinters import ExponentialSmoothing  # noqa: F401
        return True
    except Exception:
        return False


HAS_SM = _try_statsmodels()


def f_hw_manual(s: pd.Series, alpha=0.3, beta=0.05, gamma=0.3) -> np.ndarray:
    """加法 Holt-Winters（趋势+周季节性），无 statsmodels 时的等价实现。"""
    y = s.values.astype(float)
    m = 7
    if len(y) < 3 * m:
        return f_seasonal_week4(s)
    lvl = y[:m].mean()
    trd = (y[m:2 * m].mean() - y[:m].mean()) / m
    seas = np.zeros(m)
    for i in range(m):
        seas[i] = y[i] - lvl
    for t in range(len(y)):
        s_i = seas[t % m]
        l_prev, b_prev = lvl, trd
        lvl = alpha * (y[t] - s_i) + (1 - alpha) * (l_prev + b_prev)
        trd = beta * (lvl - l_prev) + (1 - beta) * b_prev
        seas[t % m] = gamma * (y[t] - l_prev - b_prev) + (1 - gamma) * s_i
    out = np.array([lvl + h * trd + seas[(len(y) + h - 1) % m] for h in range(1, H + 1)])
    return np.clip(out, 0, None)


def f_holt_winters(s: pd.Series) -> np.ndarray:
    if not HAS_SM:
        return f_hw_manual(s)
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    try:
        m = ExponentialSmoothing(s, trend="add", seasonal="add", seasonal_periods=7,
                                 initialization_method="estimated").fit()
        return np.clip(np.asarray(m.forecast(H), dtype=float), 0, None)
    except Exception:
        return f_hw_manual(s)


def f_sarima(s: pd.Series) -> np.ndarray:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    try:
        m = SARIMAX(s, order=(1, 1, 1), seasonal_order=(1, 1, 1, 7),
                    enforce_stationarity=False, enforce_invertibility=False).fit(
            disp=False, maxiter=40)
        return np.clip(np.asarray(m.forecast(H), dtype=float), 0, None)
    except Exception:
        return f_seasonal_week4(s)


LGB_PARAMS = dict(n_estimators=400, learning_rate=0.05, num_leaves=15,
                  min_child_samples=20, subsample=0.8, subsample_freq=1,
                  colsample_bytree=0.8, reg_lambda=1.0, random_state=42,
                  n_jobs=4, verbose=-1)


def fit_lgbm(design: pd.DataFrame, origin: pd.Timestamp,
             log_target: bool, params: dict | None = None):
    import lightgbm as lgb

    tr = design[(design["target_date"] <= origin) & (design["target_date"] >= TRAIN_ROW_START)]
    if len(tr) < 200:
        return None
    y = np.log1p(tr["y"].values) if log_target else tr["y"].values
    p = dict(LGB_PARAMS)
    if params:
        p.update(params)
    model = lgb.LGBMRegressor(objective="regression_l1", **p)
    model.fit(tr[FEATURES], y)
    return model


def predict_lgbm(model, design: pd.DataFrame, origin: pd.Timestamp, log_target: bool):
    x = design[design["origin"] == origin]
    if x.empty or model is None:
        return np.full(H, np.nan)
    out = model.predict(x[FEATURES])
    if log_target:
        out = np.expm1(out)
    return np.clip(out, 0, None)


# ---------------------------------------------------------------- 指标
def metrics(actual: np.ndarray, pred: np.ndarray) -> dict:
    e = pred - actual
    ae = np.abs(e)
    denom = float(np.abs(actual).sum())
    return {
        "n": int(len(actual)),
        "MAE": float(ae.mean()),
        "RMSE": float(np.sqrt(np.mean(e ** 2))),
        "WAPE": float(ae.sum() / denom) if denom > 0 else np.nan,
        "BIAS": float(e.mean()),
        "BIAS_PCT": float(e.sum() / denom) if denom > 0 else np.nan,
    }


# ---------------------------------------------------------------- 主流程
def run() -> None:
    s = load_daily(rebuild="--rebuild" in sys.argv)
    design = build_design(s)
    print(f"[data] 序列 {len(s)} 天，设计矩阵 {len(design)} 行，特征 {len(FEATURES)} 个")

    # 验证集上只决定一件事：树模型是否对目标做 log1p
    val_o = pd.date_range(SPLIT["valid"][0], SPLIT["valid"][1], freq="D")
    val_res = {}
    for log_t in (False, True):
        preds, acts = [], []
        for o in val_o:
            o = pd.Timestamp(o)
            mdl = fit_lgbm(design, o, log_t)
            pr = predict_lgbm(mdl, design, o, log_t)
            x = design[design["origin"] == o]
            preds.append(pr)
            acts.append(x["y"].values)
        val_res[log_t] = metrics(np.concatenate(acts), np.concatenate(preds))
        print(f"[valid] log1p={log_t}: {val_res[log_t]}")
    LOG_TARGET = bool(val_res[True]["WAPE"] < val_res[False]["WAPE"])
    print(f"[valid] 选定 log1p={LOG_TARGET}")

    test_o = pd.date_range(SPLIT["test"][0], SPLIT["test"][1], freq="D")
    methods = [
        ("naive_last", "基准", f_naive_last),
        ("naive_week", "基准", f_naive_week),
        ("ma7", "基准", f_ma7),
        ("ma28", "基准", f_ma28),
        ("seasonal_week4", "基准", f_seasonal_week4),
    ]
    methods += [("holt_winters", "统计模型", f_holt_winters)]
    if HAS_SM:
        methods += [("sarima_111_111_7", "统计模型", f_sarima)]

    rows = []
    timing = {}
    for name, family, fn in methods:
        t0 = time.time()
        for o in test_o:
            o = pd.Timestamp(o)
            hist = s[s.index <= o]
            try:
                pr = np.asarray(fn(hist), dtype=float)
            except Exception as exc:  # 单点失败不应中断整轮
                print(f"[warn] {name} @ {o.date()} 失败：{exc}")
                pr = np.full(H, np.nan)
            x = design[design["origin"] == o]
            for i, (h, act, td) in enumerate(zip(x["h"].values, x["y"].values,
                                                 x["target_date"].values)):
                rows.append({"model": name, "family": family, "origin": o,
                             "h": int(h), "target_date": pd.Timestamp(td),
                             "actual": float(act), "pred": float(pr[i])})
        timing[name] = round(time.time() - t0, 1)
        print(f"[run] {name}: {timing[name]}s")

    # 机器学习模型：同样逐起点重训
    t0 = time.time()
    for o in test_o:
        o = pd.Timestamp(o)
        mdl = fit_lgbm(design, o, LOG_TARGET)
        pr = predict_lgbm(mdl, design, o, LOG_TARGET)
        x = design[design["origin"] == o]
        for i, (h, act, td) in enumerate(zip(x["h"].values, x["y"].values,
                                             x["target_date"].values)):
            rows.append({"model": "lgbm_direct", "family": "机器学习",
                         "origin": o, "h": int(h), "target_date": pd.Timestamp(td),
                         "actual": float(act), "pred": float(pr[i])})
    timing["lgbm_direct"] = round(time.time() - t0, 1)
    print(f"[run] lgbm_direct: {timing['lgbm_direct']}s")

    pred = pd.DataFrame(rows)

    # 简单平均集成（HW + LightGBM），作为“是否需要复杂模型”的对照
    wide = pred[pred["model"].isin(["holt_winters", "lgbm_direct"])].pivot_table(
        index=["origin", "h", "target_date", "actual"], columns="model", values="pred")
    if {"holt_winters", "lgbm_direct"}.issubset(set(wide.columns)):
        wide = wide.dropna()
        blend = wide.mean(axis=1).rename("pred").reset_index()
        blend["model"] = "blend_hw_lgbm"
        blend["family"] = "集成"
        pred = pd.concat([pred, blend[pred.columns]], ignore_index=True)

    # 非营业日规则：只用训练期数据判定“某星期几是否营业”，不参看测试期
    tr_s = s[s.index <= pd.Timestamp(SPLIT["valid"][1])]
    med = tr_s.groupby(tr_s.index.dayofweek).median()
    closed = sorted(int(d) for d in med.index if med[d] < 0.02 * tr_s.median())
    print(f"[rule] 训练期判定非营业星期几（0=周一）：{closed}")

    for src, new in (("lgbm_direct", "lgbm_direct_rule"),
                     ("blend_hw_lgbm", "blend_hw_lgbm_rule")):
        sub = pred[pred["model"] == src].copy()
        if sub.empty:
            continue
        mask = pd.to_datetime(sub["target_date"]).dt.dayofweek.isin(closed)
        sub.loc[mask, "pred"] = 0.0
        sub["model"] = new
        sub["family"] = "规则后处理"
        pred = pd.concat([pred, sub], ignore_index=True)

    pred.to_csv(RES / "predictions_long.csv", index=False)

    # 汇总：全部预测合并
    overall = (pred.groupby(["family", "model"])
               .apply(lambda g: pd.Series(metrics(g["actual"].values, g["pred"].values)),
                      include_groups=False)
               .reset_index()
               .sort_values("WAPE"))
    overall["fit_seconds"] = overall["model"].map(timing)
    overall.to_csv(RES / "metrics_overall.csv", index=False)

    by_h = (pred.groupby(["model", "h"])
            .apply(lambda g: pd.Series(metrics(g["actual"].values, g["pred"].values)),
                   include_groups=False)
            .reset_index())
    by_h.to_csv(RES / "metrics_by_horizon.csv", index=False)

    # 按目标日重抽样的 WAPE 置信区间：看方法差异是否超出噪声
    per_day = (pred.assign(ae=(pred["pred"] - pred["actual"]).abs())
               .groupby(["model", "target_date"])
               .agg(ae=("ae", "sum"), act=("actual", "sum"))
               .reset_index())
    dates = sorted(per_day["target_date"].unique())
    ae_p = per_day.pivot(index="target_date", columns="model", values="ae").reindex(dates)
    act_p = per_day.pivot(index="target_date", columns="model", values="act").reindex(dates)
    act_v = act_p.bfill(axis=1).iloc[:, 0].fillna(0).values
    rng = np.random.default_rng(7)
    B = 2000
    ridx = rng.integers(0, len(dates), size=(B, len(dates)))
    den_b = act_v[ridx].sum(axis=1)
    REF = "seasonal_week4"

    def boot_wape(col: str) -> np.ndarray:
        num = ae_p[col].fillna(0).values[ridx].sum(axis=1)
        return np.where(den_b > 0, num / den_b, np.nan)

    bw = {c: boot_wape(c) for c in ae_p.columns}
    rows_b = []
    for c in ae_p.columns:
        d = bw[c] - bw[REF] if REF in bw else np.array([np.nan])
        rows_b.append({
            "model": c,
            "WAPE_point": float(ae_p[c].sum() / act_v.sum()),
            "WAPE_lo95": float(np.nanpercentile(bw[c], 2.5)),
            "WAPE_hi95": float(np.nanpercentile(bw[c], 97.5)),
            "diff_vs_" + REF: float(np.nanmean(d)),
            "diff_lo95": float(np.nanpercentile(d, 2.5)),
            "diff_hi95": float(np.nanpercentile(d, 97.5)),
        })
    boot = pd.DataFrame(rows_b).sort_values("WAPE_point")
    boot.to_csv(RES / "metrics_bootstrap.csv", index=False)
    print("\n[boot] 按目标日 2000 次重抽样：")
    print(boot.to_string(index=False))

    # 误差来源
    p2 = pred.copy()
    p2["dow"] = p2["target_date"].dt.dayofweek
    p2["month"] = p2["target_date"].dt.month
    p2["ae"] = (p2["pred"] - p2["actual"]).abs()
    best = overall.iloc[0]["model"]
    base = "naive_week"
    DIAG_MODELS = ["lgbm_direct", "seasonal_week4", "naive_week"]
    err_dow = (p2[p2["model"].isin(DIAG_MODELS)]
               .groupby(["model", "dow"])
               .agg(n=("ae", "size"), MAE=("ae", "mean"),
                    actual_sum=("actual", "sum"), ae_sum=("ae", "sum"))
               .reset_index())
    err_dow["WAPE"] = np.where(err_dow["actual_sum"] > 0,
                               err_dow["ae_sum"] / err_dow["actual_sum"], np.nan)
    err_dow.to_csv(RES / "error_by_weekday.csv", index=False)

    err_month = (p2[p2["model"].isin(DIAG_MODELS)]
                 .groupby(["model", "month"])
                 .agg(n=("ae", "size"), MAE=("ae", "mean"),
                      actual_sum=("actual", "sum"), ae_sum=("ae", "sum"))
                 .reset_index())
    err_month["WAPE"] = np.where(err_month["actual_sum"] > 0,
                                 err_month["ae_sum"] / err_month["actual_sum"], np.nan)
    err_month.to_csv(RES / "error_by_month.csv", index=False)

    worst = (p2[p2["model"] == best]
             .sort_values("ae", ascending=False)
             .head(15)[["origin", "h", "target_date", "actual", "pred", "ae", "dow"]])
    worst.to_csv(RES / "worst_cases.csv", index=False)

    # 诊断：误差是否被大额成交单主导
    corr = float(np.corrcoef(p2[p2["model"] == best]["actual"],
                             p2[p2["model"] == best]["ae"])[0, 1])

    summary = {
        "protocol": {
            "series": "日总销量（正数量、非取消）", "days": int(len(s)),
            "train": SPLIT["train"], "valid": SPLIT["valid"], "test": SPLIT["test"],
            "origins": int(len(test_o)), "horizon": H,
            "test_forecasts": int(len(pred[pred["model"] == best])),
            "log_target": LOG_TARGET, "statsmodels": HAS_SM,
        },
        "best_model": best,
        "baseline": base,
        "corr_actual_vs_ae": round(corr, 3),
        "overall": overall.to_dict(orient="records"),
    }
    (RES / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                 indent=2, default=str), encoding="utf-8")

    make_figures(p2, overall, by_h, best)
    print(overall.to_string(index=False))
    print(f"\n[out] {RES}")


LABELS = {
    "naive_last": "随机游走（昨日值）",
    "naive_week": "上周同日",
    "ma7": "7 日移动平均",
    "ma28": "28 日移动平均",
    "seasonal_week4": "同星期几 4 周均值",
    "holt_winters": "Holt-Winters",
    "lgbm_direct": "LightGBM（直推 7 步）",
    "lgbm_direct_rule": "LightGBM + 非营业日规则",
    "blend_hw_lgbm": "集成（HW + LightGBM）",
    "blend_hw_lgbm_rule": "集成 + 非营业日规则",
}
FAMILY_COLOR = {
    "基准": "#95a5a6",
    "统计模型": "#e67e22",
    "机器学习": "#c0392b",
    "集成": "#8e44ad",
    "规则后处理": "#16a085",
}


def make_figures(p2, overall, by_h, best):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Patch

    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    boot = pd.read_csv(RES / "metrics_bootstrap.csv") if (RES / "metrics_bootstrap.csv").exists() else None

    # 1) 各方法 WAPE 对比（带自助法 95% 区间）
    o = overall.sort_values("WAPE").reset_index(drop=True)
    lab = [LABELS.get(m, m) for m in o["model"]]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    cols = [FAMILY_COLOR.get(f, "#95a5a6") for f in o["family"]]
    ax.barh(lab, o["WAPE"] * 100, color=cols, alpha=0.9)
    if boot is not None:
        bm = boot.set_index("model").reindex(o["model"])
        lo = (bm["WAPE_lo95"] * 100).values
        hi = (bm["WAPE_hi95"] * 100).values
        mid = (o["WAPE"] * 100).values
        ax.errorbar(mid, range(len(o)), xerr=[mid - lo, hi - mid],
                    fmt="none", ecolor="#2c3e50", elinewidth=1.1, capsize=3)
    for y, v in enumerate(o["WAPE"] * 100):
        ax.text(v + 0.6, y, f"{v:.1f}%", va="center", fontsize=9)
    ax.set_xlabel("WAPE（越低越好，%），横线为按日重抽样 95% 区间")
    ax.set_title("7 天销量预测：各方法误差对比（走前回测，56 个起点、364 条预测）")
    ax.invert_yaxis()
    ax.legend(handles=[Patch(color=v, label=k) for k, v in FAMILY_COLOR.items()],
              loc="upper left", bbox_to_anchor=(0.0, -0.14), ncol=5,
              fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "wape_by_model.png", dpi=200)
    plt.close(fig)

    # 2) 测试期实际 vs 1 步预测
    show = "lgbm_direct"
    d = p2[(p2["model"] == show) & (p2["h"] == 1)].sort_values("target_date")
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.plot(d["target_date"], d["actual"], color="#2c3e50", lw=1.6, label="实际")
    ax.plot(d["target_date"], d["pred"], color="#c0392b", lw=1.3, ls="--",
            label="LightGBM 1 步预测")
    ax.set_title("测试期（2011-10-15 至 2011-12-09）实际值与 1 步预测")
    ax.set_ylabel("日销量（件）")
    ax.legend(frameon=False)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(FIG / "pred_vs_actual.png", dpi=200)
    plt.close(fig)

    # 3) 分步长 WAPE
    b = by_h[by_h["model"].isin(["lgbm_direct", "seasonal_week4", "naive_week"])]
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    for name, g in b.groupby("model"):
        ax.plot(g["h"], g["WAPE"] * 100, marker="o", label=LABELS.get(name, name))
    ax.set_xlabel("预测步长（天）")
    ax.set_ylabel("WAPE（%）")
    ax.set_title("误差随预测步长变化")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "wape_by_horizon.png", dpi=200)
    plt.close(fig)

    # 4) 按星期几拆误差（看周六不营业的影响）
    e = pd.read_csv(RES / "error_by_weekday.csv")
    e = e[e["model"].isin(["lgbm_direct", "seasonal_week4"])]
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    width = 0.38
    for k, (name, g) in enumerate(e.groupby("model")):
        g = g.sort_values("dow")
        w = np.where(g["actual_sum"].values > 0, g["WAPE"].values * 100, np.nan)
        ax.bar(np.arange(7) + (k - 0.5) * width, w, width, label=LABELS.get(name, name))
    ax.set_xticks(range(7), names)
    ax.set_ylabel("WAPE（%）")
    ax.set_title("分星期几误差（周六实际销量为 0，比值不可计算）")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "error_by_weekday.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    run()
