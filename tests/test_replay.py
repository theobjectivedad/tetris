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


# ---------------------------------------------------------------------------
# Replay list (P19)
# ---------------------------------------------------------------------------


def test_replays_accessor_is_newest_first(monkeypatch, tmp_path) -> None:
    """GameState.replays() returns the saved replays newest first (row 1
    = most recent); empty store -> []."""
    from tetris.state import GameState

    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))
    gs = GameState()
    assert gs.replays() == []
    for i in range(1, 4):
        gs.save_replay(
            {"seed": i, "start_level": 1, "events": [[0.1, "L"]],
             "score": i * 100, "lines": i, "date": "2026-08-21 10:00"}
        )
    replays = gs.replays()
    assert [rp["seed"] for rp in replays] == [3, 2, 1]
    assert [rp["seed"] for rp in gs.replays()][:1] == [3]


def test_build_replays_modal_rows(monkeypatch, tmp_path) -> None:
    """build_replays_modal: fixed-width rows with score/lines/mode/date,
    an empty-state message, and the 1-5 play footer."""
    from tetris.state import GameState
    from tetris.ui_render import build_replays_modal

    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))
    gs = GameState()
    m_empty = build_replays_modal(gs)
    assert m_empty.title == "REPLAYS"
    assert "No saved replays yet" in "\n".join(m_empty.lines)
    assert "1-5 play" in m_empty.lines[-1]

    gs.save_replay(
        {"seed": 3, "start_level": 1, "sprint": False,
         "score": 12345, "lines": 42, "date": "2026-08-21 10:00", "events": []}
    )
    gs.save_replay(
        {"seed": 4, "start_level": 2, "sprint": True,
         "score": 999, "lines": 10, "date": "2026-08-22 09:30", "events": []}
    )
    m = build_replays_modal(gs)
    text = "\n".join(m.lines)
    # Newest first: the sprint run (saved second) is row 1.
    row1, row2 = m.lines[1], m.lines[2]
    assert row1.startswith("1  ") and "999" in row1 and "sprint" in row1 and "10" in row1
    assert "2026-08-22 09:30" in row1
    assert row2.startswith("2  ") and "12,345" in row2 and "classic" in row2
    assert "SCORE" in m.lines[0] and "DATE" in m.lines[0]
    assert text.count("sprint") >= 1


def test_replay_list_opens_and_plays(monkeypatch, tmp_path) -> None:
    """P19 end-to-end: L at game over opens the REPLAYS dialog; a digit
    starts that replay (REPLAY tag on screen); ESC aborts back to the
    game-over screen with the menu closed."""
    s = _session(monkeypatch, tmp_path)
    s.state.save_replay(
        {"seed": 11, "start_level": 1, "sprint": False,
         "score": 1234, "lines": 5, "date": "2026-08-21 10:00",
         "events": [[0.5, "H"]]}
    )
    s.engine.game_over = True  # a finished, unranked (score 0) game
    scr = FakeReplayScreen()
    s.on_frame(scr, 0.1)  # settle the game-over screen

    scr.keys.append(ord("l"))
    s.on_frame(scr, 0.2)  # open the replay list
    text = _text(scr)
    assert "REPLAYS" in text
    assert "1,234" in text
    assert "classic" in text
    assert "2026-08-21 10:00" in text

    scr.keys.append(ord("1"))
    s.on_frame(scr, 0.3)  # play the first (newest) replay
    assert s.replay_engine is not None
    assert "REPLAY" in _text(scr)

    # ESC closes/aborts: first the reader buffers it for reassembly, the
    # next read emits it — and with no menu open anymore it must NOT
    # reset the finished game.
    scr.keys.append(27)
    s.on_frame(scr, 0.4)
    s.on_frame(scr, 0.5)
    s.on_frame(scr, 0.6)
    assert s.replay_engine is None
    assert s.engine.game_over  # back on the game-over screen
    assert "GAME OVER" in _text(scr)


def test_replay_list_esc_closes_menu_not_new_game(monkeypatch, tmp_path) -> None:
    """P19: ESC on the replay list closes the dialog and returns to the
    game-over screen — it must NOT discard the score and start a new
    game (the pre-P19 ESC ordering did exactly that at game over)."""
    s = _session(monkeypatch, tmp_path)
    s.engine.game_over = True
    scr = FakeReplayScreen()
    s.on_frame(scr, 0.1)
    scr.keys.append(ord("l"))
    s.on_frame(scr, 0.2)
    assert s.menu == "replays"
    scr.keys.append(27)
    s.on_frame(scr, 0.3)  # ESC buffered by the reader
    s.on_frame(scr, 0.4)  # ESC emitted: the open menu closes
    assert s.menu is None
    assert s.engine.game_over  # the game was not restarted
    assert "REPLAYS" not in _text(scr)
    assert "GAME OVER" in _text(scr)
