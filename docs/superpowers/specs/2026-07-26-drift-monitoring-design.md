# Live-vs-backtest daily P&L drift monitoring

## Problem

`gridbot_body.py`'s `log_daily_pnl()` already computes real broker realized/unrealized
P&L for the two gridbot tickers at the end of each trading day, but only logs it —
nothing compares it against what the backtest (`backtest.py`, `GridBot.parameters`)
says a normal day should look like. If live behavior drifts from the backtested
strategy (bug, broken data feed, regime shift the strategy wasn't tuned for), nothing
surfaces it beyond manually reading `gridbot.log`.

## Goal

Fail the scheduled CI job (non-zero exit) when a day's real P&L is a statistical
outlier vs. the backtested daily-return distribution for the currently-live
`GridBot.parameters`, so a red X + GitHub's existing run-failure notification is the
alert channel — no new secret, no new integration.

This is a post-hoc, end-of-day check only. It never influences same-day trading
decisions or blocks order placement.

## Non-goals

- Not a rolling/cumulative drift detector (single-day-outlier only, see Alternatives).
- Not a new alert channel (email/Slack/webhook) — CI job failure is the whole
  notification mechanism for this iteration.
- Not a live/real-time check — evaluated only at the existing end-of-day exit points.

## Design

### Architecture

`backtest.py` gains a function to snapshot the backtested daily-return distribution
for the live `GridBot.parameters` into a new committed `backtest_stats.json`, refreshed
manually on the same cadence as the existing parameter re-tune (CLAUDE.md: every
6-12 months, or sooner on divergence). `gridbot_body.py` reads that file read-only at
startup and compares today's actual P&L% against it at the existing end-of-day exit
points. No change to the trading loop itself.

### Components

**`backtest.py`**
- `backtest()` already computes a `daily_ret` series internally (currently discarded
  after deriving `sharpe`/`ann_vol`). Expose its mean and std in the returned dict as
  `daily_mean` / `daily_std`.
- New `write_stats(df, params, path="backtest_stats.json")`: runs `backtest()` against
  the given params, writes `{"mean_daily_return": ..., "std_daily_return": ...,
  "generated_at": <ISO8601>, "params": {...}}` to `path`. Called manually (e.g. a
  `--write-stats` flag on the existing `__main__`, or a one-off invocation) — not run
  automatically by CI, since it's tied to the manual param-retune cadence, not daily
  runs.

**`misc.py`**
- New pure function `is_pnl_outlier(pnl_pct: float, stats: dict | None, threshold:
  float = 3.0) -> bool | None`:
  - Returns `None` if `stats` is `None`, or `stats["std_daily_return"]` is `0`/`NaN`
    (can't judge — not a failure, just "no opinion").
  - Otherwise returns `abs((pnl_pct - stats["mean_daily_return"]) /
    stats["std_daily_return"]) > threshold`.

**`gridbot_body.py`**
- At startup: `stats = misc.read_json("backtest_stats.json")`, defaulting to `None` on
  missing/corrupt file (log a warning, not an error — this is a missing-infra
  condition, not a trading problem).
- `log_daily_pnl()`:
  - Computes `pnl_pct = (realized + unrealized) / totalcapital` only when both
    `list_profit_loss` and `list_positions` fetches succeeded that call. If either
    fetch failed, skip the outlier check entirely (return `None`) — a data-fetch bug
    must not masquerade as strategy drift.
  - Calls `misc.is_pnl_outlier(pnl_pct, stats)`; logs `ERROR` with the z-score detail
    if `True`.
  - Returns the tri-state result (`True` / `False` / `None`) to its caller.
- `GridbotBody(api)` returns whatever `log_daily_pnl()`'s last call returned (each of
  the 3 existing exit branches — 13:21 normal exit, `hour>=14` safety net,
  `KeyboardInterrupt` — already calls `log_daily_pnl()` right before persisting
  `money.json`; each now also propagates that return value out of `GridbotBody`).
- `main()`: after `api.logout()`, `sys.exit(1)` if `GridbotBody(api)` returned `True`.
  `False`/`None` leave the process to exit 0 as today.

### Data flow

```
startup:
  read backtest_stats.json -> stats (None if missing/corrupt; logs a warning)

end-of-day (any of the 3 existing exit points):
  cancelOrders() / skip if safety-net or KeyboardInterrupt path (unchanged)
  log_daily_pnl():
    realized, unrealized = live broker queries (existing, each already has its own
      try/except defaulting to 0 - reused unchanged; fetch success is now also
      tracked)
    if both fetches succeeded:
      pnl_pct = (realized + unrealized) / totalcapital
      outlier = misc.is_pnl_outlier(pnl_pct, stats)
      if outlier: log ERROR with z-score
    else:
      outlier = None
    return outlier
  write_json("money.json", live_cash_right_now)   <- always happens, regardless of
                                                       outlier result
  (KeyboardInterrupt path also calls api.logout() here, same as today)

main(), after GridbotBody(api) returns and api.logout() runs:
  if result is True: sys.exit(1)
```

### Error handling

Skip (never fail the job for) these conditions — they're infra/data problems, not
drift signals:
- `backtest_stats.json` missing or fails to parse.
- `std_daily_return` is `0` or `NaN` (degenerate backtest window).
- Either `list_profit_loss` or `list_positions` raised that day.

The `money.json` persist must happen in every one of the 3 exit branches regardless
of any of the above — cash accounting correctness is unconditional; the drift check
is purely additive and must never risk skipping or delaying it.

### Testing

New unit tests in `tests/` (no live API/network needed, matches the existing
safety-invariant suite's style):
- `is_pnl_outlier`: normal in-range case (`False`), beyond-threshold case (`True`),
  `stats=None` (`None`), `std_daily_return=0` (`None`), exact boundary at
  `threshold` (documents whether `>` is strict).
- `backtest()`'s new `daily_mean`/`daily_std` keys present and finite on a synthetic
  price series with known distribution.

## Alternatives considered

- **B: compute the reference distribution live at every run's startup** instead of
  from a checked-in file. Rejected — adds a real yfinance fetch + backtest computation
  to the live path's critical startup, duplicating logic already in `backtest.py` for
  no accuracy benefit (the backtest itself is only as fresh as the last manual
  parameter retune anyway).
- **C: rolling live-only baseline** (track live daily P&L history, compare against its
  own trailing distribution, no backtest reference at all). Rejected — needs weeks of
  live data to bootstrap, and answers a different question ("is today unusual vs.
  recent live days") than the one asked ("is live diverging from what the backtest
  promised").
- **Rolling/cumulative drift** (vs. single-day-outlier). Deferred, not rejected — the
  user chose single-day-outlier for this iteration; cumulative drift is a plausible
  follow-up spec once single-day is live and proven useful.
- **External alert channel** (Slack/Discord/email webhook) instead of CI-job-failure.
  Deferred — CI failure + GitHub's built-in notification costs nothing new to build;
  a webhook is a natural extension if CI-failure proves too easy to miss in practice.
