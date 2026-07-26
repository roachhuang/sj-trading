# Drift Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fail the scheduled CI job when a day's real broker P&L is a statistical outlier vs. the backtested daily-return distribution for the live `GridBot.parameters`, using GitHub Actions' own run-failure notification as the alert — no new secret, no new integration.

**Architecture:** `backtest.py` gains a function that snapshots the backtested daily-return distribution (mean/std) for the live params into a committed `backtest_stats.json`, refreshed manually. `gridbot_body.py` reads that file read-only at startup and, at each of its 3 existing end-of-day exit points, compares today's real P&L% against it via a small pure helper, then exits non-zero from `main()` if it's an outlier. The trading loop and order logic are untouched.

**Tech Stack:** Python, pytest, existing `shioaji`/`yfinance`/`pandas`/`numpy` deps already in `pyproject.toml` — no new dependencies.

## Global Constraints

- Outlier threshold: 3 standard deviations (spec-approved default for `is_pnl_outlier`).
- The `money.json` persist must happen in all 3 exit branches regardless of drift-check outcome or errors in the drift check itself.
- Drift check must degrade to "skip, don't fail" (return `None`) on: missing/corrupt `backtest_stats.json`, `std_daily_return` of `0`/NaN, or a failed `list_profit_loss`/`list_positions` fetch that day.
- No new alert channel (email/Slack/webhook) in this iteration — CI job failure is the only mechanism.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/` must stay green throughout.

Spec: `docs/superpowers/specs/2026-07-26-drift-monitoring-design.md`

---

### Task 1: `misc.is_pnl_outlier`

**Files:**
- Modify: `src/sj_trading/misc.py`
- Test: `tests/test_pnl_outlier.py` (new)

**Interfaces:**
- Produces: `misc.is_pnl_outlier(pnl_pct: float, stats: dict | None, threshold: float = 3.0) -> bool | None`. `stats`, when not `None`, is a dict with keys `"mean_daily_return"` (float) and `"std_daily_return"` (float).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pnl_outlier.py
"""is_pnl_outlier must degrade to 'no opinion' (None) on missing/degenerate
stats rather than false-triggering a CI failure on infra problems."""
import math

from sj_trading.misc import is_pnl_outlier


def test_within_threshold_is_not_outlier():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.01}
    # z = (0.005 - 0.001) / 0.01 = 0.4
    assert is_pnl_outlier(0.005, stats) is False


def test_beyond_threshold_is_outlier():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.01}
    # z = (0.05 - 0.001) / 0.01 = 4.9
    assert is_pnl_outlier(0.05, stats) is True


def test_exactly_at_threshold_is_not_outlier():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    # z = 0.03 / 0.01 = 3.0 exactly -> strictly-greater-than threshold, not tripped
    assert is_pnl_outlier(0.03, stats) is False


def test_just_beyond_threshold_is_outlier():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    assert is_pnl_outlier(0.030001, stats) is True


def test_missing_stats_returns_none():
    assert is_pnl_outlier(0.05, None) is None


def test_zero_std_returns_none():
    stats = {"mean_daily_return": 0.001, "std_daily_return": 0.0}
    assert is_pnl_outlier(0.05, stats) is None


def test_nan_std_returns_none():
    stats = {"mean_daily_return": 0.001, "std_daily_return": float("nan")}
    assert is_pnl_outlier(0.05, stats) is None


def test_custom_threshold():
    stats = {"mean_daily_return": 0.0, "std_daily_return": 0.01}
    # z = 2.0
    assert is_pnl_outlier(0.02, stats, threshold=1.5) is True
    assert is_pnl_outlier(0.02, stats, threshold=2.5) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_pnl_outlier.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_pnl_outlier'`

- [ ] **Step 3: Implement `is_pnl_outlier`**

Add to `src/sj_trading/misc.py` (add `import math` to the existing top-of-file imports alongside `date, datetime, timedelta` and `json`):

