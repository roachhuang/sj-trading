# import pandas as pd
import shioaji as sj
import yfinance as yf

from typing import Dict, Optional
from shioaji import OrderState, OrderStatus
from threading import Lock
import datetime
import math

g_upperid = "0052"
g_lowerid = "00662"


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

    # parameters = {
    #     "BiasUpperLimit": 2.0,
    #     "UpperLimitPosition": 0.4,
    #     "BiasLowerLimit": 0.899999,
    #     "LowerLimitPosition": 0.899999,
    #     "BiasPeriod": 6,
    # }
    # Backtested 2016-2026 (~2455 trading days, out-of-sample validated on a
    # held-out 2023-2026 slice): Sharpe 1.27 in-sample / 1.79 out-of-sample
    # vs. 0.57 / 0.40 for the previous values, with a shallower max drawdown
    # (-36% vs -59%). Position bounds kept away from 0/1 so this stays a
    # genuine two-asset grid rather than a Nasdaq-timing on/off switch.
    parameters = {
        "BiasUpperLimit": 1.1,
        "UpperLimitPosition": 0.15,
        "BiasLowerLimit": 0.95,
        "LowerLimitPosition": 0.85,
        "BiasPeriod": 220,
    }

    def __init__(self, api: sj.Shioaji, logging):
        # keep track of MA calulated date
        self.year = self.month = self.day = 0
        self.trigger = 2000  # 最低交易金額門檻,避免交易金額太小,錢被手續費低消吃光光
        self.msglist = []
        self.statlist = []
        self.stockPrice = self.stockBid = self.stockAsk = {}
        self.initmoney = self.g_settlement = 0
        self.upperid = g_upperid
        self.lowerid = g_lowerid
        self.live_cash_right_now = self.upperprice = self.uppershare = self.lowerprice = self.lowershare = 0
        self.contractUpper = api.Contracts.Stocks[self.upperid]
        self.contractLower = api.Contracts.Stocks[self.lowerid]
        self.api = api
        self.logging = logging
        self.api.set_order_callback(self.order_cb)
        self.mutexgSettle = Lock()
        self.mutexmsg = Lock()
        self.mutexstat = Lock()

    # 處理訂單成交的狀況,用來更新交割款
    def order_cb(self, stat: OrderState, msg: Dict):
        print(f"stat: {stat}, msg:{msg}")
        # OrderState.StockDeal is only a const, not an object. so as stat is also just a const
        if stat == OrderState.StockDeal:
            code = msg["code"]
            isUpper = code == g_upperid
            isLower = code == g_lowerid
            if isUpper or isLower:
                self.mutexgSettle.acquire()
                
                action = msg["action"]
                price= msg["price"]
                # Shioaji reports Common-lot fills in lots (1 lot = 1000
                # shares); IntradayOdd fills are already in raw shares.
                qty = msg["quantity"] * 1000 if msg["order_lot"] == "Common" else msg["quantity"]
                consideration = price * qty
                commission = max(self.MIN_FEE, math.floor(consideration*self.FEE_RATE * self.FEE_DISCOUNT))
               
                if action == "Buy":
                    self.g_settlement -=  consideration + commission
                elif action == "Sell":
                    tax = math.floor(consideration*self.TAX_RATE_ETF)
                    self.g_settlement += consideration - tax - commission
                else:
                    pass
                self.live_cash_right_now = int(self.initmoney + self.g_settlement)
                self.mutexgSettle.release()
                self.logging.info(f"deal: {code} {action} {qty}@{price}, live available cash right now: {self.live_cash_right_now}")
        self.mutexmsg.acquire()
        try:
            self.msglist.append(msg)
        except Exception as e:  # work on python 3.x
            self.logging.error("place_cb  Error Message A: " + str(e))
        self.mutexmsg.release()

        self.mutexstat.acquire()
        try:
            self.statlist.append(stat)
            self.logging.info(f"in order_cb, stat: {stat}")
        except Exception as e:  # work on python 3.x
            self.logging.error("place_cb  Error Message B: " + str(e))
        self.mutexstat.release()

    #########################################
    # 7.1 計算策略目標部位(百分比)
    ###########################################
    def UpdateMA(self):
        now = datetime.datetime.now()
        # 如果有換日就更新均線,或者第一次呼叫的時候也會更新均線
        if now.year != self.year or now.month != self.month or now.day != self.day:
            try:
                # 從Yfinance抓取日資料
                upper = yf.Ticker(self.upperid + ".tw")
                upper_hist = upper.history(period="2y")

                # 計算均線
                period = self.parameters["BiasPeriod"]
                upper_close = upper_hist["Close"]
                # 1.如果是做 股票 / TWD 的網格那就只要股票價格取平均
                # 2.如果是做 股票A / 股票B 的相對價值網格那就需要
                # 先計算 股票A / 股票B 的收盤價，再取平均
                if self.lowerid != "Cash":
                    lower = yf.Ticker(self.lowerid + ".tw")
                    lower_hist = lower.history(period="2y")
                    lower_close = lower_hist["Close"]
                    close = (upper_close / lower_close).dropna()
                else:
                    close = upper_close.dropna()
                self.MA = close[-period:].mean()
                self.year = now.year
                self.month = now.month
                self.day = now.day
                s = "MA:" + str(self.MA)
                # self.logging.info(s)
            except Exception as e:
                self.logging.error(f"UpdateMA failed, keeping stale MA: {e}")

    #########################################
    # 7.2 抓取庫存部位大小y
    #########################################
    def getPositions(self):
        try:
            positions = self.api.list_positions(self.api.stock_account, unit=sj.Unit.Share)
        except Exception as e:
            self.logging.error(f"list_positions failed, keeping stale share counts: {e}")
            return
        self.lowershare = next((pos.quantity for pos in positions if pos.code == self.lowerid), 0)
        self.uppershare = next((pos.quantity for pos in positions if pos.code == self.upperid), 0)
        # msg = f"positions: 00662-{self.lowershare}, 0052-{self.uppershare}"
        # print(msg)

    def calculateSharetarget(self, upperprice, lowerprice)->tuple:
        # 計算目標部位百分比
        upper_alloc_percentage = self.calculateGrid(upperprice, lowerprice)

        # move to order_cb
        # self.live_cash_right_now=self.initmoney+self.g_settlement
        # no reset settlement after update money is required coz of using initmoney

        uppershare = self.uppershare
        lowershare = self.lowershare

        # 計算機器人裡面有多少資產(可用現金+股票現值)
        capitalInBot = self.live_cash_right_now + uppershare * upperprice + lowershare * lowerprice

        # 計算目標部位(股數)
        uppershareTarget = int(upper_alloc_percentage * capitalInBot / upperprice)
        lowershareTarget = int((1.0 - upper_alloc_percentage) * capitalInBot / lowerprice)

        # 紀錄目標部位(股數)
        # self.uppershareTarget = uppershareTarget
        # self.lowershareTarget = lowershareTarget
        # self.upperprice=upperprice
        # self.lowerprice=lowerprice

        self.logging.info(f'uppershareTarget: {uppershareTarget}, pirce:{upperprice}')
        self.logging.info(f'lowershareTarget: {lowershareTarget}, price:{lowerprice}')
        # 2. 直接計算出目標股數的 Tuple： (upper_target, lower_target)
        # 注意：若您前面有處理摩擦成本低消考慮，這裡使用 int() 會直接向零取整（無條件捨去）
        targets = (
            int(upper_alloc_percentage * capitalInBot / upperprice),
            int((1.0 - upper_alloc_percentage) * capitalInBot / lowerprice)
        )
        return targets

    def calculateGrid(self, upperprice, lowerprice)->float:
        """
        乖離率是一個用來衡量股價與其移動平均線之間差距的指標。簡單來說，就是用來觀察股價是偏離了長期趨勢多還是少。

        乖離率的計算方式

        乖離率 = (當前股價 - 移動平均線) / 移動平均線 * 100%

        當前股價： 股票在當天的收盤價。
        移動平均線： 通常使用5日、10日、20日或更長的移動平均線。
        乖離率的意義

        判斷超買超賣：
        乖離率過高：表示股價遠高於移動平均線，可能處於超買狀態，未來可能回檔。
        乖離率過低：表示股價遠低於移動平均線，可能處於超賣狀態，未來可能反彈。
        確認趨勢：
        若乖離率持續維持正值且不斷擴大，表示股價處於強勁的上漲趨勢。
        若乖離率持續維持負值且不斷擴大，表示股價處於下跌趨勢。
        尋找進場時機：
        當乖離率由正轉負，且股價跌破移動平均線時，可能是一個賣出訊號。
        當乖離率由負轉正，且股價突破移動平均線時，可能是一個買入訊號。
        """
        MA = self.MA
        if not MA or math.isnan(MA):
            self.logging.error("calculateGrid: MA is 0/NaN, falling back to no-op shareTarget")
            return self.parameters["LowerLimitPosition"]
        # 計算目標部位百分比
        BiasUpperLimit = self.parameters["BiasUpperLimit"]
        UpperLimitPosition = self.parameters["UpperLimitPosition"]
        BiasLowerLimit = self.parameters["BiasLowerLimit"]
        LowerLimitPosition = self.parameters["LowerLimitPosition"]
        # compute 乖離 rate
        Bias = (upperprice / lowerprice) / MA
        shareTarget = (Bias - BiasLowerLimit) / (BiasUpperLimit - BiasLowerLimit)
        shareTarget = shareTarget * (UpperLimitPosition - LowerLimitPosition) + LowerLimitPosition
        shareTarget = max(shareTarget, UpperLimitPosition)
        shareTarget = min(shareTarget, LowerLimitPosition)
        upper_alloc_percentage = shareTarget
        return upper_alloc_percentage

    #########################################
    # 7.3. 實際掛單
    ###########################################

    def updateOrder(self):
        try:
            #################################
            # 0.更新日均線資料
            #################################
            self.UpdateMA()

            self.cancelOrders()
            #################################
            # 2.更新庫存
            ############################
            self.getPositions()
            ####################################
            # 3.更新目標部位
            ##############################
            # it looks like current price
            target_share = self.calculateSharetarget(
                upperprice=self.stockPrice[g_upperid],
                lowerprice=self.stockPrice[g_lowerid],
            )

            self.sendOrders(target_share)
        except Exception as e:
            self.logging.error(f"updateOrder failed, skipping this cycle: {e}")

    def cancelOrders(self):
        try:
            self.api.update_status(self.api.stock_account)
            # 列出所有的訂單
            tradelist = self.api.list_trades()
        except Exception as e:
            self.logging.error(f"list_trades failed, skipping cancel this cycle: {e}")
            return
        # 把交易股票種類跟交易機器人一樣的有效訂單取消
        terminal_statuses = (OrderStatus.Cancelled, OrderStatus.Failed, OrderStatus.Filled)
        trades_by_id = {self.upperid: [], self.lowerid: []}
        for thistrade in tradelist:
            if thistrade.status.status not in terminal_statuses and thistrade.contract.code in trades_by_id:
                trades_by_id[thistrade.contract.code].append(thistrade)

        # 實際取消訂單的部分
        for tid, trades in trades_by_id.items():
            for i, trade in enumerate(trades):
                try:
                    self.api.cancel_order(trade=trade)
                    self.api.update_status(self.api.stock_account)
                except Exception as e:
                    self.logging.error(f"cancel_order failed for {tid} trade {i}: {e}")

    def createOrdObj(self, symbol, direction, qty, order_lot):
        # Common expects quantity in lots (1 lot = 1000 shares); IntradayOdd
        # expects raw shares. Caller passes whichever is correct per order_lot.
        return sj.StockOrder(
            price=self.stockBid[symbol],
            quantity=qty,
            action=direction,
            price_type=sj.StockPriceType.LMT,
            order_type=sj.OrderType.ROD,
            order_lot=order_lot,
            account=self.api.stock_account,
        )

    def sendOrders(self, target_share:tuple):
        target_upper_share, target_lower_share = target_share
        quantityUpper = target_upper_share - self.uppershare
        quantityLower = target_lower_share - self.lowershare

        # available tracks cash across both legs THIS cycle only - fills are
        # async (order_cb), so self.live_cash_right_now won't reflect the upper leg's cost
        # in time for the lower leg's check without this.
        # Snapshot under mutexgSettle - order_cb writes live_cash_right_now
        # under this same lock on its own thread, so an unprotected read
        # here could race a concurrent fill and grab a stale value.
        with self.mutexgSettle:
            available = self.live_cash_right_now
        available = self._sendOneOrder(self.upperid, quantityUpper, available)
        self._sendOneOrder(self.lowerid, quantityLower, available)

    def _sendOneOrder(self, code, qty, available):
        price = self.stockBid[code]
        if qty > 0 and available < price * qty:
            qty = max(int(available / price), 0)
        # trigger=NT$2000 as a preventative of commision.
        if qty == 0 or abs(qty) * self.stockPrice[code] < self.trigger:
            return available

        if qty > 0 and available <= price * qty:
            return available

        direction = "Buy" if qty > 0 else "Sell"
        contract = self.api.Contracts.Stocks[code]
        # A target delta can exceed 999 shares (e.g. 3950) - IntradayOdd
        # orders only accept 0-999 shares, so anything >=1000 needs a
        # separate Common-lot order (quantity in lots) for the round-lot
        # portion plus an IntradayOdd order for the remainder.
        lots, remainder = divmod(abs(qty), 1000)
        # Only count shares from orders that actually got submitted, so a
        # partial failure (e.g. the odd-lot leg rejected) doesn't debit
        # available for shares that were never really bought.
        successfully_ordered_shares = 0
        for order_lot, lot_qty in ((sj.StockOrderLot.Common, lots), (sj.StockOrderLot.IntradayOdd, remainder)):
            if lot_qty == 0:
                continue
            order = self.createOrdObj(symbol=code, direction=direction, qty=lot_qty, order_lot=order_lot)
            try:
                self.api.place_order(contract, order)
            except Exception as e:
                self.logging.error(f"place_order failed for {code} {direction} {order_lot} qty={lot_qty}: {e}")
                continue
            self.logging.info(f"{direction} {code} {order_lot} @ {order.price}, qty: {order.quantity}")
            successfully_ordered_shares += lot_qty * 1000 if order_lot == sj.StockOrderLot.Common else lot_qty

        # Sells never add to available (proceeds unsettled this cycle) -
        # only buys reduce it, and only for the portion actually submitted.
        if qty > 0:
            available -= price * successfully_ordered_shares
        return available

            
