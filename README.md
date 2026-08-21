# Terminal Tetris

A feature-rich Tetris for your terminal, built with Python's `curses`. The
game itself has no runtime dependencies; the bundled MCP play-test server
brings in `mcp` and `pyte`.

## Run

```bash
just run        # start the game
just test       # run the test suite (pytest)
just test-cov   # tests with coverage
just lint/fmt   # ruff check / format
```

Or directly: `uv run python -m tetris.main`

## Controls

| Key      | Action                                                                 |
|----------|------------------------------------------------------------------------|
| ← / →    | Move (DAS/ARR auto-repeat when held)                                   |
| ↑        | Rotate clockwise                                                       |
| Z        | Rotate counter-clockwise                                               |
| ↓        | Soft drop (+1 pt/cell)                                                 |
| SPACE    | Hard drop (+2 pts/cell)                                                |
| C        | Hold piece                                                             |
| P        | Pause                                                                  |
| S        | Settings menu (↑/↓ select, ←/→ change; persists live)                  |
| H        | High-scores dialog                                                     |
| ?        | Help dialog                                                            |
| R        | Restart (on game over or while paused)                                 |
| Q        | Quit (closes an open dialog instead)                                   |
| ESC      | Close dialog / pause / unpause; at game over: new game without saving  |
| ENTER    | At game over (top-10 score): save the typed name and start a new game  |

## Features

- **SRS rotation** with proper super-kicks (floor kicks, wall kicks)
- **7-bag randomizer** — fair piece distribution
- **5-piece next queue** — the NEXT box shows all five (head bright, rest
  dim), **hold piece** (C), **ghost piece** (piece-colored)
- **Spawn-glide animation** — the new piece slides in from the NEXT box
- **Lock delay** (0.5 s, refreshed by moves/rotations, 15-reset cap) for
  modern, forgiving landing feel
- **T-spins**: corner-rule detection with mini/full distinction, T-spin
  scoring (100/400 with no lines, 200/800/1200/1600 by lines cleared, all
  × level), counted in the SPINS stat
- **Line-clear flash animation**, floating score popups ("TETRIS +800",
  "T-SPIN +1200", …), T-spin corner flash, board shake on hard drops,
  terminal beeps (1 per clear, 2 for Tetris or T-spin mini, 3 for a full
  T-spin) — sound and shake toggle in settings
- **Modern scoring**: 100/300/500/800 × level, **combos** (+50 × combo ×
  level), **back-to-back** bonus (1.5×) for Tetris and T-spin multi-line
  clears, soft/hard drop points
- **High scores** — top 10 with names, persisted together with the settings
  in the unified `~/.local/share/terminal-tetris/state.json` (override with
  `$TETRIS_SCORES`)
- **Game-over stats** — score, lines, level, pieces, and high-score rank
  (or the best score)
- **Settings menu** (S) with live persistence: start level, drop shadow,
  hold piece, sound, screen shake
- **Help dialog** (?) with the full key legend and a scoring explainer
- **High-scores dialog** (H) with the top-10 table and name entry
- Speed increases every 10 lines
- Board auto-centers; shows a notice if the terminal is smaller than 60×27

## Project layout

- `src/tetris/pieces.py` — static SRS piece data + board dimensions
- `src/tetris/scoring.py` — scoring rules
- `src/tetris/board.py` — grid
- `src/tetris/engine.py` — pure Tetris engine; no I/O of any kind
- `src/tetris/settings.py` — user settings model; pure data
- `src/tetris/state.py` — the ONLY I/O module: unified `state.json` holding
  both high scores and settings
- `src/tetris/stats.py` — sidebar stat labels/formatting, shared by the
  curses UI and the MCP screen parser
- `src/tetris/main.py` — curses UI
- `src/tetris/mcp/` — MCP play-test server (pty + pyte mirror, run with
  `just mcp` — see `src/tetris/mcp/README.md`)
- `tests/` — 195 pytest tests (189 test functions; some parametrized)

## Roadmap

See [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) — planned for later waves:
180° rotation, sprint mode, themes, and replays.
