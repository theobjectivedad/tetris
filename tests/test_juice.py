"""Juice-round tests (Phase 3): the landing feel (P22 lock flash, P23
lock-delay pulse).

These drive the real ``Session`` with a tiny fake screen (one key per
frame, no clock coupling, per-cell attributes recorded) — the ``run_game``
rig in ``test_ui`` patches ``curses`` monochrome globally and terminates
on two q's, so it can't verify the per-cell attributes these effects rely
on.
"""

from __future__ import annotations

import curses

import pytest

from tetris.engine import Tetris
from tetris.pieces import BOARD_W
from tetris.ui_session import LOCK_FLASH_SECONDS, Session


class FakeScreen:
    """A minimal curses screen: a per-frame key queue plus an addstr grid
    with per-cell attributes (non-zero attrs only)."""

    def __init__(self, rows: int = 40, cols: int = 80) -> None:
        self.rows = rows
        self.cols = cols
        self.keys: list[int] = []
        self.grid: dict[tuple[int, int], str] = {}
        self.grid_attr: dict[tuple[int, int], int] = {}
        self.frames: list[dict[tuple[int, int], str]] = []

    def nodelay(self, flag: bool) -> None:
        pass

    def keypad(self, flag: bool) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    def getch(self) -> int:
        return self.keys.pop(0) if self.keys else -1

    def erase(self) -> None:
        self.grid = {}
        self.grid_attr = {}

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        for i, ch in enumerate(text):
            self.grid[(y, x + i)] = ch
            if attr:
                self.grid_attr[(y, x + i)] = attr

    def refresh(self) -> None:
        self.frames.append(dict(self.grid))


def _session(monkeypatch, tmp_path) -> Session:
    """A real Session against a temp state file, with the curses color
    calls stubbed (init_colors early-returns without a live screen)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    monkeypatch.setattr(curses, "color_pair", lambda *a: 0)
    monkeypatch.setattr(curses, "beep", lambda *a: None)
    return Session(Tetris, 0.0)


# ---------------------------------------------------------------------------
# P22: lock flash
# ---------------------------------------------------------------------------


def test_lock_flash_captured_on_hard_drop(monkeypatch, tmp_path) -> None:
    """A no-clear hard drop captures the locked cells for the P22 flash:
    four cells (every piece has four), expiring LOCK_FLASH_SECONDS later,
    and the frame draws them in the highlight accent (bold on this
    monochrome session)."""
    s = _session(monkeypatch, tmp_path)
    scr = FakeScreen()
    scr.keys.append(ord(" "))
    s.on_frame(scr, 1.0)
    assert s.effects.lock_flash is not None
    cells, until = s.effects.lock_flash
    assert until == pytest.approx(1.0 + LOCK_FLASH_SECONDS)
    assert len(cells) == 4
    bold = [a for a in scr.grid_attr.values() if a & curses.A_BOLD]
    assert bold


def test_no_lock_flash_on_initial_spawn(monkeypatch, tmp_path) -> None:
    """The first piece glides in without a preceding lock — no flash."""
    s = _session(monkeypatch, tmp_path)
    s.on_frame(FakeScreen(), 1.0)
    assert s.effects.lock_flash is None


def test_reset_game_clears_lock_flash(monkeypatch, tmp_path) -> None:
    s = _session(monkeypatch, tmp_path)
    scr = FakeScreen()
    scr.keys.append(ord(" "))
    s.on_frame(scr, 1.0)
    assert s.effects.lock_flash is not None
    s.reset_game()
    assert s.effects.lock_flash is None


# ---------------------------------------------------------------------------
# P23: lock-delay pulse
# ---------------------------------------------------------------------------


def test_pulse_flips_scene_key_while_grounded(monkeypatch, tmp_path) -> None:
    """The 8 Hz pulse makes the scene key alternate while the piece rests,
    so the static-scene skip redraws the dim/bright halves (each phase
    lasts 1/8 s); an in-flight piece keeps a stable key across the same
    interval."""
    s = _session(monkeypatch, tmp_path)
    s.engine._grounded = True
    assert s._scene_key(1.0, 80, 40, None) != s._scene_key(1.13, 80, 40, None)
    s.engine._grounded = False
    assert s._scene_key(1.0, 80, 40, None) == s._scene_key(1.13, 80, 40, None)


def test_pulse_draws_dim_half_frames(monkeypatch, tmp_path) -> None:
    """The grounded piece's cells carry A_DIM on the dim half of the pulse.
    The board's inner area is rows 7-9 near the spawn (by = 6 on a 40-row
    screen, the piece's first rows); the dim border walls and the dimmed
    ghost lie outside that window."""
    s = _session(monkeypatch, tmp_path)
    # Block the spawn column with a filled row so the engine itself
    # registers the piece as grounded (the tick would clear a manually
    # set flag while the piece can still fall).
    for x in range(BOARD_W):
        s.engine.board.set_cell(x, 2, "J")
    s.last_seq = s.engine.spawn_seq  # no spawn glide for the test's piece
    s.anim_start = None
    scr = FakeScreen()
    s.on_frame(scr, 1.0)  # phase int(1.0*8)%2 == 0: bright
    s.on_frame(scr, 1.13)  # phase int(1.13*8)%2 == 1: dim
    top_dim = [
        a
        for (y, x), a in scr.grid_attr.items()
        if 7 <= y <= 9 and 31 <= x <= 50 and a & curses.A_DIM
    ]
    assert top_dim
