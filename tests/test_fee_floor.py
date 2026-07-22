"""order_cb must apply the correct minimum brokerage fee per lot type
(NT$20 Common, NT$1 IntradayOdd) - a prior bug used one flat floor for
both, silently overcharging/undercharging odd-lot fills."""
import math
from unittest.mock import MagicMock

from shioaji import OrderState

from sj_trading.gridbot import GridBot

FEE_RATE = GridBot.FEE_RATE
FEE_DISCOUNT = GridBot.FEE_DISCOUNT
TAX_RATE_ETF = GridBot.TAX_RATE_ETF


def make_bot():
    bot = GridBot(MagicMock(), MagicMock())
    bot.start_cash = 0
    bot.live_cash_right_now = 0
    return bot


def deal_msg(code, action, price, quantity, order_lot):
    return {"code": code, "action": action, "price": price, "quantity": quantity, "order_lot": order_lot}


def test_common_lot_buy_uses_20_floor_when_percentage_fee_is_smaller():
    bot = make_bot()
    price, qty = 10, 1  # 1 lot = 1000 shares, principal = 10000
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.upperid, "Buy", price, qty, "Common"))

    principal = price * 1000
    pct_fee = math.floor(principal * FEE_RATE * FEE_DISCOUNT)
    assert pct_fee < 20  # confirms this case actually exercises the floor
    expected_commission = 20
    assert bot.g_settlement == -(principal + expected_commission)


def test_intraday_odd_buy_uses_1_floor_when_percentage_fee_is_smaller():
    bot = make_bot()
    price, qty = 10, 1  # raw shares for IntradayOdd, principal = 10
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.lowerid, "Buy", price, qty, "IntradayOdd"))

    principal = price * qty
    pct_fee = math.floor(principal * FEE_RATE * FEE_DISCOUNT)
    assert pct_fee < 1  # confirms this case actually exercises the floor
    expected_commission = 1
    assert bot.g_settlement == -(principal + 1)


def test_common_lot_sell_deducts_tax_and_floored_commission():
    bot = make_bot()
    price, qty = 100, 1  # 1 lot = 1000 shares, principal = 100000
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.upperid, "Sell", price, qty, "Common"))

    principal = math.floor(price * 1000)
    commission = max(20, math.floor(principal * FEE_RATE * FEE_DISCOUNT))
    tax = math.floor(principal * TAX_RATE_ETF)
    assert bot.g_settlement == principal - tax - commission


def test_percentage_fee_wins_over_floor_on_large_common_lot_trade():
    bot = make_bot()
    price, qty = 1000, 5  # principal = 5,000,000 -> pct fee well above 20
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.upperid, "Buy", price, qty, "Common"))

    principal = price * qty * 1000
    pct_fee = math.floor(principal * FEE_RATE * FEE_DISCOUNT)
    assert pct_fee > 20
    assert bot.g_settlement == -(principal + pct_fee)


def test_live_cash_right_now_reflects_start_cash_plus_settlement():
    bot = make_bot()
    bot.start_cash = 50000
    bot.live_cash_right_now = 50000
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.lowerid, "Buy", 10, 1, "IntradayOdd"))

    assert bot.live_cash_right_now == bot.start_cash + bot.g_settlement


def test_live_cash_right_now_does_not_double_count_prior_fills():
    """A 2nd fill must not re-add the 1st fill's settlement delta - g_settlement
    is cumulative, so live_cash_right_now must be recomputed from start_cash
    each time, not accumulated on top of its own prior value."""
    bot = make_bot()
    bot.start_cash = 100000
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.upperid, "Buy", 10, 1, "IntradayOdd"))
    bot.order_cb(OrderState.StockDeal, deal_msg(bot.upperid, "Sell", 10, 1, "IntradayOdd"))

    assert bot.live_cash_right_now == bot.start_cash + bot.g_settlement


def test_deal_for_untracked_ticker_does_not_touch_settlement():
    bot = make_bot()
    bot.order_cb(OrderState.StockDeal, deal_msg("2330", "Buy", 500, 1, "Common"))
    assert bot.g_settlement == 0
