"""evaluate_daily_drift is the pure decision core behind gridbot_body's
end-of-day drift check - kept separate from log_daily_pnl (which does live
Shioaji API calls) so this logic is testable without mocking the broker
API. A data-fetch failure or a zero/absent capital base must never be
treated as a drift signal."""
from sj_trading.gridbot_body import evaluate_daily_drift

STATS = {"mean_daily_return": 0.0, "std_daily_return": 0.01}


def test_normal_day_is_not_outlier():
    # pnl_pct = 500/100000 = 0.005, z = 0.5
    assert evaluate_daily_drift(realized=300, unrealized=200, totalcapital=100_000,
                                 fetch_ok=True, stats=STATS) is False


def test_extreme_day_is_outlier():
    # pnl_pct = 5000/100000 = 0.05, z = 5.0
    assert evaluate_daily_drift(realized=5000, unrealized=0, totalcapital=100_000,
                                 fetch_ok=True, stats=STATS) is True


def test_failed_fetch_never_flags_outlier():
    assert evaluate_daily_drift(realized=999_999, unrealized=0, totalcapital=100_000,
                                 fetch_ok=False, stats=STATS) is None


def test_missing_stats_returns_none():
    assert evaluate_daily_drift(realized=5000, unrealized=0, totalcapital=100_000,
                                 fetch_ok=True, stats=None) is None


def test_zero_totalcapital_returns_none():
    assert evaluate_daily_drift(realized=100, unrealized=0, totalcapital=0,
                                 fetch_ok=True, stats=STATS) is None
