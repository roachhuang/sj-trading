"""Cross-checks the bad-data detectors in gridbot.py/backtest.py against
TWSE-confirmed real-world price moves (see CLAUDE.md Lessons):

- 00662 (NASDAQ-100-linked, exempt from the domestic +-10% limit) moved
  +14.3% then +12.3% on 2025-04-07/04-10 during the tariff-shock selloff -
  a real, TWSE-confirmed move that must NOT be flagged as bad data.
- 0052 had a genuine 1-for-7 split on 2025-11-17, a +85.7% single-day
  jump that must be detected and handled as a scale discontinuity, not
  averaged over as if it were real price action.
"""
import pandas as pd
import pytest
from unittest.mock import MagicMock

from sj_trading.gridbot import GridBot
from sj_trading.backtest import _adjust_split_defects


def make_series(*closes):
    idx = pd.date_range("2025-04-01", periods=len(closes), freq="D")
    return pd.Series(closes, index=idx)


def test_twse_confirmed_tariff_shock_move_is_not_truncated():
    """00662 real move: 100 -> 114.3 (+14.3%) -> 128.35 (+12.3%). Must survive intact."""
    bot = GridBot(MagicMock(), MagicMock())
    close = make_series(100.0, 114.3, 128.35, 130.0, 131.0)

    result = bot._truncate_at_bad_data(close)

    assert len(result) == len(close)
    assert result.equals(close)


def test_twse_confirmed_0052_split_jump_is_truncated():
    """0052 real move: genuine 1-for-7 split, +85.7% single-day jump.
    The truncate is strictly-after the jump date, so the jump day itself
    (bad on both sides: pre-split scale in the numerator, post-split in the
    pct_change) is conservatively dropped along with everything before it."""
    bot = GridBot(MagicMock(), MagicMock())
    pre_split = [20.0, 20.1, 19.9]
    post_split = [20.0 * 1.857, 37.5, 37.8]  # +85.7% jump, then normal post-split moves
    close = make_series(*(pre_split + post_split))

    result = bot._truncate_at_bad_data(close)

    assert len(result) == len(post_split) - 1
    assert result.equals(close.iloc[len(pre_split) + 1:])


def test_twse_confirmed_split_is_rescaled_to_a_continuous_series():
    """backtest.py's _adjust_split_defects must eliminate the discontinuity
    (unlike the live truncate, which drops history) so a full backtest can
    run over pre- and post-split data on one consistent scale."""
    pre_split = [20.0, 20.1, 19.9]
    jump_ratio = 1.857
    post_split = [pre_split[-1] * jump_ratio, 37.5, 37.8]
    close = make_series(*(pre_split + post_split))

    adjusted = _adjust_split_defects(close)

    assert len(adjusted) == len(close)
    max_move = adjusted.pct_change().abs().max()
    assert max_move < 0.30
    # pre-split prices should be rescaled by the same ratio as the jump
    assert adjusted.iloc[0] == pytest.approx(pre_split[0] * jump_ratio)
