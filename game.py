"""Tetris game logic — pure Python, no terminal I/O.

Everything testable lives here. The curses UI (main.py) is a thin layer
on top of the ``Tetris`` class.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

BOARD_W = 10
BOARD_H = 20

# Piece definitions: list of rotation states (SRS), each a list of (x, y) offsets.
PIECES: dict[str, list[list[tuple[int, int]]]] = {
    "I": [
        [(0, 0), (1, 0), (2, 0), (3, 0)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 0), (1, 0), (2, 0), (3, 0)],
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
        [(0, 1), (0, 2), (1, 2), (2, 2)],
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

SCORE_TABLE = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}
COMBO_BONUS = 50        # per combo step, × level
B2B_MULTIPLIER = 1.5    # back-to-back Tetris
SOFT_DROP_POINTS = 1    # per cell
HARD_DROP_POINTS = 2    # per cell
FLASH_FRAMES = 8        # frames a cleared row stays visible


@dataclass
class Piece:
    kind: str
    x: int
    y: int
    rot: int = 0

    def cells(self) -> list[tuple[int, int]]:
        return [(self.x + dx, self.y + dy) for dx, dy in PIECES[self.kind][self.rot]]

    def cells_at_rot(self, rot: int) -> list[tuple[int, int]]:
        return [(self.x + dx, self.y + dy) for dx, dy in PIECES[self.kind][rot]]


class Tetris:
    """Full game state. All mutation goes through the methods below."""

    def __init__(self) -> None:
        self.bag: list[str] = []
        self.board: list[list[str]] = [[""] * BOARD_W for _ in range(BOARD_H)]
        self.score = 0
        self.lines = 0
        self.level = 1
        self.pieces = 0
        self.combo = 0
        self.b2b = False
        self.game_over = False
        self.paused = False
        self.drop_interval = 0.5
        self.holding: str | None = None
        self.can_hold = True
        self.pending_clears: list[int] = []
        self.flash_frames = 0
        self.next_kind = self._refill()
        self.piece = self._spawn()

    # -- piece queue -------------------------------------------------

    def _refill(self) -> str:
        if not self.bag:
            self.bag = list(PIECES.keys())
            random.shuffle(self.bag)
        return self.bag.pop()

    def _spawn(self) -> Piece:
        kind = self.next_kind
        self.next_kind = self._refill()
        piece = Piece(kind=kind, x=BOARD_W // 2 - 2, y=0)
        if self._collides(piece):
            self.game_over = True
        return piece

    # -- collision ---------------------------------------------------

    def _collides(self, piece: Piece, rot: int | None = None) -> bool:
        cells = piece.cells_at_rot(rot) if rot is not None else piece.cells()
        for cx, cy in cells:
            if cx < 0 or cx >= BOARD_W or cy >= BOARD_H:
                return True
            if cy >= 0 and self.board[cy][cx]:
                return True
        return False

    # -- player actions -----------------------------------------------

    def move(self, dx: int) -> bool:
        if self.game_over or self.frozen:
            return False
        p = Piece(self.piece.kind, self.piece.x + dx, self.piece.y, self.piece.rot)
        if not self._collides(p):
            self.piece = p
            return True
        return False

    def rotate(self, d: int = 1) -> bool:
        """Rotate clockwise (d=1) or counter-clockwise (d=-1) with SRS kicks."""
        if self.game_over or self.frozen:
            return False
        p = self.piece
        new_rot = (p.rot + d) % 4
        table = KICKS_I if p.kind == "I" else KICKS_JLSTZ
        for dx, dy in table[(p.rot, new_rot)]:
            q = Piece(p.kind, p.x + dx, p.y - dy, new_rot)  # SRS y is up; ours is down
            if not self._collides(q, new_rot):
                self.piece = q
                return True
        return False

    def soft_drop(self) -> bool:
        return self._move_down(award=True)

    def _move_down(self, award: bool) -> bool:
        if self.game_over or self.frozen:
            return False
        p = self.piece
        q = Piece(p.kind, p.x, p.y + 1, p.rot)
        if not self._collides(q):
            self.piece = q
            if award:
                self.score += SOFT_DROP_POINTS
            return True
        self._lock()
        return False

    def hard_drop(self) -> int:
        """Drop instantly. Returns cells fallen (for scoring/effects)."""
        if self.game_over or self.frozen:
            return 0
        p = self.piece
        dist = 0
        while not self._collides(Piece(p.kind, p.x, p.y + 1, p.rot), p.rot):
            p = Piece(p.kind, p.x, p.y + 1, p.rot)
            dist += 1
        self.piece = p
        self.score += HARD_DROP_POINTS * dist
        self._lock()
        return dist

    def hold(self) -> None:
        """Hold the current piece (swap with hold slot if occupied)."""
        if not self.can_hold or self.game_over or self.frozen:
            return
        kind = self.piece.kind
        if self.holding is None:
            self.holding = kind
            self.piece = self._spawn()
        else:
            held = self.holding
            self.holding = kind
            self.piece = Piece(held, x=BOARD_W // 2 - 2, y=0)
            if self._collides(self.piece):
                self.game_over = True
        self.can_hold = False

    # -- gravity / ticking ---------------------------------------------

    @property
    def frozen(self) -> bool:
        """True while a line-clear flash animation is pending."""
        return bool(self.pending_clears)

    def tick(self) -> None:
        """Advance one gravity step (call when drop_interval has elapsed)."""
        if self.game_over or self.paused or self.frozen:
            return
        self._move_down(award=False)

    def advance_flash(self) -> bool:
        """Advance flash animation one frame; returns True when it finishes
        (rows removed, scoring applied, next piece spawned)."""
        if not self.pending_clears:
            return True
        self.flash_frames -= 1
        if self.flash_frames > 0:
            return False
        self._commit_clears()
        return True

    # -- locking / clearing -------------------------------------------

    def _lock(self) -> None:
        for cx, cy in self.piece.cells():
            if cy < 0:
                self.game_over = True
                continue
            self.board[cy][cx] = self.piece.kind
        self.pieces += 1
        self.can_hold = True

        if self.game_over:
            self.piece = self._spawn()
            return

        full = [y for y, row in enumerate(self.board) if all(row)]
        if full:
            self.pending_clears = full
            self.flash_frames = FLASH_FRAMES
        else:
            self._on_lock_no_clears()
            self.piece = self._spawn()

    def _on_lock_no_clears(self) -> None:
        # Breaking a combo resets it.
        self.combo = 0

    def _commit_clears(self) -> None:
        count = len(self.pending_clears)
        self.pending_clears = []

        points = SCORE_TABLE[count] * self.level
        if count == 4:
            if self.b2b:
                points = int(points * B2B_MULTIPLIER)
            self.b2b = True
        elif count > 0:
            self.b2b = False

        if self.combo > 0:
            points += COMBO_BONUS * self.combo * self.level
        self.combo = count > 0 and self.combo + 1 or 0

        self.score += points
        self.lines += count
        self.level = self.lines // 10 + 1
        self.drop_interval = max(0.05, 0.5 * (0.8 ** (self.level - 1)))

        self.board = [row for row in self.board if not all(row)]
        while len(self.board) < BOARD_H:
            self.board.insert(0, [""] * BOARD_W)
        self.piece = self._spawn()

    def _cleared_rows(self) -> list[int]:
        return [y for y, row in enumerate(self.board) if all(row)]

    # -- helpers ---------------------------------------------------------

    def ghost_y(self) -> int:
        p = self.piece
        gy = p.y
        while not self._collides(Piece(p.kind, p.x, gy + 1, p.rot), p.rot):
            gy += 1
        return gy

    def full_rows(self) -> list[int]:
        return self._cleared_rows()


# ---------------------------------------------------------------------------
# High scores
# ---------------------------------------------------------------------------

def default_scores_path() -> Path:
    env = os.environ.get("TETRIS_SCORES")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "terminal-tetris" / "scores.json"


class HighScores:
    """Top-5 score persistence, one JSON file."""

    MAX = 5

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_scores_path()
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.entries = data[: self.MAX]
            except (json.JSONDecodeError, OSError):
                self.entries = []

    def best(self) -> int:
        return self.entries[0]["score"] if self.entries else 0

    def record(self, score: int, lines: int, level: int) -> int | None:
        """Insert a result; returns its rank (0-based) or None if not top-5."""
        if score <= 0:
            return None
        entry = {
            "score": score,
            "lines": lines,
            "level": level,
            "date": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.entries.append(entry)
        self.entries.sort(key=lambda e: e["score"], reverse=True)
        self.entries = self.entries[: self.MAX]
        rank = next((i for i, e in enumerate(self.entries) if e is entry), None)
        self._save()
        return rank

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, indent=2))
        except OSError:
            pass  # never let score saving crash the game
