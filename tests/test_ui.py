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
from tetris.game import Piece
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
        def __init__(self, rng: random.Random | None = None, start_level: int = 1) -> None:
            super().__init__(rng=rng, start_level=start_level)
            self.board[BOARD_H - 1] = ["J"] * 4 + ["", ""] + ["J"] * 5
            self.piece = Piece("O", 4, 0)

    monkeypatch.setattr(main, "Tetris", RiggedTetris)
    scr = run_game(events=[(0.2, ord(" "))], duration=1.2)

    assert "SINGLE" in grid_to_text(scr)


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
        def __init__(self, rng: random.Random | None = None, start_level: int = 1) -> None:
            super().__init__(rng=rng, start_level=start_level)
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
        def __init__(self, rng: random.Random | None = None, start_level: int = 1) -> None:
            super().__init__(rng=rng, start_level=start_level)
            seen.append(self)

    monkeypatch.setattr(main, "Tetris", WatchTetris)
    events = [(0.5 + i * 0.035, curses.KEY_LEFT) for i in range(15)]  # 0.5..0.98
    run_game(events=events, duration=2.0)
    assert seen[-1].piece.x == 0


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
        def __init__(self, rng: random.Random | None = None, start_level: int = 1) -> None:
            super().__init__(rng=rng, start_level=start_level)
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
    assert scr.grid_attr[(6, 20)] == main.BORDER_ATTR

    main.draw_box(scr, "HOLD", 46, 6, 14, 6)
    # Box interior (row by+2, first inner col) carries the bg attr.
    assert scr.grid_attr[(8, 47)] == bg
