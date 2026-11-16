"""calculateSharetarget() must split the bot's own capital (cash + both
holdings) by GridBot.TARGET_UPPER_ALLOC, rounding share counts down."""
from unittest.mock import MagicMock

from sj_trading.gridbot import GridBot


def make_bot(cash, uppershare=0, lowershare=0):
    """Bot with a mocked API holding the given cash and share counts."""
    bot = GridBot(MagicMock(), MagicMock())
    bot.live_cash_right_now = cash
    bot.uppershare, bot.lowershare = uppershare, lowershare
    return bot


def test_all_cash_is_split_by_target_allocation():
    bot = make_bot(cash=100_000)
    upper, lower = bot.calculateSharetarget(upperprice=100.0, lowerprice=50.0)
    assert upper == int(GridBot.TARGET_UPPER_ALLOC * 100_000 / 100.0)
    assert lower == int((1 - GridBot.TARGET_UPPER_ALLOC) * 100_000 / 50.0)


def test_existing_holdings_count_toward_capital():
    bot = make_bot(cash=0, uppershare=500, lowershare=1000)  # 50k + 50k
    upper, lower = bot.calculateSharetarget(upperprice=100.0, lowerprice=50.0)
    assert (upper, lower) == (650, 700)


def test_targets_never_cost_more_than_capital():
    bot = make_bot(cash=33_497)
    upper, lower = bot.calculateSharetarget(upperprice=212.35, lowerprice=97.85)
    assert upper * 212.35 + lower * 97.85 <= 33_497


def test_update_order_skips_cycle_without_orders_when_price_missing():
    """A missing quote must be a no-op cycle (updateOrder's try/except),
    not a crash or an order priced off nothing."""
    bot = make_bot(cash=100_000)
    bot.api.list_trades.return_value = []
    bot.api.list_positions.return_value = []

    bot.updateOrder()

    bot.api.place_order.assert_not_called()
