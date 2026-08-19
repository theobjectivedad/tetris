"""MCP server for play-testing terminal Tetris.

Runs the game inside a real pty (virtual terminal), mirrors its output into a
pyte screen, and exposes tools to send keys, wait, and inspect the screen —
so an agent can visually verify gameplay, rendering, and UI behavior.

Run with:  uvx --from <path-to-project> tetris-vt-server
or in the project venv:  uv run tetris-vt-server
"""

from __future__ import annotations

import codecs
import fcntl
import functools
import os
import pty
import re
import signal
import struct
import termios
import threading
import time
import tty
from pathlib import Path

from mcp.server import MCPServer
from pyte import Screen, Stream

# Directory the game lives in (where `uv run python -m tetris.main` executes).
# Same project as this server; auto-detection is a dev convenience.
def _resolve_game_dir() -> Path:
    env_dir = os.environ.get("TETRIS_GAME_DIR")
    if env_dir:
        return Path(env_dir)
    # Walk up from this file looking for the game (src layout).
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "tetris" / "main.py").is_file():
            return parent
    # Installed as a wheel (uvx): fall back to the server's working directory.
    return Path.cwd()


GAME_DIR = _resolve_game_dir()
GAME_CMD = os.environ.get("TETRIS_GAME_CMD", "uv run python -m tetris.main").split()
READY_MARKER = "HOLD"

mcp = MCPServer("tetris-vt", description="Play-test the terminal Tetris game in a virtual terminal")

# key name -> byte sequence written to the pty
KEYS: dict[str, bytes] = {
    "left": b"\x1b[D",
    "right": b"\x1b[C",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "space": b" ",
    "c": b"c",
    "p": b"p",
    "q": b"q",
    "r": b"r",
    "z": b"z",
}


