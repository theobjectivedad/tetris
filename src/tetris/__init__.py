"""Terminal Tetris.

Submodules:
    game  — pure game logic (fully unit-tested, no I/O)
    main  — curses UI & input (run with ``python -m tetris.main``)
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tetris")
except PackageNotFoundError:  # running from a bare source checkout
    __version__ = "0.0.0+unknown"
