"""Tests for unified persistence (tetris.state: scores + settings)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tetris.pieces import MAX_START_LEVEL
from tetris.settings import Settings
from tetris.state import (
    MAX_NAME,
    REPLAYS_FILE,
    GameState,
    HighScores,
    default_state_path,
)

_LEGACY_ENTRY = {"score": 420, "lines": 4, "level": 1, "date": "2024-01-01 10:00"}


def test_alias() -> None:
    assert HighScores is GameState


def test_score_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    assert gs.record(500, 2, 1) == 0
    assert gs.record(1000, 5, 2) == 0
    assert gs.record(200, 1, 1) == 2

    reloaded = GameState(path)
    assert [e["score"] for e in reloaded.entries] == [1000, 500, 200]
    assert reloaded.best() == 1000


def test_best_empty_is_zero(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    assert gs.best() == 0
    assert gs.entries == []


def test_record_and_rank(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    assert gs.record(500, 2, 1) == 0
    assert gs.record(1000, 5, 1) == 0  # higher score takes rank 1
    assert gs.record(200, 1, 1) == 2


def test_keeps_top_ten(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    for score in (10, 50, 30, 90, 20, 70, 40, 80, 60, 30, 15):
        gs.record(score, 1, 1)
    assert [e["score"] for e in gs.entries] == [
        90, 80, 70, 60, 50, 40, 30, 30, 20, 15,
    ]  # 11th score (10) dropped
    assert gs.record(5, 0, 1) is None  # below the top 10


def test_record_sprint_best(tmp_path: Path) -> None:
    """P11: sprint best time persists in its own block, lower-is-better,
    and never touches the score high-score table."""
    path = tmp_path / "state.json"
    gs = GameState(path)
    assert gs.best_sprint_time() is None

    # First clear establishes the best.
    is_new, best = gs.record_sprint(95.5)
    assert is_new is True
    assert best == 95.5
    assert gs.best_sprint_time() == 95.5

    # A faster time replaces it.
    is_new, best = gs.record_sprint(80.2)
    assert is_new is True
    assert best == 80.2

    # A slower time is rejected (no new best) and does not overwrite.
    is_new, best = gs.record_sprint(90.0)
    assert is_new is False
    assert best == 80.2

    # Sprint recording must not write to the score table.
    assert gs.entries == []

    # Persisted as its own additive block, round-trips on reload.
    raw = json.loads(path.read_text())
    assert isinstance(raw.get("sprint"), dict)
    assert raw["sprint"]["best_time"] == 80.2
    reloaded = GameState(path)
    assert reloaded.best_sprint_time() == 80.2


def test_record_sprint_tolerates_corrupt(tmp_path: Path) -> None:
    """A malformed sprint block is treated as "no best" (tolerant read)."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"scores": [], "settings": {}, "sprint": "bogus"}))
    gs = GameState(path)
    assert gs.best_sprint_time() is None
    is_new, best = gs.record_sprint(60.0)
    assert is_new is True
    assert best == 60.0


def test_replay_save_and_last(tmp_path: Path) -> None:
    """P9: save_replay appends to replays.json (next to the state file),
    keeps the 5 most recent, and last_replay returns the newest. A
    corrupt/missing log is tolerated and a fresh save heals it."""
    path = tmp_path / "state.json"
    gs = GameState(path)
    assert gs.last_replay() is None

    for i in range(7):
        gs.save_replay({"seed": i, "start_level": 1, "events": [[0.1, "L"]]})

    log = tmp_path / REPLAYS_FILE
    data = json.loads(log.read_text())
    assert len(data) == 5  # the 5 most recent
    assert [e["seed"] for e in data] == [2, 3, 4, 5, 6]
    assert gs.last_replay()["seed"] == 6

    # A corrupt log is tolerated (no raise) and the next save heals it.
    log.write_text("{not json")
    assert gs.last_replay() is None
    gs.save_replay({"seed": 99, "start_level": 1, "events": []})
    assert gs.last_replay()["seed"] == 99

    # A non-list log is treated as empty.
    log.write_text(json.dumps({"oops": True}))
    assert gs.last_replay() is None


