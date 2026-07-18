"""Backtests GridBot's bias-ratio grid strategy against historical daily
prices, and grid-searches its five tunable parameters.

    uv run python -m sj_trading.backtest

Replicates calculateGrid/calculateSharetarget/sendOrders from gridbot.py
day-by-day (daily close as both decision price and fill price), including
the trigger threshold, the +-999 share clamp, the cash-constrained buy
sizing, and realistic transaction costs. Not wired into the live bot -
this is an offline research tool for periodically re-checking whether the
current `GridBot.parameters` are still reasonable.
"""
import itertools
import math
import time

import numpy as np
import pandas as pd
import yfinance as yf

from sj_trading.gridbot import TICKERS

UPPER, LOWER = TICKERS

# Realistic Taiwan retail costs: brokerage discounted to match gridbot.py's
# actual live FEE_DISCOUNT (0.38, i.e. pay 38% of the 0.1425% headline rate)
# each side, plus the 0.1% ETF transaction tax on sells (0052/00662 are
# ETFs -> 0.1%, not the 0.3% ordinary-stock rate).
BROKERAGE = 0.1425 / 100 * 0.38
ETF_TAX = 0.1 / 100


# NOT a flat +-10%: TWSE exempts foreign-index-linked ETFs (e.g. 00662,
# tracks NASDAQ-100) from the domestic price limit, so single-day moves up
# to ~14% are real (confirmed against TWSE: 00662 +14.3%/+12.3% on
# 2025-04-07/04-10, the tariff-shock selloff). Full-history scan of both
# tickers found exactly one move exceeding this: 0052's genuine 1-for-7
# split (+85.7%, 2025-11-17). 0.30 sits comfortably above all observed real
# volatility and comfortably below any real split. A lower threshold here
# is worse than in the live bot's truncate: this function *rescales*
# everything before a false-positive jump, silently distorting real prices
# rather than just dropping history.
DAILY_LIMIT_PCT = 0.30


def _adjust_split_defects(close_series: pd.Series) -> pd.Series:
    """Retroactively rescales everything before each implausible jump by
    the jump ratio, so the whole series becomes one continuous scale -
    unlike the live bot's truncate-and-drop approach, a full backtest needs
    the history preserved rather than thrown away."""
    s = close_series.copy()
    while True:
        pct_change = s.pct_change().abs()
        bad = pct_change[pct_change > DAILY_LIMIT_PCT]
        if bad.empty:
            return s
        pos = s.index.get_loc(bad.index[-1])
        if pos == 0:
            return s
        ratio = s.iloc[pos] / s.iloc[pos - 1]
        s.iloc[:pos] = s.iloc[:pos] * ratio


def load_prices(period: str = "max") -> pd.DataFrame:
    u = _adjust_split_defects(yf.Ticker(UPPER + ".tw").history(period=period)["Close"])
    l = _adjust_split_defects(yf.Ticker(LOWER + ".tw").history(period=period)["Close"])
    df = pd.concat([u, l], axis=1, keys=["upper", "lower"]).dropna()
    df.index = df.index.tz_localize(None)
    return df


def backtest(df: pd.DataFrame, ratio: pd.Series, params: dict, init_capital: float = 100_000.0):
    """Returns None if there isn't enough history for a meaningful sample."""
    bias_upper = params["BiasUpperLimit"]
    upper_pos = params["UpperLimitPosition"]
    bias_lower = params["BiasLowerLimit"]
    lower_pos = params["LowerLimitPosition"]
    period = params["BiasPeriod"]

    ma = ratio.rolling(period).mean()
    valid = ma.notna()
    if valid.sum() < 60:
        return None

    upper_px = df["upper"].to_numpy()
    lower_px = df["lower"].to_numpy()
    bias = (ratio / ma).to_numpy()

    money = init_capital
    upper_shares = 0
    lower_shares = 0
    trigger = None  # set once, matching production's one-time bot1.trigger
    equity = np.empty(len(df))

    start = int(np.argmax(valid.to_numpy()))
    for i in range(len(df)):
        if i < start:
            equity[i] = init_capital
            continue

        up, lp = upper_px[i], lower_px[i]
        if trigger is None:
            total_capital = money + upper_shares * up + lower_shares * lp
            trigger = max(2000.0, total_capital * 0.005)

        b = bias[i]
        share_target = (b - bias_lower) / (bias_upper - bias_lower)
        share_target = share_target * (upper_pos - lower_pos) + lower_pos
        share_target = max(share_target, upper_pos)
        share_target = min(share_target, lower_pos)

        capital_in_bot = money + upper_shares * up + lower_shares * lp
        upper_target = int(share_target * capital_in_bot / up)
        lower_target = int((1.0 - share_target) * capital_in_bot / lp)

        qty_upper = max(min(upper_target - upper_shares, 999), -999)
        qty_lower = max(min(lower_target - lower_shares, 999), -999)

        if qty_upper > 0 and money < up * qty_upper:
            qty_upper = max(int(money / up), 0)
        if qty_upper != 0 and abs(qty_upper) * up >= trigger:
            if qty_upper > 0:
                principal = math.floor(up * qty_upper)
                commission = math.floor(principal * BROKERAGE)
                cost = principal + commission
                if money >= cost:
                    money -= cost
                    upper_shares += qty_upper
            else:
                principal = math.floor(up * abs(qty_upper))
                commission = math.floor(principal * BROKERAGE)
                tax = math.floor(principal * ETF_TAX)
                money += principal - commission - tax
                upper_shares += qty_upper  # negative

        if qty_lower > 0 and money < lp * qty_lower:
            qty_lower = max(int(money / lp), 0)
        if qty_lower != 0 and abs(qty_lower) * lp >= trigger:
            if qty_lower > 0:
                principal = math.floor(lp * qty_lower)
                commission = math.floor(principal * BROKERAGE)
                cost = principal + commission
                if money >= cost:
                    money -= cost
                    lower_shares += qty_lower
            else:
                principal = math.floor(lp * abs(qty_lower))
                commission = math.floor(principal * BROKERAGE)
                tax = math.floor(principal * ETF_TAX)
                money += principal - commission - tax
                lower_shares += qty_lower

        equity[i] = money + upper_shares * up + lower_shares * lp

    eq = pd.Series(equity, index=df.index).iloc[start:]
    daily_ret = eq.pct_change().dropna()
    if daily_ret.std() == 0 or len(daily_ret) < 30:
        return None
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    ann_return = (1 + total_return) ** (252 / len(eq)) - 1
    ann_vol = daily_ret.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else np.nan
    max_dd = (eq / eq.cummax() - 1).min()
    return {
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_days": len(eq),
    }


