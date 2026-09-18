# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A grid-trading bot for Taiwan-listed ETFs (0052 / 00662) built on SinoPac's **Shioaji** API. It runs once per trading day (login → subscribe to quotes → rebalance every 3 minutes → exit), either locally or via the scheduled GitHub Actions workflow.

## Trading Bot Safety
- When writing cancel/close order scripts, ALWAYS filter to the specific target tickers (e.g., 0052, 00662) — never operate on account-wide orders.
- Before trusting a 'bug' in position/order data, verify the diagnostic script itself is correct.

## Commands

```bash
uv sync                                  # install/update deps into .venv
uv run python -m sj_trading.gridbot_body # run the bot (needs SJ_API_KEY/SJ_SEC_KEY in env or .env)
uv run python -m sj_trading.backtest     # backtest/grid-search GridBot.parameters against historical data
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/  # safety-invariant tests (cancel scope, fee floors, aliasing, capital round-trip, TWSE cross-checks)
uv lock                                  # regenerate uv.lock after editing dependencies in pyproject.toml
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is required, not cosmetic: if the shell has a ROS environment sourced, `PYTHONPATH` carries ROS's `launch_testing`/`launch_pytest`/`ament_*` packages, several of which register broken pytest11 entry points (`ModuleNotFoundError: No module named 'yaml'`). This project has no ROS dependency — don't touch `PYTHONPATH` or `~/.bashrc`'s ROS sourcing (used by other projects) to fix it; this env var disables pytest's entry-point plugin autoload instead.

## Environment variables

Read from `.env` (gitignored, not committed) or process env:

| Variable | Used for |
|---|---|
| `SJ_API_KEY` / `SJ_SEC_KEY` | Shioaji login (always required) |
| `SJ_PRODUCTION` | `"true"` switches to live trading + requires CA activation; anything else (default) stays in simulation |
| `SJ_CA_PATH` / `SJ_CA_PASSWD` | Only read when `SJ_PRODUCTION=true`, to activate the CA cert for live orders |

In CI (`.github/workflows/gridbot.yml`), `SJ_API_KEY`/`SJ_SEC_KEY` come from GitHub repo secrets and `SJ_PRODUCTION` is hardcoded to `"true"` (since 2026-07-12, `a3a852a`) — **scheduled runs place real live orders**, not simulated ones. The CA cert is written from the `SJ_CA_PFX_B64` secret each run.

## Self-learning

When I correct you, or you catch yourself making a mistake: before continuing, add the lesson as a one-line rule under ## Lessons, so it never happens again.

## Lessons

- Before declaring a file/dependency "unused" and removing it, verify against a truly clean checkout (fresh clone, not an already-initialized local `.venv`) — an existing venv can mask build-time requirements (e.g. `uv_build` needing `src/sj_trading/__init__.py` to exist) that only surface on CI's from-scratch install.
- Before claiming a GitHub Actions config change "fixes" something, verify which specific token/mechanism actually governs that behavior rather than reasoning from generic docs — e.g. `actions/cache`/`actions/upload-artifact` authenticate via `ACTIONS_RUNTIME_TOKEN`, not `GITHUB_TOKEN`, so the repo's "Workflow permissions" setting doesn't affect them at all.
- Before asserting two assets are well/poorly diversified based on category priors ("both are growth/tech, so they're correlated"), check actual historical correlation data - priors can be wrong and a backtest is cheap to run.
- Before deleting or "cleaning up" any file/directory not created this session, check its git status, history, and lock state first (e.g. `git worktree list` showing `locked`) - don't act on "looks like leftover garbage" without verifying.
- Before assuming an implausible price jump is bad data (e.g. exceeds TWSE's real +-10% daily limit), check the exchange's own official feed and search for a real corporate action before calling it a bug - 0052's ~7x "impossible" jump was a genuine, publicly-disclosed 1-for-7 split that yfinance simply failed to record/misdated, not fabricated data. The fix (detect and adjust for the discontinuity) was right either way, but the "not real price action" claim was wrong until verified against TWSE directly.
- When `gridbot_body.py` gains a new file it writes for cross-run state (like `equity_snapshot.json`), it does nothing on its own — it must also be added to the workflow's persist step's `git add`/`git diff --quiet` list, or it silently resets to its last manually-committed value on every checkout. This is exactly what broke the daily drift check for weeks: `equity_snapshot.json` was written locally every run but never committed, so it stayed frozen at its 2026-08-14 value while the *baseline* diverged from real equity, false-triggering `sys.exit(1)` on every scheduled run. Cross-check the persist step's file list against every `misc.write_json(...)` call in `gridbot_body.py` whenever either changes.
- Before trusting a large "drift"/P&L-jump number from a JSON state file (e.g. `equity_snapshot.json`), verify it against the bot's own contemporaneous `gridbot.log` (`totalcapital:` line) and TWSE's actual closing prices, not just the file's face value. The apparent ~40% equity crash implied by `equity_snapshot.json` going 55388.5 (2026-08-14) → 32889.69 (2026-09-05) was never real: `55388.5` was a hand-typed placeholder entered when the file was first created (commit `d806eb7`, added *after* that morning's run had already logged real `totalcapital: 33100.45`), not a bot-computed value. Real capital was ~33100 on 2026-08-14 and ~32437 on 2026-09-02 per `gridbot.log`, and TWSE closes for both tickers were roughly flat over that window — the bot's actual performance was flat, not crashing. Same class of mistake as the entry above (unverified state-file value treated as ground truth), just introduced at file-creation time instead of at persistence time.

## Architecture

**`src/sj_trading/gridbot_body.py`** — orchestration/entry point.
- `main()`: creates the Shioaji client (`simulation=not production`), logs in, optionally activates the CA cert, calls `GridbotBody(api)`, then logs out. Since shioaji 1.7.0, `login()` no longer takes `fetch_contract` (contracts self-manage — `api.Contracts.Stocks[...]` loads on-demand on first access, no explicit fetch needed).
- `GridbotBody(api)`: snapshots the two tickers, restores prior cash balance from `money.json` (via `misc.read_json`, defaulting to 0 on first run), builds a `GridBot` instance, subscribes to tick/bidask quotes for both tickers (`api.subscribe(...)` — the modern non-deprecated form, not `api.quote.subscribe(...)`), then runs a loop that wakes every ~60s, acts every 3 minutes, and calls `bot1.updateOrder()`.
- **The loop's exit condition is wall-clock time, not elapsed duration**: the primary exit fires once `hour==13 and minute>20` (~13:21, 10 minutes before the 13:30 close, while the market is still open) — cancels all open orders, then persists `bot1.live_cash_right_now` to `money.json`. A second safety-net exit fires once `hour>=14` (late/manual start, crash-restart, clock skew that skipped the 13:21 exit) — market's already closed by then so it skips cancelOrders (nothing left to cancel) and just persists and stops. This means the script is designed to be started once, in the morning; either exit stops it by mid-afternoon, so it no longer relies on the CI job timeout to end a normal run.

**`src/sj_trading/gridbot.py`** — the actual strategy, in `GridBot`.
- Computes a bias-ratio (乖離率) between the two tickers' price ratio and its moving average (fetched via `yfinance`, `UpdateMA()`), maps that into a target capital split (`calculateGrid`/`calculateSharetarget`), then rebalances by computing share deltas and placing buy/sell orders (`sendOrders`) sized to stay above a minimum trade-value `trigger` (to avoid fee drag on tiny trades) and within available cash.
- Cash/settlement tracking happens via `order_cb`, registered with `api.set_order_callback` in `__init__`, which adjusts `self.g_settlement` on each `OrderState.StockDeal` fill and recomputes `self.money`.
- `cancelOrders()` walks `api.list_trades()` and cancels any non-terminal order matching the bot's two tickers before every rebalance.

**`src/sj_trading/misc.py`** — small utility grab-bag: JSON read/write (used to persist `bot1.money` in `money.json` across daily runs), a profit/fee calculator, tick-size lookup by price band, and date helpers. Not Shioaji-specific.

**`src/sj_trading/backtest.py`** — offline research tool, not wired into the live bot. Replicates `calculateGrid`/`calculateSharetarget`/`sendOrders` day-by-day over historical daily closes (trigger threshold, ±999 share clamp, cash-constrained sizing, realistic fees included), and grid-searches the five `GridBot.parameters` values. Run manually (`uv run python -m sj_trading.backtest`) for interactive exploration; for the unattended cron path see `retune.py` below. If live daily P&L (see below) diverges meaningfully from backtested expectations between quarterly re-tunes, that's a signal to investigate sooner rather than wait for the next scheduled run.

**`src/sj_trading/retune.py`** — wraps `backtest.py`'s `grid_search`/`validate_out_of_sample`/`write_stats` for **unattended** use from `.github/workflows/retune.yml` (quarterly cron, 1st of Jan/Apr/Jul/Oct, `workflow_dispatch` also available). Grid-searches the same five parameters, and if the best candidate's out-of-sample Sharpe clears `MIN_TEST_SHARPE` (0.5), rewrites `GridBot.parameters` directly inside `gridbot.py` between the `# AUTO-RETUNE:START`/`# AUTO-RETUNE:END` markers and refreshes `backtest_stats.json`; otherwise it leaves `GridBot.parameters` untouched and only refreshes `backtest_stats.json` against the current (unchanged) params. **This commits straight to `master` with no human review gate** — the Sharpe floor is the only safety check before a new parameter set reaches the live bot on its next scheduled `gridbot.yml` run. `git log -- src/sj_trading/gridbot.py` shows every auto-applied change; each carries a comment with the date and in/out-of-sample Sharpe.

