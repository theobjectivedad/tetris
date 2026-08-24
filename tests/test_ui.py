"""UI regression tests: drive the real game_loop with a fake curses screen
and a fake clock, so key handling is deterministic and font-independent.

Regression: held-key logic that never received key-release events caused the
piece to keep sliding after a single tap.
"""

import curses
import json
import random
import re
from itertools import pairwise

from tetris import main
from tetris.engine import Piece
from tetris.main import BOARD_H

# The unpatched engine class. run_game() swaps in a deterministic subclass so
# every run starts with the identical piece queue (cross-run comparisons rely
# on it); tests that monkeypatch main.Tetris themselves are left alone.
_REAL_TETRIS = main.Tetris


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += dt


class FakeScreen:
    def __init__(self, rows: int = 40, cols: int = 60, events: list[tuple[float, int]] | None = None) -> None:
        self.rows = rows
        self.cols = cols
        self.events = list(events or [])
        self.grid: dict[tuple[int, int], str] = {}
        # Attr per cell (only non-zero attrs are recorded) — lets tests
        # verify color usage; empty in monochrome runs where attr is 0.
        self.grid_attr: dict[tuple[int, int], int] = {}
        # Snapshot of the grid at every refresh — lets tests inspect
        # intermediate frames (e.g. a modal that is open mid-run).
        self.frames: list[dict[tuple[int, int], str]] = []

    def nodelay(self, flag: bool) -> None:
        pass

    def keypad(self, flag: bool) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    def getch(self) -> int:
        while self.events and self.events[0][0] <= fake_time.now:
            _, key = self.events.pop(0)
            return key
        return -1

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


fake_time = FakeTime()


def run_game(events: list[tuple[float, int]], duration: float = 2.0, seed: int = 99) -> FakeScreen:
    """Run game_loop with scripted key events; returns the fake screen.
    The engine RNG is seeded (via a deterministic Tetris wrapper below) so
    every run starts with the identical piece queue.

    Two terminating q's are appended at ``duration``: the first closes a
    modal if one is open (Q never quits from inside a modal), the second
    then quits — so runs terminate no matter what state they end in.
    """
    global fake_time
    random.seed(seed)
    fake_time = FakeTime()
    scr = FakeScreen(events=events)
    scr.events.append((duration, ord("q")))
    scr.events.append((duration, ord("q")))

    # The engine no longer reads the global module RNG, so wrap Tetris to
    # inject a fresh RNG seeded with ``seed`` — but only when the test has
    # not already patched main.Tetris (e.g. with a RiggedTetris).
    real_tetris = main.Tetris
    if main.Tetris is _REAL_TETRIS:

        class _DeterministicTetris(_REAL_TETRIS):
            def __init__(self, *args: object, **kwargs: object) -> None:
                kwargs.setdefault("rng", random.Random(seed))
                super().__init__(*args, **kwargs)  # type: ignore[arg-type]

        main.Tetris = _DeterministicTetris  # type: ignore[assignment]

    real_time, real_curses = main.time, main.curses
    real_curs_set, real_has_colors, real_beep = (
        real_curses.curs_set,
        real_curses.has_colors,
        real_curses.beep,
    )
    real_color_pair = real_curses.color_pair
    main.time = fake_time
    real_curses.curs_set = lambda *a, **k: None
    real_curses.has_colors = lambda: False
    real_curses.beep = lambda: None
    # color_pair() needs initscr(); the stats panel calls it per line and
    # the fake screen has no color initialization, so return a plain attr.
    real_curses.color_pair = lambda *a, **k: 0
    try:
        main.game_loop(scr)
    finally:
        main.Tetris = real_tetris
        main.time = real_time
        real_curses.curs_set = real_curs_set
        real_curses.has_colors = real_has_colors
        real_curses.beep = real_beep
        real_curses.color_pair = real_color_pair
    return scr


def piece_cols_from_grid(grid: dict[tuple[int, int], str], by: int, bx: int) -> set[int]:
    """Board columns of the live piece in a rendered grid (see piece_cols)."""
    lo, hi = bx + 1, bx + 20
    top_row = None
    for (y, x), ch in grid.items():
        if ch == "█" and by + 1 <= y <= by + 20 and lo <= x <= hi:  # noqa: SIM102
            if top_row is None or y < top_row:
                top_row = y
    if top_row is None:
        return set()
    # Cells are 2 chars wide, contiguous (no gap).
    return {
        (x - lo) // 2
        for (y, x), ch in grid.items()
        if ch == "█" and y == top_row and lo <= x <= hi
    }


