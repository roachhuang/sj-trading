"""money.json seed/commit round-trip must preserve the value exactly -
this is the sole record of live capital across daily CI runs."""
import pytest

from sj_trading import misc


@pytest.mark.parametrize("value", [0, 30000, 123456, -500, 99999.0, 0.0])
def test_round_trip_preserves_value_exactly(tmp_path, value):
    path = tmp_path / "money.json"
    misc.write_json(str(path), value)
    assert misc.read_json(str(path)) == value


def test_round_trip_survives_seed_then_overwrite(tmp_path):
    """Mirrors set_init_invest_amt.py seeding, then a normal CI commit-back
    overwrite - the second write must fully replace, not merge with, the first."""
    path = tmp_path / "money.json"
    misc.write_json(str(path), 30000)
    assert misc.read_json(str(path)) == 30000

    misc.write_json(str(path), 27431)
    assert misc.read_json(str(path)) == 27431


def test_read_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        misc.read_json(str(tmp_path / "does_not_exist.json"))


def test_read_corrupt_json_raises_value_error(tmp_path):
    path = tmp_path / "money.json"
    path.write_text("{not valid json")
    with pytest.raises(ValueError):
        misc.read_json(str(path))
