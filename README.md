# Terminal Tetris

A feature-rich Tetris that runs right in your terminal, built with
Python's `curses`. No runtime dependencies, no browser, no GPU — just
`just run` and a 60×27 terminal.

> **An AI-built demo.**
> Every line of this game — code, tests, and docs — was written
> **exclusively by Qwen 3.8 27B (NVFP4)**, a 27-billion-parameter
> language model running at 4-bit NVFP4 precision on **two DGX Spark
> workstations**, driven by the Pi coding-agent harness. No human wrote
> the code. The model planned the game, implemented all 30 feature
> items, burned down a 9-step refactor pass, and kept a full build
> log: see [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md).

## Quick start

You need [Python 3.12+](https://www.python.org/downloads/),
[uv](https://docs.astral.sh/uv/), and the
[just](https://github.com/casey/just) task runner.

```bash
git clone git@github.com:theobjectivedad/tetris.git
cd tetris
uv sync          # create the environment
just run         # play!
```

No `just`? The game is also `uv run python -m tetris.main`.

The board auto-centers itself; if your terminal is smaller than
60×27 the game tells you what it needs.

## How to play

| Key       | Action                                                             |
|-----------|--------------------------------------------------------------------|
| ← → / a d | Move. Hold a key and it auto-repeats (DAS/ARR, tunable)            |
| ↑         | Rotate clockwise                                                   |
| Z         | Rotate counter-clockwise                                           |
| X         | Rotate 180° (J, L, S, Z, and T pieces)                             |
| ↓         | Soft drop (+1 pt/cell); hold it to stream at 20 cells/s            |
| SPACE     | Hard drop (+2 pts/cell)                                            |
| C         | Hold the current piece for later                                   |
| P         | Pause (ESC also pauses/unpauses)                                   |
| S         | Settings menu — changes apply live and are saved                   |
| H         | High-score table                                                   |
| ?         | Help dialog (key legend + scoring explainer)                       |
| R         | Restart (while paused or at game over)                             |
| Q         | Quit (closes an open dialog instead)                               |
| ESC       | Close dialog / pause / unpause; at game over: new game without save |
| ENTER     | At game over with a top-10 score: save your typed name, new game   |
| G         | At game over: replay the last saved game                           |
| L         | At game over: list the five most recent replays (1–5 plays one)    |
| F         | While a replay runs: cycle speed 1× / 2× / 4×                      |

**Scoring.** A clear is worth 100 / 300 / 500 / 800 points (1–4 lines)
× your level, with bonuses stacked on top: **combos** (+50 × combo ×
level for consecutive clears), **back-to-back** (1.5× for a Tetris or
a multi-line T-spin following another one), and soft/hard drop points.
**T-spins** score 100/400 with no lines, 200/800/1200/1600 by lines
cleared — all × level — and are counted in the SPINS stat. You level
up every 10 lines, and gravity speeds up with each level.

## Game modes

- **Classic** — endless. Stack up, set a top score, chase combos.
- **Sprint** — clear **10 lines before the 180-second clock** runs
  out. Your best sprint time is saved separately and never touches the
  score table. The timer blinks at 30 s, turns red at 10 s, and ticks
  out the last five seconds. Switch modes in the settings menu (S).

## Features

- **Modern SRS rotation** with proper super-kicks (wall kicks and
  floor kicks), 180° rotation for J/L/S/Z/T, a 7-bag randomizer for
  fair piece distribution, and a 5-piece next queue.
- **Forgiving landing feel** — 0.5 s lock delay that refreshes as you
  move or rotate (15-reset cap), a grounded piece that pulses while
  the lock delay ticks, and a brief flash on the cells that just
  locked so you can always see when a piece settles.
- **Ghost piece** (piece-colored), **hold piece** (C) with a flash
  when a hold is accepted, and a **spawn-glide** animation that slides
  each new piece in from the NEXT box.
- **Replays** — every game is seeded. At game over the seed is shown;
  `G` replays your last run, `L` lists the five most recent saved runs
  (score, lines, mode, date) and 1–5 plays one, and `F` cycles
  1×/2×/4× speed while a replay runs.
- **Juice** — line-clear flash animation, floating score popups
  ("TETRIS +800", "COMBO ×4", "B2B", …), a T-spin corner flash, a
  board shake on hard drops and big clears, a "LEVEL UP" floater with
  a double beep, and a blinking red border while the stack nears the
  ceiling (the danger zone).
- **Sound moments** — terminal beeps for clears (1 per clear, 2 for a
  Tetris or level-up, 3 for a full T-spin), a 3-beep sting that ends a
  finished game (brisk and rising on a sprint clear, slow and
  descending otherwise), and a "NEW BEST!" jingle the instant you pass
  the board's top score. The stats panel also flags a live NEW BEST
  indicator while you're chasing a record mid-game.
- **High scores** — top 10 with name, score, level, play time, and
  date. Enter your name at game over with ENTER (or skip it).
- **Game-over stats** — score, lines, level, pieces, game time, best
  combo, and your high-score rank.
- **Color themes** — classic, mono, and vivid palettes; switch live
  from the settings menu.
- **Feel tuning** — DAS (held-key delay) and ARR (auto-repeat rate)
  are adjustable in settings; the down key always streams at a fixed
  20 cells/s regardless of your OS repeat rate.

## Settings

Press **S** in any game. Everything applies live and persists:
start level, drop shadow, hold piece, sound, screen shake, DAS
delay, ARR rate, color theme, and game mode.

## Your data

Scores, settings, and sprint bests live in one file:

```
~/.local/share/terminal-tetris/state.json
```

Replays are saved next to it (`replays.json`, the five most recent).
Set the `TETRIS_SCORES` environment variable to a different path to
use a separate score file — handy for trying things out without
touching your real high scores.

## For developers

- `just test` — the pytest suite (326 tests: pure engine rules,
  persistence, and UI driven through the real game loop with a fake
  curses screen)
- `just check` — the quality gate (`ruff` + `mypy --strict`)
- `just mcp` — a play-test MCP server that renders the game in a pty
  and exposes tools to send keys and read the screen; coding agents
  use it to play-test the game visually
- The core is pure (no I/O in `engine.py`, `pieces.py`, `scoring.py`,
  `board.py`); all persistence goes through `state.py`. Layout and
  hard test contracts are documented in [AGENTS.md](AGENTS.md).
