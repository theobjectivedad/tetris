# Terminal Tetris

A classic Tetris game for your terminal, built with Python's built-in `curses` module. No dependencies.

## Run

```bash
just run        # start the game
just test       # run the test suite (pytest)
just test-cov   # tests with coverage
```

Or directly:

```bash
uv run main.py
uv run pytest
```

## Controls

| Key         | Action      |
|-------------|-------------|
| ← / →       | Move        |
| ↑           | Rotate      |
| ↓           | Soft drop   |
| SPACE       | Hard drop   |
| P           | Pause       |
| Q           | Quit        |

## Features

- 7-bag piece randomizer (fair piece distribution)
- Ghost piece preview
- Next piece preview
- Wall kicks on rotation
- Standard scoring (100/300/500/800 × level)
- Speed increases every 10 lines