def piece_cols(scr: FakeScreen) -> set[int]:
    """Board columns of the live piece (topmost █ cells in the cell area).

    Layout math must match main.game_loop: total width 60 (stats 16 +
    gap 4 + board 22 = 1-col walls + 20-col cell area + gap 4 + HOLD/NEXT
    14), board block 22 rows tall. Cells are 2-col solid blocks with no
    gap, so the cell area spans bx+1 .. bx+20.
    """
    by = max(1, (scr.rows - 27) // 2)
    bx = (scr.cols - 60) // 2 + 20
    return piece_cols_from_grid(scr.grid, by, bx)


def grid_to_text(scr: FakeScreen) -> str:
    """Flatten the final frame into full-width row-major text."""
    return grid_to_text_for_frame(scr.grid, scr.cols)


def grid_to_text_for_frame(grid: dict[tuple[int, int], str], cols: int) -> str:
    """Flatten one rendered frame into full-width row-major text."""
    rows: dict[int, dict[int, str]] = {}
    for (y, x), ch in grid.items():
        rows.setdefault(y, {})[x] = ch
    return "\n".join(
        "".join(cells.get(x, " ") for x in range(cols))
        for _, cells in sorted(rows.items())
    )


def test_single_tap_moves_exactly_one_cell(monkeypatch) -> None:
    """A single tap of RIGHT must move the piece exactly one cell and then
    stop — the piece must not keep sliding (the old held-key bug)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    base = run_game(events=[])
    base_cols = piece_cols(base)
    assert base_cols, "piece not found on screen"

    tapped = run_game(events=[(0.5, curses.KEY_RIGHT)])
    tapped_cols = piece_cols(tapped)
    assert tapped_cols == {c + 1 for c in base_cols}


def test_held_key_auto_repeat_moves_one_cell_per_event(monkeypatch) -> None:
    """Terminal auto-repeat sends repeated events while a key is held:
    each event moves the piece once, no more, no less."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    base = run_game(events=[])
    base_cols = piece_cols(base)

    # Simulated hold: initial press + 3 repeat events at 300ms intervals.
    held = run_game(events=[(0.5, curses.KEY_LEFT), (0.8, curses.KEY_LEFT), (1.1, curses.KEY_LEFT), (1.4, curses.KEY_LEFT)])
    held_cols = piece_cols(held)
    assert held_cols == {c - 3 for c in base_cols}


def test_no_drift_after_single_tap(monkeypatch) -> None:
    """The regression the user reported: after a single tap the piece must
    stay put. Compare the piece's columns 500ms after the tap vs. 1500ms
    after — identical means no sliding."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    early = run_game(events=[(0.5, curses.KEY_RIGHT)], duration=1.0)
    late = run_game(events=[(0.5, curses.KEY_RIGHT)], duration=2.0)
    assert piece_cols(early) == piece_cols(late)


def test_no_movement_while_paused(monkeypatch) -> None:
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    base = run_game(events=[])
    base_cols = piece_cols(base)

    paused = run_game(events=[(0.3, ord("p")), (0.5, curses.KEY_RIGHT), (0.8, curses.KEY_RIGHT)])
    paused_cols = piece_cols(paused)
    assert paused_cols == base_cols


def test_score_popup_renders_after_line_clear(monkeypatch) -> None:
    """Rig a one-line clear (O fills the last two cells of the bottom
    row), hard-drop it, and verify the floating score text appears on
    screen once the flash animation commits the clear."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    class RiggedTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.board[BOARD_H - 1] = ["J"] * 4 + ["", ""] + ["J"] * 5
            self.piece = Piece("O", 4, 0)

    monkeypatch.setattr(main, "Tetris", RiggedTetris)
    scr = run_game(events=[(0.2, ord(" "))], duration=1.2)

    assert "SINGLE" in grid_to_text(scr)


def test_level_up_floater_renders_after_level_change(monkeypatch):
    """P16: a clear that raises the level floats "LEVEL UP" on the board
    (rig: 9 lines banked, one more single clear -> level 2)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_levelup.json")

    class RiggedTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.board[BOARD_H - 1] = ["J"] * 4 + ["", ""] + ["J"] * 5
            self.lines = 9
            self.piece = Piece("O", 4, 0)

    monkeypatch.setattr(main, "Tetris", RiggedTetris)
    scr = run_game(events=[(0.2, ord(" "))], duration=1.2)

    assert "LEVEL UP" in grid_to_text(scr)


# ---------------------------------------------------------------------------
# P17: big-clear board shake
# ---------------------------------------------------------------------------


def test_big_clear_shakes_board_for_200ms(monkeypatch):
    """P17: a tetris event shakes the board for BIG_SHAKE_SECONDS (0.2 s)
    when the shake setting is on; with the setting off, no shake. The
    shake channel is the same one a hard drop uses (effects.shake_until)."""
    from tetris.engine import Event
    from tetris.ui_session import BIG_SHAKE_SECONDS, Effects

    random.seed(42)  # Effects.shake jitters via the global RNG
    e = Effects()
    e.sound = False  # no beeps in the test
    e.shake_on = True
    e.on_events([Event("TETRIS +800", "tetris", 19)], 1.0)
    assert e.shake_until == 1.0 + BIG_SHAKE_SECONDS
    assert e.shake(1.05) != (0, 0)  # jitting while active
    assert e.shake(1.0 + BIG_SHAKE_SECONDS) == (0, 0)

    e2 = Effects()
    e2.sound = False
    e2.shake_on = False
    e2.on_events([Event("TETRIS +800", "tetris", 19)], 1.0)
    assert e2.shake_until == 0.0  # shake setting respected

    # A full T-spin also shakes; a plain single does not.
    e3 = Effects()
    e3.sound = False
    e3.on_events([Event("T-SPIN +400", "tspin", 15, center=(4, 14))], 2.0)
    assert e3.shake_until == 2.0 + BIG_SHAKE_SECONDS
    e4 = Effects()
    e4.sound = False
    e4.on_events([Event("SINGLE +100", "clear", 19)], 3.0)
    assert e4.shake_until == 0.0


def test_split_esc_sequence_reassembles_into_arrow_key(monkeypatch) -> None:
    """With nodelay() getch can return a bare ESC when the 3-byte arrow
    sequence (ESC [ C) is split across reads; the stray 'C' byte must not
    leak through as the HOLD key. A split RIGHT sequence must behave
    exactly like one assembled KEY_RIGHT."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_scores.json")

    base = run_game(events=[])
    base_cols = piece_cols(base)

    # ESC, '[', 'C' delivered one frame apart (split across reads).
    split = run_game(events=[(0.5, 27), (0.52, 0x5B), (0.54, ord("C"))])
    assert piece_cols(split) == {c + 1 for c in base_cols}

    # Same for LEFT (ESC [ D).
    base2 = run_game(events=[])
    base2_cols = piece_cols(base2)
    split_left = run_game(events=[(0.5, 27), (0.52, 0x5B), (0.54, ord("D"))])
    assert piece_cols(split_left) == {c - 1 for c in base2_cols}


# -- modal dialogs -------------------------------------------------------------


def test_no_always_on_legend_in_sidebar(monkeypatch) -> None:
    """The key legend no longer renders in the sidebar at all times — it
    lives in the help modal now (cleaner main screen)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_nolegend.json")

    text = grid_to_text(run_game(events=[], duration=0.5))
    assert "S settings" not in text
    assert "Q quit" not in text


def test_help_modal_shows_legend_and_version(monkeypatch) -> None:
    """? opens the HELP modal containing the key legend and the version.
    (Asserted on a mid-run frame: the run's final frame is the quit.)"""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_help.json")

    scr = run_game(events=[(0.3, ord("?"))], duration=1.0)
    # The modal is open from t=0.3 until the terminating q's at t=1.0.
    text = "\n".join(grid_to_text_for_frame(f, scr.cols) for f in scr.frames[40:60])
    assert "HELP" in text
    assert "S settings" in text
    assert "Q quit" in text
    assert re.search(r"v\d", text)


def test_help_modal_closes_on_q_without_quitting(monkeypatch) -> None:
    """Q while a modal is open closes the dialog, not the game: the next
    frame shows normal play, and the run only ends on the final q."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_help2.json")

    scr = run_game(events=[(0.3, ord("?")), (0.6, ord("q"))], duration=1.0)
    text = grid_to_text(scr)
    assert "HELP" not in text
    assert "SETTINGS" not in text


def test_x_key_rotates_piece_180(monkeypatch) -> None:
    """X flips the live piece 180°: a T (nub up) becomes a flat row with
    the nub below, so the rendered silhouette widens from one column to
    three. (Rigged Tetris: same first piece for the base and X runs.)"""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_x180.json")

    class RiggedTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.piece = Piece("T", 3, 0)

    monkeypatch.setattr(main, "Tetris", RiggedTetris)
    base = run_game(events=[], duration=1.0)
    assert piece_cols(base) == {4}  # T rot 0: top row is the single nub column
    xrun = run_game(events=[(0.5, ord("x"))], duration=1.0)
    assert piece_cols(xrun) == {3, 4, 5}  # T rot 2: top row is the 3-wide bar


def test_settings_menu_cycles_and_persists(monkeypatch, tmp_path) -> None:
    """s opens SETTINGS; down+right cycles the option and persists the new
    value through the unified state file (scores + settings in one doc)."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    scr = run_game(
        events=[(0.3, ord("s")), (0.5, curses.KEY_DOWN), (0.6, curses.KEY_RIGHT)],
        duration=1.0,
    )
    # Mid-run frame while the menu is still open.
    text = "\n".join(grid_to_text_for_frame(f, scr.cols) for f in scr.frames[40:60])
    assert "SETTINGS" in text

    data = json.loads(path.read_text())
    # cursor down from start_level to ghost (default True); right wraps to False
    assert data["settings"]["ghost"] is False
    assert "scores" in data  # unified document holds both


def test_settings_menu_closes_on_q(monkeypatch, tmp_path) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    scr = run_game(events=[(0.3, ord("s")), (0.6, ord("q"))], duration=1.0)
    text = grid_to_text(scr)
    assert "SETTINGS" not in text


def test_settings_menu_is_paused(monkeypatch, tmp_path) -> None:
    """The game does not fall while the settings menu is open: the piece's
    columns in a mid-run frame (menu open) equal a fresh run's position."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    by = max(1, (40 - 27) // 2)  # must match main.game_loop's layout math
    bx = (60 - 60) // 2 + 20  # board origin: stats (16) + gap (4)

    with_menu = run_game(events=[(0.3, ord("s"))], duration=2.0)
    # ~t=1.0: the menu has been open (since 0.3) for a full second — the
    # piece would have fallen a few rows by now if gravity were running.
    paused_cols = piece_cols_from_grid(with_menu.frames[50], by, bx)

    fresh = run_game(events=[], duration=0.3)
    assert paused_cols == piece_cols(fresh)


def test_game_over_uses_modal_with_time(monkeypatch, tmp_path) -> None:
    """Game over renders as the centered GAME OVER modal (top-10 score on
    a fresh score file, so it offers the name-entry footer: ENTER saves
    + new game, ESC new game without saving) and shows the game's elapsed
    time (P7). The rigged game instant-ends, so the time is 0:00."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    class OverTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.game_over = True
            self.score = 1234

    monkeypatch.setattr(main, "Tetris", OverTetris)
    scr = run_game(events=[], duration=0.5)
    text = grid_to_text(scr)
    assert "GAME OVER" in text
    assert "ENTER save + new game" in text
    assert "ESC  new game, no save" in text
    assert "Time    0:00" in text


def test_ghost_setting_toggles_ghost_piece(monkeypatch, tmp_path) -> None:
    """The 'drop shadow' setting: the ghost (▒) renders by default and is
    absent when ghost=false is saved in the state file."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    on = grid_to_text(run_game(events=[], duration=1.0))
    assert "\u2592" in on  # ▒ ghost cells

    path.write_text(json.dumps({"settings": {"ghost": False}}))
    off = grid_to_text(run_game(events=[], duration=1.0))
    assert "\u2592" not in off


# ---------------------------------------------------------------------------
# Live NEW BEST indicator (P20)
# ---------------------------------------------------------------------------


def test_live_new_best_indicator_when_beating_pre_game_best(monkeypatch, tmp_path) -> None:
    """P20: the stats panel shows NEW BEST while the live score has
    passed the board's top score from before the game started."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "scores": [
                    {"name": "X", "score": 5000, "lines": 5, "level": 2,
                     "date": "2026-08-21 10:00"},
                ],
                "settings": {},
            }
        )
    )
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    class PastBest(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.score = 6000  # already past the 5000 pre-game best

    monkeypatch.setattr(main, "Tetris", PastBest)
    assert "NEW BEST" in grid_to_text(run_game(events=[], duration=0.5))


def test_no_new_best_indicator_below_pre_game_best(monkeypatch, tmp_path) -> None:
    """P20: no indicator while the score is still below the pre-game
    best, and none at all for a sprint game (sprint never writes the
    score table)."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "scores": [
                    {"name": "X", "score": 5000, "lines": 5, "level": 2,
                     "date": "2026-08-21 10:00"},
                ],
                "settings": {},
            }
        )
    )
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    class BelowBest(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            self.score = 100

    monkeypatch.setattr(main, "Tetris", BelowBest)
    assert "NEW BEST" not in grid_to_text(run_game(events=[], duration=0.5))


def test_hold_off_renders_off_in_hold_box(monkeypatch, tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"settings": {"hold": False}}))
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    text = grid_to_text(run_game(events=[], duration=0.5))
    assert "HOLD" in text
    assert "off" in text


def test_next_box_shows_five_previews() -> None:
    """The NEXT box renders all five queued pieces at 3-row slot pitch."""
    scr = run_game(events=[], duration=2.0)
    bx = (scr.cols - 60) // 2 + 20  # board origin: stats (16) + gap (4)
    sx = bx + 26  # right column x = board (22) + gap (4)
    lines = "\n".join(grid_to_text_for_frame(f, scr.cols) for f in scr.frames[40:60]).splitlines()
    # Slot i's top row is screen row by + 9 + 3*i (inner origin by + 9,
    # 3-row pitch). `lines` is 0-indexed at the first drawn row (the board
    # top, row by), so its index is (by + 9 + 3*i) - by = 9 + 3*i.
    for i in range(5):
        row = 9 + 3 * i
        assert "\u2588" in lines[row][sx : sx + 14], f"preview missing at slot {i}"


# ---------------------------------------------------------------------------
# DAS/ARR held-key movement
# ---------------------------------------------------------------------------


def _counting_moves(monkeypatch):
    """Patch Tetris with a subclass that records every move() call."""
    calls = []

    class CountingTetris(main.Tetris):
        def move(self, dx, now=None):
            calls.append((dx, now if now is not None else 0.0))
            return super().move(dx, now)

    monkeypatch.setattr(main, "Tetris", CountingTetris)
    return calls


def _near(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def test_das_single_tap_is_exactly_one_move(monkeypatch) -> None:
    """A lone key event moves the piece exactly once — never streams."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_das.json")
    calls = _counting_moves(monkeypatch)
    run_game(events=[(0.5, curses.KEY_LEFT)], duration=2.0)
    assert len(calls) == 1 and calls[0][0] == -1 and _near(calls[0][1], 0.5)


