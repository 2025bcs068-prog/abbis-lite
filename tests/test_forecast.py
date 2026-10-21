import numpy as np
import pandas as pd
from app.ai.forecast import backtest, forecast


def make(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values), freq="D"))


def test_seasonal_signal_beats_naive():
    rng = np.random.default_rng(0)
    t = np.arange(200)
    s = make(np.clip(20 + 8 * np.sin(2 * np.pi * t / 7) + rng.normal(0, 1, 200), 0, None))
    metrics, _ = backtest(s)
    assert min(m["wape"] for m in metrics.values()) < metrics["naive"]["wape"] * 0.5
    r = forecast(s, 7)
    assert not r["low_confidence"] and len(r["forecast"]) == 7


def test_sparse_demand_triggers_rule_fallback():
    rng = np.random.default_rng(1)
    r = forecast(make(rng.poisson(0.15, 200).astype(float)), 7)
    assert r["low_confidence"] and r["model"] == "rule_14d_average"
