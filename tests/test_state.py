"""Tests for unified persistence (tetris.state: scores + settings)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tetris.pieces import MAX_START_LEVEL
from tetris.settings import Settings
from tetris.state import GameState, HighScores, default_state_path

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


def test_keeps_top_five(tmp_path: Path) -> None:
    gs = GameState(tmp_path / "state.json")
    for score in (10, 50, 30, 90, 20, 70):
        gs.record(score, 1, 1)
    assert [e["score"] for e in gs.entries] == [90, 70, 50, 30, 20]
    assert gs.record(5, 0, 1) is None  # below the top 5


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
    gs.update_settings(start_level=99, sound=1)
    assert gs.settings.start_level == MAX_START_LEVEL
    assert gs.settings.sound is True
    gs.update_settings(start_level=-3, ghost=0)
    assert gs.settings.start_level == 1
    assert gs.settings.ghost is False  # 0 coerced to bool; field stays a bool
    gs.update_settings(bogus=1)  # unknown key: ignored, no raise


def test_save_writes_unified_format(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    gs = GameState(path)
    gs.record(10, 1, 1)

    raw = json.loads(path.read_text())
    assert isinstance(raw, dict)
    assert set(raw) == {"scores", "settings"}
    assert raw["scores"][0]["score"] == 10
    assert set(raw["settings"]) == {"start_level", "ghost", "hold", "sound", "shake"}


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
