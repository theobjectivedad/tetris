"""Input handling for the curses UI: logical keys and move timing.

Owns the stateful bits of key input that were previously locals in
``tetris.main.game_loop``: the ``KeyReader`` (reassembling arrow-key ESC
sequences that ``nodelay()`` getch can split across reads, and the
DAS/ARR move/rotate throttles) and the input-timing constants that tune
it.
"""

from __future__ import annotations

import curses
from typing import cast

# Key handling tuning
# Tap = one move; holding streams after DAS at the ARR rate (self-driven,
# not dependent on the terminal's slow initial auto-repeat delay).
# DAS/ARR are the DEFAULTS; the live values come from the settings menu
# (Settings.das/arr, synced into KeyReader each time the menu changes).
DAS = 0.17           # default delay after the first tap before held-key streaming
ARR = 0.04           # default auto-repeat rate (min interval between moves)
HOLD_WINDOW = 0.06  # a dir-key event within this window counts as "still held"
ROTATE_COOLDOWN = 0.12
ESC_TTL = 0.06        # how long a partial ESC sequence is kept while reassembling;
                      # a bare ESC is emitted after this, so keep it short — with
                      # ncurses' 25ms set_escdelay only exotic/slow terminals can
                      # still split a 3-byte sequence across reads
# Bare Escape byte; some _curses builds lack the KEY_ESCAPE constant.
KEY_ESCAPE = getattr(curses, "KEY_ESCAPE", 27)

# Arrow keys arrive as ESC [ <A/B/C/D>. With nodelay() enabled, getch() can
# hand back the bare ESC if the sequence is split across reads — either the
# terminal is slower than the 25ms ncurses set_escdelay disambiguation
# window, or a read boundary cut the sequence; the stray '[' / 'C' bytes
# would then be processed as ordinary keys ('C' = hold!). Reassemble them
# here so a split sequence still becomes one arrow key.
ESC_SEQS = {
    (27, 0x5B, ord("A")): curses.KEY_UP,
    (27, 0x5B, ord("B")): curses.KEY_DOWN,
    (27, 0x5B, ord("C")): curses.KEY_RIGHT,
    (27, 0x5B, ord("D")): curses.KEY_LEFT,
}


class KeyReader:
    """Reads logical keys from a curses window.

    Owns the stateful bits of input handling that were previously locals in
    ``game_loop``: reassembling arrow-key ESC sequences that nodelay() getch
    can split across reads, and the move/rotate throttle timestamps.
    """

    def __init__(self) -> None:
        self.last_rotate = 0.0
        self.das = DAS  # live-updated from the settings menu (P4)
        self.arr = ARR
        self._esc_seq: list[int] = []
        self._esc_t = 0.0
        self._dir = 0              # active hold direction (-1/1), 0 = none
        self._dir_since = 0.0      # when the current hold started
        self._last_dir_event = 0.0
        self._last_move = 0.0

    def reset(self) -> None:
        # Restart/menu resets the move throttle, any in-flight hold, and
        # any partially reassembled ESC sequence (stale bytes must never
        # leak into or out of a modal).
        self._last_move = 0.0
        self._dir = 0
        self._esc_seq = []

    def next_key(self, stdscr: curses.window, now: float) -> int:
        """Return the next logical key, or -1 if no input is pending.

        Splits of a 3-byte arrow sequence (ESC [ A/B/C) are reassembled
        here. A lone ESC that never completes within ESC_TTL is a real
        Escape press: it is emitted, and whatever input follows it is
        still delivered next frame instead of being eaten.
        """
        # An ESC from an earlier frame that never became an arrow
        # sequence: emit it BEFORE reading further input so the input
        # that followed it is not lost.
        if self._esc_seq and now - self._esc_t > ESC_TTL:
            self._esc_seq = []
            return 27
        raw = stdscr.getch()
        if raw == -1:
            return -1
        if self._esc_seq:
            if raw == 27:
                # A fresh ESC while one is already pending: emit the
                # pending one now and start tracking the new press.
                self._esc_seq = [27]
                self._esc_t = now
                return 27
            self._esc_seq.append(raw)
            if len(self._esc_seq) == 3:
                key = ESC_SEQS.get(cast("tuple[int, int, int]", tuple(self._esc_seq)), -1)
                self._esc_seq = []
                return key  # -1: an unknown 3-byte ESC sequence, dropped
            return -1
        if raw == 27:
            self._esc_seq = [27]
            self._esc_t = now
            return -1
        return raw

    def on_direction(self, d: int, now: float) -> bool:
        """Handle a left/right key event; True if the piece should move.

        A fresh press (or direction change) moves immediately. While the key
        stays held the terminal's auto-repeat keeps events arriving; DAS/ARR
        streaming (auto_direction) takes over after the DAS delay.
        """
        fresh = self._dir != d
        if fresh:
            self._dir = d
            self._dir_since = now
        self._last_dir_event = now
        if now - self._last_move < self.arr:
            return False  # anti double-fire (e.g. ESC reassembly artifact)
        if not fresh and now - self._dir_since < self.das:
            return False  # held, but the DAS delay hasn't elapsed yet
        self._last_move = now
        return True

    def auto_direction(self, now: float) -> int:
        """Direction to auto-move this frame (DAS/ARR streaming), or 0.

        The terminal gives no key-release event, so "held" means a direction
        event arrived within HOLD_WINDOW; after a real release the window
        expires and streaming stops (at most one extra step).
        """
        if self._dir == 0 or now - self._last_dir_event > HOLD_WINDOW:
            return 0
        if now - self._dir_since < self.das or now - self._last_move < self.arr:
            return 0
        self._last_move = now
        return self._dir

    def allow_rotate(self, now: float) -> bool:
        if now - self.last_rotate >= ROTATE_COOLDOWN:
            self.last_rotate = now
            return True
        return False


__all__ = [
    "ARR",
    "DAS",
    "ESC_SEQS",
    "ESC_TTL",
    "HOLD_WINDOW",
    "KEY_ESCAPE",
    "ROTATE_COOLDOWN",
    "KeyReader",
]
