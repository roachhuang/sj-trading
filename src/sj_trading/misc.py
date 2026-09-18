from datetime import date, datetime, timedelta
import json
import logging
import math

import pandas as pd

def write_json(filename, obj):
    try:
        with open(filename, "w") as handle:
            json.dump(obj, handle)
    except Exception as e:
        logging.error(f"write_json failed for {filename}: {e}")

def read_json(filename):
    """Reads capital record from a JSON file.

    Args:
        filename (str): The name of the file containing the JSON data.

    Returns:
        object: The decoded data (OrderRecord.money in this case).

    Raises:
        ValueError: If an error occurs while parsing the JSON.
        FileNotFoundError: If an error occurs while reading the file.
    """
    try:
        with open(filename) as handle:
            return json.load(handle)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Error parsing capital record: {e}"
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


def blended_price(upper_close: pd.Series, lower_close: pd.Series) -> pd.Series:
    """50/50 blended price index from two tickers' closes, built by
    compounding each day's weighted daily return (0.5*upper_return +
    0.5*lower_return) rather than normalizing both series to a single fixed
    anchor date. Anchor-date normalization (dividing by each series'
    iloc[0]) makes any rolling-window return computed off the result depend
    on how much history happened to be loaded - since 0052/00662 have grown
    at different total rates, backtest.py's full-history anchor and
    gridbot_body.py's 2-year live fetch anchor would bake in different
    50/50 *dollar* weights and could label the identical calendar date
    differently even given identical underlying prices. Compounding daily
    returns instead makes any window-return computed off this series depend
    only on the prices within that window, regardless of series start date
    - required for label_regimes() to agree between backtest and live.
    Used as a market-direction proxy (regime labeling) distinct from
    GridBot's upper/lower bias ratio, which drives allocation between the
    two ETFs rather than indicating overall market direction."""
    aligned = pd.concat([upper_close, lower_close], axis=1, join="inner")
    u_ret = aligned.iloc[:, 0].pct_change()
    l_ret = aligned.iloc[:, 1].pct_change()
    blended_ret = (0.5 * u_ret + 0.5 * l_ret).fillna(0)
    return (1 + blended_ret).cumprod()


def label_regimes(
    price: pd.Series, window: int = 20, threshold: float = 0.02, hysteresis: float = 0.018
) -> pd.Series:
    """Bull/Bear/Sideways from a window-day rolling return, with hysteresis
    (a Schmitt-trigger dead-band) instead of a single flat threshold.

    A flat +-2% threshold (the markov-hedge-fund-method skill's convention,
    still used to *enter* a regime here) flip-flopped constantly in
    practice: measured on sj-trading's real 0052/00662 history, median
    regime run length was 2 days and day-to-day flip probability was
    17.5%, because the 20-day rolling return jitters back and forth across
    a single boundary. Entering a regime still requires crossing
    +-`threshold`; leaving it requires retreating past a looser
    +-(`threshold` - `hysteresis`) band, so a return oscillating just
    across `threshold` no longer flips the label every time it crosses.
    hysteresis=0.018 (exit band +-0.002) cut same-history flip probability
    to 9.2% and pushed median run length to 4 days (mean 10.8, vs 5.7
    unhystereted) - see tests/test_regime.py for the measurement this
    default was picked from. Flip rate plateaus around 8.7% as hysteresis
    approaches threshold - that floor is a structural limit of a fixed
    +-2% entry threshold, not a bug; widening `threshold` itself would cut
    it further at the cost of taking longer to recognize a real reversal."""
    if not 0 <= hysteresis < threshold:
        raise ValueError(f"hysteresis ({hysteresis}) must be in [0, threshold={threshold})")
    exit_band = threshold - hysteresis
    rolling_return = price.pct_change(window)

    labels = []
    state = "Sideways"
    for r in rolling_return:
        if pd.notna(r):
            if state == "Bull" and r < exit_band:
                state = "Bear" if r < -threshold else "Sideways"
            elif state == "Bear" and r > -exit_band:
                state = "Bull" if r > threshold else "Sideways"
            elif state == "Sideways":
                if r > threshold:
                    state = "Bull"
                elif r < -threshold:
                    state = "Bear"
        labels.append(state)
    return pd.Series(labels, index=price.index)


def is_pnl_outlier(pnl_pct: float, stats: dict | None, threshold: float = 3.0) -> bool | None:
    """True if pnl_pct is beyond `threshold` std devs from the backtested
    daily-return distribution in `stats`. None means "no opinion" - stats
    unavailable, missing keys, or too degenerate to judge, never treated
    as an outlier."""
    if stats is None:
        return None
    std = stats.get("std_daily_return")
    mean = stats.get("mean_daily_return")
    if std is None or mean is None or not std or math.isnan(std) or math.isnan(mean):
        return None
    z = abs((pnl_pct - mean) / std)
    return z > threshold
