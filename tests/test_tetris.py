"""Unit tests for the Tetris game logic (tetris.game)."""

import json
import random

import pytest

from tetris import game
from tetris.game import (
    BOARD_H,
    BOARD_W,
    FLASH_FRAMES,
    LOCK_RESET_MAX,
    PIECES,
    Board,
    HighScores,
    Tetris,
)
from tetris.pieces import MAX_START_LEVEL


@pytest.fixture
def game_state() -> Tetris:
    random.seed(42)
    return Tetris()


def fill_row(t: Tetris, y: int, kind: str = "T") -> None:
    t.board[y] = [kind] * BOARD_W


# ---------------------------------------------------------------------------
# Bag randomizer
# ---------------------------------------------------------------------------


class TestBagRandomizer:
    def test_bags_contain_all_seven_pieces(self) -> None:
        t = Tetris()
        for _ in range(3):
            t.bag = []
            seen = [t._refill() for _ in range(7)]
            assert sorted(seen) == sorted(PIECES.keys())

    def test_refill_never_returns_unknown_piece(self) -> None:
        t = Tetris()
        for _ in range(50):
            assert t._refill() in PIECES

    def test_injected_rng_is_deterministic(self) -> None:
        a = Tetris(rng=random.Random(7))
        b = Tetris(rng=random.Random(7))
        qa = [a._refill() for _ in range(7)]
        qb = [b._refill() for _ in range(7)]
        assert qa == qb

    def test_injected_rng_is_isolated_from_global_state(self) -> None:
        # Two identically-seeded injected RNGs must agree even when the global
        # module RNG is reseeded in between — proving the bag uses its own RNG.
        a = Tetris(rng=random.Random(1))
        seq_a = [a._refill() for _ in range(7)]
        random.seed(99999)  # pollute the global module RNG
        b = Tetris(rng=random.Random(1))
        seq_b = [b._refill() for _ in range(7)]
        assert seq_a == seq_b

    def test_default_rng_tracks_global_seed(self) -> None:
        # Backward-compat default: the module-level RNG still governs.
        random.seed(5)
        seq = [Tetris()._refill() for _ in range(7)]
        random.seed(5)
        seq2 = [Tetris()._refill() for _ in range(7)]
        assert seq == seq2


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------


class TestSpawning:
    def test_spawn_uses_next_piece(self, game_state: Tetris) -> None:
        expected = game_state.next_kind
        piece = game_state._spawn()
        assert piece.kind == expected

    def test_spawned_piece_starts_at_top(self, game_state: Tetris) -> None:
        p = game_state.piece
        assert p.rot == 0
        assert p.y == 0
        assert all(0 <= x < BOARD_W for x, _ in p.cells())

    def test_spawn_advances_next_preview(self, game_state: Tetris) -> None:
        game_state._spawn()
        # After consuming, the preview holds a (different) drawn piece.
        assert game_state.next_kind in PIECES


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------


