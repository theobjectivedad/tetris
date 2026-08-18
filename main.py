"""Terminal Tetris — a curses-based Tetris game."""

from __future__ import annotations

import curses
import random
from dataclasses import dataclass, field

BOARD_W = 10
BOARD_H = 20

# Piece definitions: list of rotation states, each a list of (x, y) offsets
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

# A color pair per piece, index 0 = empty
COLORS: dict[str, int] = {
    "I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7,
}


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
    def __init__(self) -> None:
        self.bag: list[str] = []
        self.score = 0
        self.lines = 0
        self.level = 1
        self.next_kind = self._refill()
        self.board: list[list[str]] = [[""] * BOARD_W for _ in range(BOARD_H)]
        self.game_over = False
        self.paused = False
        self.drop_interval = 0.5
        self.piece = self._spawn()

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

    def _collides(self, piece: Piece, rot: int | None = None) -> bool:
        cells = piece.cells_at_rot(rot) if rot is not None else piece.cells()
        for cx, cy in cells:
            if cx < 0 or cx >= BOARD_W or cy >= BOARD_H:
                return True
            if cy >= 0 and self.board[cy][cx]:
                return True
        return False

    def _collides_at_rot(self, piece: Piece, rot: int) -> bool:
        return self._collides(piece, rot)

    def move(self, dx: int) -> None:
        p = Piece(self.piece.kind, self.piece.x + dx, self.piece.y, self.piece.rot)
        if not self._collides(p):
            self.piece = p

    def rotate(self) -> None:
        p = self.piece
        new_rot = (p.rot + 1) % 4
        # Try wall kicks: straight, left 1, left 2, right 1, right 2
        for dx in (0, -1, 1, -2, 2):
            q = Piece(p.kind, p.x + dx, p.y, new_rot)
            if not self._collides_at_rot(q, new_rot):
                self.piece = q
                return

    def soft_drop(self) -> None:
        self.move_down()

    def hard_drop(self) -> None:
        p = self.piece
        while not self._collides_at_rot(Piece(p.kind, p.x, p.y + 1, p.rot), p.rot):
            p = Piece(p.kind, p.x, p.y + 1, p.rot)
        self.piece = p
        self._lock()

    def move_down(self) -> None:
        p = self.piece
        q = Piece(p.kind, p.x, p.y + 1, p.rot)
        if not self._collides(q):
            self.piece = q
        else:
            self._lock()

    def _lock(self) -> None:
        for cx, cy in self.piece.cells():
            if cy < 0:
                self.game_over = True
                continue
            self.board[cy][cx] = self.piece.kind
        self._clear_lines()
        self.piece = self._spawn()

    def _clear_lines(self) -> None:
        new_board = [row for row in self.board if any(c == "" for c in row)]
        cleared = BOARD_H - len(new_board)
        if cleared:
            points = [0, 100, 300, 500, 800][cleared] * self.level
            self.score += points
            self.lines += cleared
            self.level = self.lines // 10 + 1
            self.drop_interval = max(0.05, 0.5 * (0.8 ** (self.level - 1)))
            while len(new_board) < BOARD_H:
                new_board.insert(0, [""] * BOARD_W)
            self.board = new_board

    def ghost_y(self) -> int:
        p = self.piece
        gy = p.y
        while not self._collides_at_rot(Piece(p.kind, p.x, gy + 1, p.rot), p.rot):
            gy += 1
        return gy


def init_colors() -> None:
    pairs = {
        1: (curses.COLOR_CYAN, curses.COLOR_BLACK),
        2: (curses.COLOR_YELLOW, curses.COLOR_BLACK),
        3: (curses.COLOR_MAGENTA, curses.COLOR_BLACK),
        4: (curses.COLOR_GREEN, curses.COLOR_BLACK),
        5: (curses.COLOR_RED, curses.COLOR_BLACK),
        6: (curses.COLOR_BLUE, curses.COLOR_BLACK),
        7: (curses.COLOR_WHITE, curses.COLOR_BLACK),
    }
    if curses.has_colors():
        curses.start_color()
        for i, (fg, bg) in pairs.items():
            curses.init_pair(i, fg, bg)
        curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(9, curses.COLOR_YELLOW, curses.COLOR_BLACK)


