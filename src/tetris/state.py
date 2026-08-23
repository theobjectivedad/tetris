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

#: Name of the replay log file (next to the state file) and the number of
#: most-recent replays kept.
REPLAYS_FILE = "replays.json"
MAX_REPLAYS = 5


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
        self.sprint: dict[str, object] = {}  # {"best_time": float, "date": str}
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
            raw_sprint = data.get("sprint")
            if isinstance(raw_sprint, dict):
                self.sprint = raw_sprint
        # Any other shape (e.g. a JSON scalar): keep the defaults.

    def best(self) -> int:
        """Top score, or 0 for an empty board."""
        if not self.entries:
            return 0
        score = self.entries[0].get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            return score
        return 0

    # -- sprint best (P11) ---------------------------------------------

    def best_sprint_time(self) -> float | None:
        """The best (lowest) sprint clear time in seconds, or None."""
        value = self.sprint.get("best_time")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    def record_sprint(self, time_s: float) -> tuple[bool, float | None]:
        """Record a sprint clear time (P11); lower is better.

        Returns ``(is_new_best, best_time)``. Sprint times do NOT touch the
        score high-score table (clean semantics) — the best time is kept in
        its own additive ``{"sprint": {...}}`` block of the unified file.
        """
        current = self.best_sprint_time()
        is_new_best = current is None or float(time_s) < current
        if is_new_best:
            self.sprint = {
                "best_time": round(float(time_s), 1),
                "date": time.strftime("%Y-%m-%d %H:%M"),
            }
            self.save()
        return is_new_best, self.best_sprint_time()

    def record(
        self,
        score: int,
        lines: int,
        level: int,
        name: str = "",
        *,
        time_s: float = 0.0,
        best_combo: int = 0,
    ) -> int | None:
        """Insert a result; returns its rank (0-based) or None if not top-10.

        ``time_s`` (elapsed play seconds) and ``best_combo`` are stored as
        additive JSON keys; legacy entries without them remain valid.
        """
        if score <= 0:
            return None
        entry: dict[str, object] = {
            "name": _sanitize_name(name),
            "score": score,
            "lines": lines,
            "level": level,
            "time": round(float(time_s), 1),
            "best_combo": int(best_combo),
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
            "sprint": self.sprint,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass  # never let state saving crash the game

    # -- replays ---------------------------------------------------------

    def _load_replays(self) -> list[dict[str, object]]:
        """Replays from the replay log; tolerant of missing/corrupt files."""
        path = self.path.parent / REPLAYS_FILE
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict)][-MAX_REPLAYS:]

    def save_replay(self, replay: dict[str, object]) -> None:
        """Append a finished game's replay (seed + timestamped input log)
        to the replay log, keeping the ``MAX_REPLAYS`` most recent.
        Failures are swallowed — replays are a convenience, not state."""
        replays = self._load_replays()
        replays.append(replay)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            (self.path.parent / REPLAYS_FILE).write_text(
                json.dumps(replays[-MAX_REPLAYS:], indent=2)
            )
        except (OSError, TypeError, ValueError):
            pass

    def last_replay(self) -> dict[str, object] | None:
        """The most recently saved replay, or None if the log is empty."""
        replays = self._load_replays()
        return replays[-1] if replays else None

    def replays(self) -> list[dict[str, object]]:
        """The saved replays, most recent first (up to MAX_REPLAYS).

        The replay list dialog (P19) shows this order: the newest run is
        row 1. Replays older than the MAX_REPLAYS window are gone.
        """
        return list(reversed(self._load_replays()))


# Back-compat alias for the historical score-only class name.
HighScores = GameState

__all__ = [
    "MAX_NAME",
    "MAX_REPLAYS",
    "REPLAYS_FILE",
    "GameState",
    "HighScores",
    "default_state_path",
]
