"""label_regimes()/backtest_by_regime() split backtested daily returns by
market regime (Bull/Bear/Sideways off a 20-day rolling return, +-2% entry
threshold with hysteresis - same entry convention as the
markov-hedge-fund-method skill, hysteresis added on top after a flat
threshold measured a 17.5% day-to-day flip rate on real history), to check
whether GridBot.parameters' backtested edge is uniform across regimes or
concentrated in one, per the 2021-07..2023-04 near-flat window found by
manual walk-forward checking."""
import numpy as np
import pandas as pd
import pytest

from sj_trading.backtest import backtest, backtest_by_regime
from sj_trading.misc import blended_price, label_regimes

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


def test_label_regimes_detects_trend_direction():
    dates = pd.date_range("2020-01-01", periods=100, freq="B")
    rising = pd.Series(np.linspace(100, 200, 100), index=dates)
    falling = pd.Series(np.linspace(200, 100, 100), index=dates)
    flat = pd.Series(100 + np.sin(np.arange(100) * 0.1) * 0.1, index=dates)

    assert (label_regimes(rising, window=20).iloc[30:] == "Bull").all()
    assert (label_regimes(falling, window=20).iloc[30:] == "Bear").all()
    assert (label_regimes(flat, window=20).iloc[30:] == "Sideways").all()


def test_label_regimes_hysteresis_prevents_flip_near_threshold():
    """window=1 makes pct_change(1) equal the per-day return exactly, so
    this hand-designs the rolling-return trace directly: enters Bull at
    +3%, dips to +1.9% (below the 2% entry threshold but still above the
    0.2% exit band) - must stay Bull, not revert to Sideways - then drops
    to +0.1% (below the exit band) and must revert, then a sharp -3% day
    enters Bear directly."""
    dates = pd.date_range("2020-01-01", periods=6, freq="B")
    rets = np.array([0.0, 0.03, 0.019, 0.03, 0.001, -0.03])
    price = pd.Series(np.cumprod(1 + rets), index=dates)

    labels = label_regimes(price, window=1, threshold=0.02, hysteresis=0.018)
    assert labels.iloc[1] == "Bull"
    assert labels.iloc[2] == "Bull"  # hysteresis: stays Bull despite < threshold
    assert labels.iloc[3] == "Bull"
    assert labels.iloc[4] == "Sideways"  # drops below exit band -> reverts
    assert labels.iloc[5] == "Bear"


def test_label_regimes_rejects_hysteresis_not_less_than_threshold():
    price = pd.Series([1.0, 1.01, 1.02])
    with pytest.raises(ValueError):
        label_regimes(price, threshold=0.02, hysteresis=0.02)
    with pytest.raises(ValueError):
        label_regimes(price, threshold=0.02, hysteresis=-0.01)


def test_label_regimes_stability_improves_with_hysteresis():
    """Regression guard for the flip-flopping this was built to fix
    (measured on real 0052/00662 history: 17.5% day-to-day flip rate,
    median 2-day regime run with a flat threshold). Synthetic here rather
    than a live fetch, per this repo's tests never hitting the network:
    window=1 makes pct_change(1) equal the per-day return exactly, so a
    return oscillating tightly around the +2% entry threshold reproduces
    the same flip-flopping a flat threshold sees in practice."""
    n = 200
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.arange(n)
    rets = np.concatenate([[0.0], 0.02 + 0.006 * np.sin(2 * np.pi * t[1:] / 8)])
    price = pd.Series(np.cumprod(1 + rets), index=dates)

    def flip_rate(labels):
        changes = labels != labels.shift(1)
        return changes.iloc[1:].mean()

    unhystereted = label_regimes(price, window=1, hysteresis=0.0)
    hystereted = label_regimes(price, window=1)  # default hysteresis
    assert flip_rate(hystereted) < flip_rate(unhystereted)


def test_backtest_return_daily_flag():
    df = _synthetic_df()
    ratio = df["upper"] / df["lower"]
    without = backtest(df, ratio, PARAMS)
    assert "daily_returns" not in without

    with_daily = backtest(df, ratio, PARAMS, return_daily=True)
    assert isinstance(with_daily["daily_returns"], pd.Series)
    assert len(with_daily["daily_returns"]) == with_daily["n_days"] - 1


def test_backtest_by_regime_covers_all_days_and_labels():
    df = _synthetic_df()
    out = backtest_by_regime(df, PARAMS)

    assert set(out["regime"]) == {"Bull", "Bear", "Sideways"}
    ratio = df["upper"] / df["lower"]
    total_days = backtest(df, ratio, PARAMS, return_daily=True)["daily_returns"].shape[0]
    assert out["n_days"].sum() == total_days


def test_backtest_by_regime_raises_on_insufficient_history():
    df = _synthetic_df(n=20)
    with pytest.raises(ValueError):
        backtest_by_regime(df, PARAMS)


def test_blended_price_compounds_daily_returns():
    dates = pd.date_range("2020-01-01", periods=3, freq="B")
    upper = pd.Series([100.0, 110.0, 121.0], index=dates)  # +10%, +10%
    lower = pd.Series([50.0, 50.0, 50.0], index=dates)  # flat

    blended = blended_price(upper, lower)
    assert blended.iloc[0] == pytest.approx(1.0)
    assert blended.iloc[1] == pytest.approx(1.05)  # 0.5*10% + 0.5*0%
    assert blended.iloc[2] == pytest.approx(1.05 * 1.05)


def test_blended_price_window_return_is_anchor_invariant():
    """The bug this guards against: normalizing each series to iloc[0]
    made a window-return depend on how much history was loaded, so
    backtest.py's full-history anchor and gridbot_body.py's 2-year live
    fetch could label the identical calendar date differently even given
    identical prices for the window that matters."""
    rng = np.random.default_rng(1)
    dates = pd.date_range("2020-01-01", periods=120, freq="B")
    upper = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.01, 120)), index=dates)
    lower = pd.Series(50 * np.cumprod(1 + rng.normal(-0.001, 0.008, 120)), index=dates)

    full_label = label_regimes(blended_price(upper, lower), window=20).iloc[-1]
    # Same tail data, different amount of history loaded before it.
    truncated_label = label_regimes(
        blended_price(upper.iloc[50:], lower.iloc[50:]), window=20
    ).iloc[-1]
    assert full_label == truncated_label


def test_blended_price_aligns_mismatched_indices():
    dates_a = pd.date_range("2020-01-01", periods=5, freq="B")
    dates_b = dates_a[1:]  # missing the first day
    upper = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=dates_a)
    lower = pd.Series([50.0, 51.0, 52.0, 53.0], index=dates_b)

    blended = blended_price(upper, lower)
    assert len(blended) == 4
    assert blended.index.equals(dates_b)