```python
def is_pnl_outlier(pnl_pct: float, stats: dict | None, threshold: float = 3.0) -> bool | None:
    """True if pnl_pct is beyond `threshold` std devs from the backtested
    daily-return distribution in `stats`. None means "no opinion" - stats
    unavailable or too degenerate to judge, never treated as an outlier."""
    if stats is None:
        return None
    std = stats["std_daily_return"]
    if not std or math.isnan(std):
        return None
    mean = stats["mean_daily_return"]
    z = abs((pnl_pct - mean) / std)
    return z > threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_pnl_outlier.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/sj_trading/misc.py tests/test_pnl_outlier.py
git commit -m "Add misc.is_pnl_outlier for backtest-vs-live P&L drift checks"
```

---

### Task 2: `backtest.py` — expose daily distribution + `write_stats`

**Files:**
- Modify: `src/sj_trading/backtest.py`
- Test: `tests/test_backtest_stats.py` (new)

**Interfaces:**
- Consumes: nothing new from Task 1.
- Produces: `backtest()`'s returned dict gains two new keys `daily_mean: float` and `daily_std: float` (in addition to its existing `total_return`/`ann_return`/`ann_vol`/`sharpe`/`max_dd`/`n_days` keys). New function `write_stats(df: pd.DataFrame, params: dict, path: str = "backtest_stats.json") -> dict` that runs `backtest()` and writes/returns `{"mean_daily_return": float, "std_daily_return": float, "generated_at": str, "params": dict}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backtest_stats.py
"""backtest() must expose the daily-return distribution it already computes
internally (previously discarded after deriving sharpe/ann_vol), and
write_stats() must persist it in the shape gridbot_body.py's drift check
expects (mean_daily_return/std_daily_return, matching misc.is_pnl_outlier's
stats dict)."""
import json

import numpy as np
import pandas as pd

from sj_trading.backtest import backtest, write_stats

PARAMS = {
    "BiasUpperLimit": 1.4,
    "UpperLimitPosition": 0.35,
    "BiasLowerLimit": 0.70,
    "LowerLimitPosition": 0.80,
    "BiasPeriod": 30,
}


def _synthetic_df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    upper = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, n))
    lower = 50 * np.cumprod(1 + rng.normal(0.0001, 0.008, n))
    return pd.DataFrame({"upper": upper, "lower": lower}, index=dates)


def test_backtest_returns_finite_daily_mean_and_std():
    df = _synthetic_df()
    ratio = df["upper"] / df["lower"]
    result = backtest(df, ratio, PARAMS)
    assert result is not None
    assert np.isfinite(result["daily_mean"])
    assert np.isfinite(result["daily_std"])
    assert result["daily_std"] > 0


def test_write_stats_writes_expected_shape(tmp_path):
    df = _synthetic_df()
    path = tmp_path / "backtest_stats.json"
    stats = write_stats(df, PARAMS, path=str(path))

    assert set(stats) == {"mean_daily_return", "std_daily_return", "generated_at", "params"}
    assert stats["params"] == PARAMS

    with open(path) as f:
        on_disk = json.load(f)
    assert on_disk == stats


def test_write_stats_matches_backtest_daily_stats(tmp_path):
    df = _synthetic_df()
    ratio = df["upper"] / df["lower"]
    direct = backtest(df, ratio, PARAMS)

    path = tmp_path / "backtest_stats.json"
    stats = write_stats(df, PARAMS, path=str(path))

    assert stats["mean_daily_return"] == direct["daily_mean"]
    assert stats["std_daily_return"] == direct["daily_std"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_backtest_stats.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_stats'` (and/or `KeyError: 'daily_mean'` once that's fixed)

- [ ] **Step 3: Implement**

In `src/sj_trading/backtest.py`, modify the `return` at the end of `backtest()` (currently `return {"total_return": ..., "ann_return": ..., "ann_vol": ..., "sharpe": ..., "max_dd": ..., "n_days": len(eq)}`, right after `daily_ret = eq.pct_change().dropna()` is computed) to add the two new keys:

```python
    return {
        "total_return": total_return,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_days": len(eq),
        "daily_mean": float(daily_ret.mean()),
        "daily_std": float(daily_ret.std()),
    }
```

