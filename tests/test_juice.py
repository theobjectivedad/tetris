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
import json

import pytest

from tetris.engine import Event, Tetris
from tetris.pieces import BOARD_H, BOARD_W
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


# ---------------------------------------------------------------------------
# P24: danger zone
# ---------------------------------------------------------------------------


def test_danger_bar_blinks_when_stack_reaches_top(monkeypatch, tmp_path) -> None:
    """With the stack within DANGER_TOP_ROWS of the ceiling the board's top
    border carries A_BLINK (DANGER_ATTR's monochrome fallback; white on
    red on a color terminal)."""
    s = _session(monkeypatch, tmp_path)
    for x in range(BOARD_W):
        s.engine.board.set_cell(x, 1, "J")  # stack top at row 1 (<= 4)
    scr = FakeScreen()
    s.on_frame(scr, 1.0)
    # The top border row is by = max(1, (40 - 27) // 2) = 6; the inner bar
    # runs columns 31-50 (the walls at 30/51 keep the plain border attr).
    top = [a for (y, x), a in scr.grid_attr.items() if y == 6 and 31 <= x <= 50]
    assert top
    assert all(a & curses.A_BLINK for a in top)


def test_no_danger_bar_on_empty_board(monkeypatch, tmp_path) -> None:
    s = _session(monkeypatch, tmp_path)
    scr = FakeScreen()
    s.on_frame(scr, 1.0)
    top = [a for (y, x), a in scr.grid_attr.items() if y == 6 and 31 <= x <= 50]
    assert top
    assert all(not (a & curses.A_BLINK) for a in top)


# ---------------------------------------------------------------------------
# P25: combo & B2B feedback
# ---------------------------------------------------------------------------


def test_combo_and_b2b_floaters_on_qualifying_clear(monkeypatch, tmp_path) -> None:
    """A Tetris that continues a combo (post-commit combo >= 2) and carries
    the B2B streak gets the score floater plus 'COMBO ×N' above it and a
    'B2B' tag below it."""
    s = _session(monkeypatch, tmp_path)
    s.engine.combo = 3  # post-commit: this clear continued the run
    s.engine.b2b = True
    s.engine.events.append(Event("TETRIS +2400", "tetris", 15, lines=4))
    s._apply_effects(2.0)
    floaters = {(tx, row) for tx, row, _ in s.effects.floaters}
    assert ("TETRIS +2400", 15) in floaters
    assert ("COMBO ×3", 13) in floaters  # row - 2
    assert ("B2B", 16) in floaters  # row + 1


def test_no_line_spin_keeps_streak_but_shows_no_b2b(monkeypatch, tmp_path) -> None:
    """A no-line T-spin (lines=0) leaves the streak untouched but is not a
    B2B-qualifying clear — no B2B tag; the combo is 0, so no COMBO tag."""
    s = _session(monkeypatch, tmp_path)
    s.engine.b2b = True  # carried over from an earlier Tetris
    s.engine.events.append(Event("T-SPIN MINI +100", "tspin-mini", 12, lines=0))
    s._apply_effects(2.0)
    texts = [tx for tx, _, _ in s.effects.floaters]
    assert "B2B" not in texts
    assert not any(t.startswith("COMBO") for t in texts)


def test_two_line_tspin_earns_b2b_tag(monkeypatch, tmp_path) -> None:
    """A 2-line T-spin is B2B-qualifying (like a Tetris)."""
    s = _session(monkeypatch, tmp_path)
    s.engine.b2b = True
    s.engine.events.append(Event("T-SPIN +1800", "tspin", 14, lines=2))
    s._apply_effects(2.0)
    texts = [tx for tx, _, _ in s.effects.floaters]
    assert "B2B" in texts


def test_no_combo_floater_until_run_has_two(monkeypatch, tmp_path) -> None:
    """The first clear of a run (post-commit combo 1) shows no COMBO tag;
    the second consecutive clear (combo 2) does."""
    s = _session(monkeypatch, tmp_path)
    s.engine.combo = 1
    s.engine.events.append(Event("SINGLE +100", "clear", 17, lines=1))
    s._apply_effects(2.0)
    assert not any(tx.startswith("COMBO") for tx, _, _ in s.effects.floaters)

    s.engine.events.append(Event("DOUBLE +300", "clear", 16, lines=2))
    s.engine.combo = 2
    s._apply_effects(3.0)
    floaters = {(tx, row) for tx, row, _ in s.effects.floaters}
    assert ("COMBO ×2", 14) in floaters


