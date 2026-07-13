# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A grid-trading bot for Taiwan-listed ETFs (0052 / 00662) built on SinoPac's **Shioaji** API. It runs once per trading day (login → subscribe to quotes → rebalance every 3 minutes → exit), either locally or via the scheduled GitHub Actions workflow.

## Commands

```bash
uv sync                                  # install/update deps into .venv
uv run python -m sj_trading.gridbot_body # run the bot (needs SJ_API_KEY/SJ_SEC_KEY in env or .env)
uv run python -m sj_trading.backtest     # backtest/grid-search GridBot.parameters against historical data
uv lock                                  # regenerate uv.lock after editing dependencies in pyproject.toml
```

There is no test suite or linter configured in this repo yet.

## Environment variables

Read from `.env` (gitignored, not committed) or process env:

| Variable | Used for |
|---|---|
| `SJ_API_KEY` / `SJ_SEC_KEY` | Shioaji login (always required) |
| `SJ_PRODUCTION` | `"true"` switches to live trading + requires CA activation; anything else (default) stays in simulation |
| `SJ_CA_PATH` / `SJ_CA_PASSWD` | Only read when `SJ_PRODUCTION=true`, to activate the CA cert for live orders |

In CI (`.github/workflows/gridbot.yml`), `SJ_API_KEY`/`SJ_SEC_KEY` come from GitHub repo secrets and `SJ_PRODUCTION` is hardcoded to `"false"`, so scheduled runs are simulation-only until that's deliberately changed.

## Self-learning

When I correct you, or you catch yourself making a mistake: before continuing, add the lesson as a one-line rule under ## Lessons, so it never happens again.

## Lessons

## Architecture

**`src/sj_trading/gridbot_body.py`** — orchestration/entry point.
- `main()`: creates the Shioaji client (`simulation=not production`), logs in with `fetch_contract=True` (needed so `api.Contracts.Stocks[...]` resolves), optionally activates the CA cert, calls `GridbotBody(api)`, then logs out.
- `GridbotBody(api)`: snapshots the two tickers, restores prior cash balance from `money.p` (via `misc.pickle_read`, defaulting to 0 on first run), builds a `GridBot` instance, subscribes to tick/bidask quotes for both tickers (`api.subscribe(...)` — the modern non-deprecated form, not `api.quote.subscribe(...)`), then runs a loop that wakes every ~60s, acts every 3 minutes, and calls `bot1.updateOrder()`.
- **The loop's exit condition is wall-clock time, not elapsed duration**: it breaks once `hour` is in `[14, 15]` and persists `bot1.money` to `money.p` first. It also cancels all open orders once between 13:00–13:20. This means the script is designed to be started once, in the morning, and left running until early afternoon — triggering it manually outside that window (e.g. via `workflow_dispatch` in the evening) will *not* hit the exit condition and it will keep looping until the CI job timeout instead.

**`src/sj_trading/gridbot.py`** — the actual strategy, in `GridBot`.
- Computes a bias-ratio (乖離率) between the two tickers' price ratio and its moving average (fetched via `yfinance`, `UpdateMA()`), maps that into a target capital split (`calculateGrid`/`calculateSharetarget`), then rebalances by computing share deltas and placing buy/sell orders (`sendOrders`) sized to stay above a minimum trade-value `trigger` (to avoid fee drag on tiny trades) and within available cash.
- Cash/settlement tracking happens via `order_cb`, registered with `api.set_order_callback` in `__init__`, which adjusts `self.g_settlement` on each `OrderState.StockDeal` fill and recomputes `self.money`.
- `cancelOrders()` walks `api.list_trades()` and cancels any non-terminal order matching the bot's two tickers before every rebalance.

**`src/sj_trading/misc.py`** — small utility grab-bag: pickle read/write (used to persist `bot1.money` in `money.p` across daily runs), a profit/fee calculator, tick-size lookup by price band, and date helpers. Not Shioaji-specific.

**`src/sj_trading/backtest.py`** — offline research tool, not wired into the live bot. Replicates `calculateGrid`/`calculateSharetarget`/`sendOrders` day-by-day over historical daily closes (trigger threshold, ±999 share clamp, cash-constrained sizing, realistic fees included), and grid-searches the five `GridBot.parameters` values. `GridBot.parameters` was last set from this tool's output (backtested 2016–2026, out-of-sample validated on a held-out 2023–2026 slice) — re-run every 6–12 months, or sooner if live daily P&L (see below) diverges meaningfully from backtested expectations, since parameters fit to one historical window can drift out of tune as market regimes shift.

**`.github/workflows/gridbot.yml`** — scheduled trigger at `50 0 * * 1-5` (00:50 UTC = 8:50am Taipei, deliberately off the top-of-hour mark since exact-hour slots are more prone to delay/drop) plus manual `workflow_dispatch`. GitHub's cron has no concept of Taiwan market holidays, so it still fires on holidays (harmless no-op against the API that day). `timeout-minutes: 330` caps a run in case the wall-clock exit logic above doesn't fire as expected.

## State that persists across runs

- `money.p` — pickled cash balance (`bot1.money`), read at the start of each run and written at the natural 14:00–15:00 exit or on `KeyboardInterrupt`. Gitignored; treat as local/CI-ephemeral runtime state, not source.
- `gridbot.log` — INFO-level log written during the trading loop (`*.log` is gitignored).
