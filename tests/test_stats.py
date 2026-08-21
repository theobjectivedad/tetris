"""Tests for the stats single source of truth (tetris.stats).

These pin the shared contract between the renderer (main.py) and the parser
(mcp/server.py): the canonical labels and the exact display formatting.
"""

from __future__ import annotations

from tetris.engine import Tetris
from tetris.stats import STAT_LABELS, display_value, panel_stats, sidebar_stats


def test_stat_labels_are_canonical() -> None:
    assert STAT_LABELS == ("SCORE", "LINES", "LEVEL", "COMBO", "B2B", "SPINS")


def test_snapshot_keys() -> None:
    snap = Tetris().snapshot()
    assert set(snap) == {
        "score",
        "lines",
        "level",
        "combo",
        "b2b",
        "spins",
        "paused",
        "game_over",
        "time",
        "best_combo",
        "sprint",
        "won",
        "time_left",
    }
    # Classic mode: no countdown, not a win.
    assert snap["sprint"] is False
    assert snap["won"] is False
    assert snap["time_left"] is None


def test_snapshot_sprint_keys() -> None:
    """P11: a sprint engine exposes the countdown and win flag."""
    snap = Tetris(sprint=True).snapshot()
    assert snap["sprint"] is True
    assert snap["won"] is False
    assert snap["time_left"] == 180.0  # SPRINT_TIME


def test_display_value_formatting() -> None:
    assert display_value("SCORE", 12345) == "12,345"
    assert display_value("BEST", 0) == "0"
    assert display_value("LINES", 7) == "7"
    assert display_value("COMBO", 0) == "\u2014"  # em dash when idle
    assert display_value("COMBO", 3) == "3"
    assert display_value("B2B", True) == "\u2713"  # check mark
    assert display_value("B2B", False) == "\u2014"
    assert display_value("SPINS", 2) == "2"


def test_sidebar_stats_matches_snapshot() -> None:
    t = Tetris()
    t.score, t.lines, t.level = 800, 4, 1
    t.combo, t.b2b, t.spins = 2, True, 1
    pairs = dict(sidebar_stats(t.snapshot()))
    assert pairs == {
        "SCORE": "800",
        "LINES": "4",
        "LEVEL": "1",
        "COMBO": "2",
        "B2B": "\u2713",
        "SPINS": "1",
    }


def test_labels_align_with_snapshot_keys() -> None:
    # Every label must map onto a field the snapshot actually provides,
    # so the renderer can never request a missing key.
    snap = Tetris().snapshot()
    # sidebar_stats iterates STAT_LABELS and indexes the snapshot — a
    # missing key would raise here.
    assert sidebar_stats(snap)


# ---------------------------------------------------------------------------
# Sprint panel (P11): classic contract unchanged, sprint swaps SPINS→TIME
# ---------------------------------------------------------------------------


def test_panel_stats_classic_matches_sidebar() -> None:
    """panel_stats with sprint=False is the canonical sidebar contract —
    the MCP parser's labels are preserved."""
    t = Tetris()
    t.score, t.lines, t.level = 800, 4, 1
    t.combo, t.b2b, t.spins = 2, True, 1
    snap = t.snapshot()
    assert panel_stats(snap, sprint=False) == sidebar_stats(snap)
    labels = [label for label, _ in panel_stats(snap, sprint=False)]
    assert labels == list(STAT_LABELS)  # SPINS still present in classic


def test_panel_stats_sprint_time_replaces_spins() -> None:
    """In sprint mode the SPINS row becomes a TIME countdown (M:SS) driven
    by the snapshot's time_left."""
    snap: dict[str, int | bool | float] = {
        "score": 300,
        "lines": 7,
        "level": 1,
        "combo": 0,
        "b2b": False,
        "spins": 0,
        "time_left": 125.0,  # 2:05 remaining
    }
    pairs = dict(panel_stats(snap, sprint=True))
    assert "SPINS" not in pairs  # replaced, not appended
    assert pairs["TIME"] == "2:05"
    # The other classic rows are unchanged.
    assert pairs["SCORE"] == "300"
    assert pairs["LINES"] == "7"
    labels = [label for label, _ in panel_stats(snap, sprint=True)]
    assert labels == ["SCORE", "LINES", "LEVEL", "COMBO", "B2B", "TIME"]


def test_panel_stats_sprint_missing_time_left() -> None:
    """A sprint snapshot without time_left shows 0:00 (never crashes)."""
    snap: dict[str, int | bool | float] = {
        "score": 0,
        "lines": 0,
        "level": 1,
        "combo": 0,
        "b2b": False,
        "spins": 0,
    }
    pairs = dict(panel_stats(snap, sprint=True))
    assert pairs["TIME"] == "0:00"