Add near the bottom of the file, after `validate_out_of_sample` and before the `if __name__ == "__main__":` block:

```python
import datetime
import json


def write_stats(df: pd.DataFrame, params: dict, path: str = "backtest_stats.json") -> dict:
    """Snapshots the backtested daily-return distribution for `params` to
    `path`, in the shape misc.is_pnl_outlier's `stats` argument expects.
    Run manually on the same cadence as re-tuning GridBot.parameters -
    not invoked automatically by the live bot or CI."""
    ratio = df["upper"] / df["lower"]
    result = backtest(df, ratio, params)
    stats = {
        "mean_daily_return": result["daily_mean"],
        "std_daily_return": result["daily_std"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params": params,
    }
    with open(path, "w") as f:
        json.dump(stats, f)
    return stats
```

(Move the `import datetime` and `import json` to the existing top-of-file import block instead, alongside `itertools`/`math`/`time`, rather than inline — keep the file's existing import style.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_backtest_stats.py -v`
Expected: 3 passed

- [ ] **Step 5: Wire `write_stats` into the existing `__main__` block**

At the end of `src/sj_trading/backtest.py`'s `if __name__ == "__main__":` block (after the existing "Buy & hold 50/50" print), add:

```python
    print("\n=== Writing backtest_stats.json for live drift monitoring ===")
    stats = write_stats(df, GridBot.parameters)
    print(stats)
```

- [ ] **Step 6: Run full test suite to check no regressions**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/ -v`
Expected: all passing (8 from Task 1 + 3 new + pre-existing 26 = 37 passed)

- [ ] **Step 7: Commit**

```bash
git add src/sj_trading/backtest.py tests/test_backtest_stats.py
git commit -m "Add backtest.write_stats to snapshot the daily-return distribution for drift monitoring"
```

---

### Task 3: Wire drift check into `gridbot_body.py`

**Files:**
- Modify: `src/sj_trading/gridbot_body.py`
- Test: `tests/test_pnl_drift_gate.py` (new)

**Interfaces:**
- Consumes: `misc.is_pnl_outlier(pnl_pct, stats, threshold=3.0) -> bool | None` (Task 1).
- Produces: module-level pure function `evaluate_daily_drift(realized: float, unrealized: float, totalcapital: float, fetch_ok: bool, stats: dict | None) -> bool | None` in `gridbot_body.py`, kept separate from `log_daily_pnl` specifically so it's testable without mocking the Shioaji API. `GridbotBody(api)` now returns `bool | None` (the last drift-check result observed before exit). `main()` calls `sys.exit(1)` when that's `True`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pnl_drift_gate.py
"""evaluate_daily_drift is the pure decision core behind gridbot_body's
end-of-day drift check - kept separate from log_daily_pnl (which does live
Shioaji API calls) so this logic is testable without mocking the broker
API. A data-fetch failure or a zero/absent capital base must never be
treated as a drift signal."""
from sj_trading.gridbot_body import evaluate_daily_drift

STATS = {"mean_daily_return": 0.0, "std_daily_return": 0.01}


def test_normal_day_is_not_outlier():
    # pnl_pct = 500/100000 = 0.005, z = 0.5
    assert evaluate_daily_drift(realized=300, unrealized=200, totalcapital=100_000,
                                 fetch_ok=True, stats=STATS) is False


def test_extreme_day_is_outlier():
    # pnl_pct = 5000/100000 = 0.05, z = 5.0
    assert evaluate_daily_drift(realized=5000, unrealized=0, totalcapital=100_000,
                                 fetch_ok=True, stats=STATS) is True


def test_failed_fetch_never_flags_outlier():
    assert evaluate_daily_drift(realized=999_999, unrealized=0, totalcapital=100_000,
                                 fetch_ok=False, stats=STATS) is None


def test_missing_stats_returns_none():
    assert evaluate_daily_drift(realized=5000, unrealized=0, totalcapital=100_000,
                                 fetch_ok=True, stats=None) is None


def test_zero_totalcapital_returns_none():
    assert evaluate_daily_drift(realized=100, unrealized=0, totalcapital=0,
                                 fetch_ok=True, stats=STATS) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_pnl_drift_gate.py -v`
Expected: FAIL with `ImportError: cannot import name 'evaluate_daily_drift'`

- [ ] **Step 3: Implement `evaluate_daily_drift` and wire it in**

Add to `src/sj_trading/gridbot_body.py`, module level (alongside the existing `g_upperid`/`TICKERS` constants near the top, after the `import` block — add `import sys` to the existing imports too):

```python
def evaluate_daily_drift(realized: float, unrealized: float, totalcapital: float,
                          fetch_ok: bool, stats: dict | None) -> bool | None:
    """Pure decision core for the end-of-day P&L drift check - no API calls,
    so it's directly testable. None means 'skip, don't fail the job':
    a failed data fetch or a zero capital base must never look like
    drift."""
    if not fetch_ok or not totalcapital:
        return None
    pnl_pct = (realized + unrealized) / totalcapital
    return misc.is_pnl_outlier(pnl_pct, stats)
```

Then, inside `GridbotBody(api)`:

1. After the existing `bot1.start_cash = misc.read_json('money.json')` try/except block (around line 56-59), add reading the backtest stats:

```python
    try:
        backtest_stats = misc.read_json('backtest_stats.json')
    except Exception as e:
        logging.warning(f"backtest_stats.json unavailable, skipping drift check: {e}")
        backtest_stats = None
```

2. Replace the body of `log_daily_pnl()` (currently lines 76-97) so it tracks fetch success and returns the drift result:

```python
    def log_daily_pnl():
        today = datetime.date.today().isoformat()
        fetch_ok = True
        try:
            realized_list = api.list_profit_loss(
                api.stock_account, begin_date=today, end_date=today, unit=sj.Unit.Share
            )
            realized = sum(p.pnl for p in realized_list if p.code in TICKERS)
        except Exception as e:
            logging.error(f"list_profit_loss failed: {e}")
            realized = 0
            fetch_ok = False

        try:
            positions = api.list_positions(api.stock_account, unit=sj.Unit.Share)
            unrealized = sum(p.pnl for p in positions if p.code in TICKERS)
        except Exception as e:
            logging.error(f"list_positions failed: {e}")
            unrealized = 0
            fetch_ok = False

        logging.info(
            f"daily P&L (TICKERS): realized={realized:.2f}, unrealized={unrealized:.2f}, "
            f"total={realized + unrealized:.2f}"
        )

        drift = evaluate_daily_drift(realized, unrealized, totalcapital, fetch_ok, backtest_stats)
        if drift:
            pnl_pct = (realized + unrealized) / totalcapital
            mean, std = backtest_stats["mean_daily_return"], backtest_stats["std_daily_return"]
            z = (pnl_pct - mean) / std
            logging.error(
                f"drift detected: pnl_pct={pnl_pct:.4f} mean={mean:.4f} std={std:.4f} z={z:.2f}"
            )
        return drift
```

3. Update the 3 call sites to capture and propagate the result. There's a variable already implicitly available at each: the safety-net branch (`if (hour >= 14):`), the normal-exit branch (`if (hour == 13 and minute > 20):`), and the `except KeyboardInterrupt:` block. Introduce `drift_result = None` immediately before the `try: while (1):` loop starts, then in each of the 3 branches replace the bare `log_daily_pnl()` call with `drift_result = log_daily_pnl()`:

```python
    drift_result = None
    try:
        while (1):
            ...
            if (hour >= 14):
                drift_result = log_daily_pnl()
                ...
            if (hour == 13 and minute > 20):
                try:
                    bot1.cancelOrders()
                    drift_result = log_daily_pnl()
                    ...
    except KeyboardInterrupt:
        ...
        drift_result = log_daily_pnl()
        ...
```

4. At the very end of `GridbotBody(api)` (after the `except KeyboardInterrupt:` block, currently the last lines of the function), add:

```python
    return drift_result
```

5. In `main()`, after `GridbotBody(api)` is called (currently `GridbotBody(api)` on its own line, around line 264), capture the result and exit after logout:

```python
    # starting point of the code running
    drift_result = GridbotBody(api)

    # GridbotBody returns once its internal loop reaches ~14:00-15:00.
    # Log out and exit here so a scheduled run (e.g. triggered once per
    # trading day) terminates instead of waiting for a 16:00 reboot or
    # looping until Friday.
    try:
        api.logout()
    except Exception as e:
        logging.error(f"failed to call api.logout: {e}")

    if drift_result:
        sys.exit(1)
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_pnl_drift_gate.py -v`
Expected: 5 passed

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/ -v`
Expected: all passing (37 from Task 2 + 5 new = 42 passed)

- [ ] **Step 6: Manual dry-run verification**

`GridbotBody`'s loop and `main()` aren't unit-testable without a live/simulated Shioaji session (consistent with the rest of this file — it has no existing test coverage either, only `gridbot.py`'s `GridBot` class does). Per this project's verification convention, dry-run it in simulation before considering this task done:

```bash
uv run python -m sj_trading.gridbot_body
```

(needs `SJ_API_KEY`/`SJ_SEC_KEY` in `.env`, `SJ_PRODUCTION` unset or not `"true"` so it runs in simulation). Confirm in `gridbot.log`:
- No new tracebacks introduced by this change.
- If `backtest_stats.json` doesn't exist yet in the working directory (it won't, until Task 4 generates it), confirm the log shows `backtest_stats.json unavailable, skipping drift check` and the run still completes and persists `money.json` normally, with `echo $?` after the run showing exit code `0`.

- [ ] **Step 7: Commit**

```bash
git add src/sj_trading/gridbot_body.py tests/test_pnl_drift_gate.py
git commit -m "Fail CI job on live-vs-backtest daily P&L drift beyond 3 std devs"
```

---

### Task 4: Generate and commit the initial `backtest_stats.json`

**Files:**
- Create: `backtest_stats.json` (repo root, committed — same pattern as `money.json`)

This is the operational step that makes Task 3's drift check actually active (before this, it always returns `None` and skips, per Task 3 Step 6). Run it once params are already validated (it snapshots whatever `GridBot.parameters` currently is — no code change needed here, just running the tool built in Task 2).

- [ ] **Step 1: Run the backtest script to generate the file**

```bash
uv run python -m sj_trading.backtest
```

This runs the full grid search (existing behavior, unchanged) and, from Task 2 Step 5's addition, also writes `backtest_stats.json` for the live `GridBot.parameters` at the end. Confirm the final printed block shows `mean_daily_return`/`std_daily_return`/`generated_at`/`params` matching `GridBot.parameters` in `src/sj_trading/gridbot.py`.

- [ ] **Step 2: Sanity-check the file**

```bash
cat backtest_stats.json
```

Confirm `std_daily_return` is a small positive number (e.g. on the order of `0.001`-`0.02` for a daily-return std — if it's `0` or absent, something upstream broke and Task 3's check will just skip forever, silently defeating the whole feature).

- [ ] **Step 3: Commit**

```bash
git add backtest_stats.json
git commit -m "Generate initial backtest_stats.json for live drift monitoring"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture (Tasks 2+3), Components (`backtest.py`→Task 2, `misc.py`→Task 1, `gridbot_body.py`→Task 3), Data flow (Task 3 steps 1-5), Error handling (Task 1's `None`-on-degenerate-stats + Task 3's `fetch_ok`/`totalcapital` guards), Testing (Tasks 1-3 each include unit tests) are all covered. The spec's "Alternatives considered" section is explicitly not implemented (by design).
- **Placeholder scan:** none found — every step has real code.
- **Type consistency:** `is_pnl_outlier(pnl_pct, stats, threshold=3.0) -> bool | None` (Task 1) is called identically in Task 3's `evaluate_daily_drift`. `stats` dict keys (`mean_daily_return`/`std_daily_return`) match between Task 1's test fixtures, Task 2's `write_stats` output, and Task 3's usage. `GridbotBody(api) -> bool | None` return type matches what `main()` checks in Task 3 Step 3.5.
