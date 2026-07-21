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


def test_persist_money_skips_git_outside_ci(tmp_path, monkeypatch):
    """Outside CI, persist_money must only write the file - never shell out
    to git, so a local/manual run can't accidentally push to the remote."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append((a, k)))
    path = tmp_path / "money.json"

    misc.persist_money(str(path), 12345)

    assert misc.read_json(str(path)) == 12345
    assert calls == []


def test_persist_money_commits_and_pushes_in_ci(tmp_path, monkeypatch):
    """In CI, an actual value change must be committed and pushed (mocked -
    this must never invoke real git)."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    commands = []

    class FakeCompletedProcess:
        returncode = 1  # non-zero = git diff found a change

    def fake_run(cmd, *a, **k):
        commands.append(cmd)
        return FakeCompletedProcess()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("subprocess.check_output", lambda *a, **k: "master\n")
    path = tmp_path / "money.json"

    misc.persist_money(str(path), 500)

    assert misc.read_json(str(path)) == 500
    assert any(cmd[:2] == ["git", "push"] for cmd in commands)


def test_persist_money_skips_commit_when_value_unchanged(tmp_path, monkeypatch):
    """git diff --quiet returning 0 (no change) must short-circuit before
    any commit/push command runs."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    commands = []

    class FakeCompletedProcess:
        returncode = 0  # no diff

    def fake_run(cmd, *a, **k):
        commands.append(cmd)
        return FakeCompletedProcess()

    monkeypatch.setattr("subprocess.run", fake_run)
    path = tmp_path / "money.json"

    misc.persist_money(str(path), 500)

    assert commands == [["git", "diff", "--quiet", "--", str(path)]]
