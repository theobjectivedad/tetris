"""Terminal Tetris — curses UI for the game logic in tetris.game."""

from __future__ import annotations

import curses
import random
import time
from typing import cast

from . import __version__
from .game import (
    BOARD_H,
    BOARD_W,
    PIECES,
    Event,
    HighScores,
    Tetris,
)
from .stats import sidebar_stats

# Colors: pair index -> piece kind
COLORS: dict[str, int] = {"I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7}

# Drawn geometry: each cell renders as a solid 2-column block — about
# square on a terminal's ~2:1 char aspect — and cells are contiguous with
# no gap, so filled regions read as one tight solid mass. The play field
# adds a 1-col solid wall outside the cell area.
BOARD_PITCH = 2
BOARD_INNER_W = BOARD_W * BOARD_PITCH  # 20
BOARD_WALL = 1
BOARD_W_DRAWN = BOARD_INNER_W + 2 * BOARD_WALL  # 22
CELL_OFF = BOARD_WALL  # cell x=0 is drawn at bx + CELL_OFF

# Key handling tuning
# Terminals deliver their own auto-repeat while a key is held, so we just
# throttle consecutive moves. This also guards against a single tap producing
# multiple events (e.g. escape-sequence artifacts).
STEP_THROTTLE = 0.04  # min interval between horizontal moves
ROTATE_COOLDOWN = 0.12
FRAME = 0.02          # main loop frame time (50 fps)
ESC_TTL = 0.15        # how long a partial ESC sequence is kept while reassembling

# Arrow keys arrive as ESC [ <A/B/C/D>. With nodelay() enabled, getch() can
# hand back the bare ESC if the sequence is split across reads; the stray
# '[' / 'C' bytes would then be processed as ordinary keys ('C' = hold!).
# Reassemble them here so a split sequence still becomes one arrow key.
ESC_SEQS = {
    (27, 0x5B, ord("A")): curses.KEY_UP,
    (27, 0x5B, ord("B")): curses.KEY_DOWN,
    (27, 0x5B, ord("C")): curses.KEY_RIGHT,
    (27, 0x5B, ord("D")): curses.KEY_LEFT,
}


def init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    pairs = {
        1: curses.COLOR_CYAN,
        2: curses.COLOR_YELLOW,
        3: curses.COLOR_MAGENTA,
        4: curses.COLOR_GREEN,
        5: curses.COLOR_RED,
        6: curses.COLOR_BLUE,
        7: curses.COLOR_WHITE,
        8: curses.COLOR_WHITE,   # text
        9: curses.COLOR_YELLOW,  # highlights
        10: curses.COLOR_BLACK,  # flash (with white bg)
    }
    for i, fg in pairs.items():
        if i == 10:
            curses.init_pair(i, curses.COLOR_WHITE, curses.COLOR_YELLOW)
        else:
            curses.init_pair(i, fg, curses.COLOR_BLACK)


def cell_attr(kind: str) -> int:
    return curses.color_pair(COLORS[kind]) if curses.has_colors() else 0


def draw_box(stdscr: curses.window, title: str, bx: int, by: int, w: int, h: int = 6) -> tuple[int, int]:
    """Draw a titled box; returns (inner_x, inner_y)."""
    border = f"┌{'─' * (w - 2)}┐"
    try:
        stdscr.addstr(by, bx, border, curses.A_DIM)
        stdscr.addstr(by + 1, bx, f"│ {title:<{w - 4}} │", curses.A_DIM)
        for i in range(h - 3):
            stdscr.addstr(by + 2 + i, bx, f"│{' ' * (w - 2)}│", curses.A_DIM)
        stdscr.addstr(by + h - 1, bx, f"└{'─' * (w - 2)}┘", curses.A_DIM)
    except curses.error:
        pass
    return bx + 2, by + 2


def draw_piece_preview(stdscr: curses.window, kind: str, ix: int, iy: int, dim: bool = False) -> None:
    if kind not in PIECES:
        return
    cells = PIECES[kind][0]
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    top, bottom, left, right = min(ys), max(ys), min(xs), max(xs)
    width = (right - left + 1) * BOARD_PITCH
    # Box inner area is 12 cols wide, starting at ix - 1.
    ox = ix - 1 + (12 - width) // 2
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if (x, y) in cells:
                txt = "██"
                try:
                    if dim:
                        stdscr.addstr(iy + (y - top), ox + (x - left) * BOARD_PITCH, txt, curses.A_DIM)
                    else:
                        stdscr.addstr(iy + (y - top), ox + (x - left) * BOARD_PITCH, txt, cell_attr(kind))
                except curses.error:
                    pass


def draw_board(stdscr: curses.window, t: Tetris, bx: int, by: int) -> None:
    # Solid border around the play area. The walls are outside the cell
    # area (1 col per side), so blocks never render on top of them.
    right = bx + BOARD_W_DRAWN - 1
    try:
        stdscr.addstr(by, bx, "█" * BOARD_W_DRAWN, curses.A_DIM)
        stdscr.addstr(by + BOARD_H + 1, bx, "█" * BOARD_W_DRAWN, curses.A_DIM)
        for y in range(1, BOARD_H + 1):
            stdscr.addstr(by + y, bx, "█", curses.A_DIM)
            stdscr.addstr(by + y, right, "█", curses.A_DIM)
    except curses.error:
        pass

    flash_rows = set(t.pending_clears)
    ghost_cells: set[tuple[int, int]] = set()
    live_cells: dict[tuple[int, int], str] = {}
    if not t.game_over:
        ghost_cells = {
            (dx + t.piece.x, t.ghost_y() + dy) for dx, dy in PIECES[t.piece.kind][t.piece.rot]
        }
        live_cells = {(x, y): t.piece.kind for x, y in t.piece.cells() if y >= 0}

    for y in range(BOARD_H):
        row = t.board[y]
        line_parts: list[tuple[str, int | None]] = []
        for x in range(BOARD_W):
            kind = row[x]
            if (x, y) in live_cells:
                line_parts.append(("██", cell_attr(live_cells[(x, y)])))
            elif kind:
                line_parts.append(("██", curses.color_pair(10) if y in flash_rows and curses.has_colors() else cell_attr(kind)))
            elif (x, y) in ghost_cells:
                line_parts.append(("▒▒", cell_attr(t.piece.kind) | curses.A_DIM))
            else:
                line_parts.append(("  ", None))

        x_cursor = bx + CELL_OFF
        for text, attr in line_parts:
            if text != "  ":
                try:
                    if attr is None:
                        stdscr.addstr(by + y + 1, x_cursor, text)
                    else:
                        stdscr.addstr(by + y + 1, x_cursor, text, attr)
                except curses.error:
                    pass
            x_cursor += BOARD_PITCH  # solid blocks, no gap

        if y in flash_rows:
            try:
                stdscr.addstr(by + y + 1, bx, "█" * BOARD_W_DRAWN, curses.A_BLINK)
            except curses.error:
                pass


def draw_sidebar(stdscr: curses.window, t: Tetris, hs: HighScores, by: int, sx: int, new_best: bool) -> None:
    hold_x, hold_y = draw_box(stdscr, "HOLD", sx, by, 14, 6)
    draw_piece_preview(stdscr, t.holding or "", hold_x, hold_y, dim=not t.can_hold)

    next_x, next_y = draw_box(stdscr, "NEXT", sx, by + 7, 14, 8)
    draw_piece_preview(stdscr, t.next_kind, next_x, next_y)
    draw_piece_preview(stdscr, t.next_kind2, next_x, next_y + 3, dim=True)

    stats = sidebar_stats(t.snapshot(), hs.best())
    for i, (label, value) in enumerate(stats):
        try:
            stdscr.addstr(by + 16 + i, sx, f"{label:<7}{value}", curses.color_pair(8))
        except curses.error:
            pass

    controls = [
        "←/→ move    ↑/Z rotate",
        "↓ soft drop  SPACE hard",
        "C hold   P pause",
        "Q quit",
    ]
    for i, line in enumerate(controls):
        try:
            stdscr.addstr(by + 24 + i, sx, line, curses.A_DIM)
        except curses.error:
            pass

    # Version, right-aligned just below the controls menu.
    try:
        vtxt = f"v{__version__}"
        x = sx + 15 - len(vtxt)
        if x < sx:  # too long to fit; truncate and left-align within the menu
            vtxt = vtxt[:15]
            x = sx
        stdscr.addstr(by + 29, x, vtxt, curses.A_DIM)
    except curses.error:
        pass

    if new_best:
        try:
            stdscr.addstr(by + 32, sx, "★ NEW BEST ★", curses.A_REVERSE | curses.A_BLINK)
        except curses.error:
            pass


def draw_game_over(stdscr: curses.window, t: Tetris, hs: HighScores, elapsed: float, rank: int | None, bx: int, by: int) -> None:
    interior_w = BOARD_INNER_W  # the cell area, inside the walls
    lines = [
        "GAME OVER",
        "",
        f"Score:  {t.score:,}",
        f"Lines {t.lines}  Level {t.level}",
        f"Time {int(elapsed // 60):02d}:{int(elapsed % 60):02d}  Pcs {t.pieces}",
    ]
    if rank is not None:
        lines.append("★ New high score ★")
    else:
        lines.append(f"Best:   {hs.best():,}")
    lines += ["", "R replay      Q quit"]

    # darkening overlay behind the message (drawn first, text on top)
    for y in range(BOARD_H):
        try:
            stdscr.addstr(by + y + 1, bx + CELL_OFF, " " * interior_w)
        except curses.error:
            pass

    start_y = by + BOARD_H // 2 - len(lines) // 2
    for i, line in enumerate(lines):
        try:
            attr = curses.A_REVERSE if line == "GAME OVER" else (
                curses.color_pair(9) if "★" in line else 0
            )
            stdscr.addstr(start_y + i, bx + CELL_OFF + max(0, (interior_w - len(line)) // 2), line, attr)
        except curses.error:
            pass


# Beeps per effect kind.
_BEEPS = {"clear": 1, "tetris": 2, "tspin": 3, "tspin-mini": 2}


class KeyReader:
    """Reads logical keys from a curses window.

    Owns the stateful bits of input handling that were previously locals in
    ``game_loop``: reassembling arrow-key ESC sequences that nodelay() getch
    can split across reads, and the move/rotate throttle timestamps.
    """

    def __init__(self) -> None:
        self.last_step = 0.0
        self.last_rotate = 0.0
        self._esc_seq: list[int] = []
        self._esc_t = 0.0

    def reset(self) -> None:
        # Restart only resets the move throttle (matches pre-refactor behavior).
        self.last_step = 0.0

    def next_key(self, stdscr: curses.window, now: float) -> int:
        """Return the next logical key, or -1 if no input is pending.

        Splits of a 3-byte arrow sequence (ESC [ A/B/C) are reassembled here;
        a lone ESC that never completes within ESC_TTL is dropped.
        """
        raw = stdscr.getch()
        if raw == -1:
            return -1
        key = -1
        if self._esc_seq:
            if now - self._esc_t > ESC_TTL:
                self._esc_seq = []
            if not self._esc_seq:
                if raw == 27:
                    self._esc_seq = [27]
                    self._esc_t = now
            else:
                self._esc_seq.append(raw)
                if len(self._esc_seq) == 3:
                    key = ESC_SEQS.get(cast("tuple[int, int, int]", tuple(self._esc_seq)), -1)
                    self._esc_seq = []
        elif raw == 27:
            self._esc_seq = [27]
            self._esc_t = now
        else:
            key = raw
        return key

    def allow_step(self, now: float) -> bool:
        if now - self.last_step >= STEP_THROTTLE:
            self.last_step = now
            return True
        return False

    def allow_rotate(self, now: float) -> bool:
        if now - self.last_rotate >= ROTATE_COOLDOWN:
            self.last_rotate = now
            return True
        return False


class Effects:
    """Transient visual/sound effects: floating score text, board shake, and
    the T-spin corner flash. Owns that state so ``game_loop`` stays readable."""

    def __init__(self) -> None:
        self.floaters: list[tuple[str, int, float]] = []
        self.shake_until = 0.0
        self.spin_flash: tuple[tuple[int, int], float] | None = None

    def clear(self) -> None:
        self.floaters.clear()
        self.shake_until = 0.0
        self.spin_flash = None

    def on_events(self, events: list[Event], now: float) -> None:
        for ev in events:
            self.floaters.append((ev.text, ev.row, now))
            if ev.kind in ("tspin", "tspin-mini") and ev.center is not None:
                self.spin_flash = (ev.center, now)
            for _ in range(_BEEPS[ev.kind]):
                try:
                    curses.beep()
                except curses.error:
                    pass
        events.clear()
        self.floaters[:] = [f for f in self.floaters if now - f[2] < 1.2]

    def shake(self, now: float) -> tuple[int, int]:
        if now < self.shake_until:
            return random.choice((-1, 0, 1)), random.choice((-1, 0, 1))
        return (0, 0)


def game_loop(stdscr: curses.window) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    init_colors()

    hs = HighScores()
    t = Tetris()
    new_best = False
    rank: int | None = None
    start_time = time.monotonic()
    reader = KeyReader()
    effects = Effects()

    def reset_game() -> None:
        nonlocal t, new_best, rank, start_time
        t = Tetris()
        new_best = False
        rank = None
        start_time = time.monotonic()
        reader.reset()
        effects.clear()

    while True:
        now = time.monotonic()

        # ---- input -------------------------------------------------
        key = reader.next_key(stdscr, now)
        if key in (ord("q"), ord("Q")):
            break
        if key in (ord("r"), ord("R")) and t.game_over:
            reset_game()
        elif key in (ord("p"), ord("P")):
            t.paused = not t.paused
        elif not t.paused and not t.game_over:
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                d = -1 if key == curses.KEY_LEFT else 1
                # One move per event, throttled — the terminal's auto-repeat
                # provides the repeat while the key is held.
                if reader.allow_step(now):
                    t.move(d, now)
            elif key == curses.KEY_UP:
                if reader.allow_rotate(now):
                    t.rotate(1, now)
            elif key in (ord("z"), ord("Z")):
                if reader.allow_rotate(now):
                    t.rotate(-1, now)
            elif key == curses.KEY_DOWN:
                t.soft_drop()
            elif key == ord(" "):
                if t.hard_drop() > 0:
                    effects.shake_until = now + 0.12
            elif key in (ord("c"), ord("C")):
                t.hold()

        # ---- gravity + lock delay ------------------------------------
        t.tick(now)

        # ---- flash animation ------------------------------------------
        if t.frozen:
            t.advance_flash()

        # ---- effects: floating text, spin flash, beeps ----------------
        effects.on_events(t.events, now)

        # ---- game over ---------------------------------------------------
        if t.game_over and not new_best and rank is None and t.score > 0:
            rank = hs.record(t.score, t.lines, t.level)
            if rank is not None:
                new_best = True

        # ---- draw ----------------------------------------------------
        max_y, max_x = stdscr.getmaxyx()
        sidebar_x_offset = BOARD_W_DRAWN + 4
        total_w = sidebar_x_offset + 16
        need_h = BOARD_H + 9  # board block + sidebar (incl. controls) must fit
        if max_x < total_w or max_y < need_h:
            stdscr.erase()
            try:
                stdscr.addstr(1, 1, f"Terminal too small — need {total_w}×{need_h}, got {max_x}×{max_y}")
            except curses.error:
                pass
            stdscr.refresh()
            time.sleep(FRAME)
            continue

        bx = max(0, (max_x - total_w) // 2)
        by = max(1, (max_y - (BOARD_H + 7)) // 2)

        stdscr.erase()

        # Board shake: a 1-cell jitter for a couple of frames after a hard
        # drop or a big clear.
        ox, oy = effects.shake(now)

        draw_board(stdscr, t, bx + ox, by + oy)

        # Floating score text, drifting up out of the board.
        for text, row, born in effects.floaters:
            fy = by + oy + row - int((now - born) * 2.5)
            fx = bx + ox + CELL_OFF + max(0, (BOARD_INNER_W - len(text)) // 2)
            try:
                stdscr.addstr(fy, fx, text, curses.A_REVERSE)
            except curses.error:
                pass

        # T-spin corner flash: the four diagonals of the T's center.
        if effects.spin_flash is not None:
            (cx, cy), st = effects.spin_flash
            if now - st >= 0.6:
                effects.spin_flash = None
            else:
                for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    px, py = cx + dx, cy + dy
                    if not (0 <= px < BOARD_W and 0 <= py < BOARD_H):
                        continue
                    try:
                        stdscr.addstr(
                            by + oy + py, bx + ox + CELL_OFF + px * BOARD_PITCH, "▓▓", curses.A_REVERSE
                        )
                    except curses.error:
                        pass

        draw_sidebar(stdscr, t, hs, by, bx + sidebar_x_offset, new_best)
        if t.game_over:
            draw_game_over(stdscr, t, hs, time.monotonic() - start_time, rank, bx, by)
        elif t.paused:
            try:
                stdscr.addstr(by + BOARD_H // 2, bx + (BOARD_W_DRAWN - 8) // 2, " PAUSED ", curses.A_REVERSE)
            except curses.error:
                pass
        stdscr.refresh()

        time.sleep(FRAME)


def main() -> None:
    curses.wrapper(game_loop)


if __name__ == "__main__":
    main()