def test_pre_das_repeat_does_not_prime_a_false_hold() -> None:
    """Regression (the 'moves 2 spaces' bug), at the KeyReader level.

    A repeat of the held direction that arrives BEFORE the DAS delay has
    elapsed is just the terminal/OS acknowledging the press (a tap a hair
    longer than instantaneous) — it must NOT refresh the hold window. If it
    did, the hold would extend past the DAS delay and ``auto_direction``
    would stream a second cell the user never asked for.
    """
    from tetris.main import KeyReader

    r = KeyReader()  # defaults: das=0.17, arr=0.04, HOLD_WINDOW=0.06
    # Fresh press at t=1.0: one immediate move.
    assert r.on_direction(1, 1.0)
    # A single early auto-repeat 0.13s later — before DAS (0.17s) has
    # elapsed. It must not move, and (crucially) must not extend the hold.
    assert not r.on_direction(1, 1.13)
    # If the early repeat had (wrongly) refreshed the hold window, the hold
    # would still be active at the DAS boundary and auto_direction would
    # stream a second cell. It must be 0 — the tap is done.
    assert r.auto_direction(1.18) == 0
    # And no streaming anywhere in the follow-up window.
    assert all(r.auto_direction(t) == 0 for t in (1.20, 1.22, 1.24))


