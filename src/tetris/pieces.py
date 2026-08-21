"""Static Tetris domain data: board dimensions and SRS piece/kick tables.

These are pure constants with no behavior. The engine (``engine.py``) and the
UI import them from here.
"""

from __future__ import annotations

BOARD_W = 10
BOARD_H = 20
MAX_START_LEVEL = 20  # upper bound for the player-selectable starting level

# Piece definitions: list of rotation states (SRS), each a list of (x, y) offsets.
PIECES: dict[str, list[list[tuple[int, int]]]] = {
    "I": [
        # 4x4 box states per standard SRS: state 0 = row 1, state 2 = row 2
        # (the KICKS_I table is derived from exactly this geometry).
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(1, 0), (1, 1), (1, 2), (1, 3)],
    ],
    "O": [
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        [(0, 0), (1, 0), (0, 1), (1, 1)],
    ],
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}

# Standard SRS super-kick tables.
# Keyed by (from_rot, to_rot); y is positive *up* in the SRS spec, so the UI
# layer flips dy (our y grows downward).
KICKS_JLSTZ: dict[tuple[int, int], list[tuple[int, int]]] = {
    (0, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (1, 2): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (2, 3): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (3, 0): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (1, 0): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (0, 3): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (3, 2): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (2, 1): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
}
KICKS_I: dict[tuple[int, int], list[tuple[int, int]]] = {
    (0, 1): [(0, 0), (-2, 0), (1, 1), (-2, -2), (1, -2)],
    (1, 2): [(0, 0), (2, 0), (-1, 1), (2, -2), (-1, -2)],
    (2, 3): [(0, 0), (-2, 0), (1, 1), (-2, -2), (1, -2)],
    (3, 0): [(0, 0), (2, 0), (-1, 1), (2, -2), (-1, -2)],
    (1, 0): [(0, 0), (2, 0), (-1, -1), (2, 2), (-1, 2)],
    (0, 3): [(0, 0), (-2, 0), (1, -1), (-2, 2), (1, 2)],
    (3, 2): [(0, 0), (2, 0), (-1, -1), (2, 2), (-1, 2)],
    (2, 1): [(0, 0), (-2, 0), (1, -1), (-2, 2), (1, 2)],
}
# 180° rotation kicks for J/L/S/Z/T (I is excluded — its 180° is a no-op).
# All four transitions (0↔2, 1↔3) share the same offset list; y is positive
# up, same convention as the tables above.
KICKS_180_JLSTZ: dict[tuple[int, int], list[tuple[int, int]]] = {
    (0, 2): [(0, 0), (1, 0), (-1, 0), (0, 1), (1, 1)],
    (2, 0): [(0, 0), (1, 0), (-1, 0), (0, 1), (1, 1)],
    (1, 3): [(0, 0), (1, 0), (-1, 0), (0, 1), (1, 1)],
    (3, 1): [(0, 0), (1, 0), (-1, 0), (0, 1), (1, 1)],
}

__all__ = [
    "BOARD_H",
    "BOARD_W",
    "KICKS_180_JLSTZ",
    "KICKS_I",
    "KICKS_JLSTZ",
    "MAX_START_LEVEL",
    "PIECES",
]
