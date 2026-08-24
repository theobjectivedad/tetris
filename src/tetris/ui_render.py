"""Curses rendering for Terminal Tetris.

All of the drawing: color initialization and the cached cell/panel
attributes, the board-geometry and layout constants, the modal dialog
machinery (``Modal``, ``draw_modal``, and the builders for the help /
sprint / game-over / pause / settings / scores dialogs), and the
board, sidebar, and stats-panel draw functions.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, field

from . import __version__
from .engine import SPRINT_LINES, Tetris
from .pieces import BOARD_H, BOARD_W, PIECES
from .settings import OPTIONS, Settings, format_value, value_of
from .state import GameState, as_int, as_number
from .stats import format_time, panel_stats
from .themes import DEFAULT_THEME, THEMES

# -- colors: pairs and cached attributes ------------------------------------
#
# COLORS maps a piece kind to its base color pair (1-7); init_colors()
# builds pairs 1-9 from the active theme (text 8, highlight 9). The flash
# pair (10) and the border/background pairs (11/12) are fixed.
COLORS: dict[str, int] = {"I": 1, "O": 2, "T": 3, "S": 4, "Z": 5, "J": 6, "L": 7}

# Fixed color pairs (not part of the themes): the flash, border,
# danger, and background pairs. Border: gray 231 (a gray none of the
# pieces use) on capable terminals; on terminals with fewer colors
# ncurses maps the pair to the nearest available colors (the default
# foreground — always readable). The danger bar (P24) is white on red —
# it recolors the top border while the stack nears the ceiling. The
# background (a very dark gray, ext 235) is a subtle depth fill that
# becomes a no-op where the color is unmappable.
BORDER_PAIR = 11
DANGER_PAIR = 13
BG_PAIR = 12


@dataclass
class AttrCache:
    """Cached render attributes (R5) — one object owns what were seven
    module-level rebinding globals.

    Defaults are the no-color fallbacks (``border`` = A_DIM, ``danger``
    = A_BLINK — a blinking bar still reads as a warning — the rest 0).
    ``init_colors()`` + ``build_attrs()`` fill in the color pairs once a
    live screen exists (and again after a live theme switch). The render
    path reads plain fields: no ``global`` statements, no rebinding, and
    no stale re-exports — ``main.ATTRS`` and ``ui_render.ATTRS`` are the
    same object. Tests patch individual fields (e.g. ``ATTRS.danger``)
    as a seam.
    """

    cells: dict[str, int] = field(default_factory=dict)  # per piece kind
    flash: int = 0  # flash row (pair 10: white on yellow)
    stat: int = 0  # stats panel text (pair 8)
    # highlight pair (9): lock/rotation flash, hold flash, active B2B
    # (P22/P29/P30/P25)
    hilite: int = 0
    bg: int = 0  # board/panel background fill (pair 12)
    border: int = curses.A_DIM  # board/box border (pair 11: gray 231)
    danger: int = curses.A_BLINK  # danger bar (pair 13: white on red)


# The single attribute cache, filled by build_attrs() after init_colors():
# the render path used to call has_colors()/color_pair() (C crossings) per
# cell — 20-60 times a frame. Plain field reads replace that.
ATTRS = AttrCache()

# -- drawn geometry and layout ------------------------------------------------
#
# Each cell renders as a solid 2-column block — about square on a
# terminal's ~2:1 char aspect — and cells are contiguous with no gap, so
# filled regions read as one tight solid mass. The play field adds a
# 1-col solid wall outside the cell area.
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


# -- color initialization and cached attributes --------------------------------


def init_colors(theme: str = "classic") -> None:
    """(Re)initialize the color pairs.

    Pairs 1-9 come from the named theme (piece cells 1-7, text 8,
    highlight 9; unknown names fall back to classic) — safe to re-run
    live when the theme setting changes, as curses allows re-initing a
    pair (re-run build_attrs() afterwards to refresh cached attrs). The
    flash pair (10) and the border/danger/background pairs (11/13/12)
    are fixed.
    """
    if not curses.has_colors():
        return
    t = THEMES.get(theme, THEMES[DEFAULT_THEME])
    curses.start_color()
    for i, fg in {
        1: t.cells["I"],
        2: t.cells["O"],
        3: t.cells["T"],
        4: t.cells["S"],
        5: t.cells["Z"],
        6: t.cells["J"],
        7: t.cells["L"],
        8: t.text,
        9: t.highlight,
    }.items():
        curses.init_pair(i, fg, curses.COLOR_BLACK)
    # The flash pair is fixed (white on yellow) — not part of the theme.
    curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_YELLOW)
    # NOTE: never use A_REVERSE for the border — with a white-fg/black-bg
    # default it swaps to a black bar (invisible). (Some _curses builds
    # lack color_count(), so this intentionally does not branch on it —
    # ncurses degrades the extended pairs gracefully instead.)
    curses.init_pair(BORDER_PAIR, 231, curses.COLOR_BLACK)
    curses.init_pair(DANGER_PAIR, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(BG_PAIR, curses.COLOR_BLACK, 235)
    ATTRS.border = curses.color_pair(BORDER_PAIR)
    ATTRS.danger = curses.color_pair(DANGER_PAIR)


def build_attrs() -> None:
    """Fill the attribute cache after init_colors() has run
    (color_pair() needs a live curses screen)."""
    colors = curses.has_colors()
    for kind, pair in COLORS.items():
        ATTRS.cells[kind] = curses.color_pair(pair) if colors else 0
    ATTRS.flash = curses.color_pair(10) if colors else 0
    ATTRS.stat = curses.color_pair(8) if colors else 0
    # The highlight pair is latent in the themes' rendering; as a flash
    # accent it needs a visible fallback on monochrome terminals — bold
    # brightens the default foreground.
    ATTRS.hilite = curses.color_pair(9) if colors else curses.A_BOLD
    ATTRS.bg = curses.color_pair(BG_PAIR) if colors else 0


def cell_attr(kind: str) -> int:
    """Attr for a piece kind (cached by build_attrs after init_colors)."""
    return ATTRS.cells[kind]


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
    width = max(len(modal.title), max((len(line) for line in modal.lines), default=0)) + 6
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
            ATTRS.border,
        )
        for i, line in enumerate(modal.lines):
            y = by + 1 + i
            is_cursor = modal.cursor is not None and i == modal.cursor
            stdscr.addstr(y, bx, "│", ATTRS.border)
            stdscr.addstr(y, bx + width - 1, "│", ATTRS.border)
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
        stdscr.addstr(by + height - 1, bx, "└" + "─" * inner + "┘", ATTRS.border)
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
        "←/a →/d move    SPACE hard drop",
        "↑ CW  Z CCW     X rotate 180",
        "↓ soft drop     C hold",
        "P pause         ? help",
        "R restart       S settings",
        "H scores        Q quit",
        "ESC close / pause",
        "GAME OVER: ENTER save, ESC skip",
        "AT GAME OVER: L replay list",
        "AT REPLAY: F cycle 1x/2x/4x",
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
    engine: Tetris, is_new_best: bool, best_time: float | None
) -> Modal:
    """The sprint game-over dialog (P11).

    Win -> "SPRINT CLEARED" with the clear time, best time, and score.
    Loss -> "TIME UP" with the lines reached and score. The best time is
    the store's best (a win that set a new best shows the flag instead).
    """
    if engine.won:
        lines = [f"Time    {format_time(engine.play_time)}"]
        if is_new_best:
            lines.append("* NEW BEST TIME *")
        elif best_time is not None:
            lines.append(f"Best    {format_time(best_time)}")
        lines += [f"Lines   {engine.lines}/{SPRINT_LINES}", f"Score   {engine.score:,}"]
        title = "SPRINT CLEARED"
    else:
        lines = [f"Lines   {engine.lines}/{SPRINT_LINES}", f"Score   {engine.score:,}"]
        if best_time is not None:
            lines.append(f"Best    {format_time(best_time)}")
        title = "TIME UP"
    lines += [
        "",
        "R/ESC new game       Q quit",
        "G replay last game",
        "L replay list (1-5 play)",
    ]
    return Modal(title, lines)


def build_replays_modal(state: GameState) -> Modal:
    """The replay list dialog (P19), opened with L at game over.

    Lists the saved replays newest first (row 1 = the most recent): score,
    lines, mode, and the wall-clock date. Digits 1-5 start a replay; the
    game-over screen is restored when it ends or is aborted (ESC). Rows
    are a fixed 43 chars so the table lines up.
    """
    replays = state.replays()
    if not replays:
        lines = [
            "No saved replays yet.",
            "Play a game first — the five",
            "most recent finished games are kept.",
        ]
    else:
        lines = [f"{'#':>1}  {'SCORE':>8}{'LINES':>6}  {'MODE':<7}  DATE"]
        for i, rp in enumerate(replays, start=1):
            score = as_int(rp.get("score"))
            lines_c = as_int(rp.get("lines"))
            mode = "sprint" if rp.get("sprint") else "classic"
            date = str(rp.get("date") or "")[:16]
            lines.append(f"{i:>1}  {score:>8,}{lines_c:>6}  {mode:<7}  {date}")
    lines += ["", "1-5 play    ESC close"]
    return Modal("REPLAYS", lines)


def build_game_over_modal(
    engine: Tetris,
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
        f"Score   {engine.score:,}",
        f"Lines   {engine.lines}    Level {engine.level}",
        f"Pieces  {engine.pieces}",
        f"Time    {format_time(engine.play_time)}",
    ]
    if engine.best_combo > 0:
        lines.append(f"Best combo  {engine.best_combo}")
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
        # During name entry G and L are typed name characters (like any
        # letter), so the replay hints are offered only on the settled
        # game-over screen.
        lines += [
            "",
            "ENTER save + new game",
            "ESC  new game, no save",
            "Q quit",
        ]
    else:
        lines += [
            "",
            "R/ESC new game       Q quit",
            "G replay last game",
            "L replay list (1-5 play)",
        ]
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

    Columns: rank, name (— when unset), comma-formatted score, level,
    the entry's play time (M:SS; — for legacy entries without one,
    P21), and the YYYY-MM-DD date. Rows are a fixed 45 chars so the
    table lines up.
    """
    lines: list[str] = [
        f"{'#':>2}  {'NAME':<10}{'SCORE':>9}{'LVL':>4}{'TIME':>6}  DATE"
    ]
    for i, entry in enumerate(state.entries[:10], start=1):
        name = str(entry.get("name") or "").strip() or "—"
        score = as_int(entry.get("score"))
        level = as_int(entry.get("level"))
        t = as_number(entry.get("time"))
        t_str = format_time(t) if t is not None else "—"
        date = str(entry.get("date") or "")[:10]
        lines.append(f"{i:>2}  {name:<10}{score:>9,}{level:>4}{t_str:>6}  {date}")
    if not state.entries:
        lines = ["No scores yet — play a game!"]
    lines += ["", "ESC close"]
    return Modal("HIGH SCORES", lines)


