"""Sprint-mode tests (P11): 10-line time attack.

Covers the engine countdown/win/loss, the sprint game-over modal, and the
"never write the score high-score table" rule (a sprint result is a best
clear time, stored via ``GameState.record_sprint``).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from test_ui import run_game

from tetris import main
from tetris.engine import SPRINT_LINES, SPRINT_TIME
from tetris.game import BOARD_H, BOARD_W, Tetris


def fill_row(t: Tetris, y: int, kind: str = "T") -> None:
    t.board[y] = [kind] * BOARD_W


# ---------------------------------------------------------------------------
# Engine: countdown
# ---------------------------------------------------------------------------


def test_sprint_counts_down() -> None:
    t = Tetris(sprint=True)
    assert t.time_left == SPRINT_TIME
    t.tick(0.0)  # establish the tick baseline
    t.tick(1.0)  # one second of play
    assert t.time_left == SPRINT_TIME - 1.0
    assert not t.game_over


def test_sprint_timeup_is_a_loss() -> None:
    t = Tetris(sprint=True)
    t.tick(0.0)
    t.time_left = 0.1  # nearly out of time
    t.tick(1.0)  # a full second later -> past zero
    assert t.game_over
    assert t.won is False
    assert t.time_left == 0.0


def test_sprint_pause_freezes_countdown() -> None:
    t = Tetris(sprint=True)
    t.tick(0.0)
    t.tick(1.0)
    after = t.time_left
    t.paused = True
    t.tick(10.0)  # a long pause must not drain the clock
    assert t.time_left == after
    t.paused = False
    t.tick(11.0)  # one second of resumed play
    assert t.time_left == after - 1.0


def test_classic_has_no_countdown() -> None:
    t = Tetris()  # classic
    assert t.sprint is False
    assert t.time_left is None
    assert t.won is False
    t.tick(0.0)
    t.tick(1000.0)  # far beyond the sprint limit; classic ignores it
    assert not t.game_over


# ---------------------------------------------------------------------------
# Engine: win at the line target
# ---------------------------------------------------------------------------


def test_sprint_wins_at_ten_lines() -> None:
    t = Tetris(sprint=True)
    for _ in range(SPRINT_LINES):
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        if t.game_over:
            break
    assert t.won is True
    assert t.game_over is True
    assert t.lines >= SPRINT_LINES


def test_classic_never_wins() -> None:
    t = Tetris()  # classic: clearing 10+ lines levels up, never "wins"
    for _ in range(SPRINT_LINES + 2):
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
    assert t.won is False
    assert not t.game_over


# ---------------------------------------------------------------------------
# Game-over modal
# ---------------------------------------------------------------------------


def test_sprint_modal_win() -> None:
    from tetris.main import build_sprint_modal

    t = Tetris(sprint=True)
    t.won = True
    t.play_time = 95.5
    t.score = 1234
    t.lines = SPRINT_LINES
    m = build_sprint_modal(t, is_new_best=True, best_time=95.5)
    assert m.title == "SPRINT CLEARED"
    text = "\n".join(m.lines)
    assert "Time    1:35" in text
    assert "NEW BEST TIME" in text
    assert f"Lines   {SPRINT_LINES}/{SPRINT_LINES}" in text
    assert "G replay last game" in text


def test_sprint_modal_loss() -> None:
    from tetris.main import build_sprint_modal

    t = Tetris(sprint=True)
    t.won = False
    t.lines = 7
    t.score = 300
    m = build_sprint_modal(t, is_new_best=False, best_time=100.0)
    assert m.title == "TIME UP"
    text = "\n".join(m.lines)
    assert f"Lines   {7}/{SPRINT_LINES}" in text
    assert "Best    1:40" in text
    assert "NEW BEST" not in text


# ---------------------------------------------------------------------------
# UI integration: sprint never writes the score table
# ---------------------------------------------------------------------------


def _sprint_over_tetris(monkeypatch) -> None:
    """Rig ``main.Tetris`` to start already over as a WON sprint."""

    class SprintOverTetris(main.Tetris):
        def __init__(
            self,
            rng: random.Random | None = None,
            start_level: int = 1,
            sprint: bool = False,
        ) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.sprint = True
            self.won = True
            self.game_over = True
            self.score = 900
            self.play_time = 95.5
            self.lines = SPRINT_LINES

    monkeypatch.setattr(main, "Tetris", SprintOverTetris)


def test_sprint_game_over_skips_score_table(monkeypatch, tmp_path: Path) -> None:
    """A won sprint writes a best clear time but leaves the score table
    empty (clean P11 semantics)."""
    path = tmp_path / "state.json"
    # Seed the state file with sprint mode so the engine is constructed as a
    # sprint (main.py reads settings.mode from the state file).
    path.write_text(
        json.dumps({"scores": [], "settings": {"mode": "sprint"}})
    )
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    _sprint_over_tetris(monkeypatch)

    run_game(events=[], duration=1.0)

    data = json.loads(path.read_text())
    assert data["scores"] == []  # no high-score entry recorded
    assert isinstance(data["sprint"], dict)
    assert data["sprint"]["best_time"] == 95.5
