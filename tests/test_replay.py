"""Replay UI tests: replay speed (P18) and the replay list (P19).

These drive the real ``Session`` with a tiny fake screen (one key per
frame, no clock coupling). The ``run_game`` rig from ``test_ui`` can't be
used here: it terminates on two q's, but a replay consumes Q (only ESC and
F act during a replay), so a run whose replay is still alive at the run's
``duration`` would never terminate.
"""

from __future__ import annotations

import curses

import pytest
from test_ui import grid_to_text_for_frame

from tetris.engine import Tetris
from tetris.ui_session import Session


class FakeReplayScreen:
    """A minimal curses screen: a per-frame key queue plus an addstr grid."""

    def __init__(self, rows: int = 40, cols: int = 60) -> None:
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


def _text(scr: FakeReplayScreen) -> str:
    return grid_to_text_for_frame(scr.grid, scr.cols)


def _session(monkeypatch, tmp_path) -> Session:
    """A real Session against a temp state file, with the curses color
    calls stubbed (init_colors early-returns without a live screen)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    monkeypatch.setattr(curses, "color_pair", lambda *a: 0)
    monkeypatch.setattr(curses, "beep", lambda *a: None)
    return Session(Tetris, 0.0)


# ---------------------------------------------------------------------------
# Replay speed (P18)
# ---------------------------------------------------------------------------


def test_replay_virtual_time_advances_at_speed(monkeypatch, tmp_path) -> None:
    """The replay's virtual time is an accumulator scaled by the speed, so
    a mid-replay speed change advances the timeline smoothly (no jump)."""
    s = _session(monkeypatch, tmp_path)
    s.state.save_replay(
        {"seed": 7, "start_level": 1, "sprint": False, "events": [[0.5, "H"]]}
    )
    s.start_replay(10.0)
    assert s.replay_speed == 1.0
    s.replay_step(10.2)  # vt 0.2: the 0.5 event is not fed yet
    assert s.replay_next == 0
    assert s.replay_vt == pytest.approx(0.2)
    s.replay_speed = 2.0
    s.replay_step(10.3)  # vt 0.2 + 0.1 * 2 = 0.4
    assert s.replay_vt == pytest.approx(0.4)
    s.replay_step(10.4)  # vt 0.6: the 0.5 event is fed now
    assert s.replay_next == 1
    assert s.replay_vt == pytest.approx(0.6)
    s.replay_speed = 4.0
    s.replay_step(10.5)  # vt 0.6 + 0.1 * 4 = 1.0
    assert s.replay_vt == pytest.approx(1.0)


def test_replay_f_cycles_speed_and_esc_aborts(monkeypatch, tmp_path) -> None:
    """F cycles the speed 1x -> 2x -> 4x -> 1x; unrelated keys are
    consumed; ESC aborts back to the original game and resets the speed."""
    s = _session(monkeypatch, tmp_path)
    s.state.save_replay(
        {"seed": 7, "start_level": 1, "sprint": False, "events": [[0.5, "H"]]}
    )
    scr = FakeReplayScreen()
    s.start_replay(0.0)
    assert s.replay_speed == 1.0

    scr.keys.append(ord("f"))
    assert s._read_key(scr, 0.1) == -1  # F is consumed, dispatches nothing
    assert s.replay_speed == 2.0
    scr.keys.append(ord("F"))
    s._read_key(scr, 0.2)
    assert s.replay_speed == 4.0
    scr.keys.append(ord("f"))
    s._read_key(scr, 0.3)
    assert s.replay_speed == 1.0

    # Unrelated keys are consumed too (no live-game input leaks in).
    scr.keys.append(ord("x"))
    assert s._read_key(scr, 0.4) == -1
    assert s.replay_engine is not None

    # ESC aborts back to the original game and resets the speed. The bare
    # ESC is buffered one frame by the reader's sequence reassembly (the
    # same ESC_TTL path every ESC press goes through), so the abort lands
    # on the following read.
    scr.keys.append(27)
    assert s._read_key(scr, 0.5) == -1  # ESC buffered for reassembly
    assert s.replay_engine is not None
    assert s._read_key(scr, 0.6) == -1  # ESC emitted and consumed
    assert s.replay_engine is None
    assert s.replay_speed == 1.0


def test_replay_speed_tag_rendered(monkeypatch, tmp_path) -> None:
    """The REPLAY tag shows the speed while it is not 1x."""
    s = _session(monkeypatch, tmp_path)
    s.state.save_replay(
        {"seed": 7, "start_level": 1, "sprint": False, "events": []}
    )
    scr = FakeReplayScreen()
    s.start_replay(0.1)
    s.on_frame(scr, 0.1)
    assert "REPLAY" in _text(scr) and "REPLAY 2x" not in _text(scr)
    scr.keys.append(ord("f"))
    s.on_frame(scr, 0.3)
    assert "REPLAY 2x" in _text(scr)
    scr.keys.append(ord("f"))
    s.on_frame(scr, 0.5)
    assert "REPLAY 4x" in _text(scr)
