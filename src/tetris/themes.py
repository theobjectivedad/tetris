"""Theme data: which curses base colors back the game's color pairs.

Pure data — no curses import, no I/O. Each theme maps a piece kind to a
curses base color index (0–7) for the cell foreground, plus the colors for
the stats-panel text and the (currently latent) highlight pair.

The fixed pairs are intentionally NOT themed (see the plan's P12): the
line-clear flash stays white-on-yellow, the border stays gray 231, and the
board/panel background stays near-black 235 — themes only recolor
pieces and text.

``main.py``'s ``init_colors(theme)`` reads these to (re)build the color
pairs; changing the theme re-inits the affected pairs and the cached attrs.
"""

from __future__ import annotations

from dataclasses import dataclass

# curses base color indices (0-7), named for readability.
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)


@dataclass(frozen=True)
class Theme:
    """A named color scheme.

    cells: piece kind -> foreground base color for that piece's cells.
    text:  base color for the stats panel / labels (pair 8).
    highlight: base color for the highlight pair (pair 9, latent today).
    """

    name: str
    cells: dict[str, int]
    text: int
    highlight: int


THEMES: dict[str, Theme] = {
    # The original palette: I cyan, O yellow, T magenta, S green,
    # Z red, J blue, L white.
    "classic": Theme(
        name="classic",
        cells={
            "I": CYAN,
            "O": YELLOW,
            "T": MAGENTA,
            "S": GREEN,
            "Z": RED,
            "J": BLUE,
            "L": WHITE,
        },
        text=WHITE,
        highlight=YELLOW,
    ),
    # Monochrome: every cell renders white; pieces are told apart by
    # shape (and the dimmed ghost / next-queue previews).
    "mono": Theme(
        name="mono",
        cells={k: WHITE for k in ("I", "O", "T", "S", "Z", "J", "L")},
        text=WHITE,
        highlight=WHITE,
    ),
    # A brighter remap that shifts several pieces: I blue, O yellow,
    # T white, S green, Z red, J cyan, L magenta.
    "vivid": Theme(
        name="vivid",
        cells={
            "I": BLUE,
            "O": YELLOW,
            "T": WHITE,
            "S": GREEN,
            "Z": RED,
            "J": CYAN,
            "L": MAGENTA,
        },
        text=WHITE,
        highlight=YELLOW,
    ),
}

DEFAULT_THEME = "classic"

__all__ = ["DEFAULT_THEME", "THEMES", "Theme"]
