"""Sidebar stat labels and formatting — the single source of truth.

The curses UI (``main.py``) renders these and the MCP play-test server
(``mcp/server.py``) parses the rendered text to recover state. Both import
the canonical labels and value formatting from here so the renderer and the
parser can never drift apart.
"""

from __future__ import annotations

# Canonical stat labels, in sidebar order. This tuple is the shared contract:
# main.py renders exactly these, and mcp/server.py looks for exactly these.
STAT_LABELS = ("SCORE", "BEST", "LINES", "LEVEL", "COMBO", "B2B", "SPINS")

# Which field of a Tetris.snapshot() (plus "best") backs each label.
_SNAPSHOT_KEY = {
    "SCORE": "score",
    "BEST": "best",
    "LINES": "lines",
    "LEVEL": "level",
    "COMBO": "combo",
    "B2B": "b2b",
    "SPINS": "spins",
}


def display_value(label: str, value: int | bool) -> str:
    """Format a stat value exactly as the sidebar shows it."""
    if label in ("SCORE", "BEST"):
        return f"{int(value):,}"
    if label == "COMBO":
        return str(value) if int(value) > 0 else "\u2014"  # em dash
    if label == "B2B":
        return "\u2713" if value else "\u2014"  # check / em dash
    return str(value)


def sidebar_stats(snapshot: dict[str, int | bool], best: int) -> list[tuple[str, str]]:
    """Ordered (label, display-string) pairs for the sidebar.

    ``snapshot`` is ``Tetris.snapshot()``; ``best`` is the top score from
    ``HighScores`` (not engine state, so it is passed in separately).
    """
    values: dict[str, int | bool] = {**snapshot, "best": best}
    return [
        (label, display_value(label, values[_SNAPSHOT_KEY[label]]))
        for label in STAT_LABELS
    ]


__all__ = ["STAT_LABELS", "display_value", "sidebar_stats"]
