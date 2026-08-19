"""Tetris package facade.

The implementation is split across focused modules:

* :mod:`tetris.pieces`   — SRS piece/kick tables and board dimensions
* :mod:`tetris.scoring`  — scoring constants
* :mod:`tetris.engine`   — ``Piece``, ``Event`` and the pure ``Tetris`` engine
* :mod:`tetris.scores`   — ``HighScores`` persistence (the only file-I/O module)

This module re-exports the public API so existing ``from tetris.game import
...`` imports (and the ``tetris import game`` module alias used by tests) keep
working unchanged.
"""

from __future__ import annotations

from .board import Board
from .engine import FLASH_FRAMES, LOCK_DELAY, LOCK_RESET_MAX, Event, Piece, Tetris
from .pieces import BOARD_H, BOARD_W, KICKS_I, KICKS_JLSTZ, PIECES
from .scores import HighScores, default_scores_path
from .scoring import (
    B2B_MULTIPLIER,
    COMBO_BONUS,
    HARD_DROP_POINTS,
    SCORE_TABLE,
    SOFT_DROP_POINTS,
    TSPIN_NO_LINE,
    TSPIN_SCORES,
    ScoreBreakdown,
    Scorer,
)

__all__ = [  # noqa: RUF022  # grouped by source module on purpose
    # pieces
    "BOARD_H",
    "BOARD_W",
    "KICKS_I",
    "KICKS_JLSTZ",
    "PIECES",
    # scoring
    "B2B_MULTIPLIER",
    "COMBO_BONUS",
    "HARD_DROP_POINTS",
    "SCORE_TABLE",
    "Scorer",
    "ScoreBreakdown",
    "SOFT_DROP_POINTS",
    "TSPIN_NO_LINE",
    "TSPIN_SCORES",
    # engine
    "Board",
    "Event",
    "FLASH_FRAMES",
    "LOCK_DELAY",
    "LOCK_RESET_MAX",
    "Piece",
    "Tetris",
    # scores
    "HighScores",
    "default_scores_path",
]
