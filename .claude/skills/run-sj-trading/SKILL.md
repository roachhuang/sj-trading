---
name: run-sj-trading
description: Build, run, and drive sj-trading (the 0052/00662 grid-trading bot and its backtest tool). Use when asked to run sj-trading, start the gridbot, run a backtest, or smoke-test the bot's login/bootstrap.
---

sj-trading is a Python package with no GUI — drive it via
`.claude/skills/run-sj-trading/driver.sh`, which wraps the two runnable
entry points: `sj_trading.backtest` (credential-free, deterministic
research tool) and `sj_trading.gridbot_body` (the live/simulated
trading bot, needs real Shioaji credentials and live network access).

All paths below are relative to the repo root.

## Prerequisites

`uv` must be installed (this repo pins Python via `.python-version`;
`uv` provisions it automatically). No OS packages beyond that were
needed in this container.

## Setup

```bash
uv sync
```

For `bot-smoke` only: a `.env` file at repo root with real Shioaji
credentials:

```bash
SJ_API_KEY="..."      # required for bot-smoke
SJ_SEC_KEY="..."      # required for bot-smoke
SJ_PRODUCTION=false   # optional -- default is simulation; leave unset/false for smoke-testing
```

`backtest-quick`/`backtest-full` need no credentials at all — they only
hit Yahoo Finance for historical prices.

## Run (agent path)

```bash
.claude/skills/run-sj-trading/driver.sh backtest-quick   # ~1-2s, no credentials
.claude/skills/run-sj-trading/driver.sh backtest-full    # ~90-110s, no credentials
.claude/skills/run-sj-trading/driver.sh bot-smoke 30     # needs .env credentials + network
```

| command | what it does |
|---|---|
| `backtest-quick` | Imports `sj_trading.backtest` directly, loads 1y of 0052/00662 data, runs one `backtest()` + `buy_and_hold()` call. Proves the simulation logic and data loading work fast, without the full grid search. |
| `backtest-full` | The actual `uv run python -m sj_trading.backtest` entry point: 9450-combo grid search over the current pair plus out-of-sample validation. This is what `CLAUDE.md` recommends re-running every 6-12 months to re-check `GridBot.parameters`. |
| `bot-smoke [timeout_seconds]` | Runs the live bot's real entry point (`python -u -m sj_trading.gridbot_body`) just long enough to prove login → contract fetch → quote subscribe → `GridBot` bootstrap all succeed, then kills it. Default timeout 30s. The bot's own loop only exits at specific Taipei wall-clock hours (14:00-15:00) or `KeyboardInterrupt`, so it must be externally time-boxed for a smoke test — it will never exit on its own within a short window. |

Expected `bot-smoke` output ends with something like:

```
positions: 00662-0, 0052-0
init money: 30000.00
uppershare: 0.00
lowershare: 0.00
totalcapital: 30000.00
```

followed by a handful of `DeprecationWarning`s (see Gotchas) and then
silence until the timeout kills it — that's success, not a hang.

## Run (human path)

```bash
uv run main                              # entry point from pyproject.toml [project.scripts]
uv run python -m sj_trading.gridbot_body # equivalent, explicit module form
```

Runs the actual daily trading loop — intended to be started once in
the morning and left running until early afternoon (see `CLAUDE.md`).
Not something to run to completion outside that context; `Ctrl-C` to
stop early (triggers its `KeyboardInterrupt` cleanup path, which
persists `money.p`).

## Test

No test suite exists in this repo (confirmed in `CLAUDE.md`).

---

## Gotchas

- **`bot-smoke` looks hung but isn't — it's Python's stdout buffering.**
  If you redirect `uv run main`'s output to a file (or capture it)
  without `python -u` / `PYTHONUNBUFFERED=1`, Python's own `print()`
  calls (positions, init money, etc.) sit in an internal buffer and
  are lost when the process is killed by `timeout` before a flush
  happens. Meanwhile Shioaji's Rust core writes its own event log
  lines (`Response Code: ... Event: Session up`, etc.) directly to the
  file descriptor, bypassing Python's buffering entirely — so you see
  *those* lines and nothing else, which looks exactly like a hang at
  the contract-subscribe step. Always run `bot-smoke` unbuffered (the
  driver already does this via `python -u`).
- **`bot-smoke` timing is variable, not just slow.** Login + contract
  fetch (~40-50k contracts) sometimes completes in under 5s, sometimes
  takes closer to 30s, depending on live network conditions to
  Shioaji's servers — this is a real network call every time, not
  something you can make deterministic. Give it at least 30s before
  concluding something's broken.
- **Several Shioaji API calls in `gridbot_body.py` are deprecated but
  still work.** Running `bot-smoke` prints `DeprecationWarning`s for
  `api.quote.set_on_tick_stk_v1_callback`, `api.quote.on_event`,
  `api.quote.set_event_callback`, etc. — Shioaji 1.5 moved these to
  `api.X` directly (same pattern as the already-migrated
  `api.subscribe`/`sj.QuoteType`/`sj.StockOrder` elsewhere in this
  file). Harmless for now, but expect them on every `bot-smoke` run
  until that migration is finished.
- **`backtest-full` takes ~90-110s** — it's a real 9450-combo grid
  search, not a quick check. Use `backtest-quick` for a fast sanity
  check instead.
