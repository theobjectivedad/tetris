"""Terminal Tetris — curses UI for the game logic in tetris.game.

Screens: the board + sidebar, plus centered modal dialogs (help on `?`,
settings menu on `s`, game over). All high scores and user settings are
loaded and saved through the unified store in ``tetris.state``.
"""

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
    Tetris,
)
from .settings import OPTIONS, Settings, cycle, format_value, value_of
from .state import GameState
from .stats import sidebar_stats

# Colors: pair index -> piece kind
COLORS: dict[str, int] = {"I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7}

# Frame rendering: a solid white bar/line (black glyph on white background,
# pair 11) so the border is a categorical visual distinct from every piece
# color. A_REVERSE with no color pair is dropped by some terminals, so it
# only remains the no-color fallback; init_colors() upgrades BORDER_ATTR.
BORDER_ATTR = curses.A_REVERSE

# Drawn geometry: each cell renders as a solid 2-column block — about
# square on a terminal's ~2:1 char aspect — and cells are contiguous with
# no gap, so filled regions read as one tight solid mass. The play field
# adds a 1-col solid wall outside the cell area.
BOARD_PITCH = 2
BOARD_INNER_W = BOARD_W * BOARD_PITCH  # 20
BOARD_WALL = 1
BOARD_W_DRAWN = BOARD_INNER_W + 2 * BOARD_WALL  # 22
CELL_OFF = BOARD_WALL  # cell x=0 is drawn at bx + CELL_OFF

# Minimum terminal size: the board block (22) + gap (4) + sidebar (16) wide;
# the sidebar is the tallest element (stats + NEW BEST indicator).
NEED_W = BOARD_W_DRAWN + 4 + 16  # 42
NEED_H = 34

# Key handling tuning
# Tap = one move; holding streams after DAS at the ARR rate (self-driven,
# not dependent on the terminal's slow initial auto-repeat delay).
DAS = 0.17           # delay after the first tap before held-key streaming
ARR = 0.04           # auto-repeat rate (min interval between moves) while held
HOLD_WINDOW = 0.06  # a dir-key event within this window counts as "still held"
ROTATE_COOLDOWN = 0.12
FRAME = 0.02          # main loop frame time (50 fps)
ESC_TTL = 0.15        # how long a partial ESC sequence is kept while reassembling
SPAWN_ANIM_SECONDS = 0.15  # how long the new piece glides in from the NEXT box

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
        11: curses.COLOR_BLACK,  # border (solid white bar)
    }
    for i, fg in pairs.items():
        if i == 10:
            curses.init_pair(i, curses.COLOR_WHITE, curses.COLOR_YELLOW)
        elif i == 11:
            curses.init_pair(i, curses.COLOR_BLACK, curses.COLOR_WHITE)
        else:
            curses.init_pair(i, fg, curses.COLOR_BLACK)
    global BORDER_ATTR
    BORDER_ATTR = curses.color_pair(11)


def cell_attr(kind: str) -> int:
    return curses.color_pair(COLORS[kind]) if curses.has_colors() else 0


# -- modal dialogs -----------------------------------------------------------


class Modal:
    """A centered dialog box drawn over the gameplay screen.

    ``lines`` are centered inside the box; ``cursor`` (an index into
    ``lines``) renders that row as a full-width reverse bar — used by the
    settings menu. The box is opaque: every interior cell is written, so
    whatever the game drew behind it is covered.
    """

    def __init__(self, title: str, lines: list[str], cursor: int | None = None) -> None:
        self.title = title
        self.lines = lines
        self.cursor = cursor


def draw_modal(stdscr: curses.window, modal: Modal, max_x: int, max_y: int) -> None:
    """Render ``modal`` centered in the window."""
    width = max(len(modal.title), max((len(l) for l in modal.lines), default=0)) + 6
    height = len(modal.lines) + 2
    width = min(width, max_x - 2)
    height = min(height, max_y - 2)
    bx = (max_x - width) // 2
    by = (max_y - height) // 2
    inner = width - 2

    try:
        # Top border with the title centered inside it.
        gap = inner - len(modal.title) - 2
        left = gap // 2
        stdscr.addstr(
            by, bx,
            "┌" + "─" * left + f" {modal.title} " + "─" * (gap - left) + "┐",
            BORDER_ATTR,
        )
        for i, line in enumerate(modal.lines):
            y = by + 1 + i
            is_cursor = modal.cursor is not None and i == modal.cursor
            stdscr.addstr(y, bx, "│", BORDER_ATTR)
            stdscr.addstr(y, bx + width - 1, "│", BORDER_ATTR)
            # Opaque interior: blank every cell so the board/sidebar behind
            # the box cannot bleed through the padding.
            if is_cursor:
                stdscr.addstr(y, bx + 1, " " * inner, curses.A_REVERSE)
            else:
                stdscr.addstr(y, bx + 1, " " * inner)
            if line:
                stdscr.addstr(
                    y, bx + 1 + (inner - len(line)) // 2, line,
                    curses.A_REVERSE if is_cursor else 0,
                )
        stdscr.addstr(by + height - 1, bx, "└" + "─" * inner + "┘", BORDER_ATTR)
    except curses.error:
        pass


