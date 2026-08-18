# Terminal Tetris

A feature-rich Tetris for your terminal, built with Python's `curses` — no dependencies.

## Run

```bash
just run        # start the game
just test       # run the test suite (pytest)
just test-cov   # tests with coverage
just lint/fmt   # ruff check / format
```

Or directly: `uv run main.py`

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
- **Hold piece** (C), next-piece preview, ghost piece
- **Line-clear flash animation**, terminal beep on Tetris
- **Modern scoring**: 100/300/500/800 × level, **combos** (+50 × combo × level),
  **back-to-back Tetris** bonus (1.5×), soft/hard drop points
- **High scores** — top 5 persisted to `~/.local/share/terminal-tetris/scores.json`
  (override with `$TETRIS_SCORES`)
- **Game-over stats** (score, lines, level, time, pieces, high-score rank)
- Speed increases every 10 lines
- Board auto-centers; shows a notice if the terminal is too small

## Project layout

- `game.py` — pure game logic (fully unit-tested, no I/O)
- `main.py` — curses UI & input (DAS, rendering, loop)
- `tests/` — 59 pytest unit tests
