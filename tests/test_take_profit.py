"""Safety tests for the explicit pair liquidation path."""
import itertools
from types import SimpleNamespace
from unittest.mock import MagicMock

from shioaji import OrderStatus

from sj_trading import gridbot_body
from sj_trading.gridbot import GridBot, TICKERS
from sj_trading.gridbot_body import GridbotBody


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
        side_effect=lambda symbol, direction, qty, order_lot, price=None: SimpleNamespace(
            action=direction,
            order_lot=order_lot,
            quantity=qty,
            price=price if price is not None else bot.stockBid[symbol],
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


def fake_create_ord_obj(self, symbol, direction, qty, order_lot, price=None):
    """Real sj.StockOrder validates `account` is an actual Account instance,
    which a MagicMock api.stock_account isn't - same workaround as
    make_bot()'s createOrdObj override above, applied at the class level
    since GridbotBody constructs its own GridBot internally."""
    if price is None:
        price = self._get_order_price(symbol, direction)
    return SimpleNamespace(price=price, quantity=qty, action=direction, order_lot=order_lot)


def make_gridbot_body_api(close_price, buy_price, sell_price, list_positions_results):
    """Full GridbotBody-level mock - close_price is deliberately different
    from buy_price/sell_price so a regression back to seeding stockBid/
    stockAsk from `close` (see gridbot_body.py's snaprice seeding) would
    price the take-profit sell order wrong and fail the assertion."""
    api = MagicMock()
    api.contracts.get.side_effect = lambda code: SimpleNamespace(code=code)
    api.snapshots.side_effect = lambda contracts: [{
        "close": close_price[contracts[0].code],
        "buy_price": buy_price[contracts[0].code],
        "sell_price": sell_price[contracts[0].code],
    }]
    results = itertools.chain(list_positions_results, itertools.repeat(list_positions_results[-1]))
    api.list_positions.side_effect = lambda *a, **k: next(results)
    api.list_trades.return_value = []
    api.place_order.return_value = SimpleNamespace(status=SimpleNamespace(status=OrderStatus.Submitted))
    return api


def test_gridbot_body_take_profit_prices_orders_from_real_bid_ask_not_close(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SJ_TAKE_PROFIT", "true")
    monkeypatch.setattr(gridbot_body.time, "sleep", lambda s: None)
    monkeypatch.setattr(GridBot, "createOrdObj", fake_create_ord_obj)

    close_price = {TICKERS[0]: 999.0, TICKERS[1]: 999.0}
    buy_price = {TICKERS[0]: 100.0, TICKERS[1]: 50.0}
    sell_price = {TICKERS[0]: 100.5, TICKERS[1]: 50.5}
    held = [position(TICKERS[0], 1000)]
    # line-72 getPositions(), close_positions()'s internal getPositions(),
    # then the poll loop sees the fill and reports flat.
    list_positions_results = [held, held, []]

    api = make_gridbot_body_api(close_price, buy_price, sell_price, list_positions_results)

    result = GridbotBody(api)

    sent_prices = [call.args[1].price for call in api.place_order.call_args_list]
    assert sent_prices == [buy_price[TICKERS[0]]]
    assert not result  # positions ended up flat - job should not fail


def test_gridbot_body_take_profit_returns_truthy_when_positions_remain(monkeypatch, tmp_path):
    """Regression test: an incomplete take-profit liquidation must make
    GridbotBody return something truthy so main()'s
    `if job_failed: sys.exit(1)` actually fires - previously this path
    always `return None`, so a failed emergency liquidation still reported
    the CI run as a success."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SJ_TAKE_PROFIT", "true")
    monkeypatch.setattr(gridbot_body.time, "sleep", lambda s: None)
    monkeypatch.setattr(GridBot, "createOrdObj", fake_create_ord_obj)
    # First call sets the deadline (t=0 -> deadline=30); every call after
    # that inside the poll loop's while-condition reads as t=999, expiring
    # the 30s deadline instantly instead of real-time waiting for it.
    clock = itertools.chain([0], itertools.repeat(999))
    monkeypatch.setattr(gridbot_body.time, "monotonic", lambda: next(clock))

    held = [position(TICKERS[0], 1000)]
    prices = {TICKERS[0]: 100.0, TICKERS[1]: 50.0}
    api = make_gridbot_body_api(prices, prices, prices, [held])  # never fills

    result = GridbotBody(api)

    assert result


def test_seconds_until_open_waits_before_open_and_zero_after():
    import datetime
    early = datetime.datetime(2026, 10, 15, 7, 23, 0)
    assert gridbot_body.seconds_until_open(early) == (1 * 3600 + 42 * 60)
    assert gridbot_body.seconds_until_open(datetime.datetime(2026, 10, 15, 9, 5, 0)) == 0
    assert gridbot_body.seconds_until_open(datetime.datetime(2026, 10, 15, 11, 0, 0)) == 0


def test_take_profit_waits_for_open_then_reprices_from_fresh_snapshot(monkeypatch, tmp_path):
    import datetime
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SJ_TAKE_PROFIT", "true")
    monkeypatch.setattr(GridBot, "createOrdObj", fake_create_ord_obj)
    sleeps = []
    monkeypatch.setattr(gridbot_body.time, "sleep", lambda s: sleeps.append(s))

    class FakeDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 7, 23, 0)

    monkeypatch.setattr(gridbot_body.datetime, "datetime", FakeDT)

    held = [position(TICKERS[0], 1000)]
    api = make_gridbot_body_api(
        {TICKERS[0]: 1.0, TICKERS[1]: 1.0},
        {TICKERS[0]: 100.0, TICKERS[1]: 50.0},
        {TICKERS[0]: 100.5, TICKERS[1]: 50.5},
        [held, held, []],
    )
    # pre-open snapshot has empty bid/ask; post-wait snapshot has real quotes
    pre = {"close": 1.0, "buy_price": 0, "sell_price": 0}
    post = {TICKERS[0]: {"close": 100.2, "buy_price": 99.0, "sell_price": 99.5},
            TICKERS[1]: {"close": 50.0, "buy_price": 49.0, "sell_price": 49.5}}
    calls = {"n": 0}

    def snapshots(contracts):
        calls["n"] += 1
        return [pre] if calls["n"] <= 2 else [post[contracts[0].code]]

    api.snapshots.side_effect = snapshots

    result = GridbotBody(api)

    assert sleeps[0] == 1 * 3600 + 42 * 60
    assert [c.args[1].price for c in api.place_order.call_args_list] == [99.0]
    assert not result