def build_help_modal() -> Modal:
    """The `?` help dialog: the key legend plus the version.

    This is the single place the key map is documented for the player —
    it no longer sits in the sidebar at all times.
    """
    lines = [
        "←/→ move        ↑ rotate CW    Z rotate CCW",
        "↓ soft drop     SPACE hard drop",
        "C hold          P pause",
        "S settings      ? help",
        "R restart (paused/over)    Q quit",
        "",
        "Q or ? closes this dialog",
        "",
        f"v{__version__}",
    ]
    return Modal("HELP", lines)


def build_game_over_modal(t: Tetris, best: int, rank: int | None) -> Modal:
    """The game-over dialog, shown as a modal instead of inside the board."""
    lines = [
        f"Score:  {t.score:,}",
        f"Lines {t.lines}    Level {t.level}",
        f"Pieces {t.pieces}",
    ]
    if rank is not None:
        lines.append("★ New high score ★")
    else:
        lines.append(f"Best:   {best:,}")
    lines += ["", "R replay       Q quit"]
    return Modal("GAME OVER", lines)


def build_pause_modal() -> Modal:
    """The pause dialog: resume / restart / quit."""
    return Modal("PAUSED", ["", "P resume     R restart     Q quit", ""])


def build_settings_modal(settings: Settings, cursor: int) -> Modal:
    """The `s` settings dialog: one row per option, cursor row highlighted."""
    lines = [
        f"{opt.label:<14} {format_value(value_of(settings, opt.key))}" for opt in OPTIONS
    ]
    lines += ["", "↑/↓ select      ←/→ change      Q close"]
    return Modal("SETTINGS", lines, cursor=cursor)


def draw_box(stdscr: curses.window, title: str, bx: int, by: int, w: int, h: int = 6) -> tuple[int, int]:
    """Draw a titled box; returns (inner_x, inner_y)."""
    border = f"┌{'─' * (w - 2)}┐"
    try:
        stdscr.addstr(by, bx, border, BORDER_ATTR)
        stdscr.addstr(by + 1, bx, f"│ {title:<{w - 4}} │", BORDER_ATTR)
        for i in range(h - 3):
            stdscr.addstr(by + 2 + i, bx, f"│{' ' * (w - 2)}│", BORDER_ATTR)
        stdscr.addstr(by + h - 1, bx, f"└{'─' * (w - 2)}┘", BORDER_ATTR)
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


def draw_board(
    stdscr: curses.window,
    t: Tetris,
    bx: int,
    by: int,
    show_ghost: bool = True,
    hide_live: bool = False,
) -> None:
    # Solid border around the play area. The walls are outside the cell
    # area (1 col per side), so blocks never render on top of them.
    right = bx + BOARD_W_DRAWN - 1
    try:
        stdscr.addstr(by, bx, "█" * BOARD_W_DRAWN, BORDER_ATTR)
        stdscr.addstr(by + BOARD_H + 1, bx, "█" * BOARD_W_DRAWN, BORDER_ATTR)
        for y in range(1, BOARD_H + 1):
            stdscr.addstr(by + y, bx, "█", BORDER_ATTR)
            stdscr.addstr(by + y, right, "█", BORDER_ATTR)
    except curses.error:
        pass

    flash_rows = set(t.pending_clears)
    ghost_cells: set[tuple[int, int]] = set()
    live_cells: dict[tuple[int, int], str] = {}
    if not t.game_over:
        if show_ghost:
            ghost_cells = {
                (dx + t.piece.x, t.ghost_y() + dy) for dx, dy in PIECES[t.piece.kind][t.piece.rot]
            }
        # hide_live: the spawn glide draws the piece itself (see game_loop).
        if not hide_live:
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


