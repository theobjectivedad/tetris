"""The game board: a W×H grid of settled piece kinds.

``Board`` owns the grid and the small set of board-level operations the engine
needs — detecting full rows, collapsing them, and cell access — so that logic
lives in one place instead of being re-derived in several engine methods.

For compatibility with the curses UI and the existing test suite, ``Board``
also mirrors the list-of-rows protocol: you can index a row (``board[y]``),
index a cell (``board[y][x]``), iterate rows, take ``len(board)``, and slice
rows (``board[:4]``). A row returned by ``__getitem__`` is the live inner
list, so ``board[y][x] = kind`` mutates the board in place.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import overload

from .pieces import BOARD_H, BOARD_W


class Board:
    """A fixed-size (BOARD_H × BOARD_W) grid where each cell is "" (empty)
    or a piece kind (``"I"`` … ``"O"``)."""

    W = BOARD_W
    H = BOARD_H

    def __init__(self, grid: list[list[str]]) -> None:
        self._grid = grid

    # -- constructors --------------------------------------------------

    @classmethod
    def empty(cls) -> Board:
        return cls([[""] * cls.W for _ in range(cls.H)])

    @classmethod
    def from_rows(cls, rows: list[list[str]]) -> Board:
        """Wrap an existing list-of-rows grid (used by tests and UI rigging)."""
        return cls(list(rows))

    # -- cell access ---------------------------------------------------

    def cell(self, x: int, y: int) -> str:
        """Kind at column ``x``, row ``y`` (caller guarantees bounds)."""
        return self._grid[y][x]

    def set_cell(self, x: int, y: int, kind: str) -> None:
        """Write ``kind`` to column ``x``, row ``y`` (caller guarantees bounds)."""
        self._grid[y][x] = kind

    def occupied(self, x: int, y: int) -> bool:
        """True if the in-bounds cell (x, y) holds a settled piece."""
        return bool(self._grid[y][x])

    # -- row operations ------------------------------------------------

    def full_rows(self) -> list[int]:
        """Indices (top to bottom) of the completely filled rows."""
        return [y for y, row in enumerate(self._grid) if all(row)]

    def collapse(self) -> None:
        """Remove full rows and re-pad the top with empty rows (in place)."""
        self._grid = [row for row in self._grid if not all(row)]
        while len(self._grid) < self.H:
            self._grid.insert(0, [""] * self.W)

    # -- list-of-rows compatibility protocol -------------------------

    @overload
    def __getitem__(self, y: int) -> list[str]: ...

    @overload
    def __getitem__(self, y: slice) -> list[list[str]]: ...

    def __getitem__(self, y: int | slice) -> list[str] | list[list[str]]:
        return self._grid[y]

    def __setitem__(self, y: int, row: list[str]) -> None:
        self._grid[y] = row

    def __iter__(self) -> Iterator[list[str]]:
        return iter(self._grid)

    def __len__(self) -> int:
        return len(self._grid)

    @property
    def rows(self) -> list[list[str]]:
        """The underlying grid (read-only view for inspection)."""
        return self._grid


__all__ = ["Board"]