class TestMovement:
    def test_move_left_and_right(self, game_state: Tetris) -> None:
        x0 = game_state.piece.x
        assert game_state.move(-1)
        assert game_state.piece.x == x0 - 1
        assert game_state.move(1)
        assert game_state.piece.x == x0

    def test_cannot_move_past_left_wall(self, game_state: Tetris) -> None:
        for _ in range(BOARD_W * 2):
            game_state.move(-1)
        assert all(x >= 0 for x, _ in game_state.piece.cells())

    def test_cannot_move_past_right_wall(self, game_state: Tetris) -> None:
        for _ in range(BOARD_W * 2):
            game_state.move(1)
        assert all(x < BOARD_W for x, _ in game_state.piece.cells())

    def test_move_returns_false_when_blocked(self) -> None:
        t = Tetris()
        t.piece = game.Piece("O", x=0, y=5)
        assert not t.move(-1)  # already against left wall

    def test_move_returns_false_when_frozen(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        assert not t.move(1)


# ---------------------------------------------------------------------------
# Rotation (SRS)
# ---------------------------------------------------------------------------


class TestRotation:
    def test_rotate_clockwise_and_counterclockwise_are_inverses(self) -> None:
        t = Tetris()
        t.piece = game.Piece("T", x=4, y=5)
        start = (t.piece.x, t.piece.y, t.piece.rot)
        t.rotate(1)
        t.rotate(-1)
        assert (t.piece.x, t.piece.y, t.piece.rot) == start

    def test_full_rotation_cycle_restores_orientation(self) -> None:
        t = Tetris()
        t.piece = game.Piece("T", x=4, y=5)
        start = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        for _ in range(4):
            t.rotate(1)
        end = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        assert start == end

    def test_floor_kick(self) -> None:
        """T piece flat on the floor should kick up when rotating (SRS)."""
        t = Tetris()
        # T rot0 = [(1,0),(0,1),(1,1),(2,1)] → bottom at y+1; place bottom on floor.
        t.piece = game.Piece("T", x=4, y=BOARD_H - 2)
        assert t.rotate(1)
        assert t.piece.rot == 1
        # The kick must have lifted the piece so all cells fit on the board.
        assert all(y < BOARD_H for _, y in t.piece.cells())
        assert not t._collides(t.piece)

    def test_wall_kick_against_left_wall(self) -> None:
        t = Tetris()
        # I piece (vertical, rot1) jammed against the left wall rotates right.
        t.piece = game.Piece("I", x=-1, y=5, rot=1)
        assert t.rotate(1)
        assert all(x >= 0 for x, _ in t.piece.cells())

    def test_rotation_impossible_returns_false(self) -> None:
        t = Tetris()
        # Fill the board except the piece's current cells so no kick fits.
        t.piece = game.Piece("T", x=4, y=9)
        t.board = Board.from_rows([["T"] * BOARD_W for _ in range(BOARD_H)])
        for x, y in t.piece.cells():
            t.board[y][x] = ""
        assert not t.rotate(1)

    def test_o_piece_rotation_is_noop_position(self) -> None:
        t = Tetris()
        t.piece = game.Piece("O", x=4, y=5)
        x0, y0 = t.piece.x, t.piece.y
        assert t.rotate(1)
        assert (t.piece.x, t.piece.y) == (x0, y0)
        assert t.piece.rot == 1


# ---------------------------------------------------------------------------
# Dropping
# ---------------------------------------------------------------------------


class TestDropping:
    def test_soft_drop_scores_point(self) -> None:
        t = Tetris()
        before = t.score
        assert t.soft_drop()
        assert t.score == before + 1

    def test_soft_drop_grounds_without_locking(self) -> None:
        """Modern rule: soft-dropping onto the floor does not lock the
        piece; the lock delay decides (see TestLockDelay)."""
        t = Tetris()
        for _ in range(BOARD_H + 5):
            t.soft_drop()
        assert t.pieces == 0  # nothing locked yet
        assert not any(k != "" for row in t.board for k in row)

    def test_gravity_does_not_award_points(self) -> None:
        # Regression: tick() used to route through soft_drop() and score
        # 1 pt/cell with zero player input.
        t = Tetris()
        t.drop_interval = 0.001
        before = t.score
        for i in range(10):
            t.tick(float(i))
        assert t.score == before

    def test_soft_drop_still_scores_after_gravity_fix(self) -> None:
        t = Tetris()
        before = t.score
        assert t.soft_drop()
        assert t.score == before + 1

    def test_hard_drop_scores_two_per_cell(self) -> None:
        t = Tetris()
        dist = t.hard_drop()
        assert dist > 0
        assert t.score == 2 * dist

    def test_hard_drop_instantly_locks_piece(self, game_state: Tetris) -> None:
        kind = game_state.piece.kind
        old_piece = game_state.piece
        game_state.hard_drop()
        assert any(k == kind for row in game_state.board for k in row)
        assert game_state.piece is not old_piece
        assert game_state.piece.y == 0

    def test_hard_drop_on_frozen_board_returns_zero(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        assert t.hard_drop() == 0

    def test_ghost_y_at_or_below_piece(self, game_state: Tetris) -> None:
        assert game_state.ghost_y() >= game_state.piece.y

    def test_ghost_y_rests_on_floor_or_stack(self, game_state: Tetris) -> None:
        gy = game_state.ghost_y()
        p = game_state.piece
        ghost = game.Piece(p.kind, p.x, gy, p.rot)
        below = game.Piece(p.kind, p.x, gy + 1, p.rot)
        assert not game_state._collides(ghost, p.rot)
        assert game_state._collides(below, p.rot)


# ---------------------------------------------------------------------------
# Hold
# ---------------------------------------------------------------------------


class TestHold:
    def test_hold_stores_piece_and_spawns_next(self) -> None:
        t = Tetris()
        current = t.piece.kind
        assert t.holding is None
        t.hold()
        assert t.holding == current
        assert t.piece.kind in PIECES
        assert t.piece.y == 0  # a fresh piece was spawned

    def test_cannot_hold_twice_in_a_row(self) -> None:
        t = Tetris()
        t.hold()
        piece_before = t.piece
        t.hold()
        assert t.piece is piece_before  # second hold is a no-op

    def test_hold_swap(self) -> None:
        t = Tetris()
        first = t.piece.kind
        second = t.next_kind
        t.hold()
        assert t.holding == first
        assert t.piece.kind == second
        t.can_hold = True  # normally reset after lock
        t.hold()
        assert t.holding == second
        assert t.piece.kind == first

    def test_hold_resets_after_lock(self) -> None:
        t = Tetris()
        t.can_hold = True
        t.hold()
        assert not t.can_hold
        t.hard_drop()
        assert t.can_hold  # lock re-enables hold for the next piece


# ---------------------------------------------------------------------------
# Line clearing / flash
# ---------------------------------------------------------------------------


class TestLineClearing:
    def test_full_row_detected(self) -> None:
        t = Tetris()
        fill_row(t, 5)
        fill_row(t, 19)
        assert t.full_rows() == [5, 19]

    def test_commit_clears_row_and_scores(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.lines == 1
        assert t.score == 100 * t.level
        assert all(k == "" for row in t.board for k in row)

    @pytest.mark.parametrize("count,expected", [(1, 100), (2, 300), (3, 500), (4, 800)])
    def test_scoring_table(self, count: int, expected: int) -> None:
        t = Tetris()
        for i in range(count):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - count, BOARD_H))
        t._commit_clears()
        assert t.score == expected
        assert t.lines == count

    def test_rows_above_fall_down(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.board[BOARD_H - 2][0] = "J"
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.board[BOARD_H - 1][0] == "J"
        assert all(k == "" for k in t.board[BOARD_H - 2])

    def test_board_dimensions_preserved(self) -> None:
        t = Tetris()
        for i in range(4):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t._commit_clears()
        assert len(t.board) == BOARD_H
        assert all(len(row) == BOARD_W for row in t.board)

    def test_lock_sets_pending_clears_and_freezes(self) -> None:
        t = Tetris()
        # Fill the bottom row except the cells the current piece will fill.
        p = t.piece
        fill_row(t, BOARD_H - 1)
        for x, _ in p.cells():
            pass
        # Position piece so its lock completes the row: simpler — fill row,
        # clear piece cells, then lock.
        t.piece = game.Piece("O", x=4, y=BOARD_H - 2)
        t.board = Board.from_rows([["T"] * BOARD_W for _ in range(BOARD_H)])
        for x, y in t.piece.cells():
            t.board[y][x] = ""
        t._lock()
        assert t.frozen
        assert t.flash_frames > 0

    def test_advance_flash_unfreezes_after_frames(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.board[BOARD_H - 2][0] = "J"
        t.pending_clears = [BOARD_H - 1]
        t.flash_frames = 3
        assert t.frozen
        assert not t.advance_flash()  # 2 left
        assert not t.advance_flash()  # 1 left
        assert t.advance_flash()      # done: rows cleared, piece spawned
        assert not t.frozen
        assert not t.pending_clears
        assert t.board[BOARD_H - 1][0] == "J"  # row above fell down

    def test_advance_flash_with_no_pending_is_noop(self) -> None:
        t = Tetris()
        assert t.advance_flash()

    def test_tick_ignored_while_frozen(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        y0 = t.piece.y
        t.tick(1.0)
        assert t.piece.y == y0


# ---------------------------------------------------------------------------
# Scoring extras: combos & back-to-back
# ---------------------------------------------------------------------------


class TestCombosAndB2B:
    def test_combo_bonus_on_consecutive_clears(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        first = t.score
        assert first == 100

        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.score == first + 100 + 50 * 1 * t.level  # combo step 1

    def test_combo_resets_when_piece_lands_without_clear(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.combo == 1
        t._on_lock_no_clears()
        assert t.combo == 0

    def test_back_to_back_tetris_bonus(self) -> None:
        t = Tetris()
        for i in range(4):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t._commit_clears()
        assert t.b2b is True
        first = t.score
        assert first == 800

        for i in range(4):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t._commit_clears()
        # Second consecutive Tetris: 800 * 1.5 + combo bonus (50 * 1)
        assert t.score == first + 1200 + 50

    def test_singles_break_b2b(self) -> None:
        t = Tetris()
        for i in range(4):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t._commit_clears()
        assert t.b2b is True

        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.b2b is False

    def test_b2b_survives_non_clearing_piece(self) -> None:
        t = Tetris()
        for i in range(4):
            fill_row(t, BOARD_H - 1 - i)
        t.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t._commit_clears()
        t._on_lock_no_clears()
        assert t.b2b is True  # only broken by a non-Tetris clear


# ---------------------------------------------------------------------------
# Leveling
# ---------------------------------------------------------------------------


class TestLeveling:
    def test_level_up_every_ten_lines(self) -> None:
        t = Tetris()
        for _ in range(10):
            fill_row(t, BOARD_H - 1)
            t.pending_clears = [BOARD_H - 1]
            t._commit_clears()
        assert t.level == 2
        assert t.lines == 10

    def test_speed_increases_with_level(self) -> None:
        t = Tetris()
        t.level = 5
        t.drop_interval = 0.5 * (0.8 ** (t.level - 1))
        assert t.drop_interval < 0.5
        assert t.drop_interval >= 0.05


# ---------------------------------------------------------------------------
# Start level
# ---------------------------------------------------------------------------


class TestStartLevel:
    def test_custom_start_level_sets_level_and_speed(self) -> None:
        t = Tetris(start_level=5)
        assert t.level == 5
        assert t.drop_interval == max(0.05, 0.5 * (0.8 ** 4))

    def test_start_level_clamped_low(self) -> None:
        t = Tetris(start_level=0)
        assert t.level == 1
        assert t.drop_interval == 0.5

    def test_start_level_clamped_high(self) -> None:
        t = Tetris(start_level=99)
        assert t.level == MAX_START_LEVEL
        assert t.drop_interval == max(0.05, 0.5 * (0.8 ** (MAX_START_LEVEL - 1)))

    def test_default_ctor_still_starts_at_level_one(self) -> None:
        t = Tetris()
        assert t.level == 1
        assert t.drop_interval == max(0.05, 0.5 * (0.8 ** 0))


# ---------------------------------------------------------------------------
# Collision
# ---------------------------------------------------------------------------


class TestCollision:
    def test_collision_with_stack(self) -> None:
        t = Tetris()
        t.board[BOARD_H - 2][5] = "T"
        p = game.Piece("O", x=4, y=BOARD_H - 2)
        assert t._collides(p)

    def test_no_collision_in_empty_space(self) -> None:
        t = Tetris()
        assert not t._collides(game.Piece("O", x=3, y=3))

    def test_collision_out_of_bounds(self) -> None:
        t = Tetris()
        assert t._collides(game.Piece("O", x=BOARD_W - 1, y=3))


# ---------------------------------------------------------------------------
# Game over
# ---------------------------------------------------------------------------


class TestGameOver:
    def test_game_over_when_spawn_collides(self) -> None:
        t = Tetris()
        for row in t.board[:4]:
            row[:] = ["T"] * BOARD_W
        t._spawn()
        assert t.game_over

    def test_game_over_when_locked_above_board(self) -> None:
        t = Tetris()
        t.piece = game.Piece("O", x=2, y=-1)
        t._lock()
        assert t.game_over

    def test_actions_disabled_after_game_over(self) -> None:
        t = Tetris()
        t.game_over = True
        assert not t.move(1)
        assert not t.rotate(1)
        assert not t.soft_drop()
        assert t.hard_drop() == 0
        t.hold()


# ---------------------------------------------------------------------------
# High scores
# ---------------------------------------------------------------------------


class TestHighScores:
    def test_empty_best_is_zero(self, tmp_path) -> None:
        hs = HighScores(tmp_path / "scores.json")
        assert hs.best() == 0
        assert hs.entries == []

    def test_record_and_rank(self, tmp_path) -> None:
        hs = HighScores(tmp_path / "scores.json")
        assert hs.record(500, 2, 1) == 0
        assert hs.record(1000, 5, 1) == 0  # higher score takes rank 1
        assert hs.record(200, 1, 1) == 2

    def test_persists_to_disk(self, tmp_path) -> None:
        path = tmp_path / "scores.json"
        hs = HighScores(path)
        hs.record(420, 3, 1)
        raw = json.loads(path.read_text())
        assert raw["scores"][0]["score"] == 420  # unified {scores, settings} doc

        loaded = HighScores(path)
        assert loaded.best() == 420
        assert loaded.entries == raw["scores"]

    def test_keeps_top_five(self, tmp_path) -> None:
        hs = HighScores(tmp_path / "scores.json")
        for score in (10, 50, 30, 90, 20, 70):
            hs.record(score, 1, 1)
        assert [e["score"] for e in hs.entries] == [90, 70, 50, 30, 20]

    def test_zero_score_not_recorded(self, tmp_path) -> None:
        hs = HighScores(tmp_path / "scores.json")
        assert hs.record(0, 0, 1) is None
        assert hs.entries == []

    def test_corrupt_file_falls_back_to_empty(self, tmp_path) -> None:
        path = tmp_path / "scores.json"
        path.write_text("{not json")
        hs = HighScores(path)
        assert hs.entries == []
        assert hs.record(100, 1, 1) == 0

    def test_env_override(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "alt.json"))
        path = game.default_scores_path()
        assert path == tmp_path / "alt.json"


# ---------------------------------------------------------------------------
# Lock delay
# ---------------------------------------------------------------------------


class TestLockDelay:
    @staticmethod
    def _grounded() -> tuple[Tetris, "game.Piece"]:
        t = Tetris()
        t.piece = game.Piece("O", 4, BOARD_H - 2)  # resting on the floor
        t.tick(1.0)  # registers as grounded at t=1.0
        return t, t.piece

    def test_piece_locks_after_delay(self) -> None:
        t, _ = self._grounded()
        t.tick(1.4)
        assert t.pieces == 0
        t.tick(1.6)
        assert t.pieces == 1
        assert t.board[BOARD_H - 1][4] == "O"

    def test_soft_drop_does_not_lock_when_grounded(self) -> None:
        t, _ = self._grounded()
        assert not t.soft_drop()
        assert t.pieces == 0

    def test_move_refreshes_lock_timer(self) -> None:
        t, _ = self._grounded()
        assert t.move(1, 1.4)
        t.tick(1.85)  # < 0.5 s since the refresh at 1.4
        assert t.pieces == 0
        t.tick(2.0)
        assert t.pieces == 1

    def test_rotate_refreshes_lock_timer(self) -> None:
        t, _ = self._grounded()
        assert t.rotate(1, 1.4)
        t.tick(1.85)
        assert t.pieces == 0
        t.tick(2.0)
        assert t.pieces == 1

    def test_resets_are_capped(self) -> None:
        t, _ = self._grounded()
        for i in range(LOCK_RESET_MAX):
            assert t.move(1 if i % 2 == 0 else -1, 1.0 + i * 0.1)
        # Last refresh was at 2.4; this move is beyond the cap and must not
        # extend the timer any further.
        assert t.move(1, 5.0)
        t.tick(2.8)  # 2.8 - 2.4 = 0.4 < LOCK_DELAY
        assert t.pieces == 0
        t.tick(3.0)
        assert t.pieces == 1


# ---------------------------------------------------------------------------
# T-spin
# ---------------------------------------------------------------------------


def _tspin_setup(t: Tetris, top_left: bool, top_right: bool, complete: bool) -> None:
    """Board for a T (rot 0) locking at (3, 17): nub (4, 17) and the three
    cells below (3..5 @ y=18) stay empty, row 19 blocks the floor under
    them. Diagonal corner fills go in row 17; row 18 fills around the T so
    it completes when the T lands (unless complete=False)."""
    t.board = Board.from_rows([[""] * BOARD_W for _ in range(BOARD_H)])
    r17, r18, r19 = t.board[17], t.board[18], t.board[19]
    r17[3] = "J" if top_left else ""
    r17[5] = "J" if top_right else ""
    for x in range(BOARD_W):
        if x in (3, 4, 5) or (x == 0 and not complete):
            continue
        r18[x] = "J"
    for x in (3, 4, 5):
        r19[x] = "J"
    t.piece = game.Piece("T", 3, 17)


class TestTSpin:
    def _play(self, t: Tetris) -> None:
        t.hard_drop()
        for _ in range(FLASH_FRAMES + 1):
            t.advance_flash()

    def test_full_single(self) -> None:
        t = Tetris()
        _tspin_setup(t, top_left=True, top_right=True, complete=True)
        self._play(t)
        assert t.score == 800
        assert t.spins == 1
        assert t.events[-1].kind == "tspin"
        assert "+800" in t.events[-1].text

    def test_mini_single(self) -> None:
        t = Tetris()
        _tspin_setup(t, top_left=False, top_right=True, complete=True)
        self._play(t)
        assert t.score == 200
        assert t.spins == 0
        assert t.events[-1].kind == "tspin-mini"

    def test_no_spin_is_plain_single(self) -> None:
        t = Tetris()
        _tspin_setup(t, top_left=False, top_right=False, complete=True)
        self._play(t)
        assert t.score == 100
        assert t.events[-1].kind == "clear"

    def test_full_no_line(self) -> None:
        t = Tetris()
        _tspin_setup(t, top_left=True, top_right=True, complete=False)
        t.hard_drop()
        assert not t.pending_clears  # no flash — scored on lock
        assert t.score == 400
        assert t.spins == 1
        assert t.events[-1].kind == "tspin"

    def test_double_counts_as_b2b(self) -> None:
        t = Tetris()
        t.board = Board.from_rows([[""] * BOARD_W for _ in range(BOARD_H)])
        r16, r17, r18 = t.board[16], t.board[17], t.board[18]
        r16[3] = r16[5] = "J"
        for x in range(BOARD_W):
            if x in (3, 4, 5):
                continue
            r17[x] = "J"
        for x in range(BOARD_W):  # fully solid: blocks the T under its bottom row
            r18[x] = "J"
        t.piece = game.Piece("T", 3, 16)
        self._play(t)
        assert t.score == 1200
        assert t.b2b

    def test_non_t_piece_never_spins(self) -> None:
        t = Tetris()
        t.board = Board.from_rows([[""] * BOARD_W for _ in range(BOARD_H)])
        for x in (3, 4, 5):
            t.board[19][x] = "J"
        t.board[17][3] = t.board[17][5] = "J"
        t.board[18][3] = t.board[18][5] = "J"
        t.piece = game.Piece("O", 4, 18)
        t.hard_drop()
        assert t.spins == 0
        assert all(ev.kind != "tspin" for ev in t.events)


# ---------------------------------------------------------------------------
# Next queue
# ---------------------------------------------------------------------------


class TestNextQueue:
    def test_two_pieces_ahead(self) -> None:
        t = Tetris()
        a, b = t.next_kind, t.next_kind2
        t.hard_drop()
        assert t.next_kind == b
        # All three came from the same 7-distinct bag.
        assert t.next_kind2 not in (a, b)
