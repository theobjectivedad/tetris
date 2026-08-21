"""Tests for the stats single source of truth (tetris.stats).

These pin the shared contract between the renderer (main.py) and the parser
(mcp/server.py): the canonical labels and the exact display formatting.
"""

from __future__ import annotations

from tetris.game import Tetris
from tetris.stats import STAT_LABELS, display_value, sidebar_stats


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
    }


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
