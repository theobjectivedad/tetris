"""Terminal Tetris.

Submodules:
    game      — pure game logic facade (fully unit-tested, no I/O)
    settings  — user settings model (pure data, no I/O)
    state     — unified persistence: high scores + settings in one file
    main      — curses UI & input (run with ``python -m tetris.main``)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tetris")
except PackageNotFoundError:  # running from a bare source checkout
    __version__ = "0.0.0+unknown"
