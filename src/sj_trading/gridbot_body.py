#########################################
# Ch7 網格交易機器人
###########################################
import time
import shioaji as sj
import logging
import datetime
import time
import sys
from threading import Lock
# 處理ticks即時資料更新的部分
from shioaji import BidAskSTKv1, TickSTKv1

####################################################
import os
from dotenv import load_dotenv
import sj_trading.misc as misc
import sj_trading.gridbot as gridbot

load_dotenv()

g_upperid = '0052'
g_lowerid = '00662'
TICKERS = (g_upperid, g_lowerid)
ENABLE_PREMARKET = False
ans = ''


def evaluate_daily_drift(today_equity: float, prior_equity: float | None,
                          stats: dict | None) -> bool | None:
    """Pure decision core for the end-of-day P&L drift check - no API calls,
    so it's directly testable. None means 'skip, don't fail the job': a
    missing/zero prior-day equity baseline must never look like drift.
    Compares day-over-day mark-to-market equity change (matching the
    backtest's eq.pct_change() units) rather than broker P&L flows, which
    mix a daily flow (realized) with a cumulative level (unrealized) and
    would false-trigger persistently once a held position has moved more
    than the threshold cumulatively."""
    if not prior_equity:
        return None
    pnl_pct = (today_equity - prior_equity) / prior_equity
    return misc.is_pnl_outlier(pnl_pct, stats)


