"""Unified game-state persistence: one JSON file for scores and settings.

This is the single file-I/O module in the game package. The file stores
``{"scores": [...], "settings": {...}}``; legacy files (a bare score list,
or the old ``scores.json`` filename) are read transparently, and the first
save rewrites the new unified file.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .settings import Settings, coerce_setting, from_dict, with_value

#: Maximum length of a player name (truncated, never raised).
MAX_NAME = 10


def _sanitize_name(raw: object) -> str:
    """Trim and cap a raw name; never raises (tolerates non-strings)."""
    return str(raw).strip()[:MAX_NAME]


def default_state_path() -> Path:
    """Where the unified state file lives (``TETRIS_SCORES`` override kept
    for back-compat with the old score-file location)."""
    env = os.environ.get("TETRIS_SCORES")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "terminal-tetris" / "state.json"


def _coerce_entries(raw: object) -> list[dict[str, object]]:
    """Keep only dict entries from a raw JSON payload (tolerant)."""
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _entry_score(entry: dict[str, object]) -> int:
    """Sort key: the entry's score, or 0 if missing/malformed."""
    value = entry.get("score")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


class GameState:
    """Scores + settings in one JSON file — the only file-I/O persistence
    module (replaces the old HighScores-only file)."""

    MAX = 10

    def __init__(
        self, path: Path | str | None = None, *, load_legacy: bool = True
    ) -> None:
        self.path = Path(path) if path is not None else default_state_path()
        self.entries: list[dict[str, object]] = []
        self.settings = Settings()
        self._load(load_legacy)

    def _load(self, load_legacy: bool) -> None:
        path = self.path
        if not path.exists() and load_legacy:
            legacy = path.with_name("scores.json")
            if legacy != path and legacy.exists():
                path = legacy
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return  # corrupt file: keep empty scores + default settings
        if isinstance(data, list):  # legacy scores-only format
            self.entries = _coerce_entries(data)[: self.MAX]
        elif isinstance(data, dict):
            self.entries = _coerce_entries(data.get("scores"))[: self.MAX]
            raw_settings = data.get("settings")
            if isinstance(raw_settings, dict):
                self.settings = from_dict(raw_settings)
        # Any other shape (e.g. a JSON scalar): keep the defaults.

    def best(self) -> int:
        """Top score, or 0 for an empty board."""
        if not self.entries:
            return 0
        score = self.entries[0].get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            return score
        return 0

    def record(
        self, score: int, lines: int, level: int, name: str = ""
    ) -> int | None:
        """Insert a result; returns its rank (0-based) or None if not top-10."""
        if score <= 0:
            return None
        entry: dict[str, object] = {
            "name": _sanitize_name(name),
            "score": score,
            "lines": lines,
            "level": level,
            "date": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.entries.append(entry)
        self.entries.sort(key=_entry_score, reverse=True)
        self.entries = self.entries[: self.MAX]
        rank = next((i for i, e in enumerate(self.entries) if e is entry), None)
        self.save()
        return rank

    def set_entry_name(self, index: int, name: str) -> None:
        """Set the player name of a stored entry and save.

        Out-of-range indices are a silent no-op.
        """
        if not 0 <= index < len(self.entries):
            return
        self.entries[index]["name"] = _sanitize_name(name)
        self.save()

    def remove_entry(self, index: int) -> None:
        """Remove the stored entry at ``index`` and save.

        Used when the player declines to keep a recorded score (ESC on
        the game-over screen). The remaining entries stay in rank
        order. Out-of-range indices are a silent no-op.
        """
        if not 0 <= index < len(self.entries):
            return
        del self.entries[index]
        self.save()

    def update_settings(self, **changes: object) -> None:
        """Apply setting changes and save.

        Values are coerced through the shared settings coercion (start_level
        clamped to 1..MAX_START_LEVEL; bools, floats, and strings must be
        the right type and an allowed value). Unknown keys and wrong-typed
        values are ignored, so a bad change never corrupts the state.
        """
        settings = self.settings
        for key, raw in changes.items():
            value = coerce_setting(key, raw)
            if value is not None:
                settings = with_value(settings, key, value)
        self.settings = settings
        self.save()

    def save(self) -> None:
        """Write the unified file; a save failure must never crash the game."""
        payload = {
            "scores": self.entries,
            "settings": asdict(self.settings),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass  # never let state saving crash the game


# Back-compat name: the historical score-only class (see the scores.py shim).
HighScores = GameState

__all__ = ["MAX_NAME", "GameState", "HighScores", "default_state_path"]