def draw_box(
    stdscr: curses.window,
    title: str,
    bx: int,
    by: int,
    w: int,
    h: int = 6,
    attr: int | None = None,
) -> tuple[int, int]:
    """Draw a titled box; returns (inner_x, inner_y).

    ``attr`` overrides the border attribute (P29: the HOLD box flashes in
    the highlight accent while a hold was just accepted).
    """
    border = f"┌{'─' * (w - 2)}┐"
    border_attr = ATTRS.border if attr is None else attr
    try:
        stdscr.addstr(by, bx, border, border_attr)
        stdscr.addstr(by + 1, bx, f"│ {title:<{w - 4}} │", border_attr)
        for i in range(h - 3):
            # Borders keep the border attr; the interior run gets the panel
            # background (a no-op fill when colors are unavailable).
            stdscr.addstr(by + 2 + i, bx, "│", border_attr)
            stdscr.addstr(by + 2 + i, bx + 1, " " * (w - 2), ATTRS.bg)
            stdscr.addstr(by + 2 + i, bx + w - 1, "│", border_attr)
        stdscr.addstr(by + h - 1, bx, f"└{'─' * (w - 2)}┘", border_attr)
    except curses.error:
        pass
    return bx + 2, by + 2


# Per-kind rotation-0 footprint, computed once (R5): the previews and the
# spawn glide used to re-derive the bounding box on every call (6+ times
# a frame).
_FOOTPRINTS: dict[str, tuple[int, int, int, int]] = {}


