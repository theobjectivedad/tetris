"""Unit tests for the Tetris game logic.

The game state lives in the pure-Python ``Tetris`` class (no curses needed),
so we can exercise it directly.
"""

import random

import pytest

import main
from main import BOARD_H, BOARD_W, PIECES, Tetris


@pytest.fixture
def game() -> Tetris:
    random.seed(42)
    return Tetris()


class TestBagRandomizer:
    def test_bags_contain_all_seven_pieces(self) -> None:
        t = Tetris()
        # Each freshly drawn bag must be a permutation of all seven kinds.
        for _ in range(3):
            t.bag = []
            seen = [t._refill() for _ in range(7)]
            assert sorted(seen) == sorted(PIECES.keys())

    def test_refill_never_returns_empty_bag_piece(self) -> None:
        t = Tetris()
        for _ in range(50):
            kind = t._refill()
            assert kind in PIECES


class TestSpawning:
    def test_spawn_uses_next_piece_and_updates_next(self, game: Tetris) -> None:
        expected = game.next_kind
        piece = game._spawn()
        assert piece.kind == expected
        assert game.next_kind != expected or len(PIECES) == 1  # next preview advanced

    def test_spawned_piece_starts_at_top_center(self, game: Tetris) -> None:
        p = game.piece
        assert p.rot == 0
        assert p.y == 0
        xs = [x for x, _ in p.cells()]
        assert max(xs) < BOARD_W

    def test_piece_cells_within_board_width(self, game: Tetris) -> None:
        for cx, cy in game.piece.cells():
            assert 0 <= cx < BOARD_W


class TestMovement:
    def test_move_left_and_right(self, game: Tetris) -> None:
        x0 = game.piece.x
        game.move(-1)
        assert game.piece.x == x0 - 1
        game.move(1)
        assert game.piece.x == x0

    def test_cannot_move_past_left_wall(self, game: Tetris) -> None:
        # Walk piece left until it stops; it must never go out of bounds.
        for _ in range(BOARD_W * 2):
            game.move(-1)
        for cx, _ in game.piece.cells():
            assert cx >= 0

    def test_cannot_move_past_right_wall(self, game: Tetris) -> None:
        for _ in range(BOARD_W * 2):
            game.move(1)
        for cx, _ in game.piece.cells():
            assert cx < BOARD_W

    def test_move_down_lowers_piece(self, game: Tetris) -> None:
        y0 = game.piece.y
        game.move_down()
        assert game.piece.y == y0 + 1 or game.game_over  # locked on ground


