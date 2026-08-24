"""Unit tests for the Tetris game logic (tetris.engine)."""

import json
import random

import pytest

from tetris.engine import FLASH_FRAMES, LOCK_RESET_MAX, Board, Piece, Tetris
from tetris.pieces import BOARD_H, BOARD_W, MAX_START_LEVEL, PIECES
from tetris.state import HighScores, default_state_path


@pytest.fixture
def game_state() -> Tetris:
    return Tetris(rng=random.Random(42))


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

    def test_default_rng_ignores_global_seed(self) -> None:
        # The default constructor no longer falls back to the global module
        # RNG: two fresh default-constructed engines draw independent streams
        # (each from its own os.urandom-seeded Random), so reseeding the
        # global state cannot reproduce the first engine's stream. Under the
        # legacy global fallback the two sequences would have been equal.
        random.seed(5)
        seq = [Tetris()._refill() for _ in range(7)]
        random.seed(5)
        seq2 = [Tetris()._refill() for _ in range(7)]
        assert seq != seq2

    def test_default_rng_stays_isolated_from_global_state(self) -> None:
        # Polluting the global module RNG mid-game must not disturb a
        # default-constructed engine's private stream (valid bag, no crash).
        t = Tetris()
        t.bag = []  # start from a full bag (init already drew 5 for the queue)
        first_bag = [t._refill() for _ in range(7)]
        random.seed(99999)
        next_bag = [t._refill() for _ in range(7)]
        assert sorted(first_bag) == sorted(PIECES.keys())
        assert sorted(next_bag) == sorted(PIECES.keys())

    def test_same_seed_gives_identical_first_20_pieces(self) -> None:
        # Two instances built from the same seed produce the identical first
        # 20 queued pieces (replay determinism, plan P9a).
        a = Tetris(rng=random.Random(31337))
        b = Tetris(rng=random.Random(31337))
        stream_a = [a._refill() for _ in range(20)]
        stream_b = [b._refill() for _ in range(20)]
        assert stream_a == stream_b
        # 20 draws span at least two full bags: every kind appears.
        assert sorted(set(stream_a)) == sorted(PIECES.keys())


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
        t.piece = Piece("O", x=0, y=5)
        assert not t.move(-1)  # already against left wall

    def test_move_returns_false_when_frozen(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        assert not t.move(1)


# ---------------------------------------------------------------------------
# Piece rotation data (SRS state chains)
# ---------------------------------------------------------------------------


class TestPieceRotationData:
    """The PIECES rotation states must form true rotation chains.

    Regression: the L piece's 180 state used to be a mirrored shape (nub
    pointing up), so the piece visibly flipped instead of rotating.
    """

    @staticmethod
    def _rot90(cells: set[tuple[int, int]], box: int) -> set[tuple[int, int]]:
        # 90 deg clockwise in a box x box box, y growing downward.
        return {(box - 1 - y, x) for x, y in cells}

    @pytest.mark.parametrize("kind", ["J", "L", "T"])
    def test_jlstz_rotation_chain(self, kind: str) -> None:
        states = PIECES[kind]
        for i in range(4):
            expected = self._rot90(set(states[i]), 3)
            assert set(states[(i + 1) % 4]) == expected, f"{kind} state {i} -> {i + 1}"

    def test_i_rotation_chain(self) -> None:
        # I uses the 4x4 box: state 0 = row 1, state 1 = col 2, state 2 =
        # row 2, state 3 = col 1 (standard SRS geometry the KICKS_I table
        # is derived from).
        states = PIECES["I"]
        for i in range(4):
            expected = self._rot90(set(states[i]), 4)
            assert set(states[(i + 1) % 4]) == expected, f"I state {i} -> {(i + 1) % 4}"

    @pytest.mark.parametrize("kind", ["S", "Z"])
    def test_s_z_normalized_chain(self, kind: str) -> None:
        # S/Z are 180-degree symmetric: the SRS spec normalizes states 2/3
        # to the spawn band, so state 2 == state 0 and state 3 == state 1.
        states = PIECES[kind]
        assert set(states[0]) == set(states[2])
        assert set(states[1]) == set(states[3])
        assert set(states[1]) == self._rot90(set(states[0]), 3)
        assert set(states[3]) == self._rot90(set(states[2]), 3)

    def test_l_180_state_shape(self) -> None:
        """The exact cells of the L piece's 180 state: a row of three with
        the nub below the left cell (nub pointing down-left, not up)."""
        assert sorted(PIECES["L"][2]) == [(0, 1), (0, 2), (1, 1), (2, 1)]


# ---------------------------------------------------------------------------
# Rotation (SRS)
# ---------------------------------------------------------------------------


class TestRotation:
    def test_rotate_clockwise_and_counterclockwise_are_inverses(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=5)
        start = (t.piece.x, t.piece.y, t.piece.rot)
        t.rotate(1)
        t.rotate(-1)
        assert (t.piece.x, t.piece.y, t.piece.rot) == start

    def test_full_rotation_cycle_restores_orientation(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=5)
        start = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        for _ in range(4):
            t.rotate(1)
        end = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        assert start == end

    def test_floor_kick(self) -> None:
        """T piece flat on the floor should kick up when rotating (SRS)."""
        t = Tetris()
        # T rot0 = [(1,0),(0,1),(1,1),(2,1)] → bottom at y+1; place bottom on floor.
        t.piece = Piece("T", x=4, y=BOARD_H - 2)
        assert t.rotate(1)
        assert t.piece.rot == 1
        # The kick must have lifted the piece so all cells fit on the board.
        assert all(y < BOARD_H for _, y in t.piece.cells())
        assert not t._collides(t.piece)

    def test_wall_kick_against_left_wall(self) -> None:
        t = Tetris()
        # I piece (vertical, rot1) jammed against the left wall rotates right.
        t.piece = Piece("I", x=-1, y=5, rot=1)
        assert t.rotate(1)
        assert all(x >= 0 for x, _ in t.piece.cells())

    def test_rotation_impossible_returns_false(self) -> None:
        t = Tetris()
        # Fill the board except the piece's current cells so no kick fits.
        t.piece = Piece("T", x=4, y=9)
        t.board = Board.from_rows([["T"] * BOARD_W for _ in range(BOARD_H)])
        for x, y in t.piece.cells():
            t.board[y][x] = ""
        assert not t.rotate(1)

    def test_o_piece_rotation_is_noop_position(self) -> None:
        t = Tetris()
        t.piece = Piece("O", x=4, y=5)
        x0, y0 = t.piece.x, t.piece.y
        assert t.rotate(1)
        assert (t.piece.x, t.piece.y) == (x0, y0)
        assert t.piece.rot == 1


# ---------------------------------------------------------------------------
# 180° rotation (X)
# ---------------------------------------------------------------------------


class TestRotate180:
    @pytest.mark.parametrize("kind", ["J", "L", "S", "Z", "T"])
    @pytest.mark.parametrize("rot", [0, 1])
    def test_free_space_flip(self, kind: str, rot: int) -> None:
        """In open space the flip is in place: rot 0→2 / 1→3, same x/y,
        no collision, and the version counter bumps for the UI redraw."""
        t = Tetris()
        t.piece = Piece(kind, x=4, y=5, rot=rot)
        before = t.version
        assert t.rotate_180()
        assert t.piece.rot == rot + 2
        assert (t.piece.x, t.piece.y) == (4, 5)
        assert not t._collides(t.piece)
        assert t.version == before + 1

    def test_i_piece_is_refused(self) -> None:
        """The I piece's 180° is a no-op modulo the 4×4 box row, so it is
        always refused and leaves the piece untouched."""
        t = Tetris()
        t.piece = Piece("I", x=3, y=5, rot=0)
        before = t.version
        assert not t.rotate_180()
        assert (t.piece.x, t.piece.y, t.piece.rot) == (3, 5, 0)
        assert t.version == before

    def test_kick_right_when_straight_flip_blocked(self) -> None:
        """The straight flip collides (a block under the flipped nub) but
        the (1,0) kick fits: the piece lands one cell to the right."""
        t = Tetris()
        # T rot 0 at (4, 10): flipped in place its nub would land on (5, 12).
        t.piece = Piece("T", x=4, y=10)
        t.board[12][5] = "O"
        assert t.rotate_180()
        assert t.piece.rot == 2
        assert (t.piece.x, t.piece.y) == (5, 10)
        assert not t._collides(t.piece)

    def test_kick_up_when_all_side_kicks_blocked(self) -> None:
        """With (0,0), (1,0) and (-1,0) all blocked the (0,1) kick lifts
        the piece one row (SRS y-up): same x, one row higher up."""
        t = Tetris()
        t.piece = Piece("T", x=4, y=10)
        t.board[12][5] = "O"  # blocks the straight flip (nub below)
        t.board[11][7] = "O"  # blocks the (1,0) kick
        t.board[11][3] = "O"  # blocks the (-1,0) kick
        assert t.rotate_180()
        assert t.piece.rot == 2
        assert (t.piece.x, t.piece.y) == (4, 9)
        assert not t._collides(t.piece)


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

    def test_last_lock_records_hard_drop_cells(self) -> None:
        """P22: a no-clear hard drop records the locked cells so the UI
        can flash them — the recorded cells are exactly the piece's
        actual footprint after the drop."""
        t = Tetris()
        assert t.last_lock is None
        before = [(x, y) for x, y in t.piece.cells() if y >= 0]
        dist = t.hard_drop()
        assert dist > 0
        assert t.last_lock is not None
        assert sorted(t.last_lock) == sorted((x, y + dist) for x, y in before)
        for x, y in t.last_lock:
            assert t.board.occupied(x, y)

    def test_last_lock_none_when_lock_clears_lines(self) -> None:
        """P22: a lock that clears lines keeps last_lock None — the
        line-clear flash is the effect there, not the lock flash."""
        t = Tetris()
        t.piece = Piece("O", 4, BOARD_H - 2)
        for x in range(BOARD_W):
            if x not in (4, 5):
                t.board.set_cell(x, BOARD_H - 1, "J")
                t.board.set_cell(x, BOARD_H - 2, "J")
        t.tick(1.0)  # the resting piece registers as grounded
        t.tick(1.6)  # lock delay elapses: the locking lock clears two rows
        assert t.pieces == 1
        assert t.last_lock is None
        assert t.pending_clears == [BOARD_H - 2, BOARD_H - 1]

    def test_ghost_y_at_or_below_piece(self, game_state: Tetris) -> None:
        assert game_state.ghost_y() >= game_state.piece.y

    def test_ghost_y_rests_on_floor_or_stack(self, game_state: Tetris) -> None:
        gy = game_state.ghost_y()
        p = game_state.piece
        ghost = Piece(p.kind, p.x, gy, p.rot)
        below = Piece(p.kind, p.x, gy + 1, p.rot)
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
        t.piece = Piece("O", x=4, y=BOARD_H - 2)
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

    def test_clear_event_carries_line_count(self) -> None:
        """P25: clear events carry how many rows the lock cleared, so the
        UI can tell a B2B-qualifying T-spin (2+ lines) from a no-line one
        (0, which leaves the back-to-back streak untouched)."""
        t = Tetris()
        t.events.clear()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.events and t.events[-1].lines == 1

    def test_clear_event_carries_post_commit_combo_and_b2b(self) -> None:
        """R3: clear events are stamped with the post-commit combo/b2b so
        the UI's COMBO/B2B floaters read the event's snapshot, not the
        engine's live state."""
        t = Tetris()
        t.events.clear()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()  # first clear: post-commit combo 1, no B2B
        assert t.events[-1].combo == 1
        assert t.events[-1].b2b is False

        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()  # second consecutive clear: combo 2
        assert t.events[-1].combo == 2

        # A Tetris stamps the newly-active B2B streak.
        t2 = Tetris()
        t2.events.clear()
        for i in range(4):
            fill_row(t2, BOARD_H - 1 - i)
        t2.pending_clears = list(range(BOARD_H - 4, BOARD_H))
        t2._commit_clears()
        assert t2.events[-1].b2b is True
        assert t2.events[-1].lines == 4


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

    def test_level_up_event_on_level_change(self) -> None:
        """P16: crossing a level boundary emits a LEVEL UP event (the UI
        floats it mid-board and beeps); one event per boundary only."""
        t = Tetris()
        for _ in range(9):
            fill_row(t, BOARD_H - 1)
            t.pending_clears = [BOARD_H - 1]
            t._commit_clears()
        t.events.clear()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.level == 2
        ups = [ev for ev in t.events if ev.kind == "levelup"]
        assert len(ups) == 1
        assert ups[0].text == "LEVEL UP"
        assert ups[0].row == BOARD_H // 2
        assert ups[0].center is None

    def test_no_level_up_event_below_level_boundary(self) -> None:
        t = Tetris()
        for _ in range(3):
            fill_row(t, BOARD_H - 1)
            t.pending_clears = [BOARD_H - 1]
            t._commit_clears()
        assert t.level == 1
        assert not any(ev.kind == "levelup" for ev in t.events)


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
        p = Piece("O", x=4, y=BOARD_H - 2)
        assert t._collides(p)

    def test_no_collision_in_empty_space(self) -> None:
        t = Tetris()
        assert not t._collides(Piece("O", x=3, y=3))

    def test_collision_out_of_bounds(self) -> None:
        t = Tetris()
        assert t._collides(Piece("O", x=BOARD_W - 1, y=3))

    def test_stack_top(self) -> None:
        """P24: stack_top is the topmost row holding a settled cell (or
        None on an empty board) — the UI's danger bar rides on it."""
        t = Tetris()
        assert t.stack_top is None
        fill_row(t, BOARD_H - 1)
        assert t.stack_top == BOARD_H - 1
        fill_row(t, 3)
        assert t.stack_top == 3


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
        t.piece = Piece("O", x=2, y=-1)
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

    def test_keeps_top_ten(self, tmp_path) -> None:
        hs = HighScores(tmp_path / "scores.json")
        for score in (10, 50, 30, 90, 20, 70, 40, 80, 60, 30, 15):
            hs.record(score, 1, 1)
        assert [e["score"] for e in hs.entries] == [
            90, 80, 70, 60, 50, 40, 30, 30, 20, 15,
        ]

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
        path = default_state_path()
        assert path == tmp_path / "alt.json"


# ---------------------------------------------------------------------------
# Lock delay
# ---------------------------------------------------------------------------


class TestLockDelay:
    @staticmethod
    def _grounded() -> tuple[Tetris, "Piece"]:
        t = Tetris()
        t.piece = Piece("O", 4, BOARD_H - 2)  # resting on the floor
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

    def test_grounded_property(self) -> None:
        """P23: the UI's lock-delay pulse is driven by engine.grounded —
        False in flight, True once the piece rests, False again after the
        replacement piece spawns."""
        t = Tetris()
        assert not t.grounded
        t.piece = Piece("O", 4, BOARD_H - 2)
        assert not t.grounded  # the flag is per-tick, not per-piece
        t.tick(1.0)
        assert t.grounded
        t.tick(1.6)  # locks; the replacement piece spawns in flight
        assert not t.grounded

    def test_rotate_180_refreshes_lock_timer(self) -> None:
        t = Tetris()
        # T resting on the floor: the in-place flip would poke below the
        # floor, so the (0,1) kick lifts it a row — a rotate action that
        # must refresh the lock timer like any other rotate.
        t.piece = Piece("T", 4, BOARD_H - 2)
        t.tick(1.0)  # registers as grounded at t=1.0
        assert t.rotate_180(1.4)
        assert t._lock_resets == 1
        assert t.piece.rot == 2 and t.piece.y == BOARD_H - 3
        t.tick(1.85)  # < 0.5 s since the refresh at 1.4
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
    t.piece = Piece("T", 3, 17)


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
        t.piece = Piece("T", 3, 16)
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
        t.piece = Piece("O", 4, 18)
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

    def test_queue_holds_five_kinds(self) -> None:
        t = Tetris(rng=random.Random(42))
        assert len(t.queue) == 5
        assert all(kind in PIECES for kind in t.queue)
        # Spawning consumes the head and refills the tail.
        t.hard_drop()
        assert len(t.queue) == 5

    def test_next_kind_properties_track_queue_head(self) -> None:
        t = Tetris(rng=random.Random(7))
        assert t.next_kind == t.queue[0]
        assert t.next_kind2 == t.queue[1]

    def test_seeded_spawn_sequence_is_stable(self) -> None:
        # Pins the bag-pop order: the 5-deep queue pops 5 at init + 1 per
        # spawn, exactly the same piece order as the legacy 2-slot code
        # (2 at init + 1 per spawn), so seeded games are unchanged.
        t = Tetris(rng=random.Random(99))
        expected = ["S", "L", "O", "Z", "I", "T", "J", "O"]
        seq = [t.piece.kind]
        for _ in range(len(expected) - 1):
            seq.append(t.next_kind)
            t.hard_drop()
        assert seq == expected


# ---------------------------------------------------------------------------
# Version counter (render-skip)
# ---------------------------------------------------------------------------


class TestVersion:
    def test_fresh_engine_starts_at_version_zero(self) -> None:
        assert Tetris().version == 0

    def test_successful_move_bumps_version(self) -> None:
        t = Tetris()
        before = t.version
        assert t.move(-1)
        assert t.version == before + 1

    def test_failed_move_into_wall_does_not_bump(self) -> None:
        t = Tetris()
        t.piece = Piece("O", x=0, y=5)
        before = t.version
        assert not t.move(-1)  # already against the left wall
        assert t.version == before

    def test_successful_rotate_bumps_version(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=5)
        before = t.version
        assert t.rotate(1)
        assert t.version == before + 1

    def test_failed_rotate_does_not_bump(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=9)
        t.board = Board.from_rows([["T"] * BOARD_W for _ in range(BOARD_H)])
        for x, y in t.piece.cells():
            t.board[y][x] = ""
        before = t.version
        assert not t.rotate(1)
        assert t.version == before

    def test_successful_rotate_180_bumps_version(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=5)
        before = t.version
        assert t.rotate_180()
        assert t.version == before + 1

    def test_failed_rotate_180_does_not_bump(self) -> None:
        t = Tetris()
        t.piece = Piece("T", x=4, y=9)
        t.board = Board.from_rows([["T"] * BOARD_W for _ in range(BOARD_H)])
        for x, y in t.piece.cells():
            t.board[y][x] = ""
        before = t.version
        assert not t.rotate_180()
        assert t.version == before

    def test_successful_soft_drop_bumps_version(self) -> None:
        t = Tetris()
        before = t.version
        assert t.soft_drop()
        assert t.version == before + 1

    def test_soft_drop_on_floor_does_not_bump(self) -> None:
        t = Tetris()
        for _ in range(BOARD_H):
            t.soft_drop()
        before = t.version
        assert not t.soft_drop()
        assert t.version == before

    def test_hard_drop_bumps_version(self) -> None:
        t = Tetris()
        before = t.version
        assert t.hard_drop() > 0
        assert t.version > before

    def test_hold_bumps_version_only_when_state_changes(self) -> None:
        t = Tetris()
        before = t.version
        t.hold()
        assert t.version > before
        after_first = t.version
        t.hold()  # second hold is a no-op
        assert t.version == after_first

    def test_gravity_step_in_tick_bumps_version(self) -> None:
        t = Tetris()
        before = t.version
        y0 = t.piece.y
        t.tick(1.0)  # first tick: no gravity applied yet, so the piece falls
        assert t.piece.y == y0 + 1
        assert t.version == before + 1

    def test_tick_while_paused_does_not_bump(self) -> None:
        t = Tetris()
        before = t.version
        t.paused = True  # the pause flip itself bumps exactly once
        assert t.version == before + 1
        t.tick(1.0)
        t.tick(2.0)
        assert t.version == before + 1

    def test_lock_bumps_version(self) -> None:
        t = Tetris()
        t.piece = Piece("O", 4, BOARD_H - 2)
        before = t.version
        t._lock()
        assert t.version == before + 1

    def test_commit_clears_bumps_version(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        before = t.version
        t._commit_clears()
        assert t.version == before + 1

    def test_game_over_bumps_version(self) -> None:
        t = Tetris()
        for row in t.board[:4]:
            row[:] = ["T"] * BOARD_W
        before = t.version
        t._spawn()
        assert t.game_over
        assert t.version == before + 1

    def test_game_over_bumps_only_once(self) -> None:
        t = Tetris()
        for row in t.board[:4]:
            row[:] = ["T"] * BOARD_W
        t._spawn()
        assert t.game_over
        after_first = t.version
        t._spawn()  # already over: no further bump
        assert t.version == after_first

    def test_paused_bumps_only_when_value_changes(self) -> None:
        t = Tetris()
        before = t.version
        assert t.paused is False
        t.paused = True
        assert t.version == before + 1
        t.paused = True  # same value: no bump
        assert t.version == before + 1
        t.paused = False
        assert t.version == before + 2
        t.paused = False  # same value: no bump
        assert t.version == before + 2


# ---------------------------------------------------------------------------
# Play time & best combo
# ---------------------------------------------------------------------------


class TestPlayTime:
    def test_first_tick_primes_without_adding_time(self) -> None:
        t = Tetris()
        t.tick(5.0)
        assert t.play_time == 0.0

    def test_play_time_advances_across_ticks(self) -> None:
        t = Tetris()
        t.tick(1.0)
        t.tick(3.0)
        assert t.play_time == pytest.approx(2.0)

    def test_pause_adds_no_time_and_resume_has_no_jump(self) -> None:
        t = Tetris()
        t.tick(0.0)
        t.tick(1.0)  # +1.0 s
        t.paused = True
        t.tick(6.0)  # 5 s of simulated pause: must not count
        t.tick(11.0)
        assert t.play_time == pytest.approx(1.0)
        t.paused = False
        t.tick(12.0)  # resume: only the real 1 s elapsed
        assert t.play_time == pytest.approx(2.0)

    def test_frozen_flash_still_counts_time(self) -> None:
        t = Tetris()
        t.tick(0.0)
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t.flash_frames = FLASH_FRAMES
        t.tick(10.0)  # frozen: no gravity, but time keeps flowing
        t.tick(13.0)
        assert t.play_time == pytest.approx(13.0)

    def test_game_over_stops_time(self) -> None:
        t = Tetris()
        t.tick(0.0)
        t.game_over = True
        t.tick(50.0)
        assert t.play_time == 0.0

    def test_snapshot_exposes_time(self) -> None:
        t = Tetris()
        t.tick(1.0)
        t.tick(2.0)
        snap = t.snapshot()
        assert snap["time"] == pytest.approx(1.0)
        assert isinstance(snap["time"], float)

    def test_snapshot_exposes_best_combo(self) -> None:
        t = Tetris()
        snap = t.snapshot()
        assert snap["best_combo"] == 0

    def test_best_combo_tracks_highest_combo(self) -> None:
        t = Tetris()
        for _ in range(3):
            fill_row(t, BOARD_H - 1)
            t.pending_clears = [BOARD_H - 1]
            t._commit_clears()
        assert t.combo == 3
        assert t.best_combo == 3
        t._on_lock_no_clears()  # combo breaks
        assert t.combo == 0
        assert t.best_combo == 3  # the high water mark is retained

    def test_best_combo_survives_single_clears(self) -> None:
        t = Tetris()
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.combo == 1
        assert t.best_combo == 1
        fill_row(t, BOARD_H - 1)
        t.pending_clears = [BOARD_H - 1]
        t._commit_clears()
        assert t.combo == 2
        assert t.best_combo == 2


# ---------------------------------------------------------------------------
# Replay determinism (P9)
# ---------------------------------------------------------------------------

def _replay_run(seed: int, events: list[tuple[float, str]], frames: int = 500) -> tuple:
    """Re-run a recorded (game-relative time, token) input log against a
    fresh engine with the given seed, mirroring the UI's replay_step:
    tick on a fixed 20 ms frame grid, feed each event when its time has
    elapsed, and advance line-clear flashes. Returns a hashable summary
    of the final state so two runs can be compared for exact equality."""
    g = Tetris(rng=random.Random(seed))
    idx = 0
    dt = 0.02
    for frame in range(frames):
        now = frame * dt
        while idx < len(events) and events[idx][0] <= now:
            token = events[idx][1]
            if token == "L":
                g.move(-1, now)
            elif token == "R":
                g.move(1, now)
            elif token == "U":
                g.rotate(1, now)
            elif token == "Z":
                g.rotate(-1, now)
            elif token == "S":
                g.soft_drop()
            elif token == "H":
                g.hard_drop()
            elif token == "C":
                g.hold()
            idx += 1
        g.tick(now)
        if g.frozen:
            g.advance_flash()
        if g.game_over:
            break
    board = tuple(tuple(row) for row in g.board)
    return (g.score, g.lines, g.level, g.pieces, g.combo, g.b2b,
            g.spins, board, tuple(g.queue), g.spawn_seq)


def test_replay_round_trip_is_deterministic() -> None:
    """The same (seed, input log) re-run produces a bit-identical game:
    this is what makes G-replay render the exact run the player played."""
    seed = 31337
    events: list[tuple[float, str]] = []
    # A fixed, varied input log: moves, both rotations, soft/hard drops,
    # and holds, spread over ~10 s so several pieces lock and clear.
    script = [
        (0.10, "R"), (0.30, "U"), (0.45, "R"), (0.60, "H"),
        (0.80, "L"), (1.00, "S"), (1.20, "C"), (1.40, "Z"), (1.60, "H"),
        (2.00, "L"), (2.20, "L"), (2.40, "U"), (2.60, "H"),
        (3.00, "R"), (3.20, "R"), (3.40, "Z"), (3.60, "H"),
        (4.00, "C"), (4.40, "U"), (4.60, "R"), (4.80, "H"),
        (5.20, "L"), (5.40, "S"), (5.60, "S"), (5.80, "H"),
        (6.20, "R"), (6.40, "U"), (6.60, "U"), (6.80, "H"),
        (7.20, "C"), (7.60, "L"), (7.80, "H"), (8.20, "R"), (8.40, "H"),
    ]
    events.extend(script)
    a = _replay_run(seed, events)
    b = _replay_run(seed, events)
    assert a == b, "same seed + same input log must produce identical games"
    # A different seed perturbs the piece bag, so the runs diverge (guards
    # against a tautological no-op run where the seed is ignored).
    c = _replay_run(seed + 1, events)
    assert a != c, "different seeds must produce different games"
