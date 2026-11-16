"""Backtests GridBot's fixed-allocation rebalancing strategy against
historical daily prices.

    uv run python -m sj_trading.backtest

Replicates calculateSharetarget/sendOrders from gridbot.py day-by-day using
daily closes for decisions and configurable conservative execution slippage
for fills, including the trigger threshold, the cash-constrained buy sizing,
and realistic transaction costs. Not wired into the live bot's trading -
this is an offline research tool, and the source of backtest_stats.json
(the live drift check's baseline). Re-run it whenever
GridBot.TARGET_UPPER_ALLOC changes.
"""
import datetime
import json
import math

import numpy as np
import pandas as pd
import yfinance as yf

from sj_trading.gridbot import GridBot, TICKERS
from sj_trading.misc import blended_price, get_tick_unit, label_regimes

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
# volatility and comfortably below any real split. Don't lower it:
# _adjust_split_defects *rescales* everything before a false-positive jump,
# silently distorting real prices.
DAILY_LIMIT_PCT = 0.30
DEFAULT_SLIPPAGE_TICKS = 1.0


def _fill_price(price: float, action: str, slippage_ticks: float) -> float:
    """Approximate a marketable limit fill from daily-close data.

    Live buys use 賣1 and sells use 買1. Historical bid/ask data is not
    available here, so one tick per side is the conservative default. A zero
    value reproduces the old close-fill model.
    """
    if slippage_ticks < 0:
        raise ValueError("slippage_ticks must be non-negative")
    slippage = get_tick_unit(price) * slippage_ticks
    if action == "Buy":
        return price + slippage
    if action == "Sell":
        return max(price - slippage, get_tick_unit(price))
    raise ValueError(f"unsupported action: {action}")


def _adjust_split_defects(close_series: pd.Series) -> pd.Series:
    """Retroactively rescales everything before each implausible jump by
    the jump ratio, so the whole series becomes one continuous scale."""
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


def backtest(
    df: pd.DataFrame,
    upper_alloc: float,
    init_capital: float = 100_000.0,
    slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS,
    return_daily: bool = False,
):
    """Simulates holding `upper_alloc` of capital in df["upper"] and the
    rest in df["lower"], rebalancing once a day at the close under the live
    bot's rules. Returns None if there isn't enough history for a
    meaningful sample.

    Each day sells are applied before buys: the live bot cycles every 3
    minutes, so within one day a sell's proceeds are available to the
    other leg's buy a cycle later."""
    if len(df) < 60:
        return None

    px = {"upper": df["upper"].to_numpy(), "lower": df["lower"].to_numpy()}
    alloc = {"upper": upper_alloc, "lower": 1.0 - upper_alloc}
    shares = {"upper": 0, "lower": 0}
    money = init_capital
    # Set once from starting capital, matching production's one-time bot1.trigger.
    trigger = max(2000.0, init_capital * 0.005)
    equity = np.empty(len(df))

    for i in range(len(df)):
        close = {leg: px[leg][i] for leg in px}
        capital = money + sum(shares[leg] * close[leg] for leg in px)
        qty = {leg: int(alloc[leg] * capital / close[leg]) - shares[leg] for leg in px}

        for leg in sorted(px, key=lambda leg: qty[leg]):
            q = qty[leg]
            if q > 0:
                buy_price = _fill_price(close[leg], "Buy", slippage_ticks)
                if money < buy_price * q:
                    q = max(int(money / buy_price), 0)
            if q == 0 or abs(q) * close[leg] < trigger:
                continue
            if q > 0:
                principal = math.floor(buy_price * q)
                cost = principal + math.floor(principal * BROKERAGE)
                if money < cost:
                    continue
                money -= cost
            else:
                principal = math.floor(_fill_price(close[leg], "Sell", slippage_ticks) * -q)
                money += principal - math.floor(principal * BROKERAGE) - math.floor(principal * ETF_TAX)
            shares[leg] += q

        equity[i] = money + sum(shares[leg] * close[leg] for leg in px)

    eq = pd.Series(equity, index=df.index)
    daily_ret = eq.pct_change().dropna()
    if daily_ret.std() == 0 or len(daily_ret) < 30:
        return None
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    ann_return = (1 + total_return) ** (252 / len(eq)) - 1
    ann_vol = daily_ret.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else np.nan
    max_dd = (eq / eq.cummax() - 1).min()
    result = {
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_days": len(eq),
        "daily_mean": float(daily_ret.mean()),
        "daily_std": float(daily_ret.std()),
        "slippage_ticks": slippage_ticks,
    }
    if return_daily:
        result["daily_returns"] = daily_ret
    return result