def _footprint(kind: str) -> tuple[int, int, int, int]:
    """The rotation-0 bounding box (top, bottom, left, right), cached."""
    fp = _FOOTPRINTS.get(kind)
    if fp is None:
        cells = PIECES[kind][0]
        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        fp = (min(ys), max(ys), min(xs), max(xs))
        _FOOTPRINTS[kind] = fp
    return fp


def draw_piece_preview(
    stdscr: curses.window,
    kind: str,
    ix: int,
    iy: int,
    dim: bool = False,
    attr: int | None = None,
) -> None:
    if kind not in PIECES:
        return
    cells = PIECES[kind][0]
    top, bottom, left, right = _footprint(kind)
    width = (right - left + 1) * BOARD_PITCH
    # Box inner area is 12 cols wide, starting at ix - 1.
    ox = ix - 1 + (12 - width) // 2
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if (x, y) in cells:
                txt = "██"
                try:
                    if attr is not None:
                        # P29: an explicit override (the hold-box flash).
                        stdscr.addstr(iy + (y - top), ox + (x - left) * BOARD_PITCH, txt, attr)
                    elif dim:
                        stdscr.addstr(iy + (y - top), ox + (x - left) * BOARD_PITCH, txt, curses.A_DIM)
                    else:
                        stdscr.addstr(iy + (y - top), ox + (x - left) * BOARD_PITCH, txt, cell_attr(kind))
                except curses.error:
                    pass


