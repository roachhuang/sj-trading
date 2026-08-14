"""Safety tests for the explicit pair liquidation path."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from shioaji import OrderStatus

from sj_trading.gridbot import GridBot, TICKERS


def position(code, quantity):
    return SimpleNamespace(code=code, quantity=quantity)


def make_bot(positions):
    api = MagicMock()
    api.list_trades.return_value = []
    api.list_positions.return_value = positions
    api.contracts.get.side_effect = lambda code: SimpleNamespace(code=code)
    api.place_order.return_value = SimpleNamespace(
        status=SimpleNamespace(status=OrderStatus.Submitted)
    )
    bot = GridBot(api, MagicMock())
    bot.stockPrice.update({TICKERS[0]: 100.0, TICKERS[1]: 50.0})
    bot.stockBid.update({TICKERS[0]: 99.5, TICKERS[1]: 49.5})
    bot.stockAsk.update({TICKERS[0]: 100.5, TICKERS[1]: 50.5})
    bot.createOrdObj = MagicMock(
        side_effect=lambda symbol, direction, qty, order_lot: SimpleNamespace(
            action=direction,
            order_lot=order_lot,
            quantity=qty,
            price=bot.stockBid[symbol],
        )
    )
    return bot, api


def test_close_positions_sells_exact_holdings_including_small_residuals():
    bot, api = make_bot([
        position(TICKERS[0], 1001),
        position(TICKERS[1], 2),
        position("2330", 500),
    ])
    bot.trigger = 2000

    assert bot.close_positions() is True

    assert [(call.kwargs["direction"], call.kwargs["order_lot"].value, call.kwargs["qty"]) for call in bot.createOrdObj.call_args_list] == [
        ("Sell", "Common", 1),
        ("Sell", "IntradayOdd", 1),
        ("Sell", "IntradayOdd", 2),
    ]
    assert [call.args[0].code for call in api.place_order.call_args_list] == [
        TICKERS[0], TICKERS[0], TICKERS[1]
    ]


def test_close_positions_does_not_place_orders_when_positions_are_empty():
    bot, api = make_bot([])

    assert bot.close_positions() is True
    api.place_order.assert_not_called()


def test_close_positions_aborts_if_existing_orders_cannot_be_cancelled():
    bot, api = make_bot([position(TICKERS[0], 1000)])
    api.list_trades.return_value = [
        SimpleNamespace(
            status=SimpleNamespace(status=OrderStatus.Submitted),
            contract=SimpleNamespace(code=TICKERS[0]),
        )
    ]
    api.cancel_order.side_effect = RuntimeError("cancel failed")

    assert bot.close_positions() is False
    api.place_order.assert_not_called()