def test_b2b_stat_glows_when_active(monkeypatch, tmp_path) -> None:
    """The sidebar B2B value renders in the highlight accent (bold on this
    monochrome session) while the streak is active, plain otherwise."""
    s = _session(monkeypatch, tmp_path)
    s.engine.b2b = True
    scr = FakeScreen()
    s.on_frame(scr, 1.0)
    # The B2B row is the 5th stat: by + 2 + 4 = 12; the value sits at
    # block_x + 7 = 17 (x = (80 - 60) // 2).
    assert scr.grid_attr.get((12, 17), 0) & curses.A_BOLD

    s2 = _session(monkeypatch, tmp_path)
    scr2 = FakeScreen()
    s2.on_frame(scr2, 1.0)
    assert not (scr2.grid_attr.get((12, 17), 0) & curses.A_BOLD)


# ---------------------------------------------------------------------------
# P26: game-over sting
# ---------------------------------------------------------------------------


def _seed_best_score(tmp_path, score: int) -> None:
    """A state file with one score-table entry (the pre-game best)."""
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "scores": [
                    {"name": "X", "score": score, "lines": 1, "level": 1,
                     "date": "2026-01-01 10:00"}
                ],
                "settings": {},
            }
        )
    )


def test_game_over_sting_fires_once(monkeypatch, tmp_path) -> None:
    """A classic game-over flip queues the slow 3-beep sting
    (0, 0.18, 0.42 s); the pump fires each beep on its frame, and the
    sting plays once — a further frame queues nothing new."""
    s = _session(monkeypatch, tmp_path)
    beeps: list[int] = []
    monkeypatch.setattr(curses, "beep", lambda *a: beeps.append(1))
    s.engine.game_over = True
    s._apply_effects(10.0)  # the t=0 offset fires immediately
    assert beeps == [1]
    assert s.effects.beep_times == pytest.approx([10.18, 10.42])
    s._apply_effects(10.3)
    assert len(beeps) == 2
    s._apply_effects(10.5)
    assert len(beeps) == 3
    assert not s.effects.beep_times
    s._apply_effects(11.0)
    assert len(beeps) == 3  # no re-sting


def test_sprint_win_uses_rising_sting(monkeypatch, tmp_path) -> None:
    """A sprint win (won=True game-over flip) queues the brisk, rising
    pattern instead of the slow one."""
    s = _session(monkeypatch, tmp_path)
    s.engine.won = True
    s.engine.game_over = True
    s._apply_effects(10.0)
    assert s.effects.beep_times == pytest.approx([10.1, 10.2])
    assert s.was_over


def test_replayed_game_over_does_not_sting(monkeypatch, tmp_path) -> None:
    """The replayed engine's own top-out never re-triggers the sting —
    the original game already played it, and while a replay runs the
    live game's flip detector stands down."""
    s = _session(monkeypatch, tmp_path)
    beeps: list[int] = []
    monkeypatch.setattr(curses, "beep", lambda *a: beeps.append(1))
    s.engine.game_over = True
    s._apply_effects(1.0)  # the original game's sting
    assert beeps == [1]
    # Simulate a replay that tops out: the engine swaps to the replay
    # engine, which ends in game over.
    s.replay_original = s.engine
    s.replay_engine = Tetris()
    s.engine = s.replay_engine
    s.replay_engine.game_over = True
    s._apply_effects(2.0)
    assert len(beeps) == 3  # the two queued stings fired; nothing new
    assert not s.effects.beep_times
    # ...and after the replay restores the original (already-over)
    # engine, no third sting either.
    s.replay_engine = None
    s.engine = s.replay_original
    s._apply_effects(3.0)
    assert len(beeps) == 3


# ---------------------------------------------------------------------------
# P27: new-best jingle
# ---------------------------------------------------------------------------


