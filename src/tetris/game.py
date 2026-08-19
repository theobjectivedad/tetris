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
TSPIN_SCORES = {1: (200, 800), 2: (1200, 1200), 3: (1600, 1600)}  # (mini, full)
TSPIN_NO_LINE = (100, 400)  # (mini, full), no lines cleared
COMBO_BONUS = 50        # per combo step, × level
B2B_MULTIPLIER = 1.5    # back-to-back Tetris / T-spin multi
SOFT_DROP_POINTS = 1    # per cell
HARD_DROP_POINTS = 2    # per cell
FLASH_FRAMES = 8        # frames a cleared row stays visible
LOCK_DELAY = 0.5        # grace period after landing before the piece locks
LOCK_RESET_MAX = 15     # move/rotate actions that can refresh the lock timer


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


@dataclass
class Event:
    """A UI effect the game requests: floating score text, corner flash, beeps."""

    text: str
    kind: str  # "clear" | "tetris" | "tspin" | "tspin-mini"
    row: int = 10
    center: tuple[int, int] | None = None  # T-spin center, for the corner flash


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
        self.next_kind2 = self._refill()
        self.spins = 0
        self.events: list[Event] = []
        self.piece = self._spawn()
        self._grounded = False
        self._lock_t = 0.0
        self._resets = 0
        self._last_grav: float | None = None
        self._spin: bool | None = None  # T-spin of the last lock: True=full, False=mini

    # -- piece queue -------------------------------------------------

    def _refill(self) -> str:
        if not self.bag:
            self.bag = list(PIECES.keys())
            random.shuffle(self.bag)
        return self.bag.pop()

    def _reset_fall_state(self) -> None:
        self._grounded = False
        self._resets = 0
        self._last_grav = None

    def _spawn(self) -> Piece:
        kind = self.next_kind
        self.next_kind = self.next_kind2
        self.next_kind2 = self._refill()
        self._reset_fall_state()
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

    def _register_shift(self, now: float | None) -> None:
        """A successful move/rotate while grounded refreshes the lock timer
        (up to LOCK_RESET_MAX times — the guideline reset cap)."""
        if now is None or not self._grounded or self._resets >= LOCK_RESET_MAX:
            return
        self._lock_t = now
        self._resets += 1

    def move(self, dx: int, now: float | None = None) -> bool:
        if self.game_over or self.frozen:
            return False
        p = Piece(self.piece.kind, self.piece.x + dx, self.piece.y, self.piece.rot)
        if not self._collides(p):
            self.piece = p
            self._register_shift(now)
            return True
        return False

    def rotate(self, d: int = 1, now: float | None = None) -> bool:
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
                self._register_shift(now)
                return True
        return False

    def soft_drop(self) -> bool:
        """Player-initiated drop: 1 pt/cell. On the floor it does nothing —
        the lock delay (see tick) decides when the piece locks."""
        if self.game_over or self.frozen:
            return False
        p = self.piece
        q = Piece(p.kind, p.x, p.y + 1, p.rot)
        if not self._collides(q):
            self.piece = q
            self.score += SOFT_DROP_POINTS
            return True
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
            self._reset_fall_state()
            if self._collides(self.piece):
                self.game_over = True
        self.can_hold = False

    # -- gravity / ticking ---------------------------------------------

    @property
    def frozen(self) -> bool:
        """True while a line-clear flash animation is pending."""
        return bool(self.pending_clears)

    def _can_fall(self) -> bool:
        p = self.piece
        return not self._collides(Piece(p.kind, p.x, p.y + 1, p.rot), p.rot)

    def tick(self, now: float) -> None:
        """Advance the simulation at monotonic time ``now``: applies gravity
        when it is due, and locks a grounded piece once it has rested for
        LOCK_DELAY seconds (refreshed by move/rotate, capped)."""
        if self.game_over or self.paused or self.frozen:
            return
        if self._can_fall():
            if self._grounded:
                self._grounded = False
                self._resets = 0
            if self._last_grav is None or now - self._last_grav >= self.drop_interval:
                self._last_grav = now
                p = self.piece
                self.piece = Piece(p.kind, p.x, p.y + 1, p.rot)
        else:
            if not self._grounded:
                self._grounded = True
                self._lock_t = now
            elif now - self._lock_t >= LOCK_DELAY:
                self._lock()

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

    def _detect_spin(self) -> bool | None:
        """T-spin corner rule: 3+ of the four diagonal corners around the
        T's center must be occupied (out-of-bounds counts as occupied,
        the ceiling does not). A 'full' spin also needs both front
        corners — the side the nub points at."""
        if self.piece.kind != "T":
            return None
        cx, cy = self.piece.x + 1, self.piece.y + 1

        def filled(x: int, y: int) -> bool:
            if y >= BOARD_H:
                return True  # the floor counts as occupied
            if y < 0:
                return False  # the ceiling does not
            if x < 0 or x >= BOARD_W:
                return True
            return bool(self.board[y][x])

        # corners: 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right
        corners = (
            filled(cx - 1, cy - 1),
            filled(cx + 1, cy - 1),
            filled(cx - 1, cy + 1),
            filled(cx + 1, cy + 1),
        )
        if sum(corners) < 3:
            return None
        front = {0: (0, 1), 1: (1, 3), 2: (2, 3), 3: (0, 2)}[self.piece.rot]
        return bool(corners[front[0]] and corners[front[1]])

    def _lock(self) -> None:
        for cx, cy in self.piece.cells():
            if cy < 0:
                self.game_over = True
                continue
            self.board[cy][cx] = self.piece.kind
        self.pieces += 1
        self.can_hold = True
        self._spin = self._detect_spin()

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
        if self._spin is not None:
            full = self._spin
            pts = (TSPIN_NO_LINE[1] if full else TSPIN_NO_LINE[0]) * self.level
            self.score += pts
            if full:
                self.spins += 1
            cx, cy = self.piece.x + 1, self.piece.y + 1
            self.events.append(
                Event(
                    text=f"{'T-SPIN' if full else 'T-SPIN MINI'} +{pts}",
                    kind="tspin" if full else "tspin-mini",
                    row=cy,
                    center=(cx, cy),
                )
            )
        self._spin = None

    def _commit_clears(self) -> None:
        count = len(self.pending_clears)
        rows = self.pending_clears
        self.pending_clears = []
        spin = self._spin
        self._spin = None

        if spin is not None:
            pts = TSPIN_SCORES[count][1 if spin else 0] * self.level
            label = "T-SPIN" if spin or count >= 2 else "T-SPIN MINI"
            if spin:
                self.spins += 1
        else:
            pts = SCORE_TABLE[count] * self.level
            label = {1: "SINGLE", 2: "DOUBLE", 3: "TRIPLE", 4: "TETRIS"}[count]

        # Back-to-back: Tetris or T-spin clearing 2+ lines.
        qualifies = count == 4 or (spin is not None and count >= 2)
        if qualifies:
            if self.b2b:
                pts = int(pts * B2B_MULTIPLIER)
            self.b2b = True
        elif count > 0:
            self.b2b = False

        if self.combo > 0:
            pts += COMBO_BONUS * self.combo * self.level
        self.combo = count > 0 and self.combo + 1 or 0

        self.score += pts
        self.lines += count
        self.level = self.lines // 10 + 1
        self.drop_interval = max(0.05, 0.5 * (0.8 ** (self.level - 1)))

        self.events.append(
            Event(
                text=f"{label} +{pts}",
                kind=(
                    "tetris" if count == 4
                    else ("tspin" if spin else "tspin-mini") if spin is not None
                    else "clear"
                ),
                row=rows[-1],
            )
        )

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