def test_post_das_repeat_still_streams() -> None:
    """Counterpart guard: a repeat that arrives AFTER DAS has elapsed is a
    real hold and must keep the piece streaming (we must not have over-
    corrected the tap fix into breaking held-key streaming)."""
    from tetris.main import KeyReader

    r = KeyReader()  # das=0.17, arr=0.04
    assert r.on_direction(1, 1.0)  # tap
    # Repeats arrive every 30ms; the first one past DAS (1.17) is at 1.19.
    for t in (1.03, 1.06, 1.09, 1.12, 1.15, 1.18, 1.21, 1.24):
        r.on_direction(1, t)
        r.auto_direction(t)
    # A genuine hold must have produced several streaming moves by 1.24s.
    assert r._last_move > 1.05


def test_das_held_key_streams_after_delay(monkeypatch) -> None:
    """Held key: one tap move, a DAS gap with no moves, then ~40ms (ARR)
    streaming that stops shortly after the last event (no release event
    exists, so the hold window bounds the tail)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_das.json")
    calls = _counting_moves(monkeypatch)
    events = [(0.5 + i * 0.035, curses.KEY_LEFT) for i in range(8)]  # 0.5..0.745
    run_game(events=events, duration=2.0)
    times = [t for _, t in calls]
    assert _near(times[0], 0.5), "first move must be the immediate tap"
    assert all(dx == -1 for dx, _ in calls)
    # DAS gap: nothing between the tap and the DAS delay elapsing.
    assert not any(0.54 < t <= 0.66 for t in times)
    # Streaming exists and runs at the ARR cadence (>= ~40ms apart).
    assert len(times) >= 3
    for a, b in pairwise(times[1:]):
        assert b - a >= 0.038
    # Bounded tail: at most one extra step after the hold window expires.
    assert times[-1] <= 0.82


def test_das_direction_change_moves_immediately(monkeypatch) -> None:
    """Switching direction mid-hold is a fresh press: it moves at once and
    resets the DAS clock (no waiting for the old direction's DAS)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_das.json")
    calls = _counting_moves(monkeypatch)
    events = [
        (0.5, curses.KEY_LEFT),
        (0.535, curses.KEY_LEFT),  # during DAS: suppressed
        (0.6, curses.KEY_RIGHT),   # fresh press: immediate move
    ]
    run_game(events=events, duration=2.0)
    right_moves = [t for dx, t in calls if dx == 1]
    assert len(right_moves) == 1 and _near(right_moves[0], 0.6)


def test_das_hold_drives_piece_to_left_wall(monkeypatch) -> None:
    """Sanity: holding left for ~0.5s (streaming) pushes the piece fully
    against the left wall — far more than a terminal's ~4 auto-repeat
    events in that window would achieve."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_das.json")
    seen = []

    class WatchTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            seen.append(self)

    monkeypatch.setattr(main, "Tetris", WatchTetris)
    events = [(0.5 + i * 0.035, curses.KEY_LEFT) for i in range(15)]  # 0.5..0.98
    run_game(events=events, duration=2.0)
    assert seen[-1].piece.x == 0


# ---------------------------------------------------------------------------
# Soft-drop streaming (P14)
# ---------------------------------------------------------------------------


def test_soft_drop_tap_is_one_cell_plus_bounded_tail() -> None:
    """A clean tap of down drops exactly one cell immediately, plus at
    most one stream step before the hold window expires (mirrors the
    direction keys' bounded tail)."""
    from tetris.ui_input import KeyReader

    r = KeyReader()
    assert r.on_soft_drop(1.0)  # fresh press: immediate drop
    steps = [t for t in (1.02, 1.04, 1.05, 1.06, 1.08, 1.10) if r.auto_soft_drop(t)]
    assert steps == [1.05]  # one tail step, then the hold window expires


def test_soft_drop_hold_streams_at_20_cells_per_second() -> None:
    """Holding down (press + OS auto-repeat every 35 ms) drops at the
    fixed SOFT_DROP_RATE cadence, NOT at the OS repeat rate: no repeat
    event moves the piece directly, and stream steps are spaced at the
    stream cadence."""
    from tetris.ui_input import KeyReader

    r = KeyReader()
    assert r.on_soft_drop(1.0)  # press
    # OS auto-repeat events every 35 ms — each must be swallowed...
    for t in (1.035, 1.07, 1.105, 1.14, 1.175, 1.21, 1.245):
        assert not r.on_soft_drop(t)
    # ...while the stream owns the cadence (20 cells/s while held).
    grid = [round(1.0 + i * 0.01, 2) for i in range(1, 31)]
    steps = [t for t in grid if r.auto_soft_drop(t)]
    assert len(steps) >= 5  # ~300 ms of hold -> ~6 stream steps
    for a, b in pairwise(steps):
        assert b - a >= 0.049  # stream cadence, not the 35 ms event rate


def test_soft_drop_new_press_after_release_drops_immediately() -> None:
    """A press after the hold window has expired is a fresh press:
    immediate drop again (no DAS-style wait for the down key)."""
    from tetris.ui_input import KeyReader

    r = KeyReader()
    assert r.on_soft_drop(1.0)
    assert not r.on_soft_drop(1.03)  # still the same hold
    assert r.on_soft_drop(1.2)  # 0.17 s later: fresh press


def test_soft_drop_reset_clears_hold() -> None:
    """A menu round-trip (reader.reset) kills any in-flight down hold so
    stale repeats can't leak a stream into the next scene."""
    from tetris.ui_input import KeyReader

    r = KeyReader()
    assert r.on_soft_drop(1.0)
    assert r.on_soft_drop(1.03) is False
    r.reset()
    assert not r.auto_soft_drop(1.1)  # hold is dead after reset
    assert r.on_soft_drop(1.2)  # next press is fresh again


def test_reset_clears_rotate_cooldown() -> None:
    """R6: a restart (reader.reset) also clears the rotate cooldown, so a
    rotation is allowed immediately after a restart instead of being
    blocked for up to ROTATE_COOLDOWN by a rotation made just before it."""
    from tetris.ui_input import KeyReader

    r = KeyReader()
    assert r.allow_rotate(100.0)  # a rotation just before the restart
    assert not r.allow_rotate(100.05)  # the cooldown is still active
    r.reset()
    assert r.allow_rotate(100.06)  # restart: immediately allowed


def test_soft_drop_streaming_via_game_loop(monkeypatch) -> None:
    """P14 end-to-end: holding down (press + simulated OS auto-repeat at
    35 ms) produces drops spaced at the stream cadence (~50 ms), never
    at the event cadence (35 ms) — the OS repeats are swallowed and the
    stream drives the piece."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_softdrop.json")
    calls: list[float] = []

    class CountingSoftDrop(main.Tetris):
        def soft_drop(self) -> bool:
            calls.append(fake_time.now)
            return super().soft_drop()

    monkeypatch.setattr(main, "Tetris", CountingSoftDrop)
    events = [(0.5 + i * 0.035, curses.KEY_DOWN) for i in range(12)]  # 0.5..0.885
    run_game(events=events, duration=2.0)

    assert calls, "no soft drops recorded"
    assert _near(calls[0], 0.5), "first drop must be the immediate tap"
    # Stream cadence: every drop after the tap is >= ~SOFT_DROP_RATE apart;
    # had the OS repeats moved the piece, some pair would be 35 ms apart.
    for a, b in pairwise(calls[1:]):
        assert b - a >= 0.045
    # The hold streamed ~400 ms of input -> ~8-10 drops total.
    assert 8 <= len(calls) <= 11


# ---------------------------------------------------------------------------
# a/d alternate movement keys (P15)
# ---------------------------------------------------------------------------


def test_a_d_keys_move_piece_like_arrows(monkeypatch) -> None:
    """P15: a/d are home-row aliases for left/right — a single tap of each
    moves the piece exactly one cell, same as the arrow keys."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_ad.json")

    base = run_game(events=[])
    base_cols = piece_cols(base)
    assert base_cols, "piece not found on screen"

    left = run_game(events=[(0.5, ord("a"))])
    assert piece_cols(left) == {c - 1 for c in base_cols}

    right = run_game(events=[(0.5, ord("d"))])
    assert piece_cols(right) == {c + 1 for c in base_cols}

    # Uppercase works too (keypad-off terminals may deliver shifted keys).
    right_upper = run_game(events=[(0.5, ord("D"))])
    assert piece_cols(right_upper) == {c + 1 for c in base_cols}


def test_held_a_streams_like_held_left(monkeypatch) -> None:
    """P15: holding a streams through the same DAS/ARR path as holding
    the left arrow (repeats + streaming push the piece to the wall)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_ad.json")
    seen = []

    class WatchTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            seen.append(self)

    monkeypatch.setattr(main, "Tetris", WatchTetris)
    events = [(0.5 + i * 0.035, ord("a")) for i in range(15)]  # 0.5..0.98
    run_game(events=events, duration=2.0)
    assert seen[-1].piece.x == 0  # same as the held-LEFT wall test


# ---------------------------------------------------------------------------
# Pause menu
# ---------------------------------------------------------------------------


def _frames_text(scr: FakeScreen, lo: int, hi: int) -> str:
    return "\n".join(grid_to_text_for_frame(f, scr.cols) for f in scr.frames[lo:hi])


def test_pause_shows_modal_with_options(monkeypatch) -> None:
    """P opens a PAUSED modal offering resume / restart / quit."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_pause.json")
    scr = run_game(events=[(0.3, ord("p"))], duration=2.0)
    text = _frames_text(scr, 20, 45)  # 0.4s-0.9s, while paused
    assert "PAUSED" in text
    assert "resume" in text and "restart" in text and "quit" in text


