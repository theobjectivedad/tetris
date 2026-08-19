"""Scoring rules and constants.

The :class:`Scorer` owns the point math for a single lock. It is a pure
function of its inputs — no game state, no I/O — so the full scoring table
(T-spin, back-to-back, combo, level) is unit-testable in isolation. The engine
delegates to it and is responsible for applying the returned deltas to its
state (score, lines, level, combo, b2b, spins counter).
"""

from __future__ import annotations

from dataclasses import dataclass

SCORE_TABLE = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}
TSPIN_SCORES = {1: (200, 800), 2: (1200, 1200), 3: (1600, 1600)}  # (mini, full)
TSPIN_NO_LINE = (100, 400)  # (mini, full), no lines cleared
COMBO_BONUS = 50        # per combo step, × level
B2B_MULTIPLIER = 1.5    # back-to-back Tetris / T-spin multi
SOFT_DROP_POINTS = 1    # per cell
HARD_DROP_POINTS = 2    # per cell

# Human-readable labels for normal line clears (no T-spin).
_CLEAR_LABELS = {1: "SINGLE", 2: "DOUBLE", 3: "TRIPLE", 4: "TETRIS"}


@dataclass(frozen=True)
class ScoreBreakdown:
    """The result of scoring one lock.

    ``points`` is the amount to add to the score; the ``*_after`` fields are
    the new values for the engine's running combo / back-to-back state, and
    ``spins_delta`` is how much to add to the T-spin counter.
    """

    points: int
    label: str
    kind: str          # "clear" | "tetris" | "tspin" | "tspin-mini"
    combo_after: int
    b2b_after: bool
    spins_delta: int


class Scorer:
    """Pure scoring rules for a single piece lock."""

    @staticmethod
    def breakdown(
        *,
        line_count: int,
        spin: bool | None,
        combo: int,
        b2b: bool,
        level: int,
    ) -> ScoreBreakdown:
        """Score a lock.

        Args:
            line_count: rows cleared by this lock (0 for a no-line T-spin).
            spin: ``True`` for a full T-spin, ``False`` for a mini, ``None``
                if the piece is not a T / not a spin.
            combo: the running combo count *before* this lock.
            b2b: whether back-to-back was already active *before* this lock.
            level: the level *before* this lock (level is recomputed after).
        """
        is_spin = spin is not None
        full = bool(spin)

        # -- base points -------------------------------------------------
        if is_spin:
            if line_count >= 1:
                base = TSPIN_SCORES[line_count][1 if full else 0]
            else:
                base = TSPIN_NO_LINE[1 if full else 0]
            label = "T-SPIN" if (full or line_count >= 2) else "T-SPIN MINI"
        else:
            base = SCORE_TABLE[line_count]
            label = _CLEAR_LABELS[line_count]

        pts = base * level

        # -- back-to-back (Tetris or T-spin clearing 2+ lines) -----------
        qualifies = line_count == 4 or (is_spin and line_count >= 2)
        if qualifies:
            if b2b:
                pts = int(pts * B2B_MULTIPLIER)
            b2b_after = True
        elif line_count > 0:
            b2b_after = False
        else:
            b2b_after = b2b  # no-line T-spin leaves b2b untouched

        # -- combo (only line clears extend it; the bonus applies then too)
        if line_count > 0 and combo > 0:
            pts += COMBO_BONUS * combo * level
        combo_after = (line_count > 0 and combo + 1) or 0

        # -- effect kind (drives floating text + beep count in the UI) ----
        if line_count == 4:
            kind = "tetris"
        elif is_spin:
            kind = "tspin" if full else "tspin-mini"
        else:
            kind = "clear"

        return ScoreBreakdown(
            points=pts,
            label=label,
            kind=kind,
            combo_after=combo_after,
            b2b_after=b2b_after,
            spins_delta=1 if full else 0,
        )


__all__ = [
    "B2B_MULTIPLIER",
    "COMBO_BONUS",
    "HARD_DROP_POINTS",
    "SCORE_TABLE",
    "SOFT_DROP_POINTS",
    "TSPIN_NO_LINE",
    "TSPIN_SCORES",
    "ScoreBreakdown",
    "Scorer",
]
