"""Stat labels and formatting — the single source of truth.

The curses UI (``main.py``) renders these in the left stats panel and the
MCP play-test server (``mcp/server.py``) parses the rendered text to recover
state. Both import the canonical labels and value formatting from here so the
renderer and the parser can never drift apart.
"""

from __future__ import annotations

from typing import cast

# Canonical stat labels, in panel order. This tuple is the shared contract:
# main.py renders exactly these, and mcp/server.py looks for exactly these.
STAT_LABELS = ("SCORE", "LINES", "LEVEL", "COMBO", "B2B", "SPINS")

# Which field of a Tetris.snapshot() backs each label.
_SNAPSHOT_KEY = {
    "SCORE": "score",
    "LINES": "lines",
    "LEVEL": "level",
    "COMBO": "combo",
    "B2B": "b2b",
    "SPINS": "spins",
}


def display_value(label: str, value: bool | float) -> str:
    """Format a stat value exactly as the stats panel shows it."""
    if label in ("SCORE", "BEST"):
        return f"{int(value):,}"
    if label == "COMBO":
        return str(value) if int(value) > 0 else "\u2014"  # em dash
    if label == "B2B":
        return "\u2713" if value else "\u2014"  # check / em dash
    return str(value)


def format_time(total_seconds: float) -> str:
    """M:SS display for the game-over screen (e.g. ``3:45``)."""
    s = int(total_seconds)
    return f"{s // 60}:{s % 60:02d}"


def _display(label: str, snapshot: dict[str, int | bool | float | None]) -> str:
    """The display string for ``label``'s snapshot field.

    The classic labels are always backed by a non-None field (the only
    None in a snapshot is the sprint-only ``time_left``), so the cast
    here is sound.
    """
    return display_value(label, cast("bool | float", snapshot[_SNAPSHOT_KEY[label]]))


def sidebar_stats(snapshot: dict[str, int | bool | float | None]) -> list[tuple[str, str]]:
    """Ordered (label, display-string) pairs for the left stats panel.

    ``snapshot`` is ``Tetris.snapshot()``.
    """
    return [(label, _display(label, snapshot)) for label in STAT_LABELS]


def panel_stats(
    snapshot: dict[str, int | bool | float | None], sprint: bool = False
) -> list[tuple[str, str]]:
    """Ordered (label, display-string) pairs for the left stats panel.

    Classic (``sprint=False``) is identical to :func:`sidebar_stats` — the
    canonical ``STAT_LABELS`` contract the MCP parser relies on is
    unchanged. In sprint mode the SPINS row is replaced by a ``TIME``
    countdown (M:SS) driven by the snapshot's ``time_left`` field.
    """
    if not sprint:
        return sidebar_stats(snapshot)
    pairs: list[tuple[str, str]] = []
    for label in STAT_LABELS:
        if label == "SPINS":
            time_left = snapshot.get("time_left")
            if isinstance(time_left, (int, float)) and not isinstance(time_left, bool):
                pairs.append(("TIME", format_time(max(0.0, float(time_left)))))
            else:
                pairs.append(("TIME", "0:00"))
        else:
            pairs.append((label, _display(label, snapshot)))
    return pairs


__all__ = [
    "STAT_LABELS",
    "display_value",
    "format_time",
    "panel_stats",
    "sidebar_stats",
]
