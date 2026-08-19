"""Legacy shim: high-score persistence now lives in ``tetris.state``.

``state.py`` is the real module (one JSON file holding both scores and
settings); this file keeps the historical ``tetris.scores`` import path
working for existing consumers.
"""

from __future__ import annotations

from .state import (
    GameState,
    default_state_path,
)
from .state import (
    GameState as HighScores,
)
from .state import (
    default_state_path as default_scores_path,
)

__all__ = ["GameState", "HighScores", "default_scores_path", "default_state_path"]