class GameSession:
    """A running game process attached to a pty, mirrored into a pyte screen."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.screen = Screen(width, height)
        self.stream = Stream(self.screen)
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.alive = False
        self.exit_code: int | None = None
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            # child: size the tty, drop env that would fight ncurses, exec the game
            winsize = struct.pack("HHHH", height, width, 0, 0)
            for dev in (0, 1, 2):
                try:
                    fcntl.ioctl(dev, termios.TIOCSWINSZ, winsize)
                except OSError:
                    pass
            # Keep the line discipline raw from the start. Input written
            # while the child is still booting (e.g. `uv run` resolving) is
            # lost in canonical mode — with raw, early keystrokes queue up
            # and the game reads them once it gets to getch().
            try:
                tty.setraw(0)
            except OSError:
                pass
            for var in ("COLUMNS", "LINES"):
                os.environ.pop(var, None)
            # ncurses needs a real terminfo entry; MCP hosts may not set TERM
            if os.environ.get("TERM", "").strip() in ("", "dumb"):
                os.environ["TERM"] = "xterm-256color"
            os.chdir(GAME_DIR)
            os.execvp(GAME_CMD[0], GAME_CMD)
        self.alive = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")()
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError:
                continue
            if text:
                with self.lock:
                    self.stream.feed(text)
        self.alive = False
        try:
            _, status = os.waitpid(self.pid, 0)
            self.exit_code = os.waitstatus_to_exitcode(status)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass

    def send(self, data: bytes) -> None:
        if not self.alive:
            return
        os.write(self.fd, data)

    def text_lines(self) -> list[str]:
        with self.lock:
            return [line.rstrip() for line in self.screen.display]

    def stop(self, hard: bool = True) -> str:
        if not self.alive:
            return "process already exited"
        if not hard:
            self.send(b"q")
            deadline = time.monotonic() + 2.0
            while self.alive and time.monotonic() < deadline:
                time.sleep(0.02)
        if self.alive:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass
            self.thread.join(timeout=2.0)
        return f"stopped (exit code {self.exit_code})"


session: GameSession | None = None

# MCP hosts (including pi) execute tool calls from a single message
# CONCURRENTLY. All tools mutate the shared `session`, so serialize them.
_tool_lock = threading.Lock()


def serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _tool_lock:
            return fn(*args, **kwargs)
    return wrapper


def require_session() -> GameSession:
    if session is None:
        raise ValueError("no game running — call tetris_start first")
    return session


def require_alive() -> GameSession:
    sess = require_session()
    if not sess.alive:
        raise ValueError("game process already exited (code " + str(sess.exit_code) + ")")
    return sess


def wait_for_ready(sess: GameSession, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not sess.alive:
            return f"process exited before the board appeared (exit code {sess.exit_code})"
        text = "\n".join(sess.text_lines())
        if READY_MARKER in text:
            return "ready"
        if "Terminal too small" in text:
            m = re.search(r"need (\d+)×(\d+)", text)
            need = m.group(0) if m else ""
            return f"ERROR: terminal too small ({need}) — start with larger width/height"
        time.sleep(0.05)
    return "timeout waiting for the board (check the game output / tetris_screen)"


# ---------------------------------------------------------------- tools


@mcp.tool()
@serialized
def tetris_start(width: int = 60, height: int = 30) -> str:
    """Start the Tetris game in a virtual terminal (pty) of the given size.

    Restarts the game if one is already running. Minimum size is 53×23;
    use larger dimensions if the layout reports 'Terminal too small'.
    """
    global session
    if session is not None and session.alive:
        session.stop()
        time.sleep(0.1)
    sess = GameSession(width, height)
    status = wait_for_ready(sess)
    session = sess
    if status != "ready":
        return status
    return (
        f"game running (pid {sess.pid}) in a {width}×{height} virtual terminal.\n"
        "Keys: left/right move, up/Z rotate, down soft drop, space hard drop, "
        "c hold, p pause, r restart, q quit.\n"
        "Use tetris_key to send input, tetris_wait to let time pass, "
        "tetris_screen to view the screen, tetris_stats for score/level/lines."
    )


@mcp.tool()
@serialized
def tetris_key(key: str, count: int = 1, interval: float = 0.06) -> str:
    """Send a key (or the same key repeated `count` times, `interval` seconds apart).

    Keys: left, right, up, down, space, c, p, q, r, z.
    To simulate holding a key, repeat with interval ~0.05-0.1 (terminal
    auto-repeat rate). Unknown names are sent as a single character.
    """
    sess = require_alive()
    data = KEYS.get(key.lower())
    if data is None:
        if len(key) == 1:
            data = key.encode()
        else:
            return f"unknown key {key!r} — valid: {', '.join(KEYS)} or a single character"
    for i in range(max(1, count)):
        sess.send(data)
        if i < count - 1 and interval > 0:
            time.sleep(interval)
    return f"sent {key!r} ×{count}"


@mcp.tool()
@serialized
def tetris_wait(seconds: float) -> str:
    """Wait real seconds (the game keeps running; gravity, flashes, etc. advance)."""
    sess = require_alive()
    time.sleep(seconds)
    if sess.alive:
        return f"waited {seconds}s — game still running"
    return f"waited {seconds}s — but the game PROCESS EXITED (code {sess.exit_code}); the last screen is still available"


@mcp.tool()
@serialized
def tetris_screen(y0: int = 0, y1: int | None = None, x0: int = 0, x1: int | None = None) -> str:
    """Return the current screen (or a crop) as numbered text lines.

    Each line is 'NN|content|' where NN is the row number — use row/column
    coordinates to locate pieces. '█' = solid cell, '▒' = ghost piece.
    """
    sess = require_session()
    lines = sess.text_lines()
    h, w = len(lines), max(len(l) for l in lines) if lines else 0
    y1 = (y1 if y1 is not None else h) - 1
    x1 = x1 if x1 is not None else w
    out = []
    for y in range(max(0, y0), min(y1, h - 1) + 1):
        out.append(f"{y:02d}|{lines[y][max(0, x0) : x1]}|")
    header = "" if sess.alive else "[process exited — showing last screen]\n"
    return header + "\n".join(out)


@mcp.tool()
@serialized
def tetris_stats() -> str:
    """Parse SCORE/BEST/LINES/LEVEL/COMBO/B2B from the sidebar and detect
    PAUSED / GAME OVER state."""
    sess = require_session()
    text = "\n".join(sess.text_lines())
    result = []
    for label in ("SCORE", "BEST", "LINES", "LEVEL", "COMBO", "B2B", "SPINS"):
        # The label sits mid-line, after the board wall — match on word
        # boundary rather than line start.
        m = re.search(rf"\b{label}\s+(\S+)", text)
        if m:
            result.append(f"{label}={m.group(1)}")
    if " PAUSED " in text:
        result.append("PAUSED")
    if "GAME OVER" in text:
        result.append("GAME OVER")
    if not result:
        return "could not find sidebar stats — is the game on screen?"
    return ", ".join(result)


@mcp.tool()
@serialized
def tetris_state() -> str:
    """Process state: running/exited, uptime, terminal size."""
    if session is None:
        return "no game started"
    if session.alive:
        return (
            f"running for {time.monotonic() - session.started:.1f}s, "
            f"terminal {session.width}×{session.height}"
        )
    return f"exited (code {session.exit_code})"


@mcp.tool()
@serialized
def tetris_stop(hard: bool = True) -> str:
    """Stop the game. hard=true kills the process; hard=false sends 'q' and
    waits for a clean exit."""
    global session
    if session is None:
        return "no game started"
    result = session.stop(hard=hard)
    session = None
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