def _draw_spawn_glide(
    stdscr: curses.window,
    t: Tetris,
    frac: float,
    bx: int,
    by: int,
    ox: int,
    oy: int,
) -> None:
    """Draw the newly spawned piece mid-glide from the NEXT box head to its
    spawn position. ``frac`` runs 0 (at the NEXT head) to 1 (at the grid
    position). The piece is drawn unclipped: it legitimately crosses the
    board's right wall while flying in from the sidebar.

    The "from" point uses the unshaken layout (the flight may ignore
    shake); the "to" point is the piece's current grid top-left (shake
    included), recomputed by the caller every frame so player input during
    the glide lands correctly.
    """
    kind = t.piece.kind
    live = [(x, y) for x, y in t.piece.cells() if y >= 0]
    if not live:
        return  # the whole piece is above the rim: nothing on the board yet
    min_x = min(x for x, _ in live)
    min_y = min(y for _, y in live)
    to_x = bx + ox + CELL_OFF + min_x * BOARD_PITCH
    to_y = by + oy + min_y + 1

    # "from": the NEXT box's head preview (see draw_piece_preview /
    # draw_sidebar): inner origin (bx + BOARD_W_DRAWN + 4 + 2, by + 9),
    # preview centered in the 12-col inner area on its rotation-0 footprint.
    base = PIECES[kind][0]
    width_px = (max(x for x, _ in base) - min(x for x, _ in base) + 1) * BOARD_PITCH
    next_x = bx + BOARD_W_DRAWN + 4 + 2
    next_y = by + 9
    from_x = next_x - 1 + (12 - width_px) // 2
    from_y = next_y + min(y for _, y in base)

    px = round(from_x + (to_x - from_x) * frac)
    py = round(from_y + (to_y - from_y) * frac)
    for x, y in live:
        row, col = py + (y - min_y), px + (x - min_x) * BOARD_PITCH
        try:
            stdscr.addstr(row, col, "██", cell_attr(kind))
        except curses.error:
            pass


def draw_sidebar(stdscr: curses.window, t: Tetris, state: GameState, by: int, sx: int, new_best: bool) -> None:
    hold_x, hold_y = draw_box(stdscr, "HOLD", sx, by, 14, 6)
    if state.settings.hold:
        draw_piece_preview(stdscr, t.holding or "", hold_x, hold_y, dim=not t.can_hold)
    else:
        # Hold disabled: show a dimmed "off" in the box instead of a preview.
        try:
            stdscr.addstr(hold_y, sx + 5, "off", curses.A_DIM)
        except curses.error:
            pass

    next_x, next_y = draw_box(stdscr, "NEXT", sx, by + 7, 14, 17)
    # Five previews at 3-row pitch (one blank row between slots) so 2-row
    # pieces never touch. Box height 17 = 14 inner rows: 5 slots at pitch 3
    # span 4*3 + 2 = 14 rows, so the last slot's bottom row just fits.
    # Head bright, rest dim.
    for i, kind in enumerate(t.queue[:5]):
        draw_piece_preview(stdscr, kind, next_x, next_y + i * 3, dim=i > 0)

    stats = sidebar_stats(t.snapshot(), state.best())
    for i, (label, value) in enumerate(stats):
        try:
            stdscr.addstr(by + 24 + i, sx, f"{label:<7}{value}", curses.color_pair(8))
        except curses.error:
            pass

    # The key legend and version no longer live here — they moved to the
    # help modal (`?`) to keep the main screen clean for the player.

    if new_best:
        try:
            stdscr.addstr(by + 32, sx, "★ NEW BEST ★", curses.A_REVERSE | curses.A_BLINK)
        except curses.error:
            pass  # also covers 34-row terminals where the row is off-screen


# Beeps per effect kind.
_BEEPS = {"clear": 1, "tetris": 2, "tspin": 3, "tspin-mini": 2}


