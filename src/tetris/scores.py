"""High-score persistence.

Deliberately isolated from the pure engine: this is the only module in the
game package that touches the filesystem.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def default_scores_path() -> Path:
    env = os.environ.get("TETRIS_SCORES")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "terminal-tetris" / "scores.json"


class HighScores:
    """Top-5 score persistence, one JSON file."""

    MAX = 5

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_scores_path()
        self.entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.entries = data[: self.MAX]
            except (json.JSONDecodeError, OSError):
                self.entries = []

    def best(self) -> int:
        return self.entries[0]["score"] if self.entries else 0

    def record(self, score: int, lines: int, level: int) -> int | None:
        """Insert a result; returns its rank (0-based) or None if not top-5."""
        if score <= 0:
            return None
        entry = {
            "score": score,
            "lines": lines,
            "level": level,
            "date": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.entries.append(entry)
        self.entries.sort(key=lambda e: e["score"], reverse=True)
        self.entries = self.entries[: self.MAX]
        rank = next((i for i, e in enumerate(self.entries) if e is entry), None)
        self._save()
        return rank

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, indent=2))
        except OSError:
            pass  # never let score saving crash the game


__all__ = ["HighScores", "default_scores_path"]
