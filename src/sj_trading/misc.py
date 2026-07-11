from datetime import date, datetime, timedelta
import pickle


def pickle_dump(filename, obj):
    try:
        with open(filename, "wb") as handle:
            pickle.dump(obj, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        print(f"An error occurred: {e}")

def pickle_read(filename):
    """Reads capital record from a file using pickle.

    Args:
        filename (str): The name of the file containing the pickled data.

    Returns:
        object: The unpickled data (OrderRecord.money in this case).

    Raises:
        pickle.UnpicklingError: If an error occurs during unpickling.
        IOError: If an error occurs while reading the file.
    """
    try:
        with open(filename, "rb") as handle:
            return pickle.load(handle)
    except pickle.UnpicklingError as e:
        raise ValueError(
            f"Error unpickling capital record: {e}"
        ) from e  # Chain the original exception
    except IOError as e:
        raise FileNotFoundError(
            f"Error reading capital record file: {e}"
        ) from e  # Chain the original exception


def calculate_profit(buy_price: float, sell_price: float, quantity: int, tax_rate:float) -> int:
    # Transaction Costs is the key. break even: 0.208% after discount
    discount = 0.38
    service_fee_rate = 0.001425 * discount
    # tax_rate = 1/1000 if etf else 3/1000

    """Calculates the net profit from a stock transaction.	
	Args:
		buy_price (float): The purchase price per share.
		sell_price (float): The selling price per share.
		quantity (int): The number of shares.
	Returns:
		float: The net profit from the transaction.
	"""
    total_buy_cost = quantity * buy_price * (1 + service_fee_rate)
    total_sell_amt = quantity * sell_price
    total_sell_fees = total_sell_amt * (service_fee_rate + tax_rate)
    net_profit = total_sell_amt - total_buy_cost - total_sell_fees
    return int(net_profit)
    # return round(net_profit, 2)


def get_tick_unit(stock_price: float) -> float:
    """
    Returns the fluctuation unit (TICK) for the given stock price.

    Args:
            stock_price (float): The current stock price.

    Returns:
            float: The TICK value for the stock price.
    """
    if stock_price <= 10:
        return 0.01
    elif stock_price <= 50:
        return 0.05
    elif stock_price <= 100:
        return 0.1
    elif stock_price <= 500:
        return 0.5
    elif stock_price <= 1000:
        return 1.0
    else:
        return 5.0


def normalize(df):
    # return (df - df.min()) / (df.max() - df.min())
    # return (df - df.mean()) / df.std()
    return df / df.iloc[0, :]


def get_user_confirmation(question: str) -> bool:
    while True:
        user_input = input(f"{question} (y/n)?").lower()
        if user_input == "y":
            return True
        elif user_input == "n":
            return False
        else:
            print("Invalid input. Please enter 'y' or 'n'.")


#######################################
# 用datetime取得兩年前/一年前/昨天的日期
#######################################
def get_today() -> datetime.date:
    return datetime.today().date()
    # return datetime.date.today()  if just import datetime


def sub_N_Days(days: int) -> datetime.date:
    return (datetime.today() - timedelta(days)).date()


def add_N_Days(days: int, date=None) -> datetime.date:
    if date is None:
        date = datetime.today()
    return date + timedelta(days)
