#!/usr/bin/env bash
# Driver for sj-trading. Run from the repo root (paths are relative to
# there). Requires `uv sync` to have been run at least once.
#
# Usage:
#   .claude/skills/run-sj-trading/driver.sh backtest-quick
#   .claude/skills/run-sj-trading/driver.sh backtest-full
#   .claude/skills/run-sj-trading/driver.sh bot-smoke [timeout_seconds]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

cmd="${1:-}"

case "$cmd" in
  backtest-quick)
    # No credentials needed. Direct-invocation smoke test: imports the
    # library and runs one backtest + one buy&hold comparison over a
    # short (1y) window - proves the calculateSharetarget/sendOrders
    # simulation logic and data loading work, in ~1-2s, without
    # rewriting backtest_stats.json.
    uv run python -c "
from sj_trading.backtest import load_prices, backtest, buy_and_hold
df = load_prices(period='1y')
print(f'loaded {len(df)} rows: {df.index.min().date()} to {df.index.max().date()}')
print('backtest:', backtest(df, 0.65))
print('buy&hold:', buy_and_hold(df))
"
    ;;

  backtest-full)
    # The actual documented CLI entry point: full-history backtest of the
    # live allocation next to a few other fixed splits, per-regime
    # breakdown, and it REWRITES backtest_stats.json. Takes a few seconds.
    uv run python -m sj_trading.backtest
    ;;

  bot-smoke)
    # Requires real Shioaji credentials in .env (SJ_API_KEY/SJ_SEC_KEY).
    # Runs the live/simulated bot's actual entry point just long enough
    # to prove login -> contract fetch -> quote subscribe -> GridBot
    # bootstrap all succeed, then kills it (the bot's own loop only
    # exits at specific Taipei wall-clock hours, so it must be
    # externally time-boxed for a smoke test).
    #
    # IMPORTANT: must run unbuffered (python -u), or Python's own
    # print() output (positions/init money/etc.) never reaches the
    # timeout-killed process's stdout - only the Shioaji Rust core's
    # direct fd writes (login/session/subscribe events) do, since those
    # bypass Python's buffering entirely. This looks exactly like a
    # hang otherwise.
    timeout_s="${2:-30}"
    timeout -k 3 "$timeout_s" uv run python -u -m sj_trading.gridbot_body
    ;;

  *)
    echo "usage: $0 {backtest-quick|backtest-full|bot-smoke [timeout_seconds]}" >&2
    exit 1
    ;;
esac
