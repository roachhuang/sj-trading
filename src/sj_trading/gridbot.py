import shioaji as sj

from typing import Dict
from shioaji import OrderState, OrderStatus
from threading import Lock
import math

g_upperid = "0052"
g_lowerid = "00662"
TICKERS = (g_upperid, g_lowerid)


class GridBot:
    g_settlement: int
    upperid: str
    lowerid: str
    # 台灣證券交易法定費率
    FEE_RATE = 0.001425
    FEE_DISCOUNT = 0.38
    TAX_RATE_STOCK = 0.003    # 一般股票證交稅
    TAX_RATE_ETF = 0.001      # ETF 證交稅
    MIN_FEE = 1               # odd lot 手續費最低限制
    MAX_BUY_SPREAD_PCT = 0.005  # skip buys when 賣1 is more than 0.5% above 買1

    # Fixed share of the bot's capital held in upperid (0052); the rest goes
    # to lowerid (00662). Replaced a bias-ratio (乖離率) tilt on 2026-11-16:
    # over 2016-06..2026-11 the tilt backtested at Sharpe 1.16 against 1.18
    # for a constant mix at the tilt's own average allocation (~0.64), so
    # the signal added nothing a fixed split doesn't already give. 0.65
    # keeps the bot holding what it was already holding on average - it is
    # a continuity choice, not an optimised one. If this changes, re-run
    # backtest.py so backtest_stats.json (the drift check's baseline)
    # matches.
    TARGET_UPPER_ALLOC = 0.65

    def __init__(self, api: sj.Shioaji, logging):
        self.trigger = 2000  # 最低交易金額門檻,避免交易金額太小,錢被手續費低消吃光光
        # Must be 3 independent dicts, not a chained assignment - gridbot_body.py
        # syncs these from 3 separately-maintained dicts every cycle; aliasing
        # them together made every write collapse onto whichever was written
        # last (stockAsk), so stockPrice/stockBid silently became the ask price.
        self.stockPrice = {}
        self.stockBid = {}
        self.stockAsk = {}
        self.start_cash = self.g_settlement = 0
        self.upperid, self.lowerid = TICKERS
        self.live_cash_right_now = self.uppershare = self.lowershare = 0
        self.api = api
        self.logging = logging
        self.api.set_order_callback(self.order_cb)
        self.mutexgSettle = Lock()

    # 處理訂單成交的狀況,用來更新交割款
    def order_cb(self, stat: OrderState, msg: Dict):
        print(f"stat: {stat}, msg:{msg}")
        # OrderState.StockDeal is only a const, not an object. so as stat is also just a const
        if stat == OrderState.StockDeal:
            code = msg["code"]
            if code in TICKERS:
                try:
                    action = msg["action"]
                    price = msg["price"]
                    # Shioaji reports Common-lot fills in lots (1 lot = 1000
                    # shares); IntradayOdd fills are already in raw shares.
                    qty = msg["quantity"] * 1000 if msg["order_lot"] == "Common" else msg["quantity"]
                    principal = math.floor(price * qty)
                    min_brokerage_fee = 20 if msg["order_lot"] == "Common" else self.MIN_FEE
                    commission = max(min_brokerage_fee, math.floor(principal*self.FEE_RATE * self.FEE_DISCOUNT))

                    # `with` guarantees release even if something below raises -
                    # a raw acquire()/release() here previously meant any
                    # exception (e.g. a missing dict key) would leak the lock
                    # forever, deadlocking every future order_cb/sendOrders call.
                    with self.mutexgSettle:
                        if action == "Buy":
                            self.g_settlement -= (principal + commission)
                        elif action == "Sell":
                            tax = math.floor(principal*self.TAX_RATE_ETF)
                            self.g_settlement += (principal - tax - commission)
                        else:
                            pass
                        self.live_cash_right_now = self.start_cash + self.g_settlement
                    self.logging.info(f"deal: {code} {action} {qty}@{price}, live available cash right now: {self.live_cash_right_now}")
                except Exception as e:
                    self.logging.error(f"order_cb settlement update failed: {e}")
        self.logging.info(f"in order_cb, stat: {stat}")

    #########################################
    # 7.2 抓取庫存部位大小y
    #########################################
    def getPositions(self) -> bool:
        try:
            positions = self.api.list_positions(self.api.stock_account, unit=sj.Unit.Share)
        except Exception as e:
            self.logging.error(f"list_positions failed, keeping stale share counts: {e}")
            return False
        shares = {tid: next((pos.quantity for pos in positions if pos.code == tid), 0) for tid in TICKERS}
        self.uppershare, self.lowershare = shares[self.upperid], shares[self.lowerid]
        return True

    def calculateSharetarget(self, upperprice, lowerprice) -> tuple:
        """Target share counts for (upperid, lowerid): TARGET_UPPER_ALLOC of
        the bot's capital (its cash plus both holdings at the given prices)
        in upperid and the rest in lowerid, rounded down to whole shares."""
        capitalInBot = self.live_cash_right_now + self.uppershare * upperprice + self.lowershare * lowerprice

        uppershareTarget = int(self.TARGET_UPPER_ALLOC * capitalInBot / upperprice)
        lowershareTarget = int((1.0 - self.TARGET_UPPER_ALLOC) * capitalInBot / lowerprice)

        self.logging.info(f'uppershareTarget: {uppershareTarget}, pirce:{upperprice}')
        self.logging.info(f'lowershareTarget: {lowershareTarget}, price:{lowerprice}')
        return uppershareTarget, lowershareTarget

    #########################################
    # 7.3. 實際掛單
    ###########################################

    def updateOrder(self):
        try:
            if not self.cancelOrders():
                self.logging.error("updateOrder: cancelOrders incomplete, skipping this cycle to avoid placing new orders on top of stale ones")
                return
            #################################
            # 2.更新庫存
            ############################
            if not self.getPositions():
                self.logging.error("updateOrder: getPositions failed, skipping this cycle (stale share counts)")
                return
            ####################################
            # 3.更新目標部位
            ##############################
            # it looks like current price
            target_share = self.calculateSharetarget(
                upperprice=self.stockPrice[self.upperid],
                lowerprice=self.stockPrice[self.lowerid],
            )

            self.sendOrders(target_share)
        except Exception as e:
            self.logging.error(f"updateOrder failed, skipping this cycle: {e}")

    def cancelOrders(self) -> bool:
        try:
            self.api.update_status(self.api.stock_account)
            # 列出所有的訂單
            tradelist = self.api.list_trades()
        except Exception as e:
            self.logging.error(f"list_trades failed, skipping cancel this cycle: {e}")
            return False
        # 把交易股票種類跟交易機器人一樣的有效訂單取消
        terminal_statuses = (OrderStatus.Cancelled, OrderStatus.Failed, OrderStatus.Filled)
        trades_by_id = {tid: [] for tid in TICKERS}
        for thistrade in tradelist:
            if thistrade.status.status not in terminal_statuses and thistrade.contract.code in trades_by_id:
                trades_by_id[thistrade.contract.code].append(thistrade)

        # 實際取消訂單的部分
        all_cancelled = True
        for tid, trades in trades_by_id.items():
            for i, trade in enumerate(trades):
                try:
                    self.api.cancel_order(trade=trade)
                    self.api.update_status(self.api.stock_account)
                except Exception as e:
                    self.logging.error(f"cancel_order failed for {tid} trade {i}: {e}")
                    all_cancelled = False
        return all_cancelled

    def createOrdObj(self, symbol, direction, qty, order_lot, price):
        """Build a limit ROD order. `qty` is in lots for Common (1 lot =
        1000 shares) and in raw shares for IntradayOdd - the caller passes
        whichever is correct per order_lot."""
        return sj.StockOrder(
            price=price,
            quantity=qty,
            action=direction,
            price_type=sj.StockPriceType.LMT,
            order_type=sj.OrderType.ROD,
            order_lot=order_lot,
            account=self.api.stock_account,
        )

    def _get_order_price(self, symbol, direction):
        """Use 賣1 for buys and 買1 for sells, with a wide-spread buy guard."""
        bid = self.stockBid.get(symbol)
        if not bid or bid <= 0:
            self.logging.error(f"_get_order_price: 買1 for {symbol} is 0/missing")
            return None
        if direction == "Sell":
            return bid

        ask = self.stockAsk.get(symbol)
        if not ask or ask <= 0:
            self.logging.error(f"_get_order_price: 賣1 for {symbol} is 0/missing")
            return None
        if ask < bid:
            self.logging.error(f"_get_order_price: crossed book for {symbol}, 買1={bid}, 賣1={ask}")
            return None
        spread_pct = (ask - bid) / bid
        if spread_pct > self.MAX_BUY_SPREAD_PCT:
            self.logging.info(
                f"skip buy for {symbol}: spread={spread_pct:.3%} exceeds "
                f"{self.MAX_BUY_SPREAD_PCT:.3%}"
            )
            return None
        return ask

    def sendOrders(self, target_share: tuple):
        shares = {self.upperid: self.uppershare, self.lowerid: self.lowershare}
        targets = dict(zip(TICKERS, target_share))

        # available tracks cash across both legs THIS cycle only - fills are
        # async (order_cb), so self.live_cash_right_now won't reflect the upper leg's cost
        # in time for the lower leg's check without this.
        # Snapshot under mutexgSettle - order_cb writes live_cash_right_now
        # under this same lock on its own thread, so an unprotected read
        # here could race a concurrent fill and grab a stale value.
        with self.mutexgSettle:
            available = self.live_cash_right_now
        for tid in TICKERS:
            available = self._sendOneOrder(tid, targets[tid] - shares[tid], available)

    def close_positions(self) -> bool:
        """Submit sell orders for all currently-held bot positions.

        This is intentionally separate from rebalancing: a close operation
        must sell the exact current holdings, including small odd-lot
        residuals, and must not calculate a new target that could buy again.
        """
        if not self.cancelOrders():
            self.logging.error("close_positions: could not cancel existing orders")
            return False
        if not self.getPositions():
            self.logging.error("close_positions: could not refresh positions")
            return False

        available = self.live_cash_right_now
        for tid, shares in ((self.upperid, self.uppershare), (self.lowerid, self.lowershare)):
            available = self._sendOneOrder(tid, -shares, available, ignore_trigger=True)
        return True

    def _sendOneOrder(self, symbol, qty, available, ignore_trigger=False):
        """Trade `qty` shares of `symbol` (positive buys, negative sells)
        and return the cash still available for the next leg this cycle.

        Sells never add to `available` (proceeds are unsettled this cycle);
        buys reduce it only by the portion actually submitted."""
        if qty == 0:
            return available
        direction = "Buy" if qty > 0 else "Sell"
        price = self._get_order_price(symbol, direction)
        if price is None:
            return available
        qty = self._size_order(symbol, qty, price, available, ignore_trigger)
        if qty == 0:
            return available
        ordered_shares = self._place_lots(symbol, direction, abs(qty), price)
        if direction == "Buy":
            available -= price * ordered_shares
        return available

    def _size_order(self, symbol, qty, price, available, ignore_trigger=False) -> int:
        """Pure sizing rule: how many of the wanted `qty` shares to really
        trade at `price`. Returns 0 to skip the order.

        A buy is capped at what `available` cash covers and must leave some
        cash over (cost strictly below `available`) as fee headroom. Any
        order worth less than `self.trigger` is skipped so the minimum
        brokerage fee doesn't eat tiny trades, unless `ignore_trigger`."""
        if qty > 0:
            if available < price * qty:
                qty = max(int(available / price), 0)
            if available <= price * qty:
                return 0
        if not ignore_trigger and abs(qty) * self.stockPrice[symbol] < self.trigger:
            return 0
        return qty

    def _place_lots(self, symbol, direction, shares, price) -> int:
        """Submit `shares` as a Common-lot order plus an IntradayOdd order
        and return how many shares were actually accepted.

        IntradayOdd orders only take 0-999 shares, so anything >=1000 needs
        a separate Common-lot order (quantity in lots) for the round-lot
        portion. Only submitted orders are counted, so a partial failure
        (e.g. the odd-lot leg rejected) isn't reported as bought/sold."""
        contract = self.api.contracts.get(symbol)
        lots, remainder = divmod(shares, 1000)
        successfully_ordered_shares = 0
        for order_lot, lot_qty in ((sj.StockOrderLot.Common, lots), (sj.StockOrderLot.IntradayOdd, remainder)):
            if lot_qty == 0:
                continue
            order = self.createOrdObj(
                symbol=symbol,
                direction=direction,
                qty=lot_qty,
                order_lot=order_lot,
                price=price,
            )
            try:
                trade_result = self.api.place_order(contract, order)
            except Exception as e:
                self.logging.error(f"place_order failed for {symbol} {direction} {order_lot} qty={lot_qty}: {e}")
                continue
            # place_order() not raising only means the API call went through -
            # a broker-side rejection (bad price, order restrictions) comes
            # back as a normal Trade with status=Failed, no exception at all.
            if trade_result.status.status == OrderStatus.Failed:
                self.logging.error(f"place_order rejected by broker for {symbol} {direction} {order_lot} qty={lot_qty}: {trade_result.status.status}")
                continue
            self.logging.info(f"{direction} {symbol} {order_lot} @ {order.price}, qty: {order.quantity}")
            successfully_ordered_shares += lot_qty * 1000 if order_lot == sj.StockOrderLot.Common else lot_qty
        return successfully_ordered_shares
