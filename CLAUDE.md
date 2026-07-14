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

- Before declaring a file/dependency "unused" and removing it, verify against a truly clean checkout (fresh clone, not an already-initialized local `.venv`) — an existing venv can mask build-time requirements (e.g. `uv_build` needing `src/sj_trading/__init__.py` to exist) that only surface on CI's from-scratch install.
- Before claiming a GitHub Actions config change "fixes" something, verify which specific token/mechanism actually governs that behavior rather than reasoning from generic docs — e.g. `actions/cache`/`actions/upload-artifact` authenticate via `ACTIONS_RUNTIME_TOKEN`, not `GITHUB_TOKEN`, so the repo's "Workflow permissions" setting doesn't affect them at all.
- Before asserting two assets are well/poorly diversified based on category priors ("both are growth/tech, so they're correlated"), check actual historical correlation data - priors can be wrong and a backtest is cheap to run.
- Before deleting or "cleaning up" any file/directory not created this session, check its git status, history, and lock state first (e.g. `git worktree list` showing `locked`) - don't act on "looks like leftover garbage" without verifying.

## Architecture

**`src/sj_trading/gridbot_body.py`** — orchestration/entry point.
- `main()`: creates the Shioaji client (`simulation=not production`), logs in with `fetch_contract=True` (needed so `api.Contracts.Stocks[...]` resolves), optionally activates the CA cert, calls `GridbotBody(api)`, then logs out.
- `GridbotBody(api)`: snapshots the two tickers, restores prior cash balance from `money.json` (via `misc.read_json`, defaulting to 0 on first run), builds a `GridBot` instance, subscribes to tick/bidask quotes for both tickers (`api.subscribe(...)` — the modern non-deprecated form, not `api.quote.subscribe(...)`), then runs a loop that wakes every ~60s, acts every 3 minutes, and calls `bot1.updateOrder()`.
- **The loop's exit condition is wall-clock time, not elapsed duration**: it breaks once `hour` is in `[14, 15]` and persists `bot1.money` to `money.json` first. It also cancels all open orders once between 13:00–13:20. This means the script is designed to be started once, in the morning, and left running until early afternoon — triggering it manually outside that window (e.g. via `workflow_dispatch` in the evening) will *not* hit the exit condition and it will keep looping until the CI job timeout instead.

**`src/sj_trading/gridbot.py`** — the actual strategy, in `GridBot`.
- Computes a bias-ratio (乖離率) between the two tickers' price ratio and its moving average (fetched via `yfinance`, `UpdateMA()`), maps that into a target capital split (`calculateGrid`/`calculateSharetarget`), then rebalances by computing share deltas and placing buy/sell orders (`sendOrders`) sized to stay above a minimum trade-value `trigger` (to avoid fee drag on tiny trades) and within available cash.
- Cash/settlement tracking happens via `order_cb`, registered with `api.set_order_callback` in `__init__`, which adjusts `self.g_settlement` on each `OrderState.StockDeal` fill and recomputes `self.money`.
- `cancelOrders()` walks `api.list_trades()` and cancels any non-terminal order matching the bot's two tickers before every rebalance.

**`src/sj_trading/misc.py`** — small utility grab-bag: JSON read/write (used to persist `bot1.money` in `money.json` across daily runs), a profit/fee calculator, tick-size lookup by price band, and date helpers. Not Shioaji-specific.

**`src/sj_trading/backtest.py`** — offline research tool, not wired into the live bot. Replicates `calculateGrid`/`calculateSharetarget`/`sendOrders` day-by-day over historical daily closes (trigger threshold, ±999 share clamp, cash-constrained sizing, realistic fees included), and grid-searches the five `GridBot.parameters` values. `GridBot.parameters` was last set from this tool's output (backtested 2016–2026, out-of-sample validated on a held-out 2023–2026 slice) — re-run every 6–12 months, or sooner if live daily P&L (see below) diverges meaningfully from backtested expectations, since parameters fit to one historical window can drift out of tune as market regimes shift.

**`.github/workflows/gridbot.yml`** — scheduled trigger at `50 0 * * 1-5` (00:50 UTC = 8:50am Taipei, deliberately off the top-of-hour mark since exact-hour slots are more prone to delay/drop) plus manual `workflow_dispatch`. GitHub's cron has no concept of Taiwan market holidays, so it still fires on holidays (harmless no-op against the API that day). `timeout-minutes: 330` caps a run in case the wall-clock exit logic above doesn't fire as expected.

## State that persists across runs

- `money.json` — cash balance (`bot1.money`) as a tracked file in the repo (not gitignored), read at the start of each run and written at the natural 14:00–15:00 exit or on `KeyboardInterrupt`. The CI workflow commits the updated value back to `master` after each run (`contents: write` permission), so `git log -- money.json` is an audit trail of every day's ending balance.
  - **This replaced an `actions/cache`-based `money.p` pickle** (removed 2026-07-14, after a hard-cancelled CI run lost real fills because `actions/cache`'s save is a post-job hook that's skipped on forced cancellation, and cache entries evict after 7 days of inactivity — a holiday gap could silently reset the balance to 0). Committing the file sidesteps both: no eviction, and it's directly readable/editable (including from GitHub's web UI) without needing to trigger a workflow run at all.
  - **Local and CI copies now do sync**, since it's the same tracked file both places read/write — a local edit via `set_init_invest_amt.py` only takes effect on the next CI run once committed and pushed. The `seed_money`/`seed_only` `workflow_dispatch` inputs still exist as a phone/CLI-only correction path (`seed_only=true` skips CA activation and the live trading run, only writing and committing the new balance), but a direct edit + push works too.
  - A hard-cancelled CI job still can't reach the commit-back step (same as the old cache save) — that's inherent to force-cancellation, not a storage-backend property. After any forced cancel, reconcile `money.json` manually against actual broker fills before the next scheduled run.
- `gridbot.log` — INFO-level log written during the trading loop (`*.log` is gitignored).

# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
