import pandas as pd
import shioaji as sj
import shioaji.order as stOrder
import shioaji.shioaji
import yfinance as yf

from typing import Dict, List, Optional
from shioaji.constant import OrderState, Action, StockOrderCond
from threading import Lock
import datetime

g_upperid = "0052"
g_lowerid = "00662"


class GridBot:
    g_settlement: int
    upperid: str
    lowerid: str

    # parameters = {
    #     "BiasUpperLimit": 2.0,
    #     "UpperLimitPosition": 0.4,
    #     "BiasLowerLimit": 0.899999,
    #     "LowerLimitPosition": 0.899999,
    #     "BiasPeriod": 6,
    # }
    parameters = {
        "BiasUpperLimit": 1.4,
        "UpperLimitPosition": 0.1,
        "BiasLowerLimit": 0.899999,
        "LowerLimitPosition": 0.899999,
        "BiasPeriod": 73,
    }
    # year: int
    # month: int
    # day: int
    # stockPrice: object
    # stockBid: object
    # stockAsk: object
    # upperprice: int
    # uppershare: int
    # lowerprice: int
    # lowershare: int
    # uppershareTarget: int
    # lowershareTarget: int
    # trigger: int  # 最低交易金額門檻,避免交易金額太小,錢被手續費低消吃光光
    # money: int
    # initmoney: int
    # contractUpper: any
    # contractLower: any
    # api: shioaji.Shioaji
    # mutexgSettle: any
    # mutexmsg: any
    # mutexstat: any
    # statlist: List
    # msglist: List

    def __init__(self, api: shioaji.Shioaji, logging):
        # keep track of MA calulated date
        self.year = self.month = self.day = 0
        self.trigger = 2000  # 最低交易金額門檻,避免交易金額太小,錢被手續費低消吃光光
        self.msglist = []
        self.statlist = []
        self.stockPrice = self.stockBid = self.stockAsk = {}
        self.initmoney = self.g_settlement = 0
        self.upperid = g_upperid
        self.lowerid = g_lowerid
        self.money = self.upperprice = self.uppershare = self.lowerprice = self.lowershare = 0
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
                print(f"stk deal: {stat.StockDeal.value}, msg:{msg}")
                # global g_settlement
                action = msg["action"]
                price = msg["price"]
                quantity = msg["quantity"]
                self.mutexgSettle.acquire()
                if action == "Buy":
                    self.g_settlement -= price * quantity
                elif action == "Sell":
                    self.g_settlement += price * quantity
                else:
                    pass
                self.money = self.initmoney + self.g_settlement
                self.mutexgSettle.release()
        self.mutexmsg.acquire()
        try:
            self.msglist.append(msg)
            self.logging.info(f"in order_cb, {msg}")
        except Exception as e:  # work on python 3.x
            self.logging.error("place_cb  Error Message A: " + str(e))
        self.mutexmsg.release()

        self.mutexstat.acquire()
        try:
            self.statlist.append(stat)
            self.logging.info(f" in ord_cb stlist: {self.statlist}")
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
            # 從Yfinance抓取日資料
            upper = yf.Ticker(self.upperid + ".tw")
            upper_hist = upper.history(period="3mo")

            # 計算均線
            period = self.parameters["BiasPeriod"]
            upper_close = upper_hist["Close"]
            # 1.如果是做 股票 / TWD 的網格那就只要股票價格取平均
            # 2.如果是做 股票A / 股票B 的相對價值網格那就需要
            # 先計算 股票A / 股票B 的收盤價，再取平均
            if self.lowerid != "Cash":
                lower = yf.Ticker(self.lowerid + ".tw")
                lower_hist = lower.history(period="3mo")
                lower_close = lower_hist["Close"]
                close = (upper_close / lower_close).dropna()
            else:
                close = upper_close.dropna()
            self.MA = close[-period:].sum() / period
            self.year = now.year
            self.month = now.month
            self.day = now.day
            s = "MA:" + str(self.MA)
            self.logging.info(s)

    #########################################
    # 7.2 抓取庫存部位大小y
    #########################################
    def getPositions(self):             
        positions = self.api.list_positions(self.api.stock_account, unit=sj.constant.Unit.Share)       
        self.lowershare = next((pos.quantity for pos in positions if pos.code == self.lowerid), 0)
        self.uppershare = next((pos.quantity for pos in positions if pos.code == self.upperid), 0)
        msg = f"positions: 00662-{self.lowershare}, 0052-{self.uppershare}"
        print(msg)

    # def getPositions(self):
    #     # self.api.update_status(self.api.stock_account)
    #     # print('list trade:', api.list_trades())
    #     portfolio = self.api.list_positions(self.api.stock_account, unit=sj.constant.Unit.Share)
    #     # df_positions = pd.DataFrame(portfolio)
    #     df_positions = pd.DataFrame(s.__dict__ for s in portfolio)
    #     ser_quantity = df_positions.loc[df_positions["code"] == self.upperid]["quantity"]
    #     if ser_quantity.size == 0:
    #         self.uppershare = 0
    #     else:
    #         print("ser_qty_size:", ser_quantity.size)
    #         self.uppershare = int(ser_quantity.iloc[0])

    #     if self.lowerid != "Cash":
    #         ser_quantity = df_positions.loc[df_positions["code"] == self.lowerid]["quantity"]
    #         if ser_quantity.size == 0:
    #             self.lowershare = 0
    #         else:
    #             self.lowershare = int(ser_quantity.iloc[0])
    #     msg = f"positions: 00662-{self.lowershare}, 0052-{self.uppershare}"
    #     print(msg)

    def calculateSharetarget(self, upperprice, lowerprice):
        # 計算目標部位百分比
        shareTarget = self.calculateGrid(upperprice, lowerprice)

        # move to order_cb
        # self.money=self.initmoney+self.g_settlement
        # no reset settlement after update money is required coz of using initmoney

        uppershare = self.uppershare
        lowershare = self.lowershare
        money = self.money

        # 計算機器人裡面有多少資產(現金+股票)
        capitalInBot = money + uppershare * upperprice + lowershare * lowerprice

        # 計算目標部位(股數)
        uppershareTarget = int(shareTarget * capitalInBot / upperprice)
        lowershareTarget = int((1.0 - shareTarget) * capitalInBot / lowerprice)

        # 紀錄目標部位(股數)
        self.uppershareTarget = uppershareTarget
        self.lowershareTarget = lowershareTarget
        # self.upperprice=upperprice
        # self.lowerprice=lowerprice

        self.logging.info("uppershareTarget:" + str(uppershareTarget))
        self.logging.info("lowershareTarget:" + str(lowershareTarget))
        self.logging.info("upperprice:" + str(upperprice))
        self.logging.info("lowerprice:" + str(lowerprice))

    def calculateGrid(self, upperprice, lowerprice):
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
        # 計算目標部位百分比
        BiasUpperLimit = self.parameters["BiasUpperLimit"]
        UpperLimitPosition = self.parameters["UpperLimitPosition"]
        BiasLowerLimit = self.parameters["BiasLowerLimit"]
        LowerLimitPosition = self.parameters["LowerLimitPosition"]
        BiasPeriod = self.parameters["BiasPeriod"]
        # compute 乖離 rate
        Bias = (upperprice / lowerprice) / MA
        shareTarget = (Bias - BiasLowerLimit) / (BiasUpperLimit - BiasLowerLimit)
        shareTarget = shareTarget * (UpperLimitPosition - LowerLimitPosition) + LowerLimitPosition
        shareTarget = max(shareTarget, UpperLimitPosition)
        shareTarget = min(shareTarget, LowerLimitPosition)
        print("0052 shareTaget:", shareTarget)
        return shareTarget

    #########################################
    # 7.3. 實際掛單
    ###########################################

    def updateOrder(self):
        try:
            #################################
            # 0.更新日均線資料
            #################################
            self.UpdateMA()
            #################################
            # 1.刪除掛單
            ############################
            self.cancelOrders()
            #################################
            # 2.更新庫存
            ############################
            self.getPositions()
            ####################################
            # 3.更新目標部位
            ##############################
            # it looks like current price
            self.calculateSharetarget(
                upperprice=self.stockPrice[g_upperid],
                lowerprice=self.stockPrice[g_lowerid],
            )
            #######################################
            # 4.掛單
            ############################################
            self.sendOrders()
        except Exception as e:  # work on python 3.x
            self.logging.info(" updateOrder Error Message: " + str(e))

    def cancelOrders(self):
        self.api.update_status(self.api.stock_account)
        # 列出所有的訂單
        tradelist = self.api.list_trades()
        tradeUpper = []
        tradeLower = []
        for i in range(0, len(tradelist), 1):
            thistrade = tradelist[i]
            thisstatus = thistrade.status.status
            # 單子的狀態太多種,先列出來
            isCancelled = thisstatus == stOrder.Status.Cancelled
            isFailed = thisstatus == stOrder.Status.Failed
            isFilled = thisstatus == stOrder.Status.Filled
            isInactive = thisstatus == stOrder.Status.Inactive
            isPartFilled = thisstatus == stOrder.Status.PartFilled
            isPendingSubmit = thisstatus == stOrder.Status.PendingSubmit
            isPreSubmitted = thisstatus == stOrder.Status.PreSubmitted
            isSubmitted = thisstatus == stOrder.Status.Submitted

            # 把交易股票種類跟交易機器人一樣的有效訂單取消
            cond1 = not (isCancelled or isFailed or isFilled)
            cond2 = thistrade.contract.code == self.upperid
            cond3 = thistrade.contract.code == self.lowerid
            cond4 = self.lowerid != "Cash"
            if cond1 and cond2:
                tradeUpper.append(thistrade)
            if cond1 and cond3 and cond4:
                tradeLower.append(thistrade)

        # 實際取消訂單的部分
        for i in range(0, len(tradeUpper), 1):
            self.api.cancel_order(trade=tradeUpper[i])
            self.api.update_status(self.api.stock_account)
            s = f"{tradeUpper[i].status.status}/{tradeUpper[i].status.cancel_quantity}"
            self.logging.info(s)
        if self.lowerid != "Cash":
            for i in range(0, len(tradeLower), 1):
                self.api.cancel_order(trade=tradeLower[i])
                self.api.update_status(self.api.stock_account)
                s = f"{tradeLower[i].status.status}/{tradeLower[i].status.cancel_quantity}"
                self.logging.info(s)

    def createOrdObj(self, symbol, direction, qty):
        return self.api.Order(
            price=self.stockBid[symbol],
            quantity=qty,
            action=direction,
            price_type=sj.constant.StockPriceType.LMT,
            order_type=sj.constant.OrderType.ROD,
            order_lot=sj.constant.StockOrderLot.IntradayOdd,
            account=self.api.stock_account,
        )

    def sendOrders(self):
        # 計算要掛多少股
        quantityUpper = self.uppershareTarget - self.uppershare
        quantityLower = self.lowershareTarget - self.lowershare
        quantityUpper = min(quantityUpper, 999)
        quantityUpper = max(quantityUpper, -999)
        quantityLower = min(quantityLower, 999)
        quantityLower = max(quantityLower, -999)
        # 確保掛單的量不會把交割款用完
        code = self.upperid
        money = self.money
        if quantityUpper > 0:  # buy
            cost = self.stockBid[code] * quantityUpper
            if money < cost:
                quantityUpper = max(int(money / self.stockBid[code]), 0)

        # quantityUpperValid = abs(quantityUpper) > 0
        # 這邊做掛單,前面做了掛單量==0股的特殊檢查
        if quantityUpper != 0:
            # 在交易金額大於trigger(NT$2000)的時候掛單, coz of commision cost.
            if abs(quantityUpper) * self.stockPrice[code] >= self.trigger:
                cost = self.stockBid[code] * quantityUpper
                contract = self.api.Contracts.Stocks[code]
                # 掛買單的話,要把交割款扣掉買單的金額
                # 避免後面掛分母的單的時候交割款不夠
                if quantityUpper > 0:
                    if money > cost:
                        money = money - cost  # local money int
                        self.logging.info(f"left money: {money}")

                        order = self.createOrdObj(
                            symbol=self.upperid,
                            direction=sj.constant.Action.Buy,
                            qty=quantityUpper,
                        )
                        trade = self.api.place_order(contract, order)
                        self.logging.info(f"{direction} {code} @ {order.price}, qty: {order.quantity}")
                else:
                    order = self.createOrdObj(
                        symbol=self.upperid,
                        direction=sj.constant.Action.Sell,
                        qty=abs(quantityUpper),
                    )
                    trade = self.api.place_order(contract, order)
                    self.logging.info(f"{direction} {code} @ {order.price}, qty: {order.quantity}")

        # 這邊開始掛分母的單
        # 首先確保掛單的量不會把交割款用完
        code = self.lowerid
        if quantityLower > 0:
            cost = self.stockBid[code] * quantityLower
            if money < cost:
                quantityLower = max(int(money / self.stockBid[code]), 0)

        # quantityLowerValid = abs(quantityLower) > 0
        # 這邊做掛單,前面做了掛單量==0股的特殊檢查
        if self.lowerid != "Cash" and quantityLower != 0:
            # 在交易金額大於trigger的時候掛單
            if abs(quantityLower) * self.stockPrice[code] >= self.trigger:
                contract = self.api.Contracts.Stocks[code]
                cost = self.stockBid[code] * quantityLower
                direction = "Buy" if quantityLower > 0 else "Sell"
                order = self.createOrdObj(
                    symbol=self.lowerid,
                    direction=direction,
                    qty=abs(quantityLower),
                )

                trade = self.api.place_order(contract, order)
                self.logging.info(f"{direction} {code} @ {order.price}, qty: {order.quantity}")

                # if quantityLower > 0:
                #     order = self.createOrdObj(
                #         symbol=self.lowerid,
                #         direction=shioaji.constant.Action.Buy,
                #         qty=quantityLower,
                #     )

                #     trade = self.api.place_order(contract, order)
                #     s = str(datetime.datetime.now())
                #     s = f"{s} buy {contract}@ {order.price}, qty: {order.quantity}"
                #     self.logging.info(s)
                # else:
                #     order = self.createOrdObj(
                #         symbol=self.lowerid,
                #         direction=shioaji.constant.Action.Sell,
                #         qty=abs(quantityLower),
                #     )
                #     trade = self.api.place_order(contract, order)
                #     s = str(datetime.datetime.now())
                #     s = f"{s} sell {contract}@ {order.price}, qty: {order.quantity}"
                #     self.logging.info(s)
