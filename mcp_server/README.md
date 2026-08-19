# tetris-vt

An [MCP](https://modelcontextprotocol.io) server for **play-testing the terminal
Tetris game visually**. It runs the real game (`main.py`) inside a pty
(virtual terminal) and mirrors the terminal output into a
[pyte](https://github.com/selectel/pyte) screen, so an AI agent (or a human
script) can send keystrokes, let time pass, and inspect exactly what is on
screen — the same visual feedback a player gets.

This is a development/QA tool: it lets the agent take over part of the
play-testing responsibility and catch visual or control bugs during
development.

## Layout

```
mcp_server/
├── pyproject.toml            # package metadata + dependencies (uv-managed)
├── README.md                 # this file
├── AGENTS.md                 # guidance for AI agents working on this package
└── src/tetris_vt/
    ├── __init__.py
    └── server.py             # MCP server: pty session, pyte mirror, tools
```

## Setup

```sh
cd mcp_server
uv sync          # creates .venv with all dependencies
```

## Running manually (stdio, MCP protocol)

```sh
# from a uvx ephemeral env (what the MCP registration uses):
uvx --from $(pwd) tetris-vt-server

# or from the local venv:
uv run tetris-vt-server
```

Quick handshake probe:

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  | uv run tetris-vt-server | head -1
```

## Registering with pi (pi-mcp-adapter)

The project root `.mcp.json` registers it:

```json
{
  "mcpServers": {
    "tetris": {
      "command": "uvx",
      "args": ["--no-cache", "--from", "<abs path>/mcp_server", "tetris-vt-server"],
      "env": {
        "TETRIS_GAME_DIR": "<abs path>/tetris-game-root",
        "TETRIS_SCORES": "/tmp/tetris-mcp-scores.json"
      }
    }
  }
}
```

* `--no-cache` is **required for development** — uv caches local-path builds
  and will not rebuild after you edit `server.py` (`--refresh`/`--reinstall`
  don't invalidate it). Costs ~5 s of dependency re-downloads per start.
* `requestTimeoutMs` — the MCP SDK's default 10 s request timeout is too
  short for a cold `--no-cache` build; the registration uses 120 s.
* `TETRIS_GAME_DIR` must point at the game's repo root (see Gotchas).
* `TETRIS_SCORES` keeps QA runs from touching your real high scores.

## Configuration (env vars)

| Variable         | Default                              | Purpose                                  |
| ---------------- | ------------------------------------ | ---------------------------------------- |
| `TETRIS_GAME_DIR`| repo root (auto-detected; **set explicitly for uvx**) | Directory the game is launched in       |
| `TETRIS_GAME_CMD`| `uv run main.py`                     | Command that launches the game           |
| `TETRIS_SCORES`  | `~/.local/share/terminal-tetris/scores.json` | Score file (set a temp path for isolated QA runs) |

## Tools

| Tool             | Description                                                        |
| ---------------- | ------------------------------------------------------------------ |
| `tetris_start`   | (Re)start the game in a virtual terminal (`width`/`height`)        |
| `tetris_key`     | Send a key: `left right up down space c p q r z` (repeatable)      |
| `tetris_wait`    | Wait real seconds; the game keeps running                          |
| `tetris_screen`  | Screen (or a crop) as numbered text lines; `█` solid, `▒` ghost    |
| `tetris_stats`   | Parse SCORE/BEST/LINES/LEVEL/COMBO/B2B/SPINS; detect PAUSED / GAME OVER  |
| `tetris_state`   | Process running/exited, uptime, terminal size                      |
| `tetris_stop`    | Stop the game (clean `q` or hard kill)                             |

## Typical play-test session

```
tetris_start(width=60, height=30)                      # min 53x23
tetris_screen()                      # inspect initial layout
tetris_key("right", count=3)         # tap right 3 times
tetris_screen(y0=0, y1=12)           # verify piece moved 3 cells, nothing else
tetris_key("space")                  # hard drop
tetris_wait(0.5)                     # let the line-clear flash finish
tetris_stats()                       # score/lines/level
tetris_stop()
```

## Design notes

- **One game at a time.** The server keeps a single `GameSession` (pty
  process + pyte screen) between tool calls, so snapshots reflect the live
  game state.
- **Reader thread** continuously drains the pty into the pyte screen under a
  lock; `tetris_screen` just reads the mirrored grid.
- **Keys are raw terminal bytes** (`\x1b[C` etc.) — the game's curses layer
  does the parsing, exactly like a real terminal. The game processes one key
  per 20 ms frame, so send repeated keys with `interval >= 0.05`.
- **Wide characters are fine** in pyte's mirror; keep the game's 1-col-wide
  `█` assumption in mind when reasoning about pixel columns (cell pitch = 3).
