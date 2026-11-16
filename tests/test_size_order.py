"""Sizing rules for a single order leg (GridBot._size_order): cash cap on
buys, fee headroom, and the minimum-trade-value trigger."""
from unittest.mock import MagicMock

from sj_trading.gridbot import GridBot


def make_bot(last_price=100.0, trigger=2000):
    """Bot with a mocked API, one quoted ticker and the given trigger."""
    bot = GridBot(MagicMock(), MagicMock())
    bot.stockPrice[bot.upperid] = last_price
    bot.trigger = trigger
    return bot


def test_buy_within_cash_is_unchanged():
    bot = make_bot()
    assert bot._size_order(bot.upperid, 30, 100.0, available=10_000) == 30


def test_buy_is_capped_at_available_cash():
    bot = make_bot()
    assert bot._size_order(bot.upperid, 100, 100.0, available=5_050) == 50


def test_buy_that_would_spend_all_cash_is_skipped():
    """Cost must stay strictly below available, leaving headroom for fees."""
    bot = make_bot()
    assert bot._size_order(bot.upperid, 30, 100.0, available=3_000) == 0


def test_buy_with_no_or_negative_cash_is_skipped_not_turned_into_a_sell():
    bot = make_bot(trigger=0)
    assert bot._size_order(bot.upperid, 30, 100.0, available=0) == 0
    assert bot._size_order(bot.upperid, 30, 100.0, available=-500) == 0


def test_order_below_trigger_is_skipped_in_both_directions():
    bot = make_bot()
    assert bot._size_order(bot.upperid, 19, 100.0, available=10_000) == 0
    assert bot._size_order(bot.upperid, -19, 100.0, available=0) == 0


def test_cash_capped_buy_that_falls_below_trigger_is_skipped():
    bot = make_bot()
    assert bot._size_order(bot.upperid, 100, 100.0, available=1_950) == 0


def test_sell_ignores_cash_and_ignore_trigger_allows_small_residuals():
    bot = make_bot()
    assert bot._size_order(bot.upperid, -25, 100.0, available=0) == -25
    assert bot._size_order(bot.upperid, -2, 100.0, available=0, ignore_trigger=True) == -2