def draw_board(stdscr: curses.window, t: Tetris, bx: int, by: int) -> None:
    # border
    try:
        stdscr.addstr(by, bx, "█" * (BOARD_W * 2 + 1), curses.A_DIM if curses.has_colors() else 0)
    except curses.error:
        pass
    for y in range(BOARD_H):
        row_cells: list[str] = []
        row_colors: dict[int, int] = {}
        for x in range(BOARD_W):
            kind = t.board[y][x]
            row_cells.append("██" if kind else "  ")
            if kind:
                row_colors[x * 2] = COLORS[kind]
        # ghost + active piece
        if not t.game_over:
            ghost_cells = {
                (dx + t.piece.x, t.ghost_y() + dy)
                for dx, dy in PIECES[t.piece.kind][t.piece.rot]
            }
            for cx, cy in ghost_cells:
                if 0 <= cy < BOARD_H and 0 <= cx < BOARD_W and not t.board[cy][cx]:
                    row_cells[cx] = "▒▒"
                    row_colors[cx * 2] = -1
            for cx, cy in t.piece.cells():
                if 0 <= cy < BOARD_H and 0 <= cx < BOARD_W:
                    row_cells[cx] = "██"
                    row_colors[cx * 2] = COLORS[t.piece.kind]
        line = " ".join(row_cells)
        for col, attr in row_colors.items():
            if attr == -1:
                stdscr.addstr(by + y + 1, bx + col, row_cells[col], curses.A_DIM)
            elif curses.has_colors():
                stdscr.addstr(by + y + 1, bx + col, row_cells[col], curses.color_pair(attr))
            else:
                stdscr.addstr(by + y + 1, bx + col, row_cells[col])
        if not row_colors:
            stdscr.addstr(by + y + 1, bx, line)


def draw_sidebar(stdscr: curses.window, t: Tetris, bx: int, by: int) -> None:
    sx = bx + BOARD_W * 2 + 5
    info = [
        ("NEXT", ""),
        *next_piece_rows(t.next_kind),
        ("", ""),
        ("SCORE", str(t.score)),
        ("LINES", str(t.lines)),
        ("LEVEL", str(t.level)),
        ("", ""),
        ("←/→ move", "↑ rotate"),
        ("↓ soft drop", "SPACE hard drop"),
        ("P pause", "Q quit"),
    ]
    for i, (label, value) in enumerate(info):
        line = f"{label:<10}{value}"
        try:
            if curses.has_colors():
                stdscr.addstr(by + i, sx, line, curses.color_pair(8))
            else:
                stdscr.addstr(by + i, sx, line)
        except curses.error:
            pass


def next_piece_rows(kind: str) -> list[tuple[str, str]]:
    cells = PIECES[kind][0]
    ys = [cy for _, cy in cells]
    xs = [cx for cx, _ in cells]
    top, bottom = min(ys), max(ys)
    left, right = min(xs), max(xs)
    rows: list[tuple[str, str]] = []
    for y in range(top, bottom + 1):
        line = ""
        for x in range(left, right + 1):
            line += "██ " if (x, y) in cells else "   "
        rows.append(("", line))
    return rows


def game_loop(stdscr: curses.window) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    if curses.has_colors():
        init_colors()
    stdscr.keypad(True)

    import time
    t = Tetris()
    last_drop = time.monotonic()

    while True:
        # input
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (ord("p"), ord("P")):
            t.paused = not t.paused
        elif not t.paused and not t.game_over:
            if key == curses.KEY_LEFT:
                t.move(-1)
            elif key == curses.KEY_RIGHT:
                t.move(1)
            elif key == curses.KEY_UP:
                t.rotate()
            elif key == curses.KEY_DOWN:
                t.soft_drop()
                last_drop = time.monotonic()
            elif key == ord(" "):
                t.hard_drop()
                last_drop = time.monotonic()

        # gravity
        now = time.monotonic()
        if not t.paused and not t.game_over and now - last_drop >= t.drop_interval:
            t.move_down()
            last_drop = now

        # draw
        stdscr.erase()
        bx, by = 2, 2
        draw_board(stdscr, t, bx, by)
        draw_sidebar(stdscr, t, bx, by)
        if t.game_over:
            msg = " GAME OVER — press Q to quit "
            try:
                stdscr.addstr(
                    BOARD_H // 2 + by, bx + BOARD_W - 6, msg,
                    curses.A_REVERSE | (curses.color_pair(9) if curses.has_colors() else 0),
                )
            except curses.error:
                pass
        elif t.paused:
            try:
                stdscr.addstr(BOARD_H // 2 + by, bx + 2, " PAUSED ", curses.A_REVERSE)
            except curses.error:
                pass
        stdscr.refresh()

        time.sleep(0.02)


def main() -> None:
    curses.wrapper(game_loop)


if __name__ == "__main__":
    main()
