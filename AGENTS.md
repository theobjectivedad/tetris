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

The game is a **pure core** (no I/O, fully unit-testable) under a thin
curses **UI layer**. `state.py` is the only module that touches disk.

```
keyboard ──▶ ui_input.KeyReader (DAS/ARR throttles, ESC reassembly)
                 │
                 ▼
main.game_loop  (50 fps frame loop; owns the `time` clock)
                 │  now
                 ▼
ui_session.Session.on_frame ──▶ engine.Tetris (pure game state)
                 │                  ▲
                 ▼                  │ pieces / scoring / board (pure data)
ui_render (board, sidebar, stats, modals; themes.py = palettes)

state.GameState ◀──▶ state.json on disk (scores, settings, sprint best, replays)
```

- `src/tetris/pieces.py` — static SRS piece/kick tables + board dimensions.
- `src/tetris/scoring.py` — pure scoring rules (`Scorer.breakdown`).
- `src/tetris/board.py` — the grid: cell access, full-row detection, collapse.
- `src/tetris/engine.py` — the pure Tetris engine (`Tetris`); NO I/O of any kind.
- `src/tetris/settings.py` — user settings model + option metadata; pure data.
- `src/tetris/state.py` — the ONLY file-I/O module: unified `state.json`
  holding high scores, settings, sprint best, and the replay log.
- `src/tetris/stats.py` — stat labels/formatting; shared UI + MCP contract.
- `src/tetris/themes.py` — color-theme palettes; pure data (no curses import).
- `src/tetris/main.py` — curses setup + 50 fps frame loop; re-exports for tests.
- `src/tetris/ui_input.py` — key reading and input timing (`KeyReader`).
- `src/tetris/ui_session.py` — per-game state machine (`Session.on_frame`).
- `src/tetris/ui_render.py` — all drawing: board, sidebar, stats panel, modals.
- `src/tetris/mcp/` — play-test MCP server (renders the game in a pty).
- `tests/` — pytest suite at repo root.

### Hard contracts

Breaking any of these silently breaks the test suite or the MCP parser:

- `main.py` keeps exactly `Tetris`, `time`, `curses`, `game_loop`,
  `build_attrs`, `draw_board`, and `BOARD_H` as the module-level monkeypatch
  contract (plus `FRAME` and `main`) — UI tests monkeypatch `main.Tetris` /
  `main.time` with `monkeypatch.setattr` and drive `main.game_loop` with a
  fake clock (the frame loop reads the clock through module-level `time`
  on purpose). Tests import everything else from its canonical module:
  modal builders / `ATTRS` / `draw_box` / `BG_PAIR` from `tetris.ui_render`,
  `KeyReader` / `ARR` / `DAS` from `tetris.ui_input`.
- `ui_render.ATTRS` — the module-level `AttrCache` singleton is the render-
  attr patch seam (tests patch fields such as `ATTRS.danger`);
  `init_colors`/`build_attrs` fill it. There are no rebinding attr globals
  to import.
- `Tetris.__init__(rng=..., start_level=..., sprint=...)` kwarg names —
  tests subclass the engine and pass these.
- `engine.Event`'s additive optional fields (`lines`, `combo`, `b2b`) —
  the engine stamps post-commit combo/b2b at scoring time and the UI's
  COMBO/B2B floaters read the event snapshot; tests construct events with
  keyword args, so fields stay append-only with defaults.
- `stats.STAT_LABELS` — the UI renders exactly these labels and the MCP
  parser (`mcp/server.py`) looks for exactly these; change both sides together.
- `Tetris.snapshot()` is the single source of the sidebar stat values.
- `Board` mirrors a list-of-rows protocol (`board[y]`, `board[y][x] = kind`,
  iteration, slicing, `len`) used by the UI and the test rig.

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

- Keep the core pure: no terminal or file I/O in `engine.py`, `pieces.py`,
  `scoring.py`, `board.py`, `settings.py`, `stats.py`, or `themes.py`.
  All persistence goes through `state.py`, the single persistence point.
- The MCP server parses the rendered sidebar text using `stats.STAT_LABELS` —
  keep those label strings stable (changing them silently breaks the MCP parser;
  update both sides together if it's ever necessary).
- UI regression tests drive `game_loop` with a fake curses screen
  (`tests/test_ui.py`). Preserve that pattern for new UI tests — do not spawn
  real terminals in the test suite.
