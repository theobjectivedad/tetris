# AGENTS.md — Terminal Tetris

Guidance for AI coding agents working in this repository.

## Project

Terminal Tetris — a curses-based Tetris game.

- Python 3.12+ (see `.python-version`), venv managed by `uv`.
- Task runner: `just` (see `Justfile`).
- Game entry point: `uv run python -m tetris.main`.
- Playtest MCP server: `just mcp` — runs `tetris-vt-server` (stdio). Agents use it
  for visual play-testing (renders the game in a pty and exposes tools to send keys
  and read the screen). Set `TETRIS_SCORES` to a `/tmp` path for isolated runs so
  the MCP server doesn't clobber the user's real scores/settings file.

## Layout

- `src/tetris/pieces.py` — static SRS piece data + board dimensions.
- `src/tetris/scoring.py` — scoring rules.
- `src/tetris/board.py` — grid (board cell value type).
- `src/tetris/engine.py` — pure Tetris engine; NO I/O of any kind.
- `src/tetris/settings.py` — user settings model; pure data.
- `src/tetris/state.py` — the ONLY file-I/O module: unified `state.json` holding
  both high scores and settings. `scores.py` is a legacy re-export shim for
  backward compatibility; do not add new imports of it.
- `src/tetris/stats.py` — sidebar stat labels/formatting; shared by the curses UI
  and the MCP screen parser.
- `src/tetris/main.py` — curses UI.
- `src/tetris/mcp/` — play-test MCP server.
- `tests/` — pytest suite at repo root.

## Commands

- `just run` — play the game.
- `just test` — run the pytest suite.
- `just check` — full quality gate: `ruff check src tests` + `mypy --strict src/tetris`.
- `just build` — build wheel + sdist into `dist/`.
- `just release <version>` — tag a release (`v`-prefixed), build, push tag.
- `just clean` — remove build/test caches and artifacts.
- `just install-hooks` — install the git pre-commit hooks.
- `just mcp` — run the MCP play-test server.

## Quality gates

- `just check` (ruff + mypy --strict) and `uv run pytest` MUST pass before
  committing. These are also enforced by the pre-commit hooks (ruff + mypy), so
  hook failures block commits.
- All public functions fully type-annotated — mypy runs in `--strict` mode.
- Line length: 88 (ruff).

## Commit policy

- Commit after each major milestone, not per tiny edit.
- Commit messages: imperative and descriptive (e.g. `Add SRS wall kicks to engine`).
- Keep the working tree clean — nothing half-finished left in it.
- GPG signing is disabled locally: commit without signing. Do NOT try to
  re-enable commit signing.
- No force-pushes.
- Releases: `just release <version>` (creates a `v`-prefixed annotated tag).

## Conventions

- Keep the engine pure: no terminal or file I/O in `engine.py`, `pieces.py`,
  `scoring.py`, `board.py`, or `settings.py`. All persistence goes through
  `state.py`, the single persistence point.
- The MCP server parses the rendered sidebar text using `stats.STAT_LABELS` —
  keep those label strings stable (changing them silently breaks the MCP parser;
  update both sides together if it's ever necessary).
- UI regression tests drive `game_loop` with a fake curses screen
  (`tests/test_ui.py`). Preserve that pattern for new UI tests — do not spawn
  real terminals in the test suite.