def draw_board(
    stdscr: curses.window,
    engine: Tetris,
    bx: int,
    by: int,
    show_ghost: bool = True,
    hide_live: bool = False,
    lock_cells: frozenset[tuple[int, int]] | None = None,
    pulse_dim: bool = False,
    rotate_flash: bool = False,
    danger: bool = False,
) -> None:
    """Draw the board frame, background, cells, ghost, and live piece.

    ``lock_cells`` (P22): the just-locked cells, drawn in the highlight
    accent while the lock flash is active. ``pulse_dim`` (P23): dim the
    live piece this frame — the 8 Hz lock-delay pulse. ``rotate_flash``
    (P30): draw the live piece in the highlight accent while a successful
    rotation's flash is active (wins over the pulse). ``danger`` (P24):
    blink the top border red while the stack nears the ceiling.
    """
    # Solid border around the play area. The walls are outside the cell
    # area (1 col per side), so blocks never render on top of them.
    right = bx + BOARD_W_DRAWN - 1
    top_attr = ATTRS.danger | curses.A_BLINK if danger else ATTRS.border
    try:
        stdscr.addstr(by, bx, BOARD_BAR, top_attr)
        stdscr.addstr(by + BOARD_H + 1, bx, BOARD_BAR, ATTRS.border)
        for y in range(1, BOARD_H + 1):
            stdscr.addstr(by + y, bx, "█", ATTRS.border)
            stdscr.addstr(by + y, right, "█", ATTRS.border)
    except curses.error:
        pass

    # Panel background behind the cell area (one addstr per row): a very
    # dark gray on 256-color terminals, a no-op fill otherwise. Cells,
    # the ghost, and the flash rows are drawn on top of it.
    try:
        for y in range(BOARD_H):
            stdscr.addstr(by + y + 1, bx + CELL_OFF, " " * BOARD_INNER_W, ATTRS.bg)
    except curses.error:
        pass

    flash_rows = tuple(engine.pending_clears)
    ghost_cells: set[tuple[int, int]] = set()
    live_cells: dict[tuple[int, int], str] = {}
    if not engine.game_over:
        if show_ghost:
            ghost_cells = {
                (dx + engine.piece.x, engine.ghost_y() + dy) for dx, dy in PIECES[engine.piece.kind][engine.piece.rot]
            }
        # hide_live: the spawn glide draws the piece itself (see game_loop).
        if not hide_live:
            live_cells = {(x, y): engine.piece.kind for x, y in engine.piece.cells() if y >= 0}

    for y in range(BOARD_H):
        row = engine.board[y]
        x_cursor = bx + CELL_OFF
        for x in range(BOARD_W):
            kind = row[x]
            if (x, y) in live_cells:
                if rotate_flash:
                    # P30: a successful rotation highlights the piece.
                    text, attr = "██", ATTRS.hilite | curses.A_BOLD
                elif pulse_dim:
                    # P23: the grounded piece pulses while the lock delay
                    # ticks — the "it's about to lock" cue.
                    text, attr = "██", cell_attr(live_cells[(x, y)]) | curses.A_DIM
                else:
                    text, attr = "██", cell_attr(live_cells[(x, y)])
            elif kind:
                if y in flash_rows:
                    text, attr = "██", ATTRS.flash
                elif lock_cells is not None and (x, y) in lock_cells:
                    # P22: the just-locked cells flash in the highlight accent.
                    text, attr = "██", ATTRS.hilite | curses.A_BOLD
                else:
                    text, attr = "██", cell_attr(kind)
            elif (x, y) in ghost_cells:
                text, attr = "▒▒", cell_attr(engine.piece.kind) | curses.A_DIM
            else:
                x_cursor += BOARD_PITCH  # empty cell: advance, draw nothing
                continue
            try:
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
    engine: Tetris,
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
    kind = engine.piece.kind
    live = [(x, y) for x, y in engine.piece.cells() if y >= 0]
    if not live:
        return  # the whole piece is above the rim: nothing on the board yet
    min_x = min(x for x, _ in live)
    min_y = min(y for _, y in live)
    to_x = bx + ox + CELL_OFF + min_x * BOARD_PITCH
    to_y = by + oy + min_y + 1

    # "from": the NEXT box's head preview (see draw_piece_preview /
    # draw_sidebar): inner origin (bx + BOARD_W_DRAWN + 4 + 2, by + 9),
    # preview centered in the 12-col inner area on its rotation-0 footprint.
    top, _, left, right = _footprint(kind)
    width_px = (right - left + 1) * BOARD_PITCH
    next_x = bx + BOARD_W_DRAWN + 4 + 2
    next_y = by + 9
    from_x = next_x - 1 + (12 - width_px) // 2
    from_y = next_y + top

    px = round(from_x + (to_x - from_x) * frac)
    py = round(from_y + (to_y - from_y) * frac)
    for x, y in live:
        row, col = py + (y - min_y), px + (x - min_x) * BOARD_PITCH
        try:
            stdscr.addstr(row, col, "██", cell_attr(kind))
        except curses.error:
            pass


