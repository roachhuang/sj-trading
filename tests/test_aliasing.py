"""stockPrice/stockBid/stockAsk must be 3 independent dicts - a prior
aliasing bug chained them together so every write collapsed onto
whichever was assigned last, silently turning stockPrice/stockBid into
copies of the ask price."""
from unittest.mock import MagicMock

from sj_trading.gridbot import GridBot


def make_bot():
    return GridBot(MagicMock(), MagicMock())


def test_price_dicts_are_distinct_objects():
    bot = make_bot()
    assert bot.stockPrice is not bot.stockBid
    assert bot.stockBid is not bot.stockAsk
    assert bot.stockPrice is not bot.stockAsk


def test_writing_one_dict_does_not_mutate_the_others():
    bot = make_bot()
    bot.stockPrice["0052"] = 100.0
    bot.stockBid["0052"] = 99.5
    bot.stockAsk["0052"] = 100.5

    assert bot.stockPrice["0052"] == 100.0
    assert bot.stockBid["0052"] == 99.5
    assert bot.stockAsk["0052"] == 100.5


def test_new_bot_instances_do_not_share_price_dicts():
    """Guards against a class-level mutable default (shared across all
    instances) reintroducing the same class of aliasing bug."""
    bot_a = make_bot()
    bot_b = make_bot()
    bot_a.stockPrice["0052"] = 1.0
    assert "0052" not in bot_b.stockPrice
