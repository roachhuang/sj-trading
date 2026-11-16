The bot holds a fixed 65% 0052 / 35% 00662 split (GridBot.TARGET_UPPER_ALLOC in gridbot.py). If you change it, run `uv run python -m sj_trading.backtest` and commit the new backtest_stats.json.

source .venv/bin/activate or 
. venv/bin/activate

profit taking:
Actions tab → left sidebar → click exact workflow name "Gridbot Daily Run" (not "All workflows"). Dropdown only shows on that workflow's own page, top right, same row as "Filter workflow runs".