def test_new_best_jingle_fires_once(monkeypatch, tmp_path) -> None:
    """The frame the live score first passes the pre-game best: a
    'NEW BEST!' floater at the board center plus a brisk 3-beep; the
    floater appears exactly once per game."""
    _seed_best_score(tmp_path, 5000)
    s = _session(monkeypatch, tmp_path)
    assert s.best_at_start == 5000
    beeps: list[int] = []
    monkeypatch.setattr(curses, "beep", lambda *a: beeps.append(1))
    s.engine.score = 6000
    s._apply_effects(5.0)  # the t=0 offset fires immediately
    rows = [row for tx, row, _ in s.effects.floaters if tx == "NEW BEST!"]
    assert rows == [BOARD_H // 2]
    assert s._new_best_fired
    assert beeps == [1]
    s._apply_effects(6.0)
    assert len(beeps) == 3
    assert sum(
        1 for tx, _, _ in s.effects.floaters if tx == "NEW BEST!"
    ) == 1  # one floater, no second jingle


def test_new_best_jingle_not_below_best(monkeypatch, tmp_path) -> None:
    _seed_best_score(tmp_path, 5000)
    s = _session(monkeypatch, tmp_path)
    s.engine.score = 100  # well below the pre-game best
    s._apply_effects(5.0)
    assert not any(tx == "NEW BEST!" for tx, _, _ in s.effects.floaters)
    assert not s._new_best_fired
    assert not s.effects.beep_times


# ---------------------------------------------------------------------------
# P28: sprint urgency
# ---------------------------------------------------------------------------


def _session_sprint(monkeypatch, tmp_path) -> Session:
    """A real Session configured for sprint mode (settings.mode = sprint)."""
    (tmp_path / "state.json").write_text(
        json.dumps({"scores": [], "settings": {"mode": "sprint"}})
    )
    return _session(monkeypatch, tmp_path)


def test_sprint_ticks_in_final_seconds(monkeypatch, tmp_path) -> None:
    """A live sprint beeps once per second boundary during the final
    five seconds (1..5) and stays silent outside them."""
    s = _session_sprint(monkeypatch, tmp_path)
    beeps: list[int] = []
    monkeypatch.setattr(curses, "beep", lambda *a: beeps.append(1))
    assert s.engine.sprint
    s.engine.time_left = 30.5
    s._apply_effects(100.0)  # outside the final five: silent
    assert not beeps
    for sec in (5, 4, 3, 2, 1):
        s.engine.time_left = float(sec) + 0.25
        s._apply_effects(100.0 + float(sec))
        assert len(beeps) == 5 - sec + 1
    # The 0 boundary (game over) belongs to the sting, not the tick.
    s.engine.time_left = 0.25
    s._apply_effects(200.0)
    assert len(beeps) == 5


def test_sprint_timer_turns_danger_in_final_ten(monkeypatch, tmp_path) -> None:
    """The sprint TIME stat uses the danger attr while 10 s or less
    remain (on top of the 30-s-or-less blink), plain above that."""
    import tetris.ui_render as ur

    monkeypatch.setattr(ur, "DANGER_ATTR", 0x4000)  # a visible sentinel
    s = _session_sprint(monkeypatch, tmp_path)
    scr = FakeScreen()
    s.engine.time_left = 9.5
    s.on_frame(scr, 100.0)
    # The TIME row is the last stat (index 5): by + 2 + 5 = 13; the value
    # sits at block_x + 7 = 17 (x = (80 - 60) // 2).
    assert scr.grid_attr.get((13, 17), 0) & 0x4000
    s.engine.time_left = 25.0
    s.on_frame(scr, 101.0)
    assert not (scr.grid_attr.get((13, 17), 0) & 0x4000)


# ---------------------------------------------------------------------------
# P29: hold flash
# ---------------------------------------------------------------------------


def test_hold_flash_on_accepted_hold(monkeypatch, tmp_path) -> None:
    """An accepted hold flips can_hold and opens the P29 flash window —
    the frame draws the HOLD box border in the highlight accent (bold on
    this monochrome session). A rejected second hold does not re-flash."""
    s = _session(monkeypatch, tmp_path)
    scr = FakeScreen()
    scr.keys.append(ord("c"))
    s.on_frame(scr, 1.0)
    assert not s.engine.can_hold
    assert 1.0 < s.effects.hold_flash_until <= 1.0 + 0.2
    # The HOLD box's top-left border: sx = bx + 26 = 56, top row by = 6.
    assert scr.grid_attr.get((6, 56), 0) & curses.A_BOLD
    # A second hold is rejected (window) — no fresh flash.
    before = s.effects.hold_flash_until
    scr.keys.append(ord("c"))
    s.on_frame(scr, 1.2)
    assert s.effects.hold_flash_until == before


# ---------------------------------------------------------------------------
# P30: rotation flash
# ---------------------------------------------------------------------------


def test_rotate_flash_on_successful_rotation(monkeypatch, tmp_path) -> None:
    """A successful rotation opens the P30 flash window and the frame
    draws the piece in the highlight accent (bold on this monochrome
    session); a rejected rotation opens no window and flashes nothing."""
    s = _session(monkeypatch, tmp_path)
    s.last_seq = s.engine.spawn_seq  # no spawn glide: the piece is drawn
    s.anim_start = None
    scr = FakeScreen()
    scr.keys.append(curses.KEY_UP)
    s.on_frame(scr, 1.0)
    assert s.effects.rotate_flash_until == pytest.approx(1.0 + 0.1)
    assert any(a & curses.A_BOLD for a in scr.grid_attr.values())

    s2 = _session(monkeypatch, tmp_path)
    s2.last_seq = s2.engine.spawn_seq
    s2.anim_start = None
    s2.engine.rotate = lambda d=1, now=None: False  # a rejected rotation
    scr2 = FakeScreen()
    scr2.keys.append(curses.KEY_UP)
    s2.on_frame(scr2, 1.0)
    assert s2.effects.rotate_flash_until == 0.0
    assert not any(a & curses.A_BOLD for a in scr2.grid_attr.values())