def draw_stats_panel(stdscr: curses.window, engine: Tetris, by: int, x: int, new_best: bool) -> None:
    """The left column: the six stats at ``x`` (label dim, value bright,
    color pair 8), starting two rows below the block top, and the
    "★ NEW BEST ★" indicator two rows below the last stat row. The key
    legend and version live in the help modal (`?`) instead."""
    for i, (label, value) in enumerate(panel_stats(engine.snapshot(), sprint=engine.sprint)):
        value_attr = ATTRS.stat
        # Sprint (P11/P28): blink the countdown once 30 s or less remain,
        # in the danger color for the final 10 s.
        if (
            engine.sprint
            and label == "TIME"
            and engine.time_left is not None
            and engine.time_left <= 30
        ):
            if engine.time_left <= 10:  # danger: the final 10 s
                value_attr = ATTRS.danger | curses.A_BLINK
            else:  # warning blink: 30 s or less
                value_attr = ATTRS.stat | curses.A_BLINK
        # P25: an active back-to-back streak glows in the highlight color.
        if label == "B2B" and engine.b2b:
            value_attr = ATTRS.hilite
        try:
            stdscr.addstr(
                by + 2 + i, x, f"{label:<7}", ATTRS.stat | curses.A_DIM
            )
            stdscr.addstr(by + 2 + i, x + 7, value, value_attr)
        except curses.error:
            pass

    if new_best:
        try:
            stdscr.addstr(by + 9, x, "★ NEW BEST ★", curses.A_REVERSE | curses.A_BLINK)
        except curses.error:
            pass


def draw_sidebar(
    stdscr: curses.window,
    engine: Tetris,
    state: GameState,
    by: int,
    sx: int,
    hold_flash: bool = False,
) -> None:
    """The right column: the HOLD box at ``sx`` and the NEXT box below it.

    ``hold_flash`` (P29): draw the HOLD box border and its preview in the
    highlight accent while a hold was just accepted.
    """
    flash_attr = ATTRS.hilite if hold_flash else None
    hold_x, hold_y = draw_box(stdscr, "HOLD", sx, by, HOLD_W, 6, attr=flash_attr)
    if state.settings.hold:
        draw_piece_preview(
            stdscr, engine.holding or "", hold_x, hold_y,
            dim=not engine.can_hold, attr=flash_attr,
        )
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
    for i, kind in enumerate(engine.queue[:5]):
        draw_piece_preview(stdscr, kind, next_x, next_y + i * 3, dim=i > 0)


__all__ = [
    "ATTRS",
    "BG_PAIR",
    "BOARD_BAR",
    "BOARD_INNER_W",
    "BOARD_PITCH",
    "BOARD_WALL",
    "BOARD_W_DRAWN",
    "BORDER_PAIR",
    "CELL_OFF",
    "COLORS",
    "DANGER_PAIR",
    "HOLD_W",
    "NEED_H",
    "NEED_W",
    "PANEL_GAP",
    "STATS_W",
    "AttrCache",
    "Modal",
    "_draw_spawn_glide",
    "_footprint",
    "build_attrs",
    "build_game_over_modal",
    "build_help_modal",
    "build_pause_modal",
    "build_replays_modal",
    "build_scores_modal",
    "build_settings_modal",
    "build_sprint_modal",
    "cell_attr",
    "draw_board",
    "draw_box",
    "draw_modal",
    "draw_piece_preview",
    "draw_sidebar",
    "draw_stats_panel",
    "init_colors",
]