def buy_and_hold(df: pd.DataFrame):
    u0, l0 = df["upper"].iloc[0], df["lower"].iloc[0]
    bh = 0.5 * df["upper"] / u0 + 0.5 * df["lower"] / l0
    ret = bh.iloc[-1] / bh.iloc[0] - 1
    daily = bh.pct_change().dropna()
    ann_ret = (1 + ret) ** (252 / len(bh)) - 1
    sharpe = ann_ret / (daily.std() * np.sqrt(252))
    max_dd = (bh / bh.cummax() - 1).min()
    return {"total_return": ret, "ann_return": ann_ret, "sharpe": sharpe, "max_dd": max_dd}


def compare_allocations(
    df: pd.DataFrame,
    allocs,
    slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS,
) -> pd.DataFrame:
    """One backtest row per upper-ticker allocation in `allocs`, for eyeballing
    how sensitive the result is to the split. Deliberately a comparison
    table, not an optimiser: over 2016-2026 Sharpe barely moves across
    splits, so picking the top row would just be hindsight."""
    rows = []
    for a in allocs:
        res = backtest(df, a, slippage_ticks=slippage_ticks)
        if res is not None:
            rows.append({"upper_alloc": a, **res})
    return pd.DataFrame(rows)


def write_stats(
    df: pd.DataFrame,
    upper_alloc: float,
    path: str = "backtest_stats.json",
    slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS,
) -> dict:
    """Snapshots the backtested daily-return distribution for `upper_alloc`
    to `path`, in the shape misc.is_pnl_outlier's `stats` argument expects.
    Run manually (and commit the file) whenever GridBot.TARGET_UPPER_ALLOC
    changes - not invoked automatically by the live bot or CI."""
    result = backtest(df, upper_alloc, slippage_ticks=slippage_ticks)
    if result is None:
        raise ValueError("backtest returned None - not enough history")
    stats = {
        "mean_daily_return": result["daily_mean"],
        "std_daily_return": result["daily_std"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target_upper_alloc": upper_alloc,
        "execution_slippage_ticks": slippage_ticks,
    }
    with open(path, "w") as f:
        json.dump(stats, f, indent=2)
    return stats


def backtest_by_regime(
    df: pd.DataFrame,
    upper_alloc: float,
    window: int = 20,
    threshold: float = 0.02,
    slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS,
) -> pd.DataFrame:
    """Splits the strategy's daily returns by market regime and reports
    Sharpe per regime, to check whether backtested performance is uniform
    or concentrated in one regime. The bot is always fully invested, so
    expect Bear-regime Sharpe to be negative.

    Regime is labeled off the 50/50 blended price (overall market
    direction) - see misc.blended_price/label_regimes."""
    result = backtest(df, upper_alloc, slippage_ticks=slippage_ticks, return_daily=True)
    if result is None:
        raise ValueError("backtest returned None - not enough history")
    daily_ret = result["daily_returns"]

    blended = blended_price(df["upper"], df["lower"])
    regime = label_regimes(blended, window=window, threshold=threshold).reindex(daily_ret.index)

    rows = []
    for label in ("Bull", "Bear", "Sideways"):
        r = daily_ret[regime == label]
        if len(r) < 10:
            rows.append(
                {"regime": label, "n_days": len(r), "ann_return": np.nan, "sharpe": np.nan}
            )
            continue
        ann_return = (1 + r).prod() ** (252 / len(r)) - 1
        ann_vol = r.std() * np.sqrt(252)
        sharpe = ann_return / ann_vol if ann_vol > 0 else np.nan
        rows.append(
            {"regime": label, "n_days": len(r), "ann_return": ann_return, "sharpe": sharpe}
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    live_alloc = GridBot.TARGET_UPPER_ALLOC
    df = load_prices()
    print("data range:", df.index.min().date(), "to", df.index.max().date(), f"({len(df)} days)")

    print(f"\n=== Live allocation ({live_alloc}) vs other fixed splits ===")
    table = compare_allocations(df, sorted({0.35, 0.5, live_alloc, 0.8}))
    print(table[["upper_alloc", "sharpe", "ann_return", "ann_vol", "max_dd"]].to_string(index=False))

    print("\n=== Buy & hold 50/50 (never rebalanced) ===")
    print(buy_and_hold(df))

    print("\n=== Live allocation by market regime ===")
    print(backtest_by_regime(df, live_alloc).to_string(index=False))

    print("\n=== Writing backtest_stats.json for live drift monitoring ===")
    print(write_stats(df, live_alloc))
