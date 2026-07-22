"""_sendOneOrder must place an order once the affordability clip produces
a valid quantity - a stale redundant check used to discard any buy that
would spend exactly all available cash, silently skipping legitimate,
correctly-sized trades."""
from unittest.mock import MagicMock

from shioaji import OrderStatus

from sj_trading import gridbot as gridbot_module
from sj_trading.gridbot import GridBot


def make_bot():
    bot = GridBot(MagicMock(), MagicMock())
    bot.stockBid = {bot.upperid: 20.0}
    bot.stockPrice = {bot.upperid: 20.0}
    bot.trigger = 2000
    bot.api.place_order.return_value = MagicMock(status=MagicMock(status=OrderStatus.PendingSubmit))
    return bot


def test_exact_fit_affordable_buy_is_placed_not_skipped(monkeypatch):
    bot = make_bot()
    monkeypatch.setattr(gridbot_module.sj, "StockOrder", MagicMock())

    # target delta wants 200 shares @ 20, but only 2000 cash available ->
    # clips to exactly 100 shares (2000 / 20), an exact, fully affordable fit.
    result = bot._sendOneOrder(bot.upperid, 200, 2000)

    bot.api.place_order.assert_called_once()
    assert gridbot_module.sj.StockOrder.call_args.kwargs["quantity"] == 100
    assert result == 0


def test_unaffordable_buy_below_trigger_is_still_skipped():
    bot = make_bot()

    # clips to 95 shares (1900 / 20) - order value 1900 is below the 2000
    # trigger, so this must still be skipped (unrelated to the fix above).
    result = bot._sendOneOrder(bot.upperid, 200, 1900)

    bot.api.place_order.assert_not_called()
    assert result == 1900
