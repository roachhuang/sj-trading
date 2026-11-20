# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fixed-allocation rebalancing bot (65% 0052 / 35% 00662, historically a bias-ratio "grid" bot - hence the names) for Taiwan-listed ETFs built on SinoPac's **Shioaji** API. It runs once per trading day, either locally or via the scheduled GitHub Actions workflow.

**CI places real live orders**: `.github/workflows/gridbot.yml` hardcodes `SJ_PRODUCTION="true"` (since 2026-07-12). Scheduled runs are not simulated.

## Trading Bot Safety
- When writing cancel/close order scripts, ALWAYS filter to the specific target tickers (e.g., 0052, 00662) — never operate on account-wide orders.
- Before trusting a 'bug' in position/order data, verify the diagnostic script itself is correct.
- A code path's early-return/failure branch that's supposed to fail the CI job (return something truthy so `main()`'s `if job_failed: sys.exit(1)` fires) must actually be checked, not assumed — `GridbotBody`'s `SJ_TAKE_PROFIT` path `return None`'d unconditionally until 2026-09-18, so an incomplete emergency liquidation only logged an error and the run still reported success. Fixed: `return not positions_flat`.
- A fallback value's comment claiming "no-op"/neutral behavior must be checked against what the value actually does, not trusted at face value — `calculateGrid()`'s MA-is-NaN fallback returned `parameters["LowerLimitPosition"]` (0.80) labeled "no-op" until 2026-09-18, but that's the *maximum* possible allocation toward the upper ticker, so a yfinance blip would've driven an aggressive rebalance instead of skipping the cycle. Fixed by raising instead, letting `updateOrder()`'s existing try/except skip the cycle like its other failure guards do.

## Commands

```bash
uv sync                                  # install/update deps into .venv
uv run python -m sj_trading.gridbot_body # run the bot (needs SJ_API_KEY/SJ_SEC_KEY in env or .env)
uv run python -m sj_trading.backtest     # backtest GridBot.TARGET_UPPER_ALLOC against historical data + rewrite backtest_stats.json
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/  # safety-invariant tests
uv lock                                  # regenerate uv.lock after editing dependencies in pyproject.toml
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is required, not cosmetic: if the shell has a ROS environment sourced, `PYTHONPATH` carries ROS's `launch_testing`/`launch_pytest`/`ament_*` packages, several of which register broken pytest11 entry points (`ModuleNotFoundError: No module named 'yaml'`). This project has no ROS dependency — don't touch `PYTHONPATH` or `~/.bashrc`'s ROS sourcing (used by other projects) to fix it; this env var disables pytest's entry-point plugin autoload instead.

## Self-learning

When I correct you, or you catch yourself making a mistake: before continuing, add the lesson as a one-line rule under ## Lessons, so it never happens again.

## Lessons

- Before declaring a file/dependency "unused" and removing it, verify against a truly clean checkout (fresh clone, not an already-initialized local `.venv`) — an existing venv can mask build-time requirements (e.g. `uv_build` needing `src/sj_trading/__init__.py` to exist) that only surface on CI's from-scratch install. Never delete package `__init__.py` files.
- Before claiming a GitHub Actions config change "fixes" something, verify which specific token/mechanism actually governs that behavior rather than reasoning from generic docs — e.g. `actions/cache`/`actions/upload-artifact` authenticate via `ACTIONS_RUNTIME_TOKEN`, not `GITHUB_TOKEN`, so the repo's "Workflow permissions" setting doesn't affect them at all.
- Before asserting two assets are well/poorly diversified based on category priors ("both are growth/tech, so they're correlated"), check actual historical correlation data - priors can be wrong and a backtest is cheap to run.
- Before deleting or "cleaning up" any file/directory not created this session, check its git status, history, and lock state first (e.g. `git worktree list` showing `locked`) - don't act on "looks like leftover garbage" without verifying.
- Before assuming an implausible price jump is bad data (e.g. exceeds TWSE's real +-10% daily limit), check the exchange's own official feed and search for a real corporate action before calling it a bug - 0052's ~7x "impossible" jump was a genuine, publicly-disclosed 1-for-7 split that yfinance simply failed to record/misdated, not fabricated data. The fix (detect and adjust for the discontinuity) was right either way, but the "not real price action" claim was wrong until verified against TWSE directly.
- When `gridbot_body.py` gains a new file it writes for cross-run state (like `equity_snapshot.json`), it does nothing on its own — it must also be added to the workflow's persist step's `git add`/`git diff --quiet` list, or it silently resets to its last manually-committed value on every checkout. This is exactly what broke the daily drift check for weeks: `equity_snapshot.json` was written locally every run but never committed, so it stayed frozen at its 2026-08-14 value while the *baseline* diverged from real equity, false-triggering `sys.exit(1)` on every scheduled run. Cross-check the persist step's file list against every `misc.write_json(...)` call in `gridbot_body.py` whenever either changes.
- Before calling a tuned strategy's edge real, compare it to the dumbest equivalent (a constant mix at the strategy's own average allocation), and only call a metric "out-of-sample" if the parameters were chosen without that window - the bias-ratio tilt's "OOS Sharpe 1.71" came from params picked on the full history, and a no-signal 64/36 mix matched it (Sharpe 1.18 vs 1.16, 2016-2026), which is why the signal and `retune.py` were removed on 2026-11-16.
- Before trusting a large "drift"/P&L-jump number from a JSON state file (e.g. `equity_snapshot.json`), verify it against the bot's own contemporaneous `gridbot.log` (`totalcapital:` line) and TWSE's actual closing prices, not just the file's face value. The apparent ~40% equity crash implied by `equity_snapshot.json` going 55388.5 (2026-08-14) → 32889.69 (2026-09-05) was never real: `55388.5` was a hand-typed placeholder entered when the file was first created (commit `d806eb7`, added *after* that morning's run had already logged real `totalcapital: 33100.45`), not a bot-computed value. Real capital was ~33100 on 2026-08-14 and ~32437 on 2026-09-02 per `gridbot.log`, and TWSE closes for both tickers were roughly flat over that window — the bot's actual performance was flat, not crashing. Same class of mistake as the entry above (unverified state-file value treated as ground truth), just introduced at file-creation time instead of at persistence time.

## State that persists across runs

- `money.json` / `equity_snapshot.json` are committed back to `master` by CI after each run. A hard-cancelled CI job can't reach that commit step — after any forced cancel, reconcile `money.json` manually against actual broker fills before the next scheduled run. (This replaced an `actions/cache` pickle on 2026-07-14, which lost fills on forced cancellation and evicted after 7 idle days.)
- `backtest_stats.json` is not refreshed automatically — re-run `backtest.py` and commit it whenever `GridBot.TARGET_UPPER_ALLOC` changes.
- The drift alert's `regime=Bull/Bear/Sideways` tag is **annotation only, not a gate**. `backtest_by_regime()` puts the 65/35 mix at negative Sharpe in Bear (-1.3) and ~zero in Sideways, so a real Bear-regime alert may just mean "the strategy doesn't work well right now."
  - Gating checklist status (don't wire this into gating until all four hold, and re-check (4) periodically since it's thin evidence so far): (1) label stability — a flat threshold flip-flopped 17.5% of days (median 2-day regime run) on real 0052/00662 history; `label_regimes()`'s hysteresis cut that to 9.2%/4-day median, better but not proven "stable enough" to trust unattended. (2) repeat across independent historical bear windows — done 2026-09-18: 16 independent Bear episodes (2018 correction through 2025, `n_days>=15`); 11/16 (69%) negative, median episode Sharpe -1.24, including the 2020 COVID crash (-1.37) and every 2022 rate-hike leg (all < -1.2) — the pattern repeats, not a fluke of one window. (3) live/backtest agree with no lookahead — done 2026-09-18: `blended_price()`'s original anchor-date normalization made live (2-year fetch) and backtest (full-history) label the identical date differently; fixed by compounding daily returns instead. (4) audit every past drift alert against that day's regime label for a suppressed real bug — done 2026-09-18 for all 19 `gridbot.yml` runs (2026-08-06 to 2026-09-18). Exactly one alert ever fired: 2026-09-02 (z=-31.76), the hand-typed `equity_snapshot.json` baseline bug, labeled Sideways. n=1 alert over 6 weeks is thin evidence, not a real stress test of gating.

## When a drift alert fires

`gridbot.yml` exits non-zero. The optional Telegram push is a head start, not a replacement — the job's exit code is the authoritative signal. The workflow logs only to `gridbot.log` (in the `gridbot-logs-<run_id>` artifact), not the Action console.

Work through these in order — verify before concluding, cheapest/most-likely-cause first — rather than assuming the number is real:

1. **Pull the actual alert line.** Download the `gridbot-logs-<run_id>` artifact from that run, open `gridbot.log`, find `drift detected: pnl_pct=... mean=... std=... z=... regime=...`.
2. **Check the inputs before trusting the number.** Cross-check `equity_snapshot.json`'s baseline against the previous day's `gridbot.log` `totalcapital:` line, not just the file's face value — this is exactly what went wrong on 2026-09-02 (see Lessons). Confirm `backtest_stats.json`'s `target_upper_alloc` matches the live `GridBot.TARGET_UPPER_ALLOC`.
3. **Cross-check against reality.** Compare today's `totalcapital:` to yesterday's, and check TWSE's actual closing prices for 0052/00662 that day — did the market really move that much?
4. **Read the regime tag as urgency, not verdict.** `regime=Bear` → plausibly just a normal bad stretch for this strategy; lower urgency but still verify. `regime=Bull` → more suspicious; investigate harder. Never use it to auto-dismiss the alert.
5. **Check actual broker state.** Confirm real fills/holdings (`api.list_positions`/`list_trades`, or the SinoPac account directly) match what the bot's internal ledger thinks it has — this repo has a real history of ledger bugs (the `stockPrice`/`stockBid`/`stockAsk` aliasing bug in `gridbot.py`).
6. **Only after 2-5 are clean, treat it as real** — either genuine underperformance (a human call on whether to intervene, e.g. via `SJ_TAKE_PROFIT`; never automatic) or a real bug to fix.
7. **If it's a new failure mode, add a one-line Lesson** above so the same false trigger doesn't cost another investigation next time.

## Verification
- After refactors or bug fixes to trading logic, verify with dry-runs and cross-check against authoritative sources (e.g., TWSE) before considering the task complete.
