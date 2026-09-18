"""Quarterly cron entry point: grid-searches GridBot.parameters against
fresh price history and, if the new best params clear a minimum
out-of-sample sanity bar, rewrites them straight into gridbot.py and
refreshes backtest_stats.json for the live drift check.

    uv run python -m sj_trading.retune

Not run manually in normal use - backtest.py is still the interactive
research tool for exploring the grid by hand. This module only wraps it
for unattended, no-human-in-the-loop use from
.github/workflows/retune.yml. Because that means new parameters can go
live on the next scheduled gridbot run with no review, this file must
stay conservative about *when* it applies a change - see MIN_TEST_SHARPE
below - rather than about how it searches.
"""
import re
import sys

from sj_trading.backtest import (
    load_prices,
    grid_search,
    validate_out_of_sample,
    write_stats,
)
from sj_trading.gridbot import GridBot

GRIDBOT_PY = "src/sj_trading/gridbot.py"

# Refuse to auto-apply a candidate whose out-of-sample (held-out, most
# recent 30% of history) Sharpe doesn't clear this floor. Guards against
# grid_search finding a combo that's merely overfit to the in-sample
# period - NaN (too few trades/no variance) or a negative Sharpe both fail
# this, and the run falls back to re-snapshotting backtest_stats.json
# against the *current* production params instead of touching gridbot.py.
MIN_TEST_SHARPE = 0.5

PARAM_BLOCK_RE = re.compile(
    r"(# AUTO-RETUNE:START\n)(.*?)(    # AUTO-RETUNE:END)",
    re.DOTALL,
)


def _format_block(params: dict, train_res: dict, test_res: dict) -> str:
    import datetime

    comment = (
        "    # Auto-retuned by retune.py ({date}). In-sample Sharpe\n"
        "    # {train_sharpe:.2f}, out-of-sample Sharpe {test_sharpe:.2f}\n"
        "    # (floor: {floor}). See backtest_stats.json for the full\n"
        "    # snapshot.\n"
    ).format(
        date=datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        train_sharpe=train_res["sharpe"],
        test_sharpe=test_res["sharpe"],
        floor=MIN_TEST_SHARPE,
    )
    dict_block = (
        "    parameters = {{\n"
        '        "BiasUpperLimit": {BiasUpperLimit},\n'
        '        "UpperLimitPosition": {UpperLimitPosition},\n'
        '        "BiasLowerLimit": {BiasLowerLimit},\n'
        '        "LowerLimitPosition": {LowerLimitPosition},\n'
        '        "BiasPeriod": {BiasPeriod},\n'
        "    }}\n"
    ).format(**params)
    return comment + dict_block


def apply_params(params: dict, train_res: dict, test_res: dict) -> None:
    with open(GRIDBOT_PY) as f:
        src = f.read()

    replacement = _format_block(params, train_res, test_res) + "    # AUTO-RETUNE:END"
    new_src, n = PARAM_BLOCK_RE.subn(
        lambda m: m.group(1) + replacement, src
    )
    if n != 1:
        raise RuntimeError(
            f"expected exactly 1 AUTO-RETUNE block in {GRIDBOT_PY}, found {n} - "
            "markers may have been edited or removed"
        )
    with open(GRIDBOT_PY, "w") as f:
        f.write(new_src)


def main() -> int:
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
    if results.empty:
        print("grid_search returned no valid combos, leaving GridBot.parameters untouched")
        write_stats(df, GridBot.parameters)
        return 0

    best = results.sort_values("sharpe", ascending=False).iloc[0]
    best_params = {
        "BiasUpperLimit": float(best.BiasUpperLimit),
        "UpperLimitPosition": float(best.UpperLimitPosition),
        "BiasLowerLimit": float(best.BiasLowerLimit),
        "LowerLimitPosition": float(best.LowerLimitPosition),
        "BiasPeriod": int(best.BiasPeriod),
    }
    train_res, test_res = validate_out_of_sample(df, best_params)
    print("candidate params:", best_params)
    print("train (in-sample):", train_res)
    print("test (out-of-sample):", test_res)

    test_sharpe = test_res["sharpe"]
    if test_sharpe != test_sharpe or test_sharpe < MIN_TEST_SHARPE:  # NaN-safe
        print(
            f"out-of-sample Sharpe {test_sharpe} below floor {MIN_TEST_SHARPE} "
            "(or NaN) - keeping current GridBot.parameters, only refreshing "
            "backtest_stats.json"
        )
        write_stats(df, GridBot.parameters)
        return 0

    print("out-of-sample Sharpe clears floor - applying new parameters")
    apply_params(best_params, train_res, test_res)
    write_stats(df, best_params)
    return 0


if __name__ == "__main__":
    sys.exit(main())
