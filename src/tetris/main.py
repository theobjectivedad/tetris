"""Terminal Tetris — curses UI entry point and frame loop.

The UI lives in three modules: ``tetris.ui_input`` (key reading and
input timing), ``tetris.ui_render`` (colors, modals, and board/sidebar
drawing), and ``tetris.ui_session`` (the per-game state machine — the
former ``game_loop`` body). This module keeps the curses setup and the
50 fps frame loop, which reads the clock through the module-level
``time`` import so the UI tests can swap ``main.time`` for a fake clock.

Layout: three columns centered as one block — the stats panel (left),
the board (center), and the HOLD/NEXT column (right) — plus centered
modal dialogs (help on `?`, high scores on `h`, settings menu on `s`,
game over with high-score name entry). Escape closes any open dialog;
at game over it starts a new game without saving the score (Enter
saves the high score and starts a new game).
All high scores and user settings are loaded and saved through the
unified store in ``tetris.state``.
"""

from __future__ import annotations

import curses
import time

from .engine import Tetris
from .pieces import BOARD_H
from .ui_render import build_attrs, draw_board
from .ui_session import Session

FRAME = 0.02          # main loop frame time (50 fps)


def game_loop(stdscr: curses.window) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    # ncurses otherwise waits its 1000ms default ESCDELAY after a bare ESC
    # for a possible escape-sequence tail before returning it, which made
    # ESC feel ~1s sluggish. 25ms still lets fast terminals deliver a split
    # arrow sequence (ESC [ A/B/C) as one key, and the app-level ESC_TTL
    # reassembly remains the fallback for slower terminals.
    curses.set_escdelay(25)
    session = Session(Tetris, time.monotonic())
    while True:
        frame_start = time.monotonic()
        # The frame loop reads the clock through the module-level ``time``
        # import (never through the session), so the UI tests'
        # ``main.time = fake_time`` drives it. ``on_frame`` returns True
        # once the player has quit.
        if session.on_frame(stdscr, frame_start):
            break
        # Sleep the remainder of the 50 fps frame budget. A fixed
        # ``time.sleep(FRAME)`` would add the full 20 ms on top of whatever
        # the frame's work took, so the real cadence drifted to 20 ms +
        # frame cost (jittery on slow terminals). With the drift
        # correction the average cadence stays at FRAME.
        time.sleep(max(0.0, FRAME - (time.monotonic() - frame_start)))


def main() -> None:
    curses.wrapper(game_loop)


if __name__ == "__main__":
    main()


# Hard contract (AGENTS.md): the UI tests monkeypatch main.Tetris / main.time
# and drive main.game_loop with a fake clock; the rest of the re-exports were
# removed in R8 — tests import the modal builders, KeyReader, and the attr
# seam (ATTRS) from their canonical modules (tetris.ui_render / ui_input).
__all__ = [
    "BOARD_H",
    "FRAME",
    "Tetris",
    "build_attrs",
    "curses",
    "draw_board",
    "game_loop",
    "main",
    "time",
]