def buy_and_hold(df: pd.DataFrame):
    u0, l0 = df["upper"].iloc[0], df["lower"].iloc[0]
    bh = 0.5 * df["upper"] / u0 + 0.5 * df["lower"] / l0
    ret = bh.iloc[-1] / bh.iloc[0] - 1
    daily = bh.pct_change().dropna()
    ann_ret = (1 + ret) ** (252 / len(bh)) - 1
    sharpe = ann_ret / (daily.std() * np.sqrt(252))
    max_dd = (bh / bh.cummax() - 1).min()
    return {"total_return": ret, "ann_return": ann_ret, "sharpe": sharpe, "max_dd": max_dd}


def grid_search(df, bias_upper_list, bias_lower_list, upper_pos_list, lower_pos_list, period_list):
    """Position-bound constraints (up_pos < low_pos) keep results as a
    genuine two-asset grid; pass e.g. [0.15..0.35] / [0.65..0.85] rather
    than ranges touching 0/1, which degenerate into a binary switch between
    the two tickers instead of a blended allocation."""
    ratio = df["upper"] / df["lower"]
    results = []
    t0 = time.time()
    combos = 0
    for period in period_list:
        ma = ratio.rolling(period).mean()
        if ma.notna().sum() < 60:
            continue
        for bu, bl in itertools.product(bias_upper_list, bias_lower_list):
            if bu <= bl:
                continue
            for up_pos, low_pos in itertools.product(upper_pos_list, lower_pos_list):
                if up_pos >= low_pos:
                    continue
                params = {
                    "BiasUpperLimit": bu,
                    "UpperLimitPosition": up_pos,
                    "BiasLowerLimit": bl,
                    "LowerLimitPosition": low_pos,
                    "BiasPeriod": period,
                }
                res = backtest(df, ratio, params)
                combos += 1
                if res is not None:
                    results.append({**params, **res})
    print(f"tested {combos} combos in {time.time()-t0:.1f}s, {len(results)} valid")
    return pd.DataFrame(results)


def validate_out_of_sample(df, params, train_frac=0.7):
    split = int(len(df) * train_frac)
    train_df, test_df = df.iloc[:split], df.iloc[split:]
    train_res = backtest(train_df, train_df["upper"] / train_df["lower"], params)
    test_res = backtest(test_df, test_df["upper"] / test_df["lower"], params)
    return train_res, test_res


if __name__ == "__main__":
    from sj_trading.gridbot import GridBot

    df = load_prices()
    print("data range:", df.index.min().date(), "to", df.index.max().date(), f"({len(df)} days)")

    results = grid_search(
        df,
        bias_upper_list=[1.05, 1.08, 1.1, 1.15, 1.2, 1.3, 1.4],
        bias_lower_list=[0.7, 0.75, 0.8, 0.85, 0.9, 0.95],
        upper_pos_list=[0.15, 0.2, 0.25, 0.3, 0.35],
        lower_pos_list=[0.65, 0.7, 0.75, 0.8, 0.85],
        period_list=[30, 45, 60, 73, 90, 120, 150, 180, 220],
    )

    print("\n=== Top 10 by Sharpe ===")
    print(results.sort_values("sharpe", ascending=False).head(10).to_string(index=False))

    best = results.sort_values("sharpe", ascending=False).iloc[0]
    best_params = {
        "BiasUpperLimit": best.BiasUpperLimit,
        "UpperLimitPosition": best.UpperLimitPosition,
        "BiasLowerLimit": best.BiasLowerLimit,
        "LowerLimitPosition": best.LowerLimitPosition,
        "BiasPeriod": int(best.BiasPeriod),
    }
    train_res, test_res = validate_out_of_sample(df, best_params)
    print("\nBest params:", best_params)
    print("Train-period (in-sample):", train_res)
    print("Test-period (out-of-sample):", test_res)

    print("\n=== Current production parameters, for comparison ===")
    ratio = df["upper"] / df["lower"]
    print(GridBot.parameters, "->", backtest(df, ratio, GridBot.parameters))

    print("\n=== Buy & hold 50/50 ===")
    print(buy_and_hold(df))
