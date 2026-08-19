"""Unit tests for the pure scoring rules (tetris.scoring.Scorer).

These pin the full scoring table — normal clears, T-spins, back-to-back and
combo — so the engine refactor (delegating scoring to the Scorer) and any
future rule change stay auditable without driving the whole engine.
"""

from __future__ import annotations

import pytest

from tetris.scoring import ScoreBreakdown, Scorer


def bd(
    line_count: int = 0,
    spin: bool | None = None,
    combo: int = 0,
    b2b: bool = False,
    level: int = 1,
) -> ScoreBreakdown:
    return Scorer.breakdown(
        line_count=line_count, spin=spin, combo=combo, b2b=b2b, level=level
    )


# ---------------------------------------------------------------------------
# Normal line clears
# ---------------------------------------------------------------------------


def test_single() -> None:
    r = bd(line_count=1)
    assert (r.points, r.label, r.kind) == (100, "SINGLE", "clear")
    assert (r.combo_after, r.b2b_after, r.spins_delta) == (1, False, 0)


def test_double() -> None:
    r = bd(line_count=2)
    assert (r.points, r.label, r.kind) == (300, "DOUBLE", "clear")
    assert r.combo_after == 1


def test_triple() -> None:
    r = bd(line_count=3)
    assert (r.points, r.label, r.kind) == (500, "TRIPLE", "clear")


def test_tetris_starts_back_to_back() -> None:
    r = bd(line_count=4)
    assert (r.points, r.label, r.kind) == (800, "TETRIS", "tetris")
    assert r.b2b_after is True  # first Tetris arms B2B (no multiplier yet)


def test_scale_with_level() -> None:
    assert bd(line_count=1, level=3).points == 300
    assert bd(line_count=4, level=5).points == 4000


# ---------------------------------------------------------------------------
# Back-to-back
# ---------------------------------------------------------------------------


def test_b2b_tetris_multiplier() -> None:
    # Second consecutive Tetris gets 1.5x.
    assert bd(line_count=4, b2b=True).points == 1200
    assert bd(line_count=4, b2b=True).b2b_after is True


def test_b2b_reset_by_normal_clear() -> None:
    # A non-qualifying clear (single) breaks the chain.
    assert bd(line_count=1, b2b=True).b2b_after is False


# ---------------------------------------------------------------------------
# Combo
# ---------------------------------------------------------------------------


def test_combo_bonus_applied_and_extended() -> None:
    # Continuing a combo on a double: 300 + 50 * combo(1) * level(1) = 350.
    r = bd(line_count=2, combo=1)
    assert r.points == 350
    assert r.combo_after == 2


def test_combo_bonus_scales_with_level() -> None:
    # double @ level 3 = 300*3 = 900, plus 50 * combo(2) * level(3) = 300 -> 1200.
    assert bd(line_count=2, combo=2, level=3).points == 1200


# ---------------------------------------------------------------------------
# T-spins
# ---------------------------------------------------------------------------


def test_tspin_no_line_full() -> None:
    r = bd(spin=True, level=2)
    assert r.points == 800  # 400 * level 2
    assert (r.label, r.kind) == ("T-SPIN", "tspin")
    assert r.spins_delta == 1
    assert r.combo_after == 0  # no-line spin does not extend combo
    # and does not touch b2b:
    assert bd(spin=True, b2b=True).b2b_after is True
    assert bd(spin=True, b2b=False).b2b_after is False


def test_tspin_no_line_mini() -> None:
    r = bd(spin=False)
    assert r.points == 100
    assert (r.label, r.kind) == ("T-SPIN MINI", "tspin-mini")
    assert r.spins_delta == 0


def test_tspin_single_mini() -> None:
    r = bd(line_count=1, spin=False)
    assert r.points == 200
    assert (r.label, r.kind) == ("T-SPIN MINI", "tspin-mini")
    assert r.spins_delta == 0
    assert r.b2b_after is False  # single T-spin does not qualify for B2B


def test_tspin_double_full_with_b2b() -> None:
    # 1200 * 1.5 = 1800; qualifies (spin + 2 lines).
    r = bd(line_count=2, spin=True, b2b=True)
    assert r.points == 1800
    assert (r.label, r.kind) == ("T-SPIN", "tspin")
    assert r.spins_delta == 1
    assert r.b2b_after is True


def test_tspin_triple_scales_with_level() -> None:
    assert bd(line_count=3, spin=True, level=2).points == 3200  # 1600 * 2


# ---------------------------------------------------------------------------
# Sanity: the kind used for UI effects is one of the four known values
# ---------------------------------------------------------------------------


def test_kind_always_valid() -> None:
    valid = {"clear", "tetris", "tspin", "tspin-mini"}
    # Non-spin locks: 1-4 lines. T-spin locks: 0-3 lines (a single T piece
    # cannot clear 4 lines), never a non-spin/no-clear lock (nothing to score).
    for count in range(1, 5):
        assert bd(line_count=count, spin=None).kind in valid
    for count in range(4):
        for spin in (True, False):
            assert bd(line_count=count, spin=spin).kind in valid


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