def GridbotBody(api):
    # gridBody runs from here
    contracts = {tid: api.contracts.get(tid) for tid in TICKERS}
    # 成交價
    snaprice = {tid: api.snapshots([contracts[tid]]) for tid in TICKERS}
    stockPrice = {tid: snaprice[tid][0]['close'] for tid in TICKERS}
    # 最高買價
    stockBid = {tid: snaprice[tid][0]['close'] for tid in TICKERS}
    # 最低賣價
    stockAsk = {tid: snaprice[tid][0]['close'] for tid in TICKERS}
    # # 最高買價
    # stockBid = {g_upperid: snaprice[g_upperid][0]['buy_price'],
    #             g_lowerid: snaprice[g_lowerid][0]['buy_price']}
    # # 最低賣價
    # stockAsk = {g_upperid: snaprice[g_upperid][0]['sell_price'],
    #             g_lowerid: snaprice[g_lowerid][0]['sell_price']}

    # 創建交易機器人物件
    # logging.basicConfig(filename='gridbotlog.log', level=logging.DEBUG)
    logging.basicConfig(
        filename="gridbot.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    # 把資料寫到硬碟和從硬碟讀取資料用的函數
    bot1 = gridbot.GridBot(api, logging)
    # 更新交易機器人裡的股票數量
    bot1.getPositions()

    try:
        bot1.start_cash = misc.read_json('money.json')
    except Exception as e:
        logging.error(f"read_json failed: {e}")

    try:
        backtest_stats = misc.read_json('backtest_stats.json')
    except Exception as e:
        logging.warning(f"backtest_stats.json unavailable, skipping drift check: {e}")
        backtest_stats = None

    try:
        prior_equity = misc.read_json('equity_snapshot.json')
    except Exception as e:
        logging.warning(f"equity_snapshot.json unavailable, skipping drift check: {e}")
        prior_equity = None
    # order_cb recomputes live_cash_right_now = start_cash + g_settlement on every
    # fill, so start_cash must hold the day's fixed opening balance (not itself
    # be updated) - live_cash_right_now is the one that moves.
    # 昨天剩下的 cash =今天可用的 cash
    bot1.live_cash_right_now = bot1.start_cash
    # Seed the bot's prices from the opening snapshot so an explicit close can
    # run before the quote subscriptions and normal rebalance loop start.
    bot1.stockPrice.update(stockPrice)
    bot1.stockBid.update(stockBid)
    bot1.stockAsk.update(stockAsk)

    take_profit = os.environ.get("SJ_TAKE_PROFIT", "false").lower() == "true"
    if take_profit:
        logging.warning("SJ_TAKE_PROFIT=true: closing 0052/00662 and stopping")
        close_ok = bot1.close_positions()
        positions_flat = close_ok and bot1.uppershare == 0 and bot1.lowershare == 0
        deadline = time.monotonic() + 30
        while close_ok and not positions_flat and time.monotonic() < deadline:
            time.sleep(1)
            if bot1.getPositions():
                positions_flat = bot1.uppershare == 0 and bot1.lowershare == 0

        if not positions_flat:
            logging.error(
                "take-profit incomplete: remaining shares 0052=%s 00662=%s",
                bot1.uppershare,
                bot1.lowershare,
            )
            bot1.cancelOrders()
        else:
            logging.info("take-profit complete: both pair positions are flat")

        # order_cb updates live_cash_right_now for every fill; persist the
        # resulting ledger even when only part of the close filled.
        try:
            misc.write_json("money.json", bot1.live_cash_right_now)
        except Exception as e:
            logging.error(f"take-profit money.json write failed: {e}")
        return None

    # reads bot1.uppershare/lowershare fresh on each call - they change as
    # the bot trades through the day, so this must not be memoized.
    def stock_value():
        shares = {g_upperid: bot1.uppershare, g_lowerid: bot1.lowershare}
        return sum(stockPrice[tid] * shares[tid] for tid in TICKERS)

    # capital @ this point in time, not necessary today's mkt open prices coz github's delaylaunch
    totalcapital = bot1.live_cash_right_now + stock_value()
    # 更新Trigger大小,在資產很多的時候固定2000會有點少
    bot1.trigger = max(2000, totalcapital*0.005)

    def log_daily_pnl(today_equity):
        today = datetime.date.today().isoformat()
        try:
            realized_list = api.list_profit_loss(
                api.stock_account, begin_date=today, end_date=today, unit=sj.Unit.Share
            )
            realized = sum(p.pnl for p in realized_list if p.code in TICKERS)
        except Exception as e:
            logging.error(f"list_profit_loss failed: {e}")
            realized = 0

        try:
            positions = api.list_positions(api.stock_account, unit=sj.Unit.Share)
            unrealized = sum(p.pnl for p in positions if p.code in TICKERS)
        except Exception as e:
            logging.error(f"list_positions failed: {e}")
            unrealized = 0

        logging.info(
            f"daily P&L (TICKERS): realized={realized:.2f}, unrealized={unrealized:.2f}, "
            f"total={realized + unrealized:.2f}"
        )

        try:
            drift = evaluate_daily_drift(today_equity, prior_equity, backtest_stats)
            if drift:
                pnl_pct = (today_equity - prior_equity) / prior_equity
                mean, std = backtest_stats["mean_daily_return"], backtest_stats["std_daily_return"]
                z = (pnl_pct - mean) / std
                logging.error(
                    f"drift detected: pnl_pct={pnl_pct:.4f} mean={mean:.4f} std={std:.4f} z={z:.2f}"
                )
        except Exception as e:
            logging.error(f"drift check failed, skipping: {e}")
            drift = None
        return drift

    logging.info("starting cash for today's run: {:.2f}".format(bot1.live_cash_right_now))
    logging.info("uppershare value: {:.2f}".format(stockPrice[g_upperid]*bot1.uppershare))
    logging.info("lowershare value: {:.2f}".format(stockPrice[g_lowerid]*bot1.lowershare))
    logging.info("totalcapital: {:.2f}".format(totalcapital))
    # 決定要不要新增更多資金進交易機器人裡, ans won't be '' after 2nd round.
    # here declare ans as global is for updating the global value of ans
    # global ans
    # if (ans == ''):
    #     ans = input("perform withdraw or deposit(y/n):\n")
    #     if (ans == 'y'):
    #         amount = input(
    #             "withdraw or deposit amount(>0:deposit,<0:withdraw):\n")
    #         bot1.initmoney = bot1.initmoney+int(amount)
    # bot1.live_cash_right_now = bot1.initmoney

    # 用來處理多線程的變數,在更新價格和訂單成交回報時會用到
    # It contains Lock objects associated with identifiers g_upperid and g_lowerid. These locks are used to synchronize
    # access to the dictionaries stockPrice, stockBid, and stockAsk, which are accessed concurrently by multiple threads.
    mutexDict = {tid: Lock() for tid in TICKERS}
    mutexBidAskDict = {tid: Lock() for tid in TICKERS}

    # 告訴系統要訂閱
    # 1.ticks資料(用來看成交價)
    # 2.買賣價資料
    for tid in TICKERS:
        api.subscribe(contracts[tid], quote_type=sj.QuoteType.Tick, version=sj.QuoteVersion.v1)
        api.subscribe(contracts[tid], quote_type=sj.QuoteType.BidAsk, version=sj.QuoteVersion.v1)

    @api.on_tick_stk_v1()
    def STKtick_callback(tick: TickSTKv1):
        code = tick.code
        mutexDict[code].acquire()
        stockPrice[code] = float(tick.close)
        mutexDict[code].release()

    # 處理bidask即時資料更新的部分
    @api.on_bidask_stk_v1()
    def STK_BidAsk_callback(bidask: BidAskSTKv1):
        code = bidask.code
        mutexBidAskDict[code].acquire()
        bidlist = [float(i) for i in bidask.bid_price]
        asklist = [float(i) for i in bidask.ask_price]
        stockBid[code] = bidlist[0]
        stockAsk[code] = asklist[0]
        mutexBidAskDict[code].release()

    @api.on_event()
    def event_callback(resp_code: int, event_code: int, info: str, event: str):
        # t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f'Event code: {event_code} | Event: {event}')
        # print(f'Event code: {event_code} | Event: {event}')

    # 用來更新買賣訊號和下單的迴圈
    drift_result = None
    try:
        while (1):
            current_time = time.time()
            # 60secs
            cooldown = 60
            # sleep to n seconds
            til_second = 20
            time_to_sleep = til_second + cooldown - (current_time % cooldown)
            time.sleep(time_to_sleep)

            now = datetime.datetime.now()
            hour = now.hour
            minute = now.minute
            # second = now.second
            # modify/send order
            # 1.every 3 minutes
            # 2.between 15 second to 45 second
            if (minute % 3 != 0):
                continue
            # Safety net: still running past the normal close-exit window below
            # (late/manual start, crash-restart, clock skew). Market's already
            # closed at 13:30 by this point, so there's nothing left to cancel
            # via the exchange - just persist and stop instead of looping
            # forever on `continue` until the CI job timeout kills it.
            if (hour >= 14):
                today_equity = bot1.live_cash_right_now + stock_value()
                drift_result = log_daily_pnl(today_equity)
                try:
                    misc.write_json("money.json", bot1.live_cash_right_now)
                    misc.write_json("equity_snapshot.json", today_equity)
                except Exception as e:
                    logging.error('jobs_per1min  Error Message B: ' + str(e))
                break

            # cancel all orders 10m before mkt close
            if (hour == 13 and minute > 20):
                try:
                    bot1.cancelOrders()
                    today_equity = bot1.live_cash_right_now + stock_value()
                    drift_result = log_daily_pnl(today_equity)
                    misc.write_json("money.json", bot1.live_cash_right_now)
                    misc.write_json("equity_snapshot.json", today_equity)
                except Exception as e:
                    logging.error('jobs_per1min  Error Message A: ' + str(e))
                break

            # premarket gate: skip trading before the 9:00 open (pre-open call
            # auction) unless explicitly enabled
            if (not ENABLE_PREMARKET):
                if (hour < 9):
                    continue

            # 處理成交價不在買賣價中間的狀況
            # Acquires the locks associated with each ticker in mutexDict/mutexBidAskDict,
            # used to synchronize access to stockPrice/stockBid/stockAsk across threads.
            for tid in TICKERS:
                mutexDict[tid].acquire()
                mutexBidAskDict[tid].acquire()

            for tid in TICKERS:
                if stockPrice[tid] > stockAsk[tid] or stockPrice[tid] < stockBid[tid]:
                    stockPrice[tid] = (stockAsk[tid] + stockBid[tid]) / 2

            # save prices to gridbot
            for tid in TICKERS:
                bot1.stockPrice[tid] = stockPrice[tid]
                bot1.stockBid[tid] = stockBid[tid]
                bot1.stockAsk[tid] = stockAsk[tid]
            for tid in TICKERS:
                mutexDict[tid].release()
                mutexBidAskDict[tid].release()
            
            # 更新買賣單, we can place order anytime before 2pm
            bot1.updateOrder()

    except KeyboardInterrupt:
        logging.warning("\n Ctrl-C detected. Exiting gracefully...")
        try:
            bot1.cancelOrders()
        except Exception as e:
            logging.error(f"cancelOrders failed on KeyboardInterrupt: {e}")
        today_equity = bot1.live_cash_right_now + stock_value()
        drift_result = log_daily_pnl(today_equity)
        try:
            misc.write_json("money.json", bot1.live_cash_right_now)
            misc.write_json("equity_snapshot.json", today_equity)
        except Exception as e:
            logging.error(f"write_json failed on KeyboardInterrupt: {e}")
        try:
            api.logout()
        except Exception as e:
            print("An error occurred:", e)
        finally:
            print(
                "This code is always executed, regardless of whether an exception occurred or not")
        exit

    return drift_result

# start here
def main():
    production = os.environ.get("SJ_PRODUCTION", "false").lower() == "true"
    api = sj.Shioaji(simulation=not production)
    print(sj.__version__)
    api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SEC_KEY"],
    )
    if production:
        SJ_CA_PATH = "Sinopac.pfx"
        res = api.activate_ca(
            ca_path=SJ_CA_PATH,
            ca_passwd=os.environ["SJ_CA_PASSWD"],
            person_id=os.environ["SJ_PERSON_ID"]
        )
        if not res:
            raise RuntimeError("CA activation failed")
        print(api.usage())

    # starting point of the code running
    drift_result = GridbotBody(api)

    # GridbotBody returns once its internal loop reaches ~14:00-15:00.
    # Log out and exit here so a scheduled run (e.g. triggered once per
    # trading day) terminates instead of waiting for a 16:00 reboot or
    # looping until Friday.
    try:
        api.logout()
    except Exception as e:
        logging.error(f"failed to call api.logout: {e}")

    if drift_result:
        sys.exit(1)

if __name__ == '__main__':
    main()
