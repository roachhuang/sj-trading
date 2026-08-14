"""backtest() must expose the daily-return distribution it already computes
internally (previously discarded after deriving sharpe/ann_vol), and
write_stats() must persist it in the shape gridbot_body.py's drift check
expects (mean_daily_return/std_daily_return, matching misc.is_pnl_outlier's
stats dict)."""
import json

import numpy as np
import pandas as pd

from sj_trading.backtest import _fill_price, backtest, write_stats

PARAMS = {
    "BiasUpperLimit": 1.4,
    "UpperLimitPosition": 0.35,
    "BiasLowerLimit": 0.70,
    "LowerLimitPosition": 0.80,
    "BiasPeriod": 30,
}


def _synthetic_df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    upper = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, n))
    lower = 50 * np.cumprod(1 + rng.normal(0.0001, 0.008, n))
    return pd.DataFrame({"upper": upper, "lower": lower}, index=dates)


def test_backtest_returns_finite_daily_mean_and_std():
    df = _synthetic_df()
    ratio = df["upper"] / df["lower"]
    result = backtest(df, ratio, PARAMS)
    assert result is not None
    assert np.isfinite(result["daily_mean"])
    assert np.isfinite(result["daily_std"])
    assert result["daily_std"] > 0


def test_write_stats_writes_expected_shape(tmp_path):
    df = _synthetic_df()
    path = tmp_path / "backtest_stats.json"
    stats = write_stats(df, PARAMS, path=str(path))

    assert set(stats) == {
        "mean_daily_return",
        "std_daily_return",
        "generated_at",
        "params",
        "execution_slippage_ticks",
    }
    assert stats["execution_slippage_ticks"] == 1.0
    assert stats["params"] == PARAMS

    with open(path) as f:
        on_disk = json.load(f)
    assert on_disk == stats


def test_write_stats_matches_backtest_daily_stats(tmp_path):
    df = _synthetic_df()
    ratio = df["upper"] / df["lower"]
    direct = backtest(df, ratio, PARAMS)

    path = tmp_path / "backtest_stats.json"
    stats = write_stats(df, PARAMS, path=str(path))

    assert stats["mean_daily_return"] == direct["daily_mean"]
    assert stats["std_daily_return"] == direct["daily_std"]


def test_fill_price_models_one_tick_each_side_and_zero_baseline():
    assert _fill_price(100.0, "Buy", 1) == 100.1
    assert _fill_price(100.0, "Sell", 1) == 99.9
    assert _fill_price(100.0, "Buy", 0) == 100.0
    assert _fill_price(100.0, "Sell", 0) == 100.0