def test_pause_toggles_closed(monkeypatch) -> None:
    """A second P closes the pause modal and unpauses the game."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_pause.json")
    scr = run_game(events=[(0.3, ord("p")), (1.0, ord("p"))], duration=2.0)
    assert "PAUSED" in _frames_text(scr, 20, 45)   # 0.4s-0.9s: paused
    assert "PAUSED" not in _frames_text(scr, 55, 80)  # 1.1s-1.6s: resumed


def test_pause_r_restarts(monkeypatch) -> None:
    """R while paused starts a fresh game (score reset)."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_ui_pause.json")

    created = []

    class ScoredTetris(main.Tetris):
        def __init__(self, rng: random.Random | None = None, start_level: int = 1, sprint: bool = False) -> None:
            super().__init__(rng=rng, start_level=start_level, sprint=sprint)
            created.append(self)
            if len(created) == 1:  # rig only the pre-restart game
                self.score = 500

    monkeypatch.setattr(main, "Tetris", ScoredTetris)
    scr = run_game(events=[(0.3, ord("p")), (1.0, ord("r"))], duration=2.0)
    early = _frames_text(scr, 20, 45)
    late = _frames_text(scr, 55, 80)
    assert "SCORE  500" in early
    assert "PAUSED" not in late
    assert "SCORE  0" in late and "SCORE  500" not in late


def test_bg_panel_attr_on_board_and_boxes(monkeypatch) -> None:
    """With colors, the board's cell area and the HOLD/NEXT box interiors
    carry the BG_PAIR attr (a no-op in the monochrome run_game tests), and
    the board border keeps its own attr."""
    import curses

    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "color_pair", lambda pair: 0x100 * pair)
    main.build_attrs()  # the render path uses the cached attrs
    bg = 0x100 * main.BG_PAIR

    scr = FakeScreen()
    main.draw_board(scr, main.Tetris(), 20, 6)
    # Empty mid-board cells carry the bg fill (cells/ghost draw on top).
    assert scr.grid_attr[(6 + 10, 20 + 1)] == bg
    assert scr.grid_attr[(6 + 19, 20 + 19)] == bg
    # The top border row keeps the border attr, not the bg fill.
    assert scr.grid_attr[(6, 20)] == main.ATTRS.border

    main.draw_box(scr, "HOLD", 46, 6, 14, 6)
    # Box interior (row by+2, first inner col) carries the bg attr.
    assert scr.grid_attr[(8, 47)] == bg
