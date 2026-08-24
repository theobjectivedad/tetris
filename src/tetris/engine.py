"""Core Tetris engine — pure Python, no terminal or file I/O.

Everything testable lives here. The curses UI (``main.py``) is a thin layer
on top of the ``Tetris`` class. Static SRS data lives in ``pieces.py`` and
scoring constants in ``scoring.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .board import Board
from .pieces import (
    BOARD_H,
    BOARD_W,
    KICKS_180_JLSTZ,
    KICKS_I,
    KICKS_JLSTZ,
    MAX_START_LEVEL,
    PIECES,
)
from .scoring import HARD_DROP_POINTS, SOFT_DROP_POINTS, Scorer

FLASH_FRAMES = 8        # frames a cleared row stays visible
LOCK_DELAY = 0.5        # grace period after landing before the piece locks
LOCK_RESET_MAX = 15     # move/rotate actions that can refresh the lock timer
QUEUE_LEN = 5           # pieces visible in the next-piece queue
SPRINT_LINES = 10       # lines to clear to win a sprint (P11)
SPRINT_TIME = 180.0     # seconds allotted for a sprint (P11)
DANGER_TOP_ROWS = 4     # P24: the UI danger bar engages when the stack
                        # reaches this row (or the ceiling)


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
    """A UI effect the game requests: floating score text, corner flash, beeps.

    ``kind`` shares its vocabulary with ``scoring.ScoreBreakdown.kind``
    ("clear" | "tetris" | "tspin" | "tspin-mini"), plus "levelup" for the
    milestone when a line clear raises the level; the kind drives the UI's
    beeps, the T-spin corner flash, and the big-clear board shake. ``row``
    is the board row the floating text rises from; ``center`` is the
    T-spin center (the corner-flash anchor); ``lines`` (P25) is how many
    rows this lock cleared — 0 for a no-line T-spin — so the UI can tell a
    B2B-qualifying T-spin (2+ lines) from a no-line one (which leaves the
    back-to-back streak untouched).

    ``combo``/``b2b`` (R3) are the post-commit combo and back-to-back
    state stamped at scoring time, so the UI's COMBO/B2B floaters read
    the event's snapshot instead of the engine's live state.
    """

    text: str
    kind: str
    row: int
    center: tuple[int, int] | None = None
    lines: int = 0
    combo: int = 0
    b2b: bool = False


class Tetris:
    """Full game state. All mutation goes through the methods below."""

    def __init__(
        self,
        rng: random.Random | None = None,
        start_level: int = 1,
        sprint: bool = False,
    ) -> None:
        # -- randomness and piece supply ---------------------------------
        # An injectable RNG keeps the piece bag deterministic and isolated
        # from global state when provided. When omitted, a fresh
        # ``random.Random()`` (seeded from os.urandom) is used: every game
        # gets its own private stream and the global module RNG is never
        # read.
        self._rng = rng or random.Random()
        self.bag: list[str] = []
        self.queue: list[str] = [self._refill() for _ in range(QUEUE_LEN)]

        # -- board and live piece -----------------------------------------
        self.board: Board = Board.empty()
        self.holding: str | None = None
        self.can_hold = True
        self.spawn_seq = 0  # bumped by _spawn; lets the UI detect spawns

        # -- scoring -------------------------------------------------------
        self.score = 0
        self.lines = 0
        self.level = max(1, min(MAX_START_LEVEL, start_level))
        self.pieces = 0
        self.combo = 0
        self.best_combo = 0  # highest combo reached this game
        self.b2b = False
        self.spins = 0

        # -- sprint mode (P11) ---------------------------------------------
        # A 10-line time attack. ``won`` is True only on a win (lines
        # reached before time up); ``time_left`` counts down and is None in
        # classic mode.
        self.sprint = sprint
        self.won = False
        self.time_left: float | None = SPRINT_TIME if sprint else None

        # -- lifecycle and transient effects -------------------------------
        self.game_over = False
        self._paused = False
        self.version = 0  # bumped on every observable state change
        self.play_time = 0.0  # real seconds played (excl. pause/game over)
        self.drop_interval = self._drop_interval_for(self.level)
        self.pending_clears: list[int] = []
        self.flash_frames = 0
        self.events: list[Event] = []

        # -- private fall/lock timing ---------------------------------------
        # Declared before the first _spawn so _reset_fall_state only ever
        # refreshes existing state.
        self._grounded = False
        self._lock_since = 0.0  # when the piece started resting (refreshed)
        self._lock_resets = 0  # lock-timer refreshes used since grounding
        self._last_gravity_at: float | None = None
        self._last_tick_at: float | None = None  # last tick() time, for play_time
        self._pending_spin: bool | None = None  # last lock's T-spin: True=full, False=mini
        # P22: the cells of the most recently locked piece (no-clear locks
        # only; None otherwise) — the UI flashes them when the next piece
        # spawns (same hook as the spawn glide).
        self.last_lock: list[tuple[int, int]] | None = None

        # First piece (may flip game_over if it collides on spawn).
        self.piece = self._spawn()

    # -- pause (version-bumping property) ------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        if value != self._paused:
            self._paused = value
            self.version += 1

    def _set_game_over(self) -> None:
        """Transition to game over, bumping the version once on the flip."""
        if not self.game_over:
            self.game_over = True
            self.version += 1

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
            self._rng.shuffle(self.bag)
        return self.bag.pop()

    def _reset_fall_state(self) -> None:
        self._grounded = False
        self._lock_resets = 0
        self._last_gravity_at = None

    def _spawn(self) -> Piece:
        self.spawn_seq += 1
        kind = self.queue.pop(0)
        self.queue.append(self._refill())
        self._reset_fall_state()
        piece = Piece(kind=kind, x=BOARD_W // 2 - 2, y=0)
        if self._collides(piece):
            self._set_game_over()
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
        if now is None or not self._grounded or self._lock_resets >= LOCK_RESET_MAX:
            return
        self._lock_since = now
        self._lock_resets += 1

    def move(self, dx: int, now: float | None = None) -> bool:
        if self.game_over or self.frozen:
            return False
        p = Piece(self.piece.kind, self.piece.x + dx, self.piece.y, self.piece.rot)
        if not self._collides(p):
            self.piece = p
            self._register_shift(now)
            self.version += 1
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
                self.version += 1
                return True
        return False

    def rotate_180(self, now: float | None = None) -> bool:
        """Rotate 180° with guideline kicks. The I piece is excluded (its 180°
        is a no-op modulo the 4×4 box row), so this always refuses I."""
        if self.game_over or self.frozen:
            return False
        p = self.piece
        if p.kind == "I":
            return False
        new_rot = (p.rot + 2) % 4
        for dx, dy in KICKS_180_JLSTZ[(p.rot, new_rot)]:
            q = Piece(p.kind, p.x + dx, p.y - dy, new_rot)  # SRS y is up; ours is down
            if not self._collides(q, new_rot):
                self.piece = q
                self._register_shift(now)   # 180° is a rotate action: refreshes lock delay under LOCK_RESET_MAX
                self.version += 1           # P5 render-skip counter — REQUIRED or the UI won't redraw
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
            self.version += 1
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
        self.version += 1
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
                self._set_game_over()
        self.version += 1
        self.can_hold = False

    # -- gravity / ticking ---------------------------------------------

    @property
    def frozen(self) -> bool:
        """True while a line-clear flash animation is pending."""
        return bool(self.pending_clears)

    @property
    def grounded(self) -> bool:
        """P23: True while the live piece rests on the floor or a stack —
        the lock delay is ticking. The UI pulses the piece in this state so
        the player can see when it is about to lock."""
        return self._grounded

    @property
    def stack_top(self) -> int | None:
        """P24: the topmost row (smallest y) holding a settled cell, or
        None on an empty board. The UI draws the danger bar while the
        stack is within ``DANGER_TOP_ROWS`` of the ceiling."""
        for y, row in enumerate(self.board):
            if any(row):
                return y
        return None

    def _can_fall(self) -> bool:
        p = self.piece
        return not self._collides(Piece(p.kind, p.x, p.y + 1, p.rot), p.rot)

    def tick(self, now: float) -> None:
        """Advance the simulation at monotonic time ``now``: accumulates
        play time, applies gravity when it is due, and locks a grounded
        piece once it has rested for LOCK_DELAY seconds (refreshed by
        move/rotate, capped).

        Play time counts real elapsed time while the game is live and not
        paused (including line-clear flashes); the tick timestamp keeps
        advancing while paused, so resuming produces no time jump."""
        if self.game_over:
            return
        if self._last_tick_at is not None and not self._paused:
            self.play_time += now - self._last_tick_at
            if self.sprint and self.time_left is not None:
                self.time_left -= now - self._last_tick_at
        self._last_tick_at = now
        # Sprint time-up: a loss (won stays False). Stops the countdown
        # and the whole game.
        if self.sprint and self.time_left is not None and self.time_left <= 0:
            self.time_left = 0.0
            self._set_game_over()
            return
        if self._paused or self.frozen:
            return
        if self._can_fall():
            if self._grounded:
                self._grounded = False
                self._lock_resets = 0
            if (
                self._last_gravity_at is None
                or now - self._last_gravity_at >= self.drop_interval
            ):
                self._last_gravity_at = now
                p = self.piece
                self.piece = Piece(p.kind, p.x, p.y + 1, p.rot)
                self.version += 1
        else:
            if not self._grounded:
                self._grounded = True
                self._lock_since = now
            elif now - self._lock_since >= LOCK_DELAY:
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
        self.version += 1
        self.last_lock = None
        for cx, cy in self.piece.cells():
            if cy < 0:
                self._set_game_over()
                continue
            self.board.set_cell(cx, cy, self.piece.kind)
        self.pieces += 1
        self.can_hold = True
        self._pending_spin = self._detect_spin()

        if self.game_over:
            self.piece = self._spawn()
            return

        full = self.board.full_rows()
        if full:
            self.pending_clears = full
            self.flash_frames = FLASH_FRAMES
        else:
            # P22: remember the just-locked cells so the UI can flash them;
            # clearing locks keep last_lock None (the line-clear flash is the
            # effect there).
            self.last_lock = [(cx, cy) for cx, cy in self.piece.cells() if cy >= 0]
            self._on_lock_no_clears()
            self.piece = self._spawn()

    def _on_lock_no_clears(self) -> None:
        # Breaking a combo resets it.
        self.combo = 0
        self.best_combo = max(self.best_combo, self.combo)
        if self._pending_spin is not None:
            full = self._pending_spin
            cx, cy = self.piece.x + 1, self.piece.y + 1
            bd = Scorer.breakdown(
                line_count=0, spin=full, combo=self.combo, b2b=self.b2b, level=self.level
            )
            self.score += bd.points
            self.spins += bd.spins_delta
            self.b2b = bd.b2b_after
            self.events.append(
                Event(
                    text=f"{bd.label} +{bd.points}", kind=bd.kind, row=cy, center=(cx, cy),
                    combo=self.combo, b2b=self.b2b,
                )
            )
        self._pending_spin = None

    def _commit_clears(self) -> None:
        count = len(self.pending_clears)
        rows = self.pending_clears
        self.pending_clears = []
        spin = self._pending_spin
        self._pending_spin = None

        bd = Scorer.breakdown(
            line_count=count, spin=spin, combo=self.combo, b2b=self.b2b, level=self.level
        )
        self.spins += bd.spins_delta
        self.combo = bd.combo_after
        self.best_combo = max(self.best_combo, self.combo)
        self.b2b = bd.b2b_after
        self.score += bd.points
        self.lines += count
        old_level = self.level
        self.level = self.lines // 10 + 1
        if self.level > old_level:
            # Milestone feedback (P16): the UI floats "LEVEL UP" and beeps;
            # the drop interval below makes the change visible in play.
            self.events.append(Event(text="LEVEL UP", kind="levelup", row=BOARD_H // 2))
        self.drop_interval = self._drop_interval_for(self.level)

        self.events.append(
            Event(
                text=f"{bd.label} +{bd.points}", kind=bd.kind, row=rows[-1], lines=count,
                combo=self.combo, b2b=self.b2b,
            )
        )

        self.board.collapse()
        # Sprint win (P11): reaching the line target completes the run — no
        # next piece spawns.
        if self.sprint and self.lines >= SPRINT_LINES and not self.won:
            self.won = True
            self.game_over = True
            self.version += 1
            return
        self.piece = self._spawn()
        self.version += 1

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _drop_interval_for(level: int) -> float:
        """Gravity interval (seconds) at ``level``: faster each level,
        floored at 0.05 s."""
        return max(0.05, 0.5 * (0.8 ** (level - 1)))

    def ghost_y(self) -> int:
        p = self.piece
        gy = p.y
        while not self._collides(Piece(p.kind, p.x, gy + 1, p.rot), p.rot):
            gy += 1
        return gy

    def full_rows(self) -> list[int]:
        """Indices of the completely filled rows (top to bottom)."""
        return self.board.full_rows()

    def snapshot(self) -> dict[str, int | bool | float | None]:
        """A machine-readable view of engine-owned state.

        Single source of truth for the sidebar stats (see ``tetris.stats``).
        ``HighScores.best()`` is intentionally excluded — it is not engine
        state and is passed in by the caller that owns the score store.
        ``time_left`` is None in classic mode (P11 sprint fields).
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
            "time": self.play_time,
            "best_combo": self.best_combo,
            "sprint": self.sprint,
            "won": self.won,
            "time_left": self.time_left,
        }


__all__ = [
    "DANGER_TOP_ROWS",
    "FLASH_FRAMES",
    "LOCK_DELAY",
    "LOCK_RESET_MAX",
    "SPRINT_LINES",
    "SPRINT_TIME",
    "Event",
    "Piece",
    "Tetris",
]
