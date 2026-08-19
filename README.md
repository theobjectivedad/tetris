# Terminal Tetris

A feature-rich Tetris for your terminal, built with Python's `curses`. The
game itself has no runtime dependencies; the bundled MCP play-test server
adds `mcp` and `pyte`.

## Run

```bash
just run        # start the game
just test       # run the test suite (pytest)
just test-cov   # tests with coverage
just lint/fmt   # ruff check / format
```

Or directly: `uv run python -m tetris.main`

## Controls

| Key         | Action       |
|-------------|--------------|
| ← / →       | Move (DAS auto-repeat) |
| ↑           | Rotate clockwise |
| Z           | Rotate counter-clockwise |
| ↓           | Soft drop (+1 pt/cell) |
| SPACE       | Hard drop (+2 pts/cell) |
| C           | Hold piece   |
| P           | Pause        |
| R           | Restart (on game over) |
| Q           | Quit         |

## Features

- **SRS rotation** with proper super-kicks (floor kicks, wall kicks)
- **7-bag randomizer** — fair piece distribution
- **Hold piece** (C), two-piece next queue, ghost piece (piece-colored)
- **Lock delay** (0.5 s, refreshed by moves/rotations, 15-reset cap) for
  modern, forgiving landing feel
- **T-spins**: corner-rule detection with mini/full distinction, T-spin
  scoring (100/200/400/800/1200/1600 × level), counted in the SPINS stat
- **Line-clear flash animation**, floating score popups ("TETRIS +800",
  "T-SPIN +1200", …), T-spin corner flash, board shake on hard drops,
  terminal beeps (1 for clears, 2 for Tetris, 3 for T-spins)
- **Modern scoring**: 100/300/500/800 × level, **combos** (+50 × combo × level),
  **back-to-back** bonus (1.5×) for Tetris and T-spin multi-line clears,
  soft/hard drop points
- **High scores** — top 5 persisted to `~/.local/share/terminal-tetris/scores.json`
  (override with `$TETRIS_SCORES`)
- **Game-over stats** (score, lines, level, time, pieces, high-score rank)
- Speed increases every 10 lines
- Board auto-centers; shows a notice if the terminal is too small

## Project layout

- `src/tetris/game.py` — pure game logic (fully unit-tested, no I/O)
- `src/tetris/main.py` — curses UI & input (DAS, rendering, loop)
- `src/tetris/mcp/` — MCP play-test server (`tetris-vt-server`; pty + pyte mirror,
  run with `just mcp` — see `src/tetris/mcp/README.md`)
- `tests/` — 79 pytest unit tests
