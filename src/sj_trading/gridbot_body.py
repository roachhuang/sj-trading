#########################################
# Ch7 網格交易機器人
###########################################
import time
import shioaji as sj
import logging
import datetime
import time
from threading import Lock
# 處理ticks即時資料更新的部分
from shioaji import BidAskSTKv1, Exchange, TickSTKv1

####################################################
import os
from dotenv import load_dotenv
import sj_trading.misc as misc
import sj_trading.gridbot as gridbot

load_dotenv()

g_upperid = '0052'
g_lowerid = '00662'
ENABLE_PREMARKET = False
ans = ''

def GridbotBody(api):
    # gridBody runs from here
    # 成交價
    snaprice = {}
    snaprice[g_upperid] = api.snapshots([api.Contracts.Stocks[g_upperid]])
    snaprice[g_lowerid] = api.snapshots([api.Contracts.Stocks[g_lowerid]])
    stockPrice = {g_upperid: snaprice[g_upperid][0]['close'],
                  g_lowerid: snaprice[g_lowerid][0]['close']}

    # 最高買價
    stockBid = {g_upperid: snaprice[g_upperid][0]['close'],
                g_lowerid: snaprice[g_lowerid][0]['close']}
    # 最低賣價
    stockAsk = {g_upperid: snaprice[g_upperid][0]['close'],
                g_lowerid: snaprice[g_lowerid][0]['close']}
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
        bot1.initmoney = misc.read_json('money.json')
    except Exception as e:
        logging.error(f"read_json failed, defaulting initmoney to 0: {e}")
        bot1.initmoney = 0
    # bot1.live_cash_right_now starts at 0 in GridBot.__init__ and is otherwise only set
    # inside order_cb on a fill; without this line, sendOrders sees no cash
    # (all buys clipped to 0) and a no-fill day persists 0 to money.json,
    # wiping the carried-over balance.
    # 昨天剩下的 cash =今天可用的 cash
    bot1.live_cash_right_now = bot1.initmoney
    # capital @ this point in time, not necessary today's mkt open prices coz github's delaylaunch 
    totalcapital = bot1.initmoney + \
        stockPrice[g_upperid]*bot1.uppershare + \
        stockPrice[g_lowerid]*bot1.lowershare
    # 更新Trigger大小,在資產很多的時候固定2000會有點少
    bot1.trigger = max(2000, totalcapital*0.005)

    def log_daily_pnl():
        end_capital = bot1.live_cash_right_now + \
            stockPrice[g_upperid]*bot1.uppershare + \
            stockPrice[g_lowerid]*bot1.lowershare
        pnl = end_capital - totalcapital
        pnl_pct = pnl / totalcapital * 100 if totalcapital else 0
        logging.info(
            f"daily P&L: start_capital={totalcapital:.2f}, end_capital={end_capital:.2f}, "
            f"pnl={pnl:.2f} ({pnl_pct:.2f}%)"
        )
    logging.info("starting cash for today's run: {:.2f}".format(bot1.initmoney))
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
    mutexDict = {g_upperid: Lock(), g_lowerid: Lock()}
    mutexBidAskDict = {g_upperid: Lock(), g_lowerid: Lock()}

    # 告訴系統要訂閱
    # 1.ticks資料(用來看成交價)
    # 2.買賣價資料
    contract_Upper = api.Contracts.Stocks[g_upperid]
    contract_Lower = api.Contracts.Stocks[g_lowerid]
    api.subscribe(contract_Lower, quote_type=sj.QuoteType.Tick, version=sj.QuoteVersion.v1)
    api.subscribe(contract_Upper, quote_type=sj.QuoteType.Tick, version=sj.QuoteVersion.v1)
    api.subscribe(contract_Lower, quote_type=sj.QuoteType.BidAsk, version=sj.QuoteVersion.v1)
    api.subscribe(contract_Upper, quote_type=sj.QuoteType.BidAsk, version=sj.QuoteVersion.v1)
    
    @api.on_tick_stk_v1()
    def STKtick_callback(exchange: Exchange, tick: TickSTKv1):
        code = tick['code']
        mutexDict[code].acquire()
        stockPrice[code] = float(tick['close'])
        mutexDict[code].release()
    api.quote.set_on_tick_stk_v1_callback(STKtick_callback)

    # 處理bidask即時資料更新的部分
    @api.on_bidask_stk_v1()
    def STK_BidAsk_callback(exchange: Exchange, bidask: BidAskSTKv1):
        code = bidask['code']
        mutexBidAskDict[code].acquire()
        bidlist = [float(i) for i in bidask['bid_price']]
        asklist = [float(i) for i in bidask['ask_price']]
        stockBid[code] = bidlist[0]
        stockAsk[code] = asklist[0]
        mutexBidAskDict[code].release()
    api.quote.set_on_bidask_stk_v1_callback(STK_BidAsk_callback)

    @api.quote.on_event
    def event_callback(resp_code: int, event_code: int, info: str, event: str):
        # t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")        
        logging.info(f'Event code: {event_code} | Event: {event}')
        # print(f'Event code: {event_code} | Event: {event}')
    api.quote.set_event_callback(event_callback)

    # 用來更新買賣訊號和下單的迴圈
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
            # cancel all orders 10m before mkt close
            if (hour == 13 and minute > 20):
                try:
                    bot1.cancelOrders()
                except Exception as e:
                    logging.error('jobs_per1min  Error Message A: ' + str(e))
                continue
            # it is allowed to place next-day orders after 3pm.
            if (hour >= 14 and hour <= 15):
                log_daily_pnl()
                # live available cash after execution
                try:
                    misc.write_json("money.json", bot1.live_cash_right_now)
                except Exception as e:
                    logging.error(f"write_json failed at normal exit: {e}")
                break
            # Two unrelated guards share this one flag:
            # - hour<9 = premarket gate (pre-open call auction, before 9:00)
            # - hour>13 = NOT premarket-related; hour 14/15 already broke out
            #   above, so this only fires for hour>=16 - a safety net for
            #   off-schedule runs (e.g. manual trigger at the wrong hour, or
            #   TZ misconfig) that never hit the normal 14-15 exit, so it
            #   doesn't blind-trade on stale prices late at night.
            if (not ENABLE_PREMARKET):
                if (hour < 9 or (hour > 13)):
                    continue

            # 處理成交價不在買賣價中間的狀況
            # Acquires the lock associated with the resource identified by g_upperid in the mutexDict dictionary.
            # This lock is used to synchronize access to some shared resource related to g_upperid.
            mutexDict[g_upperid].acquire()
            mutexDict[g_lowerid].acquire()
            mutexBidAskDict[g_upperid].acquire()
            mutexBidAskDict[g_lowerid].acquire()

            if (stockPrice[g_upperid] > stockAsk[g_upperid] or stockPrice[g_upperid] < stockBid[g_upperid]):
                stockPrice[g_upperid] = (
                    stockAsk[g_upperid]+stockBid[g_upperid])/2
            if (stockPrice[g_lowerid] > stockAsk[g_lowerid] or stockPrice[g_lowerid] < stockBid[g_lowerid]):
                stockPrice[g_lowerid] = (
                    stockAsk[g_lowerid]+stockBid[g_lowerid])/2

            # save prices to gridbot
            bot1.stockPrice[g_upperid] = stockPrice[g_upperid]
            bot1.stockPrice[g_lowerid] = stockPrice[g_lowerid]
            bot1.stockBid[g_upperid] = stockBid[g_upperid]
            bot1.stockBid[g_lowerid] = stockBid[g_lowerid]
            bot1.stockAsk[g_upperid] = stockAsk[g_upperid]
            bot1.stockAsk[g_lowerid] = stockAsk[g_lowerid]
            mutexDict[g_lowerid].release()
            mutexDict[g_upperid].release()
            mutexBidAskDict[g_lowerid].release()
            mutexBidAskDict[g_upperid].release()
            
            # 更新買賣單, we can place order anytime before 2pm
            bot1.updateOrder()

    except KeyboardInterrupt:
        logging.warning("\n Ctrl-C detected. Exiting gracefully...")
        try:
            bot1.cancelOrders()
        except Exception as e:
            logging.error(f"cancelOrders failed on KeyboardInterrupt: {e}")
        log_daily_pnl()
        try:
            misc.write_json("money.json", bot1.live_cash_right_now)
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

# start here
def main():
    production = os.environ.get("SJ_PRODUCTION", "false").lower() == "true"
    api = sj.Shioaji(simulation=not production)
    print(sj.__version__)
    api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SEC_KEY"],
        fetch_contract=True,
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
    GridbotBody(api)

    # GridbotBody returns once its internal loop reaches ~14:00-15:00.
    # Log out and exit here so a scheduled run (e.g. triggered once per
    # trading day) terminates instead of waiting for a 16:00 reboot or
    # looping until Friday.
    try:
        api.logout()
    except Exception as e:
        logging.error(f"failed to call api.logout: {e}")

if __name__ == '__main__':
    main()
