"""Terminal Tetris.

Submodules (pure core first, then the curses UI layer):
    pieces    — SRS piece/kick tables and board dimensions
    scoring   — scoring rules and constants
    board     — the grid (board cell storage + line collapse)
    engine    — pure Tetris engine (no I/O)
    settings  — user settings model (pure data, no I/O)
    state     — the only file-I/O module: scores, settings, sprint best, replays
    stats     — sidebar stat labels/formatting (shared UI + MCP contract)
    themes    — color-theme palettes (pure data, no curses import)
    main      — curses setup + 50 fps frame loop (run with ``python -m tetris.main``)
    ui_input  — key reading and input timing (DAS/ARR)
    ui_session— per-game state machine (Session.on_frame)
    ui_render — all drawing (board, sidebar, stats panel, modals)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tetris")
except PackageNotFoundError:  # running from a bare source checkout
    __version__ = "0.0.0+unknown"
