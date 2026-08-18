"""UI regression tests: drive the real game_loop with a fake curses screen
and a fake clock, so key handling is deterministic and font-independent.

Regression: held-key logic that never received key-release events caused the
piece to keep sliding after a single tap.
"""

import curses
import random

import main
from main import BOARD_H, BOARD_W


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += dt


class FakeScreen:
    def __init__(self, rows: int = 30, cols: int = 50, events: list[tuple[float, int]] | None = None) -> None:
        self.rows = rows
        self.cols = cols
        self.events = list(events or [])
        self.grid: dict[tuple[int, int], str] = {}

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

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        for i, ch in enumerate(text):
            self.grid[(y, x + i)] = ch

    def refresh(self) -> None:
        pass


fake_time = FakeTime()


def run_game(events: list[tuple[float, int]], duration: float = 2.0, seed: int = 99) -> FakeScreen:
    """Run game_loop with scripted key events; returns the fake screen.
    The RNG is seeded so every run starts with the identical piece queue."""
    global fake_time
    random.seed(seed)
    fake_time = FakeTime()
    scr = FakeScreen(events=events)
    scr.events.append((duration, ord("q")))

    real_time, real_curses = main.time, main.curses
    real_curs_set, real_has_colors, real_beep = (
        real_curses.curs_set,
        real_curses.has_colors,
        real_curses.beep,
    )
    main.time = fake_time
    real_curses.curs_set = lambda *a, **k: None
    real_curses.has_colors = lambda: False
    real_curses.beep = lambda: None
    try:
        main.game_loop(scr)
    finally:
        main.time = real_time
        real_curses.curs_set = real_curs_set
        real_curses.has_colors = real_has_colors
        real_curses.beep = real_beep
    return scr


def piece_cols(scr: FakeScreen) -> set[int]:
    """Board columns of the live piece (topmost █ cells below the border)."""
    border_row = 2  # board top border; interior starts at 3
    block_cols = set()
    top_row = None
    for (y, x), ch in scr.grid.items():
        if ch == "█" and y > border_row:
            if top_row is None or y < top_row:
                top_row = y
    if top_row is None:
        return set()
    for (y, x), ch in scr.grid.items():
        if ch == "█" and y == top_row:
            block_cols.add(x)
    # Cells are 2 chars wide with pitch 3; bx is the board origin.
    bx = (scr.cols - 41) // 2
    return {(x - bx) // 3 for x in block_cols}


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