**`.github/workflows/gridbot.yml`** — scheduled trigger at `23 23 * * 0-4` (23:23 UTC Sun-Thu = 07:23 Taipei Mon-Fri, deliberately off the top-of-hour mark since exact-hour slots are more prone to delay/drop; this value has drifted across several commits, so re-check it directly rather than trusting this doc) plus manual `workflow_dispatch`. GitHub's cron has no concept of Taiwan market holidays, so it still fires on holidays (harmless no-op against the API that day). `timeout-minutes: 360` caps a run in case the wall-clock exit logic above doesn't fire as expected. `concurrency: group: gridbot-live-trading` with `cancel-in-progress: false` prevents two overlapping live-trading runs (e.g. a late schedule + a manual dispatch) from placing duplicate orders against the same account.

## Environment
- CI uses `uv sync`; never delete package `__init__.py` files as this breaks the build.

## State that persists across runs

- `money.json` — cash balance (`bot1.live_cash_right_now`) as a tracked file in the repo (not gitignored), read at the start of each run and written at the ~13:21 or hour>=14 safety-net exit (see above) or on `KeyboardInterrupt`. The CI workflow commits the updated value back to `master` after each run (`contents: write` permission), so `git log -- money.json` is an audit trail of every day's ending balance.
  - **This replaced an `actions/cache`-based `money.p` pickle** (removed 2026-07-14, after a hard-cancelled CI run lost real fills because `actions/cache`'s save is a post-job hook that's skipped on forced cancellation, and cache entries evict after 7 days of inactivity — a holiday gap could silently reset the balance to 0). Committing the file sidesteps both: no eviction, and it's directly readable/editable (including from GitHub's web UI) without needing to trigger a workflow run at all.
  - **Local and CI copies now do sync**, since it's the same tracked file both places read/write — a local edit via `set_init_invest_amt.py` only takes effect on the next CI run once committed and pushed. The `seed_money`/`seed_only` `workflow_dispatch` inputs still exist as a phone/CLI-only correction path (`seed_only=true` skips CA activation and the live trading run, only writing and committing the new balance), but a direct edit + push works too.
  - A hard-cancelled CI job still can't reach the commit-back step (same as the old cache save) — that's inherent to force-cancellation, not a storage-backend property. After any forced cancel, reconcile `money.json` manually against actual broker fills before the next scheduled run.
