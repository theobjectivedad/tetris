# Terminal Tetris

A feature-rich Tetris for your terminal, built with Python's `curses`. The
game itself has no runtime dependencies; the bundled MCP play-test server
brings in `mcp` and `pyte`.

## Run

```bash
just run        # start the game
just test       # run the test suite (pytest)
just test-cov   # tests with coverage
just lint       # ruff check
just fmt        # ruff format
just check      # full quality gate (ruff + mypy --strict)
```

Or directly: `uv run python -m tetris.main`

## Controls

| Key      | Action                                                                 |
|----------|------------------------------------------------------------------------|
| ← / →    | Move (DAS/ARR auto-repeat when held)                                   |
| ↑        | Rotate clockwise                                                       |
| Z        | Rotate counter-clockwise                                               |
| X        | Rotate 180° (J/L/S/Z/T pieces)                                         |
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
| G        | Replay the last saved game (at game over)                              |

## Features

- **SRS rotation** with proper super-kicks (floor kicks, wall kicks) and
  **180° rotation** (X) for J/L/S/Z/T
- **Game modes** — classic (endless) and **sprint** (clear 10 lines before
  the 180 s clock runs out; the best sprint time is saved separately and never
  touches the score table)
- **Replays** — every game is seeded; the input log is saved and `G` at game
  over replays the last run at 1× speed
- **Color themes** — classic, mono, and vivid palettes (switch live from the
  settings menu)
- **DAS/ARR tuning** — held-key delay and auto-repeat rate are adjustable in
  settings
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
- **Game-over stats** — score, lines, level, pieces, game time, best combo,
  and high-score rank (or the best score); the game's piece seed is shown so
  the run can be replayed
- **Settings menu** (S) with live persistence: start level, drop shadow,
  hold piece, sound, screen shake, DAS delay, ARR rate, color theme, and
  game mode
- **Help dialog** (?) with the full key legend and a scoring explainer
- **High-scores dialog** (H) with the top-10 table and name entry
- Speed increases every 10 lines
- Board auto-centers; shows a notice if the terminal is smaller than 60×27

## Project layout

- `src/tetris/pieces.py` — static SRS piece/kick tables + board dimensions
- `src/tetris/scoring.py` — scoring rules
- `src/tetris/board.py` — grid (cell storage + line collapse)
- `src/tetris/engine.py` — pure Tetris engine; no I/O of any kind
- `src/tetris/settings.py` — user settings model; pure data
- `src/tetris/state.py` — the ONLY file-I/O module: unified `state.json`
  (high scores + settings + best sprint time) and `replays.json` (the 5 most
  recent replay logs), held next to the state file
- `src/tetris/stats.py` — sidebar stat labels/formatting, shared by the
  curses UI and the MCP screen parser
- `src/tetris/themes.py` — color-theme definitions (pure data)
- `src/tetris/ui_input.py` — key reading, ESC-sequence reassembly, DAS/ARR
  timing
- `src/tetris/ui_render.py` — colors, modal builders, board/sidebar drawing
- `src/tetris/ui_session.py` — the per-game state machine (session + effects)
- `src/tetris/main.py` — curses entry point + 50 fps frame loop
- `src/tetris/mcp/` — MCP play-test server (pty + pyte mirror, run with
  `just mcp` — see `src/tetris/mcp/README.md`)
- `tests/` — 275 pytest tests (260 test functions; some parametrized)

## Roadmap

The improvement plan ([IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)) is fully
shipped: 180° rotation, sprint mode, themes, replays, session stats (game
time + best combo), DAS/ARR tuning, drift-corrected 50 fps pacing, and a CI
workflow are all in. Deliberate non-goals (local 2-player multiplayer, CPU
micro-optimization of the render path, exotic-terminal acrobatics) are
documented in the plan.

> **Internal API note:** the `tetris.game` facade and the `tetris.scores`
> shim have been removed. Import from the real modules instead
> (`tetris.engine`, `tetris.pieces`, `tetris.scoring`, `tetris.settings`,
> `tetris.state`). This is a breaking change for any external code that
> imported `from tetris.game import …`.
