"""Demand forecasting with honest evaluation, uncertainty, and a rule-based fallback."""
import warnings

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app.models import HospitalRequest

SEASON = 7
LOW_CONFIDENCE_WAPE = 0.5


def demand_series(db, bank_id: str, blood_group: str) -> pd.Series:
    day = func.date(HospitalRequest.requested_at)
    rows = db.execute(
        select(day, func.sum(HospitalRequest.units_requested))
        .where(HospitalRequest.bank_id == bank_id, HospitalRequest.blood_group == blood_group)
        .group_by(day).order_by(day)
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows})
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full, fill_value=0.0)   # days with no requests = zero demand


def naive(train, h):
    return np.repeat(float(train.iloc[-1]), h)

def seasonal_naive(train, h):
    last = train.iloc[-SEASON:].to_numpy(dtype=float)
    return np.tile(last, int(np.ceil(h / SEASON)))[:h]

def moving_avg(train, h, w=14):
    return np.repeat(float(train.iloc[-w:].mean()), h)

def holt_winters(train, h):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(train.to_numpy(dtype=float), trend="add", damped_trend=True,
                                   seasonal="add", seasonal_periods=SEASON,
                                   initialization_method="estimated").fit()
    return np.clip(np.asarray(fit.forecast(h)), 0, None)

MODELS = {"naive": naive, "seasonal_naive": seasonal_naive,
          "moving_avg_14d": moving_avg, "holt_winters": holt_winters}


def backtest(series: pd.Series, h: int = 7, folds: int = 8, min_train: int = 60):
    """Rolling-origin evaluation: forecast h days ahead, `folds` times. Returns (metrics, residuals)."""
    errors = {m: [] for m in MODELS}
    actual = []
    n = len(series)
    for k in range(folds, 0, -1):
        split = n - k * h
        if split < min_train:
            continue
        train = series.iloc[:split]
        test = series.iloc[split:split + h].to_numpy(dtype=float)
        actual.append(test)
        for name, fn in MODELS.items():
            try:
                pred = fn(train, h)
            except Exception:
                pred = seasonal_naive(train, h)
            errors[name].append(test - pred)
    if not actual:
        raise ValueError("not enough history to backtest")
    total = np.abs(np.concatenate(actual)).sum()
    metrics, resid = {}, {}
    for name, errs in errors.items():
        e = np.concatenate(errs)
        wape = float(np.abs(e).sum() / total) if total > 0 else 9.99
        metrics[name] = {"wape": round(min(wape, 9.99), 3), "mae": round(float(np.abs(e).mean()), 3)}
        resid[name] = e
    return metrics, resid


def forecast(series: pd.Series, h: int = 7) -> dict:
    metrics, resid = backtest(series, h)
    best = min(metrics, key=lambda m: metrics[m]["wape"])
    low_conf = metrics[best]["wape"] > LOW_CONFIDENCE_WAPE
    if low_conf:
        # Fallback: the model is not trustworthy on this series, so use a transparent rule instead.
        model, pred, r = "rule_14d_average", moving_avg(series, h), resid["moving_avg_14d"]
    else:
        model, pred, r = best, MODELS[best](series, h), resid[best]
    lo, hi = np.percentile(r, [10, 90])
    dates = pd.date_range(series.index[-1] + pd.Timedelta(days=1), periods=h, freq="D")
    return {
        "model": model,
        "low_confidence": low_conf,
        "backtest": metrics,
        "horizon_days": h,
        "forecast": [{"date": d.date().isoformat(), "units": round(float(max(p, 0)), 2),
                      "low_80": round(float(max(p + lo, 0)), 2), "high_80": round(float(max(p + hi, 0)), 2)}
                     for d, p in zip(dates, pred)],
    }