class KeyReader:
    """Reads logical keys from a curses window.

    Owns the stateful bits of input handling that were previously locals in
    ``game_loop``: reassembling arrow-key ESC sequences that nodelay() getch
    can split across reads, and the move/rotate throttle timestamps.
    """

    def __init__(self) -> None:
        self.last_rotate = 0.0
        self._esc_seq: list[int] = []
        self._esc_t = 0.0
        self._dir = 0              # active hold direction (-1/1), 0 = none
        self._dir_since = 0.0      # when the current hold started
        self._last_dir_event = 0.0
        self._last_move = 0.0

    def reset(self) -> None:
        # Restart/menu resets the move throttle and any in-flight hold.
        self._last_move = 0.0
        self._dir = 0

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

    def on_direction(self, d: int, now: float) -> bool:
        """Handle a left/right key event; True if the piece should move.

        A fresh press (or direction change) moves immediately. While the key
        stays held the terminal's auto-repeat keeps events arriving; DAS/ARR
        streaming (auto_direction) takes over after the DAS delay.
        """
        fresh = self._dir != d
        if fresh:
            self._dir = d
            self._dir_since = now
        self._last_dir_event = now
        if now - self._last_move < ARR:
            return False  # anti double-fire (e.g. ESC reassembly artifact)
        if not fresh and now - self._dir_since < DAS:
            return False  # held, but the DAS delay hasn't elapsed yet
        self._last_move = now
        return True

    def auto_direction(self, now: float) -> int:
        """Direction to auto-move this frame (DAS/ARR streaming), or 0.

        The terminal gives no key-release event, so "held" means a direction
        event arrived within HOLD_WINDOW; after a real release the window
        expires and streaming stops (at most one extra step).
        """
        if self._dir == 0 or now - self._last_dir_event > HOLD_WINDOW:
            return 0
        if now - self._dir_since < DAS or now - self._last_move < ARR:
            return 0
        self._last_move = now
        return self._dir

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
        self.sound = True  # gated per frame from the user's sound setting

    def clear(self) -> None:
        self.floaters.clear()
        self.shake_until = 0.0
        self.spin_flash = None

    def on_events(self, events: list[Event], now: float) -> None:
        for ev in events:
            self.floaters.append((ev.text, ev.row, now))
            if ev.kind in ("tspin", "tspin-mini") and ev.center is not None:
                self.spin_flash = (ev.center, now)
            if self.sound:
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

    state = GameState()
    t = Tetris(start_level=state.settings.start_level)
    new_best = False
    rank: int | None = None
    reader = KeyReader()
    effects = Effects()
    menu: str | None = None  # None | "help" | "settings"
    menu_cursor = 0
    was_paused = False
    # Spawn animation: the new piece glides in from the NEXT box head.
    # last_seq starts at 0 (not t.spawn_seq) so the very first piece
    # glides too.
    anim_start: float | None = None
    last_seq = 0

    def reset_game() -> None:
        nonlocal t, new_best, rank, menu, was_paused, anim_start, last_seq
        t = Tetris(start_level=state.settings.start_level)
        new_best = False
        rank = None
        menu = None
        was_paused = False
        anim_start = None
        last_seq = 0  # a fresh game's first piece glides in too
        # Restart/menu: also clear any in-flight held-key stream.
        reader.reset()
        effects.clear()

    def open_menu(kind: str) -> None:
        """Open a modal menu; the game is paused for its duration."""
        nonlocal menu, was_paused, anim_start
        was_paused = t.paused
        t.paused = True
        menu = kind
        anim_start = None  # a menu open mid-flight cancels the glide
        reader.reset()  # no stale holds may survive a menu round-trip

    def close_menu() -> None:
        nonlocal menu
        t.paused = was_paused
        menu = None

    while True:
        now = time.monotonic()

        # ---- input -------------------------------------------------
        key = reader.next_key(stdscr, now)
        if key in (ord("q"), ord("Q")):
            if menu is not None:
                close_menu()  # in a modal, Q closes the dialog, never quits
            else:
                break
        elif key in (ord("r"), ord("R")) and (t.game_over or t.paused):
            reset_game()
        elif key == ord("?") and not t.game_over:
            if menu == "help":
                close_menu()  # ? toggles the help dialog
            elif menu is None:
                open_menu("help")
        elif key in (ord("s"), ord("S")) and menu is None and not t.game_over:
            menu_cursor = 0
            open_menu("settings")
        elif key in (ord("p"), ord("P")) and menu is None:
            t.paused = not t.paused
        elif menu == "settings" and not t.game_over:
            if key in (curses.KEY_UP, ord("k")):
                menu_cursor = (menu_cursor - 1) % len(OPTIONS)
            elif key in (curses.KEY_DOWN, ord("j")):
                menu_cursor = (menu_cursor + 1) % len(OPTIONS)
            elif key in (curses.KEY_RIGHT, curses.KEY_LEFT, ord(" "), curses.KEY_ENTER):
                d = -1 if key == curses.KEY_LEFT else 1
                opt = OPTIONS[menu_cursor]
                new_settings = cycle(state.settings, opt.key, d)
                # Persist immediately through the single state store.
                state.update_settings(**{opt.key: value_of(new_settings, opt.key)})
        elif menu is None and not t.paused and not t.game_over:
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                d = -1 if key == curses.KEY_LEFT else 1
                # One move per fresh press; holding streams at ARR after DAS.
                if reader.on_direction(d, now):
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
                if t.hard_drop() > 0 and state.settings.shake:
                    effects.shake_until = now + 0.12
            elif key in (ord("c"), ord("C")):
                if state.settings.hold:
                    t.hold()

        # ---- DAS/ARR streaming (held-key moves without new events) -------
        if menu is None and not t.paused and not t.game_over:
            auto = reader.auto_direction(now)
            if auto:
                t.move(auto, now)

        # ---- gravity + lock delay ------------------------------------
        t.tick(now)

        # ---- flash animation ------------------------------------------
        if t.frozen:
            t.advance_flash()

        # ---- effects: floating text, spin flash, beeps ----------------
        effects.sound = state.settings.sound
        effects.on_events(t.events, now)

        # ---- game over ---------------------------------------------------
        if t.game_over and not new_best and rank is None and t.score > 0:
            rank = state.record(t.score, t.lines, t.level)
            if rank is not None:
                new_best = True

        # ---- spawn animation ---------------------------------------------
        # A new piece has appeared (initial spawn, after a lock, a hold,
        # or a committed line clear): glide it in from the NEXT box head.
        if (
            not t.game_over
            and menu is None
            and not t.paused
            and t.spawn_seq != last_seq
        ):
            anim_start = now
            last_seq = t.spawn_seq

        # ---- draw ----------------------------------------------------
        max_y, max_x = stdscr.getmaxyx()
        sidebar_x_offset = BOARD_W_DRAWN + 4
        if max_x < NEED_W or max_y < NEED_H:
            stdscr.erase()
            try:
                stdscr.addstr(1, 1, f"Terminal too small — need {NEED_W}×{NEED_H}, got {max_x}×{max_y}")
            except curses.error:
                pass
            stdscr.refresh()
            time.sleep(FRAME)
            continue

        bx = max(0, (max_x - NEED_W) // 2)
        by = max(1, (max_y - (BOARD_H + 7)) // 2)

        stdscr.erase()

        # Board shake: a 1-cell jitter for a couple of frames after a hard
        # drop or a big clear.
        ox, oy = effects.shake(now)

        # Spawn glide: while the new piece is still in flight the live piece
        # is hidden and drawn by the glide (below) instead; once the
        # duration has elapsed the piece simply appears at its grid
        # position.
        glide_frac: float | None = None
        if anim_start is not None and not t.game_over:
            elapsed = now - anim_start
            if elapsed >= SPAWN_ANIM_SECONDS:
                anim_start = None
            else:
                glide_frac = min(1.0, elapsed / SPAWN_ANIM_SECONDS)

        draw_board(
            stdscr, t, bx + ox, by + oy,
            show_ghost=state.settings.ghost, hide_live=glide_frac is not None,
        )

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

        draw_sidebar(stdscr, t, state, by, bx + sidebar_x_offset, new_best)

        # Spawn glide: drawn last among the non-modal elements, so it can
        # legitimately overlap the board's right wall while flying in.
        if glide_frac is not None:
            _draw_spawn_glide(stdscr, t, glide_frac, bx, by, ox, oy)

        # ---- modals: game over / help / settings ---------------------
        if t.game_over:
            draw_modal(stdscr, build_game_over_modal(t, state.best(), rank), max_x, max_y)
        elif menu == "help":
            draw_modal(stdscr, build_help_modal(), max_x, max_y)
        elif menu == "settings":
            draw_modal(stdscr, build_settings_modal(state.settings, menu_cursor), max_x, max_y)
        elif t.paused:
            draw_modal(stdscr, build_pause_modal(), max_x, max_y)
        stdscr.refresh()

        time.sleep(FRAME)


def main() -> None:
    curses.wrapper(game_loop)


if __name__ == "__main__":
    main()
