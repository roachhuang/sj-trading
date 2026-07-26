"""is_pnl_outlier must degrade to 'no opinion' (None) on missing/degenerate
stats rather than false-triggering a CI failure on infra problems."""
import math

from sj_trading.misc import is_pnl_outlier


def test_within_threshold_is_not_outlier():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.01}
    # z = (0.005 - 0.001) / 0.01 = 0.4
    assert is_pnl_outlier(0.005, stats) is False


def test_beyond_threshold_is_outlier():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.01}
    # z = (0.05 - 0.001) / 0.01 = 4.9
    assert is_pnl_outlier(0.05, stats) is True


def test_exactly_at_threshold_is_not_outlier():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    # z = 0.03 / 0.01 = 3.0 exactly -> strictly-greater-than threshold, not tripped
    assert is_pnl_outlier(0.03, stats) is False


def test_just_beyond_threshold_is_outlier():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    assert is_pnl_outlier(0.030001, stats) is True


def test_missing_stats_returns_none():
    assert is_pnl_outlier(0.05, None) is None


def test_zero_std_returns_none():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.0}
    assert is_pnl_outlier(0.05, stats) is None


def test_nan_std_returns_none():
    stats = {"mean_daily_return": 0.001, "std_daily_return": float("nan")}
    assert is_pnl_outlier(0.05, stats) is None


def test_custom_threshold():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    # z = 2.0
    assert is_pnl_outlier(0.02, stats, threshold=1.5) is True
    assert is_pnl_outlier(0.02, stats, threshold=2.5) is False
