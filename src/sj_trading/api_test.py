import shioaji as sj
import os
from dotenv import load_dotenv
print(sj.__version__)

api = 0

api = sj.Shioaji(simulation=True)
api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SEC_KEY"],
        fetch_contract=False
    )
api.activate_ca(
        ca_path=os.environ["SJ_CA_PATH"],
        ca_passwd=os.environ["SJ_CA_PASSWD"],
    )

print("login and activate ca success")

# 商品檔 - 請修改此處
contract = api.Contracts.Stocks.TSE["2890"]

# 證券委託單 - 請修改此處
order = api.Order(
    price=22,  # 價格
    quantity=1,  # 數量
    action=sj.constant.Action.Buy,  # 買賣別
    price_type=sj.constant.StockPriceType.LMT,  # 委託價格類別
    order_type=sj.constant.OrderType.ROD,  # 委託條件
    account=api.stock_account,  # 下單帳號
)

# 下單
trade = api.place_order(contract, order)


# CA='c:\ekey\\551\\'+person_id+'\\S\\Sinopac.pfx'
# CA = '/home/roach/ekey/551/YOUR_PERSON_ID/S/Sinopac.pfx'
# result = api.activate_ca(
#     ca_path=CA,
#     ca_passwd=CA_passwd,
#     person_id=person_id,
# )



