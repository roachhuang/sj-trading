"""cancelOrders() must only ever cancel orders for the bot's own tickers
(0052/00662) - an earlier live incident had a cancel routine reach an
unrelated account-wide order."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from shioaji import OrderStatus

from sj_trading.gridbot import GridBot, TICKERS

FOREIGN_TICKER = "2330"


def make_trade(code, status=OrderStatus.Submitted):
    return SimpleNamespace(
        status=SimpleNamespace(status=status),
        contract=SimpleNamespace(code=code),
    )


def make_bot(trades):
    api = MagicMock()
    api.list_trades.return_value = trades
    bot = GridBot(api, MagicMock())
    return bot, api


def test_cancel_only_touches_target_tickers():
    trades = [make_trade(TICKERS[0]), make_trade(TICKERS[1]), make_trade(FOREIGN_TICKER)]
    bot, api = make_bot(trades)

    assert bot.cancelOrders() is True

    cancelled_codes = {call.kwargs["trade"].contract.code for call in api.cancel_order.call_args_list}
    assert cancelled_codes == set(TICKERS)
    assert FOREIGN_TICKER not in cancelled_codes
    assert api.cancel_order.call_count == 2


def test_cancel_ignores_terminal_status_orders():
    trades = [
        make_trade(TICKERS[0], status=OrderStatus.Filled),
        make_trade(TICKERS[1], status=OrderStatus.Cancelled),
    ]
    bot, api = make_bot(trades)

    assert bot.cancelOrders() is True
    api.cancel_order.assert_not_called()


def test_cancel_skips_orders_for_untracked_tickers_entirely():
    trades = [make_trade(FOREIGN_TICKER)]
    bot, api = make_bot(trades)

    assert bot.cancelOrders() is True
    api.cancel_order.assert_not_called()


def test_cancel_returns_false_and_keeps_scope_on_partial_failure():
    trades = [make_trade(TICKERS[0]), make_trade(TICKERS[1]), make_trade(FOREIGN_TICKER)]
    bot, api = make_bot(trades)
    api.cancel_order.side_effect = Exception("network blip")

    assert bot.cancelOrders() is False
    cancelled_codes = {call.kwargs["trade"].contract.code for call in api.cancel_order.call_args_list}
    assert FOREIGN_TICKER not in cancelled_codes
