"""Terminal Tetris — curses UI for the game logic in tetris.game.

Layout: three columns centered as one block — the stats panel (left),
the board (center), and the HOLD/NEXT column (right) — plus centered
modal dialogs (help on `?`, high scores on `h`, settings menu on `s`,
game over with high-score name entry). Escape closes any open dialog;
at game over it starts a new game without saving the score (Enter
saves the high score and starts a new game).
All high scores and user settings are loaded and saved through the
unified store in ``tetris.state``.
"""

from __future__ import annotations

import curses
import random
import time
from typing import cast

from . import __version__
from .engine import SPRINT_LINES
from .game import (
    BOARD_H,
    BOARD_W,
    PIECES,
    Event,
    Tetris,
)
from .settings import OPTIONS, Settings, cycle, format_value, value_of
from .state import MAX_NAME, GameState
from .stats import format_time, panel_stats
from .themes import DEFAULT_THEME, THEMES

# Colors: pair index -> piece kind
COLORS: dict[str, int] = {"I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7}

# Frame rendering: the border and the board/panel background use
# extended colors (gray 231 / near-black 235) so neither can read as a
# block. On terminals with fewer colors ncurses maps the pairs to the
# nearest available colors — the border falls back to the terminal's
# default foreground (always readable) and the background fill is a
# no-op. A_DIM remains the no-color fallback.
BORDER_ATTR = curses.A_DIM
BORDER_PAIR = 11
# Board/panel background pair (ext color 235 on capable terminals;
# ncurses maps it to a no-op fill on terminals with fewer colors).
BG_PAIR = 12

# Drawn geometry: each cell renders as a solid 2-column block — about
# square on a terminal's ~2:1 char aspect — and cells are contiguous with
# no gap, so filled regions read as one tight solid mass. The play field
# adds a 1-col solid wall outside the cell area.
BOARD_PITCH = 2
BOARD_INNER_W = BOARD_W * BOARD_PITCH  # 20
BOARD_WALL = 1
BOARD_W_DRAWN = BOARD_INNER_W + 2 * BOARD_WALL  # 22
CELL_OFF = BOARD_WALL  # cell x=0 is drawn at bx + CELL_OFF
BOARD_BAR = "█" * BOARD_W_DRAWN  # the solid wall row, precomputed

# Layout: three columns centered as one block — the stats panel (left),
# the board block (center), and the HOLD/NEXT column (right):
#   stats (16) + gap (4) + board (22) + gap (4) + HOLD/NEXT (14) = 60.
STATS_W = 16
PANEL_GAP = 4
HOLD_W = 14
NEED_W = STATS_W + PANEL_GAP + BOARD_W_DRAWN + PANEL_GAP + HOLD_W  # 60
# Minimum height: everything fits in the 27-row vertical block (the NEXT
# box's bottom row reaches by + 23 with by >= 1).
NEED_H = 27

# Key handling tuning
# Tap = one move; holding streams after DAS at the ARR rate (self-driven,
# not dependent on the terminal's slow initial auto-repeat delay).
# DAS/ARR are the DEFAULTS; the live values come from the settings menu
# (Settings.das/arr, synced into KeyReader each time the menu changes).
DAS = 0.17           # default delay after the first tap before held-key streaming
ARR = 0.04           # default auto-repeat rate (min interval between moves)
HOLD_WINDOW = 0.06  # a dir-key event within this window counts as "still held"
ROTATE_COOLDOWN = 0.12
FRAME = 0.02          # main loop frame time (50 fps)
ESC_TTL = 0.06        # how long a partial ESC sequence is kept while reassembling;
                      # a bare ESC is emitted after this, so keep it short — with
                      # ncurses' 25ms set_escdelay only exotic/slow terminals can
                      # still split a 3-byte sequence across reads
SPAWN_ANIM_SECONDS = 0.15  # how long the new piece glides in from the NEXT box
# Bare Escape byte; some _curses builds lack the KEY_ESCAPE constant.
KEY_ESCAPE = getattr(curses, "KEY_ESCAPE", 27)

# Arrow keys arrive as ESC [ <A/B/C/D>. With nodelay() enabled, getch() can
# hand back the bare ESC if the sequence is split across reads — either the
# terminal is slower than the 25ms ncurses set_escdelay disambiguation
# window, or a read boundary cut the sequence; the stray '[' / 'C' bytes
# would then be processed as ordinary keys ('C' = hold!). Reassemble them
# here so a split sequence still becomes one arrow key.
ESC_SEQS = {
    (27, 0x5B, ord("A")): curses.KEY_UP,
    (27, 0x5B, ord("B")): curses.KEY_DOWN,
    (27, 0x5B, ord("C")): curses.KEY_RIGHT,
    (27, 0x5B, ord("D")): curses.KEY_LEFT,
}


def init_colors(theme: str = "classic") -> None:
    """(Re)initialize the color pairs.

    Pairs 1-9 come from the named theme (piece cells 1-7, text 8,
    highlight 9; unknown names fall back to classic) — safe to re-run
    live when the theme setting changes, as curses allows re-initing a
    pair (re-run build_attrs() afterwards to refresh cached attrs). The
    flash pair (10) and the border/background pairs (11/12) are fixed.
    """
    global BORDER_ATTR
    if not curses.has_colors():
        return
    t = THEMES.get(theme, THEMES[DEFAULT_THEME])
    curses.start_color()
    pairs = {
        1: t.cells["I"],
        2: t.cells["O"],
        3: t.cells["T"],
        4: t.cells["S"],
        5: t.cells["Z"],
        6: t.cells["J"],
        7: t.cells["L"],
        8: t.text,
        9: t.highlight,
        10: curses.COLOR_BLACK,  # flash (fixed: white on yellow)
    }
    for i, fg in pairs.items():
        if i == 10:
            curses.init_pair(i, curses.COLOR_WHITE, curses.COLOR_YELLOW)
        else:
            curses.init_pair(i, fg, curses.COLOR_BLACK)
    # Border: a gray none of the pieces use (extended color 231) on
    # capable terminals; on terminals with fewer colors ncurses maps the
    # pair to the nearest available colors (the default foreground —
    # always readable — so the border stays visible). NOTE: never use
    # A_REVERSE here — with a white-fg/black-bg default it swaps to a
    # black bar (invisible). The board/panel background (pair 12) is a very
    # dark gray (ext 235): a subtle depth fill that becomes a no-op where
    # the color is unmappable. (Some _curses builds lack color_count(), so
    # this intentionally does not branch on it — ncurses degrades the
    # extended pairs gracefully instead.)
    curses.init_pair(BORDER_PAIR, 231, curses.COLOR_BLACK)
    curses.init_pair(BG_PAIR, curses.COLOR_BLACK, 235)
    BORDER_ATTR = curses.color_pair(BORDER_PAIR)


def cell_attr(kind: str) -> int:
    """Attr for a piece kind (cached by build_attrs after init_colors)."""
    return CELL_ATTRS[kind]


def bg_attr() -> int:
    """Attr for the board/panel background fill (pair 12); 0 when the
    terminal has no colors (the fill is plain background then — a no-op)."""
    return curses.color_pair(BG_PAIR) if curses.has_colors() else 0


# Cached attributes, built once by build_attrs() after init_colors(): the
# render path used to call has_colors()/color_pair() (C crossings) per cell
# — 20-60 times a frame. build_attrs() replaces that with plain lookups.
CELL_ATTRS: dict[str, int] = {}
FLASH_ATTR = 0   # flash row (pair 10: white on yellow)
STAT_ATTR = 0    # stats panel text (pair 8)
BG_ATTR = 0      # board/panel background fill (pair 12)


def build_attrs() -> None:
    """Cache the per-kind/global attrs after init_colors() has run
    (color_pair() needs a live curses screen)."""
    global FLASH_ATTR, STAT_ATTR, BG_ATTR
    for kind, pair in COLORS.items():
        CELL_ATTRS[kind] = curses.color_pair(pair) if curses.has_colors() else 0
    FLASH_ATTR = curses.color_pair(10) if curses.has_colors() else 0
    STAT_ATTR = curses.color_pair(8) if curses.has_colors() else 0
    BG_ATTR = bg_attr()


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
    """The `?` help dialog: aligned key legend, scoring explainer, version.

    This is the single place the key map is documented for the player —
    it no longer sits in the sidebar at all times. The legend is laid out
    as two fixed columns (keys / actions) and a SCORING section explains
    the stats panel (level, combo, B2B, spins); every row stays narrow
    enough to read on a 60-column terminal.
    """
    separator = "─" * 32
    lines = [
        "←/→ move        SPACE hard drop",
        "↑ rotate CW     Z rotate CCW",
        "↓ soft drop     C hold",
        "P pause         S settings",
        "H scores        ? help",
        "R restart       Q quit",
        "ESC close / pause",
        "GAME OVER: ENTER save, ESC no save",
        separator,
        "SCORING (all points × level):",
        "Single 100    Double 300",
        "Triple 500    Tetris 800",
        "T-spin: 200-1600 by lines cleared",
        "T-spin, no lines: 100 mini / 400",
        "Every 10 lines: level up (faster)",
        "COMBO +50×combo×level per clear run",
        "B2B 1.5× for Tetris/T-spin streaks",
        "SPINS: full T-spins this game",
        separator,
        f"v{__version__.split('+')[0]}",
    ]
    return Modal("HELP", lines)


def build_sprint_modal(
    t: Tetris, is_new_best: bool, best_time: float | None
) -> Modal:
    """The sprint game-over dialog (P11).

    Win -> "SPRINT CLEARED" with the clear time, best time, and score.
    Loss -> "TIME UP" with the lines reached and score. The best time is
    the store's best (a win that set a new best shows the flag instead).
    """
    if t.won:
        lines = [f"Time    {format_time(t.play_time)}"]
        if is_new_best:
            lines.append("* NEW BEST TIME *")
        elif best_time is not None:
            lines.append(f"Best    {format_time(best_time)}")
        lines += [f"Lines   {t.lines}/{SPRINT_LINES}", f"Score   {t.score:,}"]
        title = "SPRINT CLEARED"
    else:
        lines = [f"Lines   {t.lines}/{SPRINT_LINES}", f"Score   {t.score:,}"]
        if best_time is not None:
            lines.append(f"Best    {format_time(best_time)}")
        title = "TIME UP"
    lines += ["", "R/ESC new game       Q quit", "G replay last game"]
    return Modal(title, lines)


def build_game_over_modal(
    t: Tetris,
    best: int,
    rank: int | None,
    name: str = "",
    name_awaiting: bool = False,
    seed: int | None = None,
) -> Modal:
    """The game-over dialog, shown as a modal instead of inside the board.

    When ``rank`` is not None the score made the high-score table: while
    ``name_awaiting`` the player is still typing their name (the block
    cursor marks the input position); Enter saves the name and starts a
    new game, Escape starts a new game without saving.

    ``seed`` (P9): the game's RNG seed — with the saved input log it makes
    the run replayable (G re-plays the last game).
    """
    lines = [
        f"Score   {t.score:,}",
        f"Lines   {t.lines}    Level {t.level}",
        f"Pieces  {t.pieces}",
        f"Time    {format_time(t.play_time)}",
    ]
    if t.best_combo > 0:
        lines.append(f"Best combo  {t.best_combo}")
    if rank is not None:
        if name_awaiting:
            lines.append(f"ENTER YOUR NAME: {name}█")
        elif name:
            lines.append(f"★ #{rank + 1} — {name} ★")
        else:
            lines.append("★ New high score ★")
    else:
        lines.append(f"Best:   {best:,}")
    if seed is not None:
        lines.append(f"Seed: {seed}")
    if name_awaiting:
        # During name entry G is a typed name character (like any letter),
        # so replay is offered only on the settled game-over screen.
        lines += [
            "",
            "ENTER save + new game",
            "ESC  new game, no save",
            "Q quit",
        ]
    else:
        lines += ["", "R/ESC new game       Q quit", "G replay last game"]
    return Modal("GAME OVER", lines)


def build_pause_modal() -> Modal:
    """The pause dialog: resume / restart / quit."""
    return Modal("PAUSED", ["", "P/ESC resume  R restart  Q quit", ""])


def build_settings_modal(settings: Settings, cursor: int) -> Modal:
    """The `s` settings dialog: one row per option, cursor row highlighted.

    Every option row is a fixed 22 chars — the label left-justified in a
    14-col field, the value right-justified in an 8-col field — so the
    centered rows line up in perfectly aligned columns.
    """
    lines = [
        f"{opt.label:<14}{format_value(value_of(settings, opt.key)):>8}" for opt in OPTIONS
    ]
    lines += ["", "↑/↓ select    ←/→ change    ESC close"]
    return Modal("SETTINGS", lines, cursor=cursor)


def build_scores_modal(state: GameState) -> Modal:
    """The `h` high-scores dialog: the top-10 table.

    Columns: rank, name (— when unset), comma-formatted score, level, and
    the YYYY-MM-DD date. Rows are a fixed 39 chars so the table lines up.
    """
    lines: list[str] = [f"{'#':>2}  {'NAME':<10}{'SCORE':>9}{'LVL':>4}  DATE"]
    for i, entry in enumerate(state.entries[:10], start=1):
        name = str(entry.get("name") or "").strip() or "—"
        score = entry.get("score")
        score = score if isinstance(score, int) and not isinstance(score, bool) else 0
        level = entry.get("level")
        level = level if isinstance(level, int) and not isinstance(level, bool) else 0
        date = str(entry.get("date") or "")[:10]
        lines.append(f"{i:>2}  {name:<10}{score:>9,}{level:>4}  {date}")
    if not state.entries:
        lines = ["No scores yet — play a game!"]
    lines += ["", "ESC close"]
    return Modal("HIGH SCORES", lines)


def draw_box(stdscr: curses.window, title: str, bx: int, by: int, w: int, h: int = 6) -> tuple[int, int]:
    """Draw a titled box; returns (inner_x, inner_y)."""
    border = f"┌{'─' * (w - 2)}┐"
    try:
        stdscr.addstr(by, bx, border, BORDER_ATTR)
        stdscr.addstr(by + 1, bx, f"│ {title:<{w - 4}} │", BORDER_ATTR)
        for i in range(h - 3):
            # Borders keep BORDER_ATTR; the interior run gets the panel
            # background (a no-op fill when colors are unavailable).
            stdscr.addstr(by + 2 + i, bx, "│", BORDER_ATTR)
            stdscr.addstr(by + 2 + i, bx + 1, " " * (w - 2), BG_ATTR)
            stdscr.addstr(by + 2 + i, bx + w - 1, "│", BORDER_ATTR)
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
        stdscr.addstr(by, bx, BOARD_BAR, BORDER_ATTR)
        stdscr.addstr(by + BOARD_H + 1, bx, BOARD_BAR, BORDER_ATTR)
        for y in range(1, BOARD_H + 1):
            stdscr.addstr(by + y, bx, "█", BORDER_ATTR)
            stdscr.addstr(by + y, right, "█", BORDER_ATTR)
    except curses.error:
        pass

    # Panel background behind the cell area (one addstr per row): a very
    # dark gray on 256-color terminals, a no-op fill otherwise. Cells,
    # the ghost, and the flash rows are drawn on top of it.
    try:
        for y in range(BOARD_H):
            stdscr.addstr(by + y + 1, bx + CELL_OFF, " " * BOARD_INNER_W, BG_ATTR)
    except curses.error:
        pass

    flash_rows = tuple(t.pending_clears)
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
                line_parts.append(("██", FLASH_ATTR if y in flash_rows else cell_attr(kind)))
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
                stdscr.addstr(by + y + 1, bx, BOARD_BAR, curses.A_BLINK)
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
    board's right wall while flying in from the HOLD/NEXT column.

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


def draw_stats_panel(stdscr: curses.window, t: Tetris, by: int, x: int, new_best: bool) -> None:
    """The left column: the six stats at ``x`` (label dim, value bright,
    color pair 8), starting two rows below the block top, and the
    "★ NEW BEST ★" indicator two rows below the last stat row. The key
    legend and version live in the help modal (`?`) instead."""
    for i, (label, value) in enumerate(panel_stats(t.snapshot(), sprint=t.sprint)):
        value_attr = STAT_ATTR
        # Sprint (P11): blink the countdown once 30 s or less remain.
        if t.sprint and label == "TIME" and t.time_left is not None and t.time_left <= 30:
            value_attr = STAT_ATTR | curses.A_BLINK
        try:
            stdscr.addstr(
                by + 2 + i, x, f"{label:<7}", STAT_ATTR | curses.A_DIM
            )
            stdscr.addstr(by + 2 + i, x + 7, value, value_attr)
        except curses.error:
            pass

    if new_best:
        try:
            stdscr.addstr(by + 9, x, "★ NEW BEST ★", curses.A_REVERSE | curses.A_BLINK)
        except curses.error:
            pass


def draw_sidebar(stdscr: curses.window, t: Tetris, state: GameState, by: int, sx: int) -> None:
    """The right column: the HOLD box at ``sx`` and the NEXT box below it."""
    hold_x, hold_y = draw_box(stdscr, "HOLD", sx, by, HOLD_W, 6)
    if state.settings.hold:
        draw_piece_preview(stdscr, t.holding or "", hold_x, hold_y, dim=not t.can_hold)
    else:
        # Hold disabled: show a dimmed "off" in the box instead of a preview.
        try:
            stdscr.addstr(hold_y, sx + 5, "off", curses.A_DIM)
        except curses.error:
            pass

    next_x, next_y = draw_box(stdscr, "NEXT", sx, by + 7, HOLD_W, 17)
    # Five previews at 3-row pitch (one blank row between slots) so 2-row
    # pieces never touch. Box height 17 = 14 inner rows: 5 slots at pitch 3
    # span 4*3 + 2 = 14 rows, so the last slot's bottom row just fits.
    # Head bright, rest dim.
    for i, kind in enumerate(t.queue[:5]):
        draw_piece_preview(stdscr, kind, next_x, next_y + i * 3, dim=i > 0)


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
        self.das = DAS  # live-updated from the settings menu (P4)
        self.arr = ARR
        self._esc_seq: list[int] = []
        self._esc_t = 0.0
        self._dir = 0              # active hold direction (-1/1), 0 = none
        self._dir_since = 0.0      # when the current hold started
        self._last_dir_event = 0.0
        self._last_move = 0.0

    def reset(self) -> None:
        # Restart/menu resets the move throttle, any in-flight hold, and
        # any partially reassembled ESC sequence (stale bytes must never
        # leak into or out of a modal).
        self._last_move = 0.0
        self._dir = 0
        self._esc_seq = []

    def next_key(self, stdscr: curses.window, now: float) -> int:
        """Return the next logical key, or -1 if no input is pending.

        Splits of a 3-byte arrow sequence (ESC [ A/B/C) are reassembled
        here. A lone ESC that never completes within ESC_TTL is a real
        Escape press: it is emitted, and whatever input follows it is
        still delivered next frame instead of being eaten.
        """
        # An ESC from an earlier frame that never became an arrow
        # sequence: emit it BEFORE reading further input so the input
        # that followed it is not lost.
        if self._esc_seq and now - self._esc_t > ESC_TTL:
            self._esc_seq = []
            return 27
        raw = stdscr.getch()
        if raw == -1:
            return -1
        if self._esc_seq:
            if raw == 27:
                # A fresh ESC while one is already pending: emit the
                # pending one now and start tracking the new press.
                self._esc_seq = [27]
                self._esc_t = now
                return 27
            self._esc_seq.append(raw)
            if len(self._esc_seq) == 3:
                key = ESC_SEQS.get(cast("tuple[int, int, int]", tuple(self._esc_seq)), -1)
                self._esc_seq = []
                return key  # -1: an unknown 3-byte ESC sequence, dropped
            return -1
        if raw == 27:
            self._esc_seq = [27]
            self._esc_t = now
            return -1
        return raw

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
        if now - self._last_move < self.arr:
            return False  # anti double-fire (e.g. ESC reassembly artifact)
        if not fresh and now - self._dir_since < self.das:
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
        if now - self._dir_since < self.das or now - self._last_move < self.arr:
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
    # ncurses otherwise waits its 1000ms default ESCDELAY after a bare ESC
    # for a possible escape-sequence tail before returning it, which made
    # ESC feel ~1s sluggish. 25ms still lets fast terminals deliver a split
    # arrow sequence (ESC [ A/B/C) as one key, and the app-level ESC_TTL
    # reassembly remains the fallback for slower terminals.
    curses.set_escdelay(25)
    state = GameState()
    init_colors(state.settings.theme)
    build_attrs()

    # Replay (P9): the engine is fully deterministic given (seed, actions,
    # times), so each game gets a seed, and every accepted player action
    # is logged with its game-relative time. G at game over re-plays the
    # last saved replay at 1× speed.
    seed = int(time.monotonic() * 1000) % 2**32
    game_start = time.monotonic()
    replay_events: list[tuple[float, str]] = []
    replay_saved = False
    replay: dict[str, object] | None = None
    replay_engine: Tetris | None = None
    replay_original: Tetris | None = None
    replay_start = 0.0
    replay_next = 0
    replay_stop_seq = 0

    t = Tetris(
        rng=random.Random(seed),
        start_level=state.settings.start_level,
        sprint=state.settings.mode == "sprint",
    )
    new_best = False
    rank: int | None = None
    # Sprint bookkeeping (P11): recorded once when a sprint game ends.
    sprint_handled = False
    sprint_new_best = False
    sprint_best: float | None = None
    reader = KeyReader()
    effects = Effects()
    menu: str | None = None  # None | "help" | "settings" | "scores"
    menu_cursor = 0
    name_awaiting = False  # game over: high-score name still being typed
    typed_name = ""
    was_paused = False
    # Spawn animation: the new piece glides in from the NEXT box head.
    # last_seq starts at 0 (not t.spawn_seq) so the very first piece
    # glides too.
    anim_start: float | None = None
    last_seq = 0
    # Static-scene detection: the scene key of the last drawn frame.
    # When the key is unchanged (and no key was consumed, no shake is
    # active) the screen still shows the right picture, so the frame
    # skips erase()+all draws — refresh() stays once-per-frame (tests
    # index frames by time) and is a cheap no-op on unchanged content.
    prev_scene: tuple[object, ...] | None = None

    def reset_game() -> None:
        nonlocal t, new_best, rank, menu, was_paused, anim_start, last_seq
        nonlocal name_awaiting, typed_name, prev_scene
        nonlocal seed, game_start, replay_saved
        nonlocal replay, replay_engine, replay_original
        nonlocal replay_start, replay_next, replay_stop_seq
        nonlocal sprint_handled, sprint_new_best, sprint_best
        seed = int(time.monotonic() * 1000) % 2**32
        game_start = time.monotonic()
        replay_events.clear()
        replay_saved = False
        replay = None
        replay_engine = None
        replay_original = None
        replay_start = 0.0
        replay_next = 0
        replay_stop_seq = 0
        t = Tetris(
            rng=random.Random(seed),
            start_level=state.settings.start_level,
            sprint=state.settings.mode == "sprint",
        )
        new_best = False
        rank = None
        sprint_handled = False
        sprint_new_best = False
        sprint_best = None
        menu = None
        was_paused = False
        anim_start = None
        last_seq = 0  # a fresh game's first piece glides in too
        name_awaiting = False
        typed_name = ""
        prev_scene = None  # force a full redraw after a reset
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

    def commit_name() -> None:
        """Store the typed name on the new high-score entry (if any)."""
        nonlocal name_awaiting
        if rank is not None:
            state.set_entry_name(rank, typed_name)
        name_awaiting = False

    def log_action(token: str) -> None:
        """Record a player action for the current game's replay (P9):
        the token at this frame's game-relative time. Attempts are logged
        when the UI authorizes them (throttle passed) — the engine may
        still reject a move, which replays identically."""
        replay_events.append((now - game_start, token))

    def apply_replay_token(engine: Tetris, token: str, at: float) -> None:
        """Re-run one logged action on the replay engine at its original
        game-relative time (P9)."""
        if token == "L":
            engine.move(-1, at)
        elif token == "R":
            engine.move(1, at)
        elif token == "U":
            engine.rotate(1, at)
        elif token == "Z":
            engine.rotate(-1, at)
        elif token == "S":
            engine.soft_drop()
        elif token == "H":
            engine.hard_drop()
        elif token == "C":
            engine.hold()
        # Unknown tokens (e.g. ones logged by a newer build) are skipped.

    def start_replay() -> None:
        """G at game over: re-run the most recent saved replay (P9).

        A fresh engine gets the original seed/start level; the logged
        events are re-fed at their original game-relative timestamps
        (1× real time). ``t`` is swapped to the replay engine so the
        normal draw path renders the replay; the original game-over
        engine is restored when the replay ends or is aborted (ESC).
        """
        nonlocal t, replay, replay_engine, replay_original
        nonlocal replay_start, replay_next, replay_stop_seq
        nonlocal anim_start, last_seq, prev_scene
        data = state.last_replay()
        if data is None:
            return
        raw_seed = data.get("seed")
        raw_level = data.get("start_level")
        if not isinstance(raw_seed, int) or not isinstance(raw_level, int):
            return
        replay = data
        replay_original = t
        replay_engine = Tetris(
            rng=random.Random(raw_seed),
            start_level=raw_level,
            sprint=bool(data.get("sprint", False)),
        )
        t = replay_engine
        replay_start = time.monotonic()
        replay_next = 0
        replay_stop_seq = 0
        # The replay must use the original game's input timing, and must
        # not inherit a held key or a rotate cooldown from the live game.
        raw_das = data.get("das")
        raw_arr = data.get("arr")
        if isinstance(raw_das, (int, float)) and not isinstance(raw_das, bool):
            reader.das = float(raw_das)
        if isinstance(raw_arr, (int, float)) and not isinstance(raw_arr, bool):
            reader.arr = float(raw_arr)
        reader.reset()
        effects.clear()
        anim_start = None
        last_seq = 0  # the replay's first piece glides in too
        prev_scene = None  # force a full redraw onto the replay board

    def finish_replay() -> None:
        """End a replay (completed or aborted) and restore the original
        game-over screen (P9)."""
        nonlocal t, replay, replay_engine, replay_original
        nonlocal replay_next, replay_stop_seq, anim_start, last_seq, prev_scene
        if replay_original is not None:
            t = replay_original
        replay = None
        replay_engine = None
        replay_original = None
        replay_next = 0
        replay_stop_seq = 0
        reader.das = state.settings.das
        reader.arr = state.settings.arr
        reader.reset()
        effects.clear()
        anim_start = None
        last_seq = t.spawn_seq  # no glide: the final piece is already placed
        prev_scene = None  # force a redraw of the original game-over screen

    def replay_step(now: float) -> None:
        """One frame of the replay: feed every logged event whose
        game-relative time has elapsed, tick the engine, and finish when
        the run is done (P9)."""
        nonlocal replay_next, replay_stop_seq
        events = replay.get("events") if replay is not None else None
        if not isinstance(events, list) or replay_engine is None:
            finish_replay()
            return
        elapsed = now - replay_start
        while replay_next < len(events):
            ev = events[replay_next]
            if (
                not isinstance(ev, list)
                or len(ev) != 2
                or not isinstance(ev[0], (int, float))
                or isinstance(ev[0], bool)
                or not isinstance(ev[1], str)
            ):
                replay_next += 1  # skip a malformed entry
                continue
            if ev[0] > elapsed:
                break
            replay_next += 1
            apply_replay_token(replay_engine, ev[1], float(ev[0]))
        replay_engine.tick(elapsed)
        if replay_engine.frozen:
            replay_engine.advance_flash()
        # Finish: the run ended in game over, or all events are fed and
        # the piece produced by the last input has locked (a new piece —
        # the final board — has spawned).
        if replay_next >= len(events):
            if replay_stop_seq == 0:
                replay_stop_seq = replay_engine.spawn_seq
            if replay_engine.game_over or replay_engine.spawn_seq > replay_stop_seq:
                finish_replay()

    def sleep_to_frame() -> None:
        """Sleep the remainder of the 50 fps frame budget.

        A fixed ``time.sleep(FRAME)`` would add the full 20 ms on top of
        whatever the frame's work took, so the real cadence drifted to
        20 ms + frame cost (jittery on slow terminals). With the drift
        correction the average cadence stays at FRAME.
        """
        time.sleep(max(0.0, FRAME - (time.monotonic() - frame_start)))

    while True:
        frame_start = time.monotonic()
        now = frame_start

        # ---- input -------------------------------------------------
        key = reader.next_key(stdscr, now)

        # Replay (P9): while a replay is running only ESC is meaningful —
        # abort back to the game-over screen. All other keys are consumed
        # so no live-game input can leak into the replayed run.
        if replay_engine is not None:
            if key in (27, KEY_ESCAPE):
                finish_replay()
            key = -1

        # High-score name entry (game over, top-10): edit the typed name.
        # Enter commits and starts a new game (Enter arrives as 10 in most
        # pty/terminal setups, 13/KEY_ENTER in others); R/Q commit first,
        # then act on it. None of these count as typed characters.
        if t.game_over and name_awaiting:
            if key in (10, 13, curses.KEY_ENTER):
                commit_name()
                reset_game()  # keep the score, start a new game
            elif key in (ord("q"), ord("Q"), ord("r"), ord("R")):
                commit_name()  # the q/r branch below then acts on it
            elif key in (8, 127, curses.KEY_BACKSPACE):
                typed_name = typed_name[:-1]
            elif 32 <= key <= 126 and len(typed_name) < MAX_NAME:
                typed_name += chr(key)

        if key in (ord("q"), ord("Q")):
            if menu is not None:
                close_menu()  # in a modal, Q closes the dialog, never quits
            else:
                break
        elif key in (ord("r"), ord("R")) and (t.game_over or t.paused):
            reset_game()
        elif key in (ord("g"), ord("G")) and t.game_over and not name_awaiting:
            start_replay()
        elif key in (27, KEY_ESCAPE):
            # Escape is the "back" key: at game over it starts a new game
            # without saving the score (any recorded entry is discarded);
            # otherwise it closes a dialog, unpauses, or pauses in open
            # play. (Q is the long-standing quit alias.)
            if t.game_over:
                if rank is not None:
                    state.remove_entry(rank)
                reset_game()
            elif menu is not None:
                close_menu()
            elif t.paused:
                t.paused = False
            else:
                t.paused = True
        elif key == ord("?") and not t.game_over:
            if menu == "help":
                close_menu()  # ? toggles the help dialog
            elif menu is None:
                open_menu("help")
        elif key in (ord("s"), ord("S")) and menu is None and not t.game_over:
            menu_cursor = 0
            open_menu("settings")
        elif key in (ord("h"), ord("H")) and menu is None and not t.game_over:
            open_menu("scores")
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
                # Apply the change live: input timing reads the reader's
                # das/arr, and a theme change re-inits the color pairs and
                # the cached attrs without a restart.
                reader.das = state.settings.das
                reader.arr = state.settings.arr
                if opt.key == "theme":
                    init_colors(state.settings.theme)
                    build_attrs()
        elif menu is None and not t.paused and not t.game_over:
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                d = -1 if key == curses.KEY_LEFT else 1
                # One move per fresh press; holding streams at ARR after DAS.
                if reader.on_direction(d, now):
                    log_action("L" if d < 0 else "R")
                    t.move(d, now)
            elif key == curses.KEY_UP:
                if reader.allow_rotate(now):
                    log_action("U")
                    t.rotate(1, now)
            elif key in (ord("z"), ord("Z")):
                if reader.allow_rotate(now):
                    log_action("Z")
                    t.rotate(-1, now)
            elif key == curses.KEY_DOWN:
                log_action("S")
                t.soft_drop()
            elif key == ord(" "):
                log_action("H")
                if t.hard_drop() > 0 and state.settings.shake:
                    effects.shake_until = now + 0.12
            elif key in (ord("c"), ord("C")):
                log_action("C")
                if state.settings.hold:
                    t.hold()

        # ---- DAS/ARR streaming (held-key moves without new events) -------
        if menu is None and not t.paused and not t.game_over:
            auto = reader.auto_direction(now)
            if auto:
                log_action("L" if auto < 0 else "R")
                t.move(auto, now)

        # ---- gravity + lock delay (or replay step) -----------------
        if replay_engine is not None:
            replay_step(now)
        else:
            t.tick(now)

            # ---- flash animation ------------------------------------------
            if t.frozen:
                t.advance_flash()

        # ---- effects: floating text, spin flash, beeps ----------------
        effects.sound = state.settings.sound
        effects.on_events(t.events, now)

        # ---- game over ---------------------------------------------------
        # Classic: a top-10 score enters the high-score table. Sprint never
        # writes the score table (P11) — its result is a best clear time.
        if t.game_over and not new_best and rank is None and not t.sprint and t.score > 0:
            rank = state.record(
                t.score, t.lines, t.level,
                time_s=t.play_time, best_combo=t.best_combo,
            )
            if rank is not None:
                new_best = True
                name_awaiting = True

        # Sprint result (P11): recorded once. A win stores the clear time
        # (lower is better); a loss just surfaces the existing best.
        if t.game_over and t.sprint and not sprint_handled:
            sprint_handled = True
            if t.won:
                sprint_new_best, sprint_best = state.record_sprint(t.play_time)
            else:
                sprint_best = state.best_sprint_time()

        # Replay (P9): save the finished game's input log once, if the
        # player actually played it (at least one authorized action).
        if t.game_over and not replay_saved and replay_events:
            replay_saved = True
            state.save_replay(
                {
                    "seed": seed,
                    "start_level": state.settings.start_level,
                    "sprint": t.sprint,
                    "das": state.settings.das,
                    "arr": state.settings.arr,
                    "started_at": game_start,
                    "score": t.score,
                    "lines": t.lines,
                    "events": replay_events,
                }
            )

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
        sidebar_x_offset = BOARD_W_DRAWN + 4  # HOLD/NEXT x = board bx + 26
        if max_x < NEED_W or max_y < NEED_H:
            stdscr.erase()
            try:
                stdscr.addstr(1, 1, f"Terminal too small — need {NEED_W}×{NEED_H}, got {max_x}×{max_y}")
            except curses.error:
                pass
            stdscr.refresh()
            sleep_to_frame()
            continue

        # Layout: one centered block — stats panel (left), board (center),
        # HOLD/NEXT column (right).
        block_x = max(0, (max_x - NEED_W) // 2)
        bx = block_x + STATS_W + PANEL_GAP  # board origin
        by = max(1, (max_y - (BOARD_H + 7)) // 2)

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

        # T-spin corner flash expiry (checked before the scene key so the
        # expiry frame flips the key and redraws once without the flash).
        if effects.spin_flash is not None and now - effects.spin_flash[1] >= 0.6:
            effects.spin_flash = None

        # ---- static-scene skip ---------------------------------------
        # t.version (P5) bumps on every observable engine change — moves,
        # rotations, drops, holds, gravity steps, locks, clears, spawns,
        # pause flips, and game over — so one counter covers the board,
        # stats panel, and game-over modal; the rest covers layout,
        # effects, and modals. Any term flipping makes the frame dirty.
        scene: tuple[object, ...] = (
            max_x, max_y,  # resize / layout
            t.version,  # every engine state change (incl. game_over/paused)
            tuple(
                (tx, row, int((now - born) * 2.5))
                for tx, row, born in effects.floaters
            ),  # floater drift, same quantization as the draw below
            effects.spin_flash,
            glide_frac,
            now < effects.shake_until,  # the unshake frame must redraw
            menu, menu_cursor, typed_name, name_awaiting,  # modal content
            new_best, rank,
            state.settings.ghost, state.settings.hold,
            state.settings.theme,  # live recolor on switch (P12)
            # Sprint countdown (P11): the displayed second changes once per
            # second even though the engine's version only bumps on
            # gravity/locks — quantize it so the TIME row stays fresh.
            int(t.time_left) if (t.sprint and t.time_left is not None) else None,
            state.best() if t.game_over else 0,
        )
        if scene == prev_scene and key == -1 and now >= effects.shake_until:
            # Nothing changed since the last drawn frame: keep the current
            # virtual screen. refresh() still runs once per iteration —
            # it is a no-op on unchanged content, and the test suite
            # indexes frames by time, so the cadence must hold.
            stdscr.refresh()
            sleep_to_frame()
            continue
        prev_scene = scene

        stdscr.erase()

        # Board shake: a 1-cell jitter for a couple of frames after a hard
        # drop or a big clear.
        ox, oy = effects.shake(now)

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
            (cx, cy), _ = effects.spin_flash
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

        draw_stats_panel(stdscr, t, by, block_x, new_best)
        draw_sidebar(stdscr, t, state, by, bx + sidebar_x_offset)

        # Spawn glide: drawn last among the non-modal elements, so it can
        # legitimately overlap the board's right wall while flying in.
        if glide_frac is not None:
            _draw_spawn_glide(stdscr, t, glide_frac, bx, by, ox, oy)

        # Replay tag (P9): shown while the input log is being re-run.
        if replay_engine is not None:
            try:
                stdscr.addstr(by + BOARD_H + 3, bx, "REPLAY", curses.A_DIM)
            except curses.error:
                pass

        # ---- modals: game over / help / settings ---------------------
        if t.game_over:
            if t.sprint:
                draw_modal(
                    stdscr,
                    build_sprint_modal(t, sprint_new_best, sprint_best),
                    max_x,
                    max_y,
                )
            else:
                draw_modal(
                    stdscr,
                    build_game_over_modal(
                        t, state.best(), rank, typed_name, name_awaiting, seed
                    ),
                    max_x,
                    max_y,
                )
        elif menu == "help":
            draw_modal(stdscr, build_help_modal(), max_x, max_y)
        elif menu == "settings":
            draw_modal(stdscr, build_settings_modal(state.settings, menu_cursor), max_x, max_y)
        elif menu == "scores":
            draw_modal(stdscr, build_scores_modal(state), max_x, max_y)
        elif t.paused:
            draw_modal(stdscr, build_pause_modal(), max_x, max_y)
        stdscr.refresh()

        sleep_to_frame()


def main() -> None:
    curses.wrapper(game_loop)


if __name__ == "__main__":
    main()