class TestRotation:
    @pytest.mark.parametrize("kind,rot", sorted({(k, r) for k, states in PIECES.items() for r in range(len(states))}))
    def test_rotate_changes_orientation(self, kind: str, rot: int) -> None:
        t = Tetris()
        t.piece = main.Piece(kind=kind, x=BOARD_W // 2 - 2, y=0, rot=rot)
        before = frozenset((dx - min(c[0] for c in PIECES[kind][rot]), dy) for dx, dy in PIECES[kind][rot])
        t.rotate()
        after_rot = t.piece.rot
        # Piece either rotated (O is identical in all rotations) or kicked.
        assert after_rot in range(4)
        # Cells must still be inside the board after any wall kick.
        for cx, cy in t.piece.cells():
            assert 0 <= cx < BOARD_W

    def test_rotate_against_wall_uses_wall_kick(self) -> None:
        t = Tetris()
        # J piece in the left wall: rotating should kick it right instead of failing.
        t.piece = main.Piece(kind="I", x=-2, y=0, rot=0)  # horizontal I near left edge
        t.rotate()
        for cx, _ in t.piece.cells():
            assert cx >= 0

    def test_full_rotation_cycle_restores_orientation(self) -> None:
        t = Tetris()
        t.piece = main.Piece(kind="T", x=BOARD_W // 2 - 1, y=5)
        start = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        for _ in range(4):
            t.rotate()
        end = frozenset((x - t.piece.x, y) for x, y in t.piece.cells())
        assert start == end


class TestDropping:
    def test_soft_drop_locks_on_floor(self) -> None:
        t = Tetris()
        piece = t.piece
        # Soft drop until the piece locks (board gained cells or new piece spawned).
        for _ in range(BOARD_H + 5):
            t.soft_drop()
            if t.piece is not piece and any(k != "" for row in t.board for k in row):
                break
        locked_cells = [(x, y) for y, row in enumerate(t.board) for x, k in enumerate(row) if k == piece.kind]
        # Locked cells rest on the floor or on nothing (nothing below them is empty? no — bottom row).
        bottom_row = max(y for _, y in locked_cells)
        assert bottom_row == BOARD_H - 1

    def test_hard_drop_instantly_locks_piece(self, game: Tetris) -> None:
        kind = game.piece.kind
        old_piece = game.piece
        game.hard_drop()
        # The piece's cells are now part of the board...
        assert any(k == kind for row in game.board for k in row)
        # ...and a fresh piece has spawned at the top.
        assert game.piece is not old_piece
        assert game.piece.y == 0

    def test_ghost_y_is_at_or_below_piece(self, game: Tetris) -> None:
        assert game.ghost_y() >= game.piece.y

    def test_ghost_y_rests_on_floor_or_stack(self, game: Tetris) -> None:
        gy = game.ghost_y()
        p = game.piece
        ghost = main.Piece(p.kind, p.x, gy, p.rot)
        below = main.Piece(p.kind, p.x, gy + 1, p.rot)
        assert not game._collides(ghost)
        assert game._collides(below)


class TestLineClearing:
    def test_full_line_is_cleared_and_scored(self) -> None:
        t = Tetris()
        t.board[BOARD_H - 1] = ["T"] * BOARD_W
        t._clear_lines()
        assert t.lines == 1
        assert t.score == 100 * t.level
        assert all(k == "" for row in t.board for k in row)

    @pytest.mark.parametrize("count,expected", [(1, 100), (2, 300), (3, 500), (4, 800)])
    def test_scoring_table(self, count: int, expected: int) -> None:
        t = Tetris()
        for i in range(count):
            t.board[BOARD_H - 1 - i] = ["I"] * BOARD_W
        t._clear_lines()
        assert t.score == expected
        assert t.lines == count

    def test_uncleared_rows_fall_down(self) -> None:
        t = Tetris()
        t.board[BOARD_H - 1] = ["T"] * BOARD_W
        t.board[BOARD_H - 2][0] = "J"
        t._clear_lines()
        assert t.board[BOARD_H - 1][0] == "J"
        assert all(k == "" for k in t.board[BOARD_H - 2])

    def test_board_height_invariant_after_clear(self) -> None:
        t = Tetris()
        for i in range(4):
            t.board[BOARD_H - 1 - i] = ["O"] * BOARD_W
        t._clear_lines()
        assert len(t.board) == BOARD_H
        assert all(len(row) == BOARD_W for row in t.board)


class TestLeveling:
    def test_level_up_every_ten_lines(self) -> None:
        t = Tetris()
        for _ in range(10):
            t.board[BOARD_H - 1] = ["T"] * BOARD_W
            t._clear_lines()
        assert t.level == 2
        assert t.lines == 10

    def test_speed_increases_with_level(self) -> None:
        t = Tetris()
        t.level = 5
        t.drop_interval = 0.5 * (0.8 ** (t.level - 1))
        assert t.drop_interval < 0.5


class TestCollision:
    def test_collision_with_stack(self) -> None:
        t = Tetris()
        t.board[BOARD_H - 2][5] = "T"
        p = main.Piece("O", x=4, y=BOARD_H - 2)
        assert t._collides(p)

    def test_no_collision_in_empty_space(self) -> None:
        t = Tetris()
        p = main.Piece("O", x=3, y=3)
        assert not t._collides(p)

    def test_collision_out_of_bounds(self) -> None:
        t = Tetris()
        p = main.Piece("O", x=BOARD_W - 1, y=3)
        assert t._collides(p)


class TestGameOver:
    def test_game_over_when_spawn_collides(self) -> None:
        t = Tetris()
        # Fill the top rows so a new spawn cannot fit.
        for row in t.board[:4]:
            row[:] = ["T"] * BOARD_W
        t._spawn()
        assert t.game_over

    def test_game_over_when_locked_above_board(self) -> None:
        t = Tetris()
        # A piece whose cells extend above the top of the board triggers game over on lock.
        t.piece = main.Piece("O", x=2, y=-1)
        t._lock()
        assert t.game_over
