"""evaluate_daily_drift is the pure decision core behind gridbot_body's
end-of-day drift check - kept separate from log_daily_pnl (which does live
Shioaji API calls) so this logic is testable without mocking the broker
API. Compares day-over-day mark-to-market equity change (matching the
backtest's eq.pct_change() units), not broker P&L flow endpoints - a
missing/zero prior-day equity baseline must never be treated as drift."""
from sj_trading.gridbot_body import evaluate_daily_drift

STATS = {"mean_daily_return": 0.0, "std_daily_return": 0.01}


def test_normal_day_is_not_outlier():
    # today_equity=100500, prior_equity=100000 -> pnl_pct=0.005, z=0.5
    assert evaluate_daily_drift(today_equity=100_500, prior_equity=100_000, stats=STATS) is False


def test_extreme_day_is_outlier():
    # today_equity=105000, prior_equity=100000 -> pnl_pct=0.05, z=5.0
    assert evaluate_daily_drift(today_equity=105_000, prior_equity=100_000, stats=STATS) is True


def test_missing_prior_equity_returns_none():
    assert evaluate_daily_drift(today_equity=105_000, prior_equity=None, stats=STATS) is None


def test_zero_prior_equity_returns_none():
    assert evaluate_daily_drift(today_equity=105_000, prior_equity=0, stats=STATS) is None


def test_missing_stats_returns_none():
    assert evaluate_daily_drift(today_equity=105_000, prior_equity=100_000, stats=None) is None