- `backtest_stats.json` / `equity_snapshot.json` — the live-vs-backtest daily P&L drift check's inputs. `backtest_stats.json` (committed) is a snapshot of the backtested daily-return distribution for the live `GridBot.parameters`, written by `backtest.py`'s `write_stats()` — refreshed automatically every quarter by `retune.py` (see above), so it always matches whatever `GridBot.parameters` currently is; a manual `backtest.py` run + commit still works between cron cycles if needed. `equity_snapshot.json` is the bot's own prior-day ending equity, written at every end-of-day exit.
  - **The workflow's persist step must commit this file too, alongside `money.json`** — from 2026-08-14 to 2026-09-05 it wasn't (only `money.json` was in the `git add`), so every checkout reset it to its one manual 2026-08-14 commit while the checked-out baseline diverged from real equity underneath it, false-triggering the drift check's `sys.exit(1)` on every single scheduled run for days. Fixed 2026-09-05 by adding it to the same `git add`/`git diff --quiet` list as `money.json` — if this file's staging is ever split from `money.json`'s again, expect the same failure mode to return.
  - That 2026-08-14 baseline (`55388.5`) was itself never a real bot-computed value — it was hand-typed when the file was first created, hours after that morning's actual run had logged `totalcapital: 33100.45`. The drift check's "-41% drift" on 2026-09-02 was correctly flagging a bad baseline, not a real loss — see the Lessons entry above before assuming a big drift number means real P&L damage.
- `gridbot.log` — INFO-level log written during the trading loop (`*.log` is gitignored).

## Verification
- After refactors or bug fixes to trading logic, verify with dry-runs and cross-check against authoritative sources (e.g., TWSE) before considering the task complete.

