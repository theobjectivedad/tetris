# AGENTS.md — tetris MCP server (src/tetris/mcp/)

Guidance for AI agents developing or debugging this MCP server package.

## What this is

`tetris-vt` is an MCP (stdio) server that runs the Tetris game from the
*same project* (`src/tetris/main.py` + `src/tetris/game.py`) inside a **pty** and mirrors the
terminal into a **pyte** screen. Tools (`tetris_*`) let an agent send keys,
wait, and read the screen — i.e. visually play-test the game.

Stateful by design: one `GameSession` (game process + pyte screen) persists
across tool calls. The game's state lives in the *process*; this server only
observes and drives it.

## Architecture (src/tetris/mcp/server.py)

- `GameSession` — `pty.fork()` + `os.execvp(GAME_CMD)`; child sets tty
  winsize and `chdir(GAME_DIR)`. A daemon **reader thread** drains the pty
  and feeds the pyte `Stream` under `session.lock`.
- Tools are thin wrappers over the global `session`. Anything touching the
  pyte screen must hold `session.lock` (the reader thread writes to it).
- The game is ready when the sidebar renders (`HOLD` on screen);
  `wait_for_ready` also surfaces "Terminal too small" failures.

## Conventions

- The project is a single uv package (`src/` layout, `hatchling` backend)
  with one console entrypoint in the root `pyproject.toml`:
  `tetris-vt-server = "tetris.mcp.server:main"`.
- Deps: `mcp` (≥2.0 — use `from mcp.server import MCPServer`, **not** the old
  `mcp.server.fastmcp.FastMCP` import that died in 2.0) and `pyte`.
- Run locally: `uv sync` from the project root (single `.venv`); the MCP
  registration uses `uvx --no-cache --from <abs path> tetris-vt-server`
  (see Gotchas).

## Gotchas (learned the hard way)

* **uvx caches local-path builds.** After editing `server.py`, a plain
  `uvx --from <path>` starts the *stale* wheel; `--refresh` and `--reinstall`
  do not invalidate it. The `.mcp.json` registration therefore uses
  `--no-cache` (rebuilds every start, ~5 s). Symptom of a stale wheel:
  mysterious "process exited before the board appeared" while `uv run`
  works fine.
* **`__file__`-relative defaults break in a wheel.** `GAME_DIR` is resolved
  by walking up from `server.py` looking for `src/tetris/main.py`; inside
  uvx's archive that walk lands in the archive dir, so the game fails with
  `uv: Failed to spawn: python — No such file or directory`. **Always set
  `TETRIS_GAME_DIR`** in the MCP env. The last-resort fallback is the
  server's cwd.
* **Early keystrokes are lost in canonical mode.** Input written to the pty
  while the child is still booting (`uv run` resolving, ~1 s) sits in the
  canonical line buffer and is discarded when the app switches to raw.
  The child therefore calls `tty.setraw(0)` *before* `execvp` so early keys
  queue up and the game reads them once it reaches `getch()`.
* **Split arrow-key sequences.** With `nodelay()` on, curses `getch()` can
  return a bare ESC when a 3-byte `ESC [ C/D` sequence is split across
  reads; the stray `'C'` byte then leaks through as an ordinary key — in
  this game that silently triggers HOLD. `main.py` reassembles split
  sequences (see `ESC_SEQS` / `esc_seq` in `game_loop`), so arrows work
  whether the pty delivers them whole or in pieces.
* **TERM**: the pty child inherits the server's TERM; it is forced to
  `xterm-256color` when unset/`dumb`, and `COLUMNS`/`LINES` are stripped so
  ncurses reports the pty winsize.
* **pyte ≥ 0.8**: read text via the `screen.display` property (list of
  unicode strings); `screen.buffer[y][x].data` is an `int` now.
* **Exit codes**: the reader thread reaps the child with
  `os.waitstatus_to_exitcode(status)` — a SIGKILLed child must never report
  exit 0.
* **Parallel tool calls are serialized on the server.** MCP hosts (incl. pi)
  run tool calls from one message concurrently; all tools share the global
  `session`, so every tool is wrapped in `_tool_lock` (`@serialized`).
  Mixed parallel `tetris_stop`+`tetris_start`+`tetris_screen` therefore
  execute one at a time and always observe a consistent session. Don't
  remove the wrappers.
* **`printf | server` probes close stdin at EOF**, cancelling in-flight
  responses. Keep stdin open (`; sleep N`) when scripting manual probes.
* **The game reads one key per 20 ms frame.** `tetris_key(interval=…)` must
  be ≥ 0.05 or repeats collide.
* **Lock delay (0.5 s).** A piece resting on the floor does not lock
  immediately — it waits `LOCK_DELAY` seconds, and each successful
  move/rotate refreshes the timer (capped at 15 resets). When scripting
  playtests, a grounded piece will sit for half a second after the last
  key before locking; `tetris_key("space")` always locks instantly.
* **Terminal escape sequences**: send real bytes (`\x1b[C`…); the game goes
  raw itself via curses, and pyte reproduces the exact grid from the cursor
  moves (board origin `bx=(W-42)//2, by=(H-27)//2`; the play field is 22
  cols wide — 1-col solid walls outside the 20-col cell area (solid
  2-char cells, no gap) — so blocks never render over the walls; sidebar at
  `bx+26`).
* **Reader thread hygiene**: no `time.sleep` in the hot path; it must exit
  on EOF and `waitpid` the child so no zombie is left behind.
* **Keep UI markers in sync**: if the game UI changes (sidebar labels,
  ready marker), update `wait_for_ready`'s `READY_MARKER` and the
  `tetris_stats` regexes. Note the stats labels sit mid-line after the
  board wall, so `tetris_stats` matches on `\bLABEL\s+` (word boundary),
  not line start.

## Testing the server itself

1. Handshake probe (see README) — must return `serverInfo.name == "tetris-vt"`.
2. End-to-end: `tetris_start` → `tetris_screen` shows the board + HOLD/NEXT
   boxes → `tetris_key("right", count=3)` → `tetris_screen` shows the piece
   3 cells right and **nothing else moved** → `tetris_key("space")` →
   `tetris_wait(0.5)` → `tetris_stats` shows the piece locked (score ≥ drop
   points) → `tetris_stop`.
3. Exit paths: `tetris_stop(hard=False)` after the game shows GAME OVER must
   return cleanly; a killed process must make `tetris_state` report
   "exited".

## Verification checklist before committing

- `uv sync` clean; `uv run tetris-vt-server` handshake OK (from project root).
- `uvx --no-cache --from $(pwd) tetris-vt-server` handshake OK (this is the
  registered command — absolute path in `.mcp.json`).
- One full end-to-end play-test above passes with no tracebacks.
