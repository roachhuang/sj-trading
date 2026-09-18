"""calculateGrid() must be a true no-op when self.MA is 0/NaN (e.g. a
yfinance fetch blip in UpdateMA()) - a prior version returned
parameters["LowerLimitPosition"], which is actually the maximum possible
allocation toward the upper ticker, driving an aggressive unintended
rebalance on a data-fetch failure instead of doing nothing."""
from unittest.mock import MagicMock

import pytest

from sj_trading.gridbot import GridBot


def make_bot():
    bot = GridBot(MagicMock(), MagicMock())
    bot.parameters = {
        "BiasUpperLimit": 1.4,
        "UpperLimitPosition": 0.35,
        "BiasLowerLimit": 0.70,
        "LowerLimitPosition": 0.80,
        "BiasPeriod": 180,
    }
    return bot


def test_calculate_grid_raises_on_nan_ma_instead_of_returning_a_value():
    bot = make_bot()
    bot.MA = float("nan")
    with pytest.raises(ValueError):
        bot.calculateGrid(upperprice=100.0, lowerprice=50.0)


def test_calculate_grid_raises_on_zero_ma():
    bot = make_bot()
    bot.MA = 0
    with pytest.raises(ValueError):
        bot.calculateGrid(upperprice=100.0, lowerprice=50.0)


def test_update_order_skips_cycle_without_sending_orders_when_ma_invalid():
    """updateOrder() wraps calculateGrid (via calculateSharetarget) in a
    try/except, so the raise above must result in a genuine no-op cycle -
    no orders sent - not an unhandled crash."""
    bot = make_bot()
    bot.MA = float("nan")
    bot.api.list_trades.return_value = []
    bot.api.list_positions.return_value = []
    bot.stockPrice.update({bot.upperid: 100.0, bot.lowerid: 50.0})

    bot.updateOrder()

    bot.api.place_order.assert_not_called()


def test_calculate_grid_returns_valid_value_when_ma_present():
    bot = make_bot()
    bot.MA = 1.0
    result = bot.calculateGrid(upperprice=100.0, lowerprice=50.0)
    assert bot.parameters["UpperLimitPosition"] <= result <= bot.parameters["LowerLimitPosition"]