def test_replays_accessor_is_cached_until_next_save(monkeypatch, tmp_path: Path) -> None:
    """R4: replays() — the per-frame display path — serves the parsed log
    from memory while it is unchanged by us; a save_replay invalidates
    the cache so the next display re-reads the file."""
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.save_replay({"seed": 1, "start_level": 1, "events": []})

    real = GameState._read_replays_file
    reads = 0

    def counting(self: GameState) -> list[dict[str, object]]:
        nonlocal reads
        reads += 1
        return real(self)

    monkeypatch.setattr(GameState, "_read_replays_file", counting)

    reads = 0
    first = gs.replays()
    second = gs.replays()
    third = gs.replays()
    assert reads == 1  # one read, then served from the cache
    assert first == second == third

    gs.save_replay({"seed": 2, "start_level": 1, "events": []})
    reads = 0
    fresh = gs.replays()
    assert reads == 1  # the save invalidated the cache; re-read
    assert [e["seed"] for e in fresh] == [2, 1]  # newest first


def test_record_stores_name(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    assert gs.record(300, 3, 1, name="ADA ") == 0

    reloaded = GameState(path)
    assert reloaded.entries[0]["name"] == "ADA"


def test_record_stores_time_and_best_combo(tmp_path: Path) -> None:
    """P7c: record() persists the game's time and best combo as additive
    keys; a legacy entry (no such keys) still loads and sorts fine."""
    path = tmp_path / "state.json"
    gs = GameState(path)
    assert gs.record(300, 3, 1, time_s=125.432, best_combo=4) == 0

    reloaded = GameState(path)
    entry = reloaded.entries[0]
    assert entry["time"] == 125.4  # rounded to 1 decimal
    assert entry["best_combo"] == 4

    # A legacy entry lacking the new keys stays valid (best() reads score).
    import json

    path.write_text(json.dumps({"scores": [{"score": 999, "level": 2}],
                                "settings": {}}))
    legacy = GameState(path)
    assert legacy.best() == 999
    assert "time" not in legacy.entries[0]
    # And a fresh record still appends the new keys alongside legacy ones.
    legacy.record(10, 1, 1, time_s=5.0, best_combo=0)
    assert legacy.entries[1]["time"] == 5.0


def test_name_capped_at_ten(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.record(300, 3, 1, name="ABCDEFGHIJ12345")  # 15 chars

    reloaded = GameState(path)
    name = reloaded.entries[0]["name"]
    assert name == "ABCDEFGHIJ"  # first 10 chars after strip
    assert len(name) == MAX_NAME == 10


def test_set_entry_name(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.record(900, 9, 2)
    gs.record(500, 5, 1)
    gs.record(100, 1, 1)

    gs.set_entry_name(1, "BOB")
    reloaded = GameState(path)
    assert reloaded.entries[1]["name"] == "BOB"

    before = path.read_text()
    gs.set_entry_name(99, "X")  # out of range: silent no-op, no re-save
    assert path.read_text() == before

    gs.set_entry_name(0, "   ")  # whitespace-only name stores ""
    reloaded = GameState(path)
    assert reloaded.entries[0]["name"] == ""


def test_remove_entry(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.record(900, 9, 2)
    gs.record(500, 5, 1)
    gs.record(100, 1, 1)

    rank = 0
    gs.remove_entry(rank)  # discard the top entry (e.g. ESC at game over)
    reloaded = GameState(path)
    assert [e["score"] for e in reloaded.entries] == [500, 100]

    before = path.read_text()
    gs.remove_entry(99)  # out of range: silent no-op, no re-save
    assert path.read_text() == before


def test_legacy_entries_without_name(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "scores": [
                    {
                        "score": 300,
                        "lines": 3,
                        "level": 2,
                        "date": "2024-01-01 10:00",
                    }
                ],
                "settings": {},
            }
        )
    )

    gs = GameState(path)
    assert [e["score"] for e in gs.entries] == [300]
    assert gs.best() == 300
    assert gs.entries[0].get("name", "") == ""

    assert gs.record(50, 1, 1) == 1  # works; ranks below the legacy entry
    reloaded = GameState(path)
    assert [e["score"] for e in reloaded.entries] == [300, 50]
    assert reloaded.entries[0].get("name", "") == ""


def test_equal_scores_first_recorded_ranks_higher(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    first = gs.record(500, 1, 1)
    second = gs.record(500, 2, 1)
    assert first == 0
    assert second == 1  # tie inserted below the earlier entry (stable sort)
    scores = [(e["lines"], e["level"]) for e in gs.entries]
    assert scores == [(1, 1), (2, 1)]


def test_zero_score_not_recorded(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    assert gs.record(0, 0, 1) is None
    assert gs.entries == []


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.update_settings(start_level=12, ghost=False, shake=False)

    reloaded = GameState(path)
    assert reloaded.settings.start_level == 12
    assert reloaded.settings.ghost is False
    assert reloaded.settings.shake is False
    assert reloaded.settings.hold is True  # untouched default
    assert reloaded.settings.sound is True


def test_update_settings_clamps_and_coerces(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    gs.update_settings(start_level=99, sound="yes")
    assert gs.settings.start_level == MAX_START_LEVEL
    assert gs.settings.sound is True  # wrong-typed value: current kept
    gs.update_settings(ghost=False)
    gs.update_settings(start_level=-3, ghost=0)
    assert gs.settings.start_level == 1
    assert gs.settings.ghost is False  # wrong-typed: current value kept
    gs.update_settings(bogus=1)  # unknown key: ignored, no raise


def test_update_settings_persists_float_and_str(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.update_settings(das=0.25, arr=0.08, theme="mono")
    assert gs.settings.das == 0.25
    assert gs.settings.arr == 0.08
    assert gs.settings.theme == "mono"

    reloaded = GameState(path)
    assert reloaded.settings.das == 0.25
    assert reloaded.settings.arr == 0.08
    assert reloaded.settings.theme == "mono"


def test_update_settings_rejects_bad_float_and_str(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    gs.update_settings(das="fast")  # str where a float is expected
    gs.update_settings(das=1)  # int where a float is expected
    gs.update_settings(das=True)  # bool where a float is expected
    gs.update_settings(arr=0.5)  # float, but not an allowed value
    gs.update_settings(theme="neon")  # str, but not an allowed value
    gs.update_settings(theme=1)  # non-str theme
    assert gs.settings.das == 0.17
    assert gs.settings.arr == 0.04
    assert gs.settings.theme == "classic"


def test_new_settings_full_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.update_settings(
        start_level=5,
        ghost=False,
        hold=False,
        sound=True,
        shake=False,
        das=0.20,
        arr=0.02,
        theme="vivid",
    )

    reloaded = GameState(path)
    assert reloaded.settings == Settings(
        start_level=5,
        ghost=False,
        hold=False,
        sound=True,
        shake=False,
        das=0.20,
        arr=0.02,
        theme="vivid",
    )


def test_save_writes_unified_format(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.record(10, 1, 1)

    raw = json.loads(path.read_text())
    assert isinstance(raw, dict)
    assert set(raw) == {"scores", "settings", "sprint"}
    assert raw["scores"][0]["score"] == 10
    assert raw["sprint"] == {}  # no sprint played yet: empty block
    assert set(raw["settings"]) == {
        "start_level",
        "ghost",
        "hold",
        "sound",
        "shake",
        "das",
        "arr",
        "theme",
        "mode",
    }


def test_legacy_list_file_migrates(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    legacy = [
        {"score": 300, "lines": 3, "level": 2, "date": "2024-01-01 10:00"},
        {"score": 100, "lines": 1, "level": 1, "date": "2024-01-02 11:00"},
    ]
    path.write_text(json.dumps(legacy))

    gs = GameState(path)
    assert [e["score"] for e in gs.entries] == [300, 100]
    assert gs.settings == Settings()

    gs.record(50, 1, 1)  # the first save rewrites in the unified format
    raw = json.loads(path.read_text())
    assert isinstance(raw, dict)
    assert [e["score"] for e in raw["scores"]] == [300, 100, 50]
    assert raw["settings"]["start_level"] == 1


def test_legacy_scores_json_fallback(tmp_path: Path) -> None:
    legacy = tmp_path / "scores.json"
    legacy.write_text(json.dumps([_LEGACY_ENTRY]))

    gs = GameState(tmp_path / "state.json")
    assert gs.path == tmp_path / "state.json"
    assert gs.entries[0]["score"] == 420

    gs.save()  # writes the new file; the legacy file stays intact
    assert (tmp_path / "state.json").exists()
    assert legacy.exists()
    assert json.loads(legacy.read_text())[0]["score"] == 420


def test_load_legacy_disabled(tmp_path: Path) -> None:
    (tmp_path / "scores.json").write_text(json.dumps([_LEGACY_ENTRY]))
    gs = GameState(tmp_path / "state.json", load_legacy=False)
    assert gs.entries == []
    assert gs.settings == Settings()


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    gs = GameState(path)
    assert gs.entries == []
    assert gs.settings == Settings()
    assert gs.best() == 0
    assert gs.record(100, 1, 1) == 0  # recovers and saves fine


def test_non_dict_json_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('"just a string"')
    gs = GameState(path)
    assert gs.entries == []
    assert gs.settings == Settings()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "alt.json"))
    assert default_state_path() == tmp_path / "alt.json"
    gs = GameState()
    assert gs.path == tmp_path / "alt.json"


def test_default_path_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TETRIS_SCORES", raising=False)
    assert default_state_path() == (
        Path.home() / ".local" / "share" / "terminal-tetris" / "state.json"
    )
