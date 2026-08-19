"""Core Tetris engine — pure Python, no terminal or file I/O.

Everything testable lives here. The curses UI (``main.py``) is a thin layer
on top of the ``Tetris`` class. Static SRS data lives in ``pieces.py`` and
scoring constants in ``scoring.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .board import Board
from .pieces import BOARD_H, BOARD_W, KICKS_I, KICKS_JLSTZ, MAX_START_LEVEL, PIECES
from .scoring import HARD_DROP_POINTS, SOFT_DROP_POINTS, Scorer

FLASH_FRAMES = 8        # frames a cleared row stays visible
LOCK_DELAY = 0.5        # grace period after landing before the piece locks
LOCK_RESET_MAX = 15     # move/rotate actions that can refresh the lock timer
QUEUE_LEN = 5           # pieces visible in the next-piece queue


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

    def __init__(
        self,
        rng: random.Random | None = None,
        start_level: int = 1,
    ) -> None:
        # An injectable RNG keeps the piece bag deterministic and isolated from
        # global state when provided. When omitted, we fall back to the module
        # level ``random`` (i.e. global ``random.seed`` still governs) so existing
        # callers/tests that seed the global RNG keep working unchanged.
        self._rng = rng
        self.bag: list[str] = []
        self.board: Board = Board.empty()
        self.score = 0
        self.lines = 0
        self.level = max(1, min(MAX_START_LEVEL, start_level))
        self.pieces = 0
        self.combo = 0
        self.b2b = False
        self.game_over = False
        self.paused = False
        self.drop_interval = max(0.05, 0.5 * (0.8 ** (self.level - 1)))
        self.holding: str | None = None
        self.can_hold = True
        self.pending_clears: list[int] = []
        self.flash_frames = 0
        self.queue: list[str] = [self._refill() for _ in range(QUEUE_LEN)]
        self.spins = 0
        self.events: list[Event] = []
        self.spawn_seq: int = 0  # bumped by _spawn; lets the UI detect spawns
        self.piece = self._spawn()
        self._grounded = False
        self._lock_t = 0.0
        self._resets = 0
        self._last_grav: float | None = None
        self._spin: bool | None = None  # T-spin of the last lock: True=full, False=mini

    # -- piece queue -------------------------------------------------

    @property
    def next_kind(self) -> str:
        """First queued piece (back-compat with the legacy 2-slot API)."""
        return self.queue[0]

    @property
    def next_kind2(self) -> str:
        return self.queue[1]

    def _refill(self) -> str:
        if not self.bag:
            self.bag = list(PIECES.keys())
            if self._rng is None:
                random.shuffle(self.bag)
            else:
                self._rng.shuffle(self.bag)
        return self.bag.pop()

    def _reset_fall_state(self) -> None:
        self._grounded = False
        self._resets = 0
        self._last_grav = None

    def _spawn(self) -> Piece:
        self.spawn_seq += 1
        kind = self.queue.pop(0)
        self.queue.append(self._refill())
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
            if cy >= 0 and self.board.occupied(cx, cy):
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
            return self.board.occupied(x, y)

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
            self.board.set_cell(cx, cy, self.piece.kind)
        self.pieces += 1
        self.can_hold = True
        self._spin = self._detect_spin()

        if self.game_over:
            self.piece = self._spawn()
            return

        full = self.board.full_rows()
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
            cx, cy = self.piece.x + 1, self.piece.y + 1
            bd = Scorer.breakdown(
                line_count=0, spin=full, combo=self.combo, b2b=self.b2b, level=self.level
            )
            self.score += bd.points
            self.spins += bd.spins_delta
            self.b2b = bd.b2b_after
            self.events.append(
                Event(text=f"{bd.label} +{bd.points}", kind=bd.kind, row=cy, center=(cx, cy))
            )
        self._spin = None

    def _commit_clears(self) -> None:
        count = len(self.pending_clears)
        rows = self.pending_clears
        self.pending_clears = []
        spin = self._spin
        self._spin = None

        bd = Scorer.breakdown(
            line_count=count, spin=spin, combo=self.combo, b2b=self.b2b, level=self.level
        )
        self.spins += bd.spins_delta
        self.combo = bd.combo_after
        self.b2b = bd.b2b_after
        self.score += bd.points
        self.lines += count
        self.level = self.lines // 10 + 1
        self.drop_interval = max(0.05, 0.5 * (0.8 ** (self.level - 1)))

        self.events.append(
            Event(text=f"{bd.label} +{bd.points}", kind=bd.kind, row=rows[-1])
        )

        self.board.collapse()
        self.piece = self._spawn()

    def _cleared_rows(self) -> list[int]:
        return self.board.full_rows()

    # -- helpers ---------------------------------------------------------

    def ghost_y(self) -> int:
        p = self.piece
        gy = p.y
        while not self._collides(Piece(p.kind, p.x, gy + 1, p.rot), p.rot):
            gy += 1
        return gy

    def full_rows(self) -> list[int]:
        return self._cleared_rows()

    def snapshot(self) -> dict[str, int | bool]:
        """A machine-readable view of engine-owned state.

        Single source of truth for the sidebar stats (see ``tetris.stats``).
        ``HighScores.best()`` is intentionally excluded — it is not engine
        state and is passed in by the caller that owns the score store.
        """
        return {
            "score": self.score,
            "lines": self.lines,
            "level": self.level,
            "combo": self.combo,
            "b2b": self.b2b,
            "spins": self.spins,
            "paused": self.paused,
            "game_over": self.game_over,
        }


__all__ = [
    "FLASH_FRAMES",
    "LOCK_DELAY",
    "LOCK_RESET_MAX",
    "Event",
    "Piece",
    "Tetris",
]
