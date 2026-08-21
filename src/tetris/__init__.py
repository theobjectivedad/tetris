"""Terminal Tetris.

Submodules:
    pieces    — SRS piece/kick tables and board dimensions
    scoring   — scoring rules and constants
    board     — the grid (board cell storage + line collapse)
    engine    — pure Tetris engine (no I/O)
    settings  — user settings model (pure data, no I/O)
    state     — unified persistence: high scores + settings in one file
    stats     — sidebar stat labels/formatting (shared UI + MCP contract)
    themes    — color-theme definitions (pure data)
    main      — curses UI & input (run with ``python -m tetris.main``)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tetris")
except PackageNotFoundError:  # running from a bare source checkout
    __version__ = "0.0.0+unknown"
