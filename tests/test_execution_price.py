"""Execution-price rules for normal orders."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from shioaji import OrderStatus

from sj_trading.gridbot import GridBot


def make_bot(bid=100.0, ask=100.2):
    api = MagicMock()
    api.contracts.get.side_effect = lambda code: SimpleNamespace(code=code)
    api.place_order.return_value = SimpleNamespace(
        status=SimpleNamespace(status=OrderStatus.Submitted)
    )
    bot = GridBot(api, MagicMock())
    bot.stockPrice[bot.upperid] = bid
    bot.stockBid[bot.upperid] = bid
    bot.stockAsk[bot.upperid] = ask
    bot.trigger = 0
    bot.createOrdObj = MagicMock(
        return_value=SimpleNamespace(price=ask, quantity=1, order_lot="IntradayOdd")
    )
    return bot, api


def test_buy_uses_best_ask_and_sell_uses_best_bid():
    bot, _ = make_bot()

    assert bot._get_order_price(bot.upperid, "Buy") == 100.2
    assert bot._get_order_price(bot.upperid, "Sell") == 100.0


def test_buy_is_skipped_when_spread_is_too_wide():
    bot, api = make_bot(bid=100.0, ask=101.0)

    bot._sendOneOrder(bot.upperid, 1, available=1000)

    api.place_order.assert_not_called()
    bot.createOrdObj.assert_not_called()


def test_sell_is_not_blocked_by_buy_spread_guard():
    bot, api = make_bot(bid=100.0, ask=101.0)

    bot._sendOneOrder(bot.upperid, -1, available=0, ignore_trigger=True)

    api.place_order.assert_called_once()
