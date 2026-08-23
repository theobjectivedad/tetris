"""The playable-session state machine — the former ``game_loop`` body.

Owns the per-game state (engine, input reader, transient effects, modal
menu, name entry, and replay bookkeeping) and advances it one frame at a
time via :meth:`Session.on_frame`. The frame loop itself — and the
``time`` clock it runs on — stays in ``tetris.main``: ``on_frame``
receives ``now`` (``time.monotonic()`` at frame start) instead of reading
the clock itself, so the UI tests' fake clock (``main.time = fake_time``)
keeps driving the game.
"""

from __future__ import annotations

import curses
import random

from .engine import Event, Tetris
from .pieces import BOARD_H, BOARD_W
from .settings import OPTIONS, cycle, value_of
from .state import MAX_NAME, GameState
from .ui_input import KEY_ESCAPE, KeyReader
from .ui_render import (
    BOARD_INNER_W,
    BOARD_PITCH,
    BOARD_W_DRAWN,
    CELL_OFF,
    NEED_H,
    NEED_W,
    PANEL_GAP,
    STATS_W,
    _draw_spawn_glide,
    build_attrs,
    build_game_over_modal,
    build_help_modal,
    build_pause_modal,
    build_scores_modal,
    build_settings_modal,
    build_sprint_modal,
    draw_board,
    draw_modal,
    draw_sidebar,
    draw_stats_panel,
    init_colors,
)

# how long the new piece glides in from the NEXT box
SPAWN_ANIM_SECONDS = 0.15
# how long the T-spin corner flash stays on screen
SPIN_FLASH_SECONDS = 0.6
# how long the board shakes after a big clear (P17: tetris / full T-spin);
# a hard-drop shake is shorter (0.12 s, set in _dispatch_key)
BIG_SHAKE_SECONDS = 0.2

# Beeps per effect kind.
_BEEPS = {"clear": 1, "tetris": 2, "tspin": 3, "tspin-mini": 2, "levelup": 2}


class Effects:
    """Transient visual/sound effects: floating score text, board shake, and
    the T-spin corner flash. Owns that state so ``game_loop`` stays readable."""

    def __init__(self) -> None:
        self.floaters: list[tuple[str, int, float]] = []
        self.shake_until = 0.0
        self.spin_flash: tuple[tuple[int, int], float] | None = None
        self.sound = True  # gated per frame from the user's sound setting
        self.shake_on = True  # gated per frame from the user's shake setting

    def clear(self) -> None:
        self.floaters.clear()
        self.shake_until = 0.0
        self.spin_flash = None

    def on_events(self, events: list[Event], now: float) -> None:
        for ev in events:
            self.floaters.append((ev.text, ev.row, now))
            if ev.kind in ("tspin", "tspin-mini") and ev.center is not None:
                self.spin_flash = (ev.center, now)
            if ev.kind in ("tetris", "tspin") and self.shake_on:
                # P17: big clears shake the board, the same channel a hard
                # drop uses (0.12 s), but longer and stronger-feeling.
                self.shake_until = max(self.shake_until, now + BIG_SHAKE_SECONDS)
            if self.sound:
                for _ in range(_BEEPS[ev.kind]):
                    try:
                        curses.beep()
                    except curses.error:
                        pass
        events.clear()
        self.floaters[:] = [f for f in self.floaters if now - f[2] < 1.2]

    def shake(self, now: float) -> tuple[int, int]:
        if now < self.shake_until:
            return random.choice((-1, 0, 1)), random.choice((-1, 0, 1))
        return (0, 0)


class Session:
    """One play session: the state machine that used to be ``game_loop``.

    Constructed once per ``game_loop`` call; :meth:`on_frame` then runs
    once per frame (input -> DAS/ARR streaming -> gravity/replay ->
    effects -> game-over recording -> render -> static-scene skip) and
    returns True when the player has quit.

    The engine class is passed in (``tetris.main`` re-exports ``Tetris``
    and the UI tests patch ``main.Tetris``), and clock time is injected
    per frame — this class never reads ``time`` itself.
    """

    def __init__(self, tetris_cls: type[Tetris], now: float) -> None:
        self.tetris_cls = tetris_cls
        self.now = now
        self.state = GameState()
        init_colors(self.state.settings.theme)
        build_attrs()

        # Replay (P9): the engine is fully deterministic given (seed, actions,
        # times), so each game gets a seed, and every accepted player action
        # is logged with its game-relative time. G at game over re-plays the
        # last saved replay at 1× speed.
        self.seed = int(now * 1000) % 2**32
        self.game_start = now
        self.replay_events: list[tuple[float, str]] = []
        self.replay_saved = False
        self.replay: dict[str, object] | None = None
        self.replay_engine: Tetris | None = None
        self.replay_original: Tetris | None = None
        self.replay_start = 0.0
        self.replay_next = 0
        self.replay_stop_seq = 0

        self.engine = self.tetris_cls(
            rng=random.Random(self.seed),
            start_level=self.state.settings.start_level,
            sprint=self.state.settings.mode == "sprint",
        )
        self.new_best = False
        self.rank: int | None = None
        # Sprint bookkeeping (P11): recorded once when a sprint game ends.
        self.sprint_handled = False
        self.sprint_new_best = False
        self.sprint_best: float | None = None
        self.reader = KeyReader()
        self.effects = Effects()
        self.menu: str | None = None  # None | "help" | "settings" | "scores"
        self.menu_cursor = 0
        self.name_awaiting = False  # game over: high-score name still being typed
        self.typed_name = ""
        self.was_paused = False
        # Spawn animation: the new piece glides in from the NEXT box head.
        # last_seq starts at 0 (not engine.spawn_seq) so the very first
        # piece glides too.
        self.anim_start: float | None = None
        self.last_seq = 0
        # Static-scene detection: the scene key of the last drawn frame.
        # When the key is unchanged (and no key was consumed, no shake is
        # active) the screen still shows the right picture, so the frame
        # skips erase()+all draws — refresh() stays once-per-frame (tests
        # index frames by time) and is a cheap no-op on unchanged content.
        self.prev_scene: tuple[object, ...] | None = None

    def reset_game(self) -> None:
        # ``self.now`` is this frame's ``time.monotonic()`` (frame start).
        self.seed = int(self.now * 1000) % 2**32
        self.game_start = self.now
        self.replay_events.clear()
        self.replay_saved = False
        self.replay = None
        self.replay_engine = None
        self.replay_original = None
        self.replay_start = 0.0
        self.replay_next = 0
        self.replay_stop_seq = 0
        self.engine = self.tetris_cls(
            rng=random.Random(self.seed),
            start_level=self.state.settings.start_level,
            sprint=self.state.settings.mode == "sprint",
        )
        self.new_best = False
        self.rank = None
        self.sprint_handled = False
        self.sprint_new_best = False
        self.sprint_best = None
        self.menu = None
        self.was_paused = False
        self.anim_start = None
        self.last_seq = 0  # a fresh game's first piece glides in too
        self.name_awaiting = False
        self.typed_name = ""
        self.prev_scene = None  # force a full redraw after a reset
        # Restart/menu: also clear any in-flight held-key stream.
        self.reader.reset()
        self.effects.clear()

    def open_menu(self, kind: str) -> None:
        """Open a modal menu; the game is paused for its duration."""
        self.was_paused = self.engine.paused
        self.engine.paused = True
        self.menu = kind
        self.anim_start = None  # a menu open mid-flight cancels the glide
        self.reader.reset()  # no stale holds may survive a menu round-trip

    def close_menu(self) -> None:
        self.engine.paused = self.was_paused
        self.menu = None

    def commit_name(self) -> None:
        """Store the typed name on the new high-score entry (if any)."""
        if self.rank is not None:
            self.state.set_entry_name(self.rank, self.typed_name)
        self.name_awaiting = False

    def log_action(self, token: str) -> None:
        """Record a player action for the current game's replay (P9):
        the token at this frame's game-relative time. Attempts are logged
        when the UI authorizes them (throttle passed) — the engine may
        still reject a move, which replays identically."""
        self.replay_events.append((self.now - self.game_start, token))

    def apply_replay_token(self, engine: Tetris, token: str, at: float) -> None:
        """Re-run one logged action on the replay engine at its original
        game-relative time (P9)."""
        if token == "L":
            engine.move(-1, at)
        elif token == "R":
            engine.move(1, at)
        elif token == "U":
            engine.rotate(1, at)
        elif token == "Z":
            engine.rotate(-1, at)
        elif token == "S":
            engine.soft_drop()
        elif token == "H":
            engine.hard_drop()
        elif token == "C":
            engine.hold()
        elif token == "X":
            engine.rotate_180(at)
        # Unknown tokens (e.g. ones logged by a newer build) are skipped.

    def start_replay(self, now: float) -> None:
        """G at game over: re-run the most recent saved replay (P9).

        A fresh engine gets the original seed/start level; the logged
        events are re-fed at their original game-relative timestamps
        (1× real time). ``engine`` is swapped to the replay engine so the
        normal draw path renders the replay; the original game-over
        engine is restored when the replay ends or is aborted (ESC).
        """
        data = self.state.last_replay()
        if data is None:
            return
        raw_seed = data.get("seed")
        raw_level = data.get("start_level")
        if not isinstance(raw_seed, int) or not isinstance(raw_level, int):
            return
        self.replay = data
        self.replay_original = self.engine
        self.replay_engine = self.tetris_cls(
            rng=random.Random(raw_seed),
            start_level=raw_level,
            sprint=bool(data.get("sprint", False)),
        )
        self.engine = self.replay_engine
        self.replay_start = now
        self.replay_next = 0
        self.replay_stop_seq = 0
        # The replay must use the original game's input timing, and must
        # not inherit a held key or a rotate cooldown from the live game.
        raw_das = data.get("das")
        raw_arr = data.get("arr")
        if isinstance(raw_das, (int, float)) and not isinstance(raw_das, bool):
            self.reader.das = float(raw_das)
        if isinstance(raw_arr, (int, float)) and not isinstance(raw_arr, bool):
            self.reader.arr = float(raw_arr)
        self.reader.reset()
        self.effects.clear()
        self.anim_start = None
        self.last_seq = 0  # the replay's first piece glides in too
        self.prev_scene = None  # force a full redraw onto the replay board

    def finish_replay(self) -> None:
        """End a replay (completed or aborted) and restore the original
        game-over screen (P9)."""
        if self.replay_original is not None:
            self.engine = self.replay_original
        self.replay = None
        self.replay_engine = None
        self.replay_original = None
        self.replay_next = 0
        self.replay_stop_seq = 0
        self.reader.das = self.state.settings.das
        self.reader.arr = self.state.settings.arr
        self.reader.reset()
        self.effects.clear()
        self.anim_start = None
        self.last_seq = self.engine.spawn_seq  # no glide: the final piece is already placed
        self.prev_scene = None  # force a redraw of the original game-over screen

    def replay_step(self, now: float) -> None:
        """One frame of the replay: feed every logged event whose
        game-relative time has elapsed, tick the engine, and finish when
        the run is done (P9)."""
        events = self.replay.get("events") if self.replay is not None else None
        if not isinstance(events, list) or self.replay_engine is None:
            self.finish_replay()
            return
        elapsed = now - self.replay_start
        while self.replay_next < len(events):
            ev = events[self.replay_next]
            if (
                not isinstance(ev, list)
                or len(ev) != 2
                or not isinstance(ev[0], (int, float))
                or isinstance(ev[0], bool)
                or not isinstance(ev[1], str)
            ):
                self.replay_next += 1  # skip a malformed entry
                continue
            if ev[0] > elapsed:
                break
            self.replay_next += 1
            self.apply_replay_token(self.replay_engine, ev[1], float(ev[0]))
        self.replay_engine.tick(elapsed)
        if self.replay_engine.frozen:
            self.replay_engine.advance_flash()
        # Finish: the run ended in game over, or all events are fed and
        # the piece produced by the last input has locked (a new piece —
        # the final board — has spawned).
        if self.replay_next >= len(events):
            if self.replay_stop_seq == 0:
                self.replay_stop_seq = self.replay_engine.spawn_seq
            if self.replay_engine.game_over or self.replay_engine.spawn_seq > self.replay_stop_seq:
                self.finish_replay()

    def on_frame(self, stdscr: curses.window, now: float) -> bool:
        """Advance one frame; True when the player has quit.

        The frame pipeline, in order:

        1. read one logical key (replays only respond to ESC),
        2. dispatch it — name entry, menus, game-over, play input,
        3. stream held-direction moves (DAS/ARR),
        4. step the engine (or the replay),
        5. apply the engine's effects (floaters, beeps, spin flash),
        6. record a finished game (high score, sprint best, replay),
        7. start the spawn glide when a new piece appears,
        8. draw the frame (skipped while the scene is unchanged).

        ``now`` is ``time.monotonic()`` at frame start, supplied by the
        frame loop in ``tetris.main``.
        """
        self.now = now
        key = self._read_key(stdscr, now)
        if self._dispatch_key(key, now):
            return True
        self._stream_held_direction(now)
        self._step_engine(now)
        self._apply_effects(now)
        self._record_finished_game()
        self._start_spawn_glide(now)
        self._render(stdscr, now, key)
        return False

    # -- input ---------------------------------------------------------

    def _read_key(self, stdscr: curses.window, now: float) -> int:
        """Read this frame's logical key.

        Replay (P9): while a replay is running only ESC is meaningful —
        it aborts back to the game-over screen. All other keys are
        consumed so no live-game input can leak into the replayed run.
        """
        key = self.reader.next_key(stdscr, now)
        if self.replay_engine is not None:
            if key in (27, KEY_ESCAPE):
                self.finish_replay()
            key = -1
        return key

    def _dispatch_key(self, key: int, now: float) -> bool:
        """Apply one key; True when the player quit (Q in open play).

        ESC is the "back" key: at game over it starts a new game without
        saving the score (any recorded entry is discarded); otherwise it
        closes a dialog, unpauses, or pauses in open play. Q is the
        long-standing quit alias (in a modal it only closes the dialog).
        """
        # High-score name entry (game over, top-10): edit the typed name.
        # Enter commits and starts a new game (Enter arrives as 10 in most
        # pty/terminal setups, 13/KEY_ENTER in others); R/Q commit first,
        # then act on it. None of these count as typed characters.
        if self.engine.game_over and self.name_awaiting:
            if key in (10, 13, curses.KEY_ENTER):
                self.commit_name()
                self.reset_game()  # keep the score, start a new game
            elif key in (ord("q"), ord("Q"), ord("r"), ord("R")):
                self.commit_name()  # the q/r branch below then acts on it
            elif key in (8, 127, curses.KEY_BACKSPACE):
                self.typed_name = self.typed_name[:-1]
            elif 32 <= key <= 126 and len(self.typed_name) < MAX_NAME:
                self.typed_name += chr(key)

        if key in (ord("q"), ord("Q")):
            if self.menu is not None:
                self.close_menu()  # in a modal, Q closes the dialog, never quits
            else:
                return True  # quit: main.game_loop breaks the frame loop
        elif key in (ord("r"), ord("R")) and (self.engine.game_over or self.engine.paused):
            self.reset_game()
        elif key in (ord("g"), ord("G")) and self.engine.game_over and not self.name_awaiting:
            self.start_replay(now)
        elif key in (27, KEY_ESCAPE):
            if self.engine.game_over:
                if self.rank is not None:
                    self.state.remove_entry(self.rank)
                self.reset_game()
            elif self.menu is not None:
                self.close_menu()
            elif self.engine.paused:
                self.engine.paused = False
            else:
                self.engine.paused = True
        elif key == ord("?") and not self.engine.game_over:
            if self.menu == "help":
                self.close_menu()  # ? toggles the help dialog
            elif self.menu is None:
                self.open_menu("help")
        elif key in (ord("s"), ord("S")) and self.menu is None and not self.engine.game_over:
            self.menu_cursor = 0
            self.open_menu("settings")
        elif key in (ord("h"), ord("H")) and self.menu is None and not self.engine.game_over:
            self.open_menu("scores")
        elif key in (ord("p"), ord("P")) and self.menu is None:
            self.engine.paused = not self.engine.paused
        elif self.menu == "settings" and not self.engine.game_over:
            if key in (curses.KEY_UP, ord("k")):
                self.menu_cursor = (self.menu_cursor - 1) % len(OPTIONS)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.menu_cursor = (self.menu_cursor + 1) % len(OPTIONS)
            elif key in (curses.KEY_RIGHT, curses.KEY_LEFT, ord(" "), curses.KEY_ENTER):
                d = -1 if key == curses.KEY_LEFT else 1
                opt = OPTIONS[self.menu_cursor]
                new_settings = cycle(self.state.settings, opt.key, d)
                # Persist immediately through the single state store.
                self.state.update_settings(**{opt.key: value_of(new_settings, opt.key)})
                # Apply the change live: input timing reads the reader's
                # das/arr, and a theme change re-inits the color pairs and
                # the cached attrs without a restart.
                self.reader.das = self.state.settings.das
                self.reader.arr = self.state.settings.arr
                if opt.key == "theme":
                    init_colors(self.state.settings.theme)
                    build_attrs()
        elif self.menu is None and not self.engine.paused and not self.engine.game_over:
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                d = -1 if key == curses.KEY_LEFT else 1
                # One move per fresh press; holding streams at ARR after DAS.
                if self.reader.on_direction(d, now):
                    self.log_action("L" if d < 0 else "R")
                    self.engine.move(d, now)
            elif key == curses.KEY_UP:
                if self.reader.allow_rotate(now):
                    self.log_action("U")
                    self.engine.rotate(1, now)
            elif key in (ord("z"), ord("Z")):
                if self.reader.allow_rotate(now):
                    self.log_action("Z")
                    self.engine.rotate(-1, now)
            elif key in (ord("x"), ord("X")):
                if self.reader.allow_rotate(now):
                    self.log_action("X")
                    self.engine.rotate_180(now)
            elif key == curses.KEY_DOWN:
                self.log_action("S")
                self.engine.soft_drop()
            elif key == ord(" "):
                self.log_action("H")
                if self.engine.hard_drop() > 0 and self.state.settings.shake:
                    self.effects.shake_until = now + 0.12
            elif key in (ord("c"), ord("C")):
                self.log_action("C")
                if self.state.settings.hold:
                    self.engine.hold()

        return False

    # -- simulation ------------------------------------------------------

    def _stream_held_direction(self, now: float) -> None:
        """DAS/ARR streaming: held-key moves without new key events."""
        if self.menu is None and not self.engine.paused and not self.engine.game_over:
            auto = self.reader.auto_direction(now)
            if auto:
                self.log_action("L" if auto < 0 else "R")
                self.engine.move(auto, now)

    def _step_engine(self, now: float) -> None:
        """Gravity + lock delay — or one replay step while re-running."""
        if self.replay_engine is not None:
            self.replay_step(now)
            return
        self.engine.tick(now)
        if self.engine.frozen:
            self.engine.advance_flash()

    def _apply_effects(self, now: float) -> None:
        """Consume the engine's events: floating text, spin flash, beeps,
        and the big-clear shake."""
        self.effects.sound = self.state.settings.sound
        self.effects.shake_on = self.state.settings.shake
        self.effects.on_events(self.engine.events, now)

    # -- game-over bookkeeping --------------------------------------------

    def _record_finished_game(self) -> None:
        """Record a finished game: high score, sprint best, and the replay."""
        # Classic: a top-10 score enters the high-score table. Sprint never
        # writes the score table (P11) — its result is a best clear time.
        if (
            self.engine.game_over
            and not self.new_best
            and self.rank is None
            and not self.engine.sprint
            and self.engine.score > 0
        ):
            self.rank = self.state.record(
                self.engine.score, self.engine.lines, self.engine.level,
                time_s=self.engine.play_time, best_combo=self.engine.best_combo,
            )
            if self.rank is not None:
                self.new_best = True
                self.name_awaiting = True

        # Sprint result (P11): recorded once. A win stores the clear time
        # (lower is better); a loss just surfaces the existing best.
        if self.engine.game_over and self.engine.sprint and not self.sprint_handled:
            self.sprint_handled = True
            if self.engine.won:
                self.sprint_new_best, self.sprint_best = self.state.record_sprint(
                    self.engine.play_time
                )
            else:
                self.sprint_best = self.state.best_sprint_time()

        # Replay (P9): save the finished game's input log once, if the
        # player actually played it (at least one authorized action).
        if self.engine.game_over and not self.replay_saved and self.replay_events:
            self.replay_saved = True
            self.state.save_replay(
                {
                    "seed": self.seed,
                    "start_level": self.state.settings.start_level,
                    "sprint": self.engine.sprint,
                    "das": self.state.settings.das,
                    "arr": self.state.settings.arr,
                    "started_at": self.game_start,
                    "score": self.engine.score,
                    "lines": self.engine.lines,
                    "events": self.replay_events,
                }
            )

    def _start_spawn_glide(self, now: float) -> None:
        """Start the glide-in of a newly spawned piece.

        A new piece has appeared (initial spawn, after a lock, a hold, or
        a committed line clear): glide it in from the NEXT box head.
        """
        if (
            not self.engine.game_over
            and self.menu is None
            and not self.engine.paused
            and self.engine.spawn_seq != self.last_seq
        ):
            self.anim_start = now
            self.last_seq = self.engine.spawn_seq

    # -- rendering ---------------------------------------------------------

    def _render(self, stdscr: curses.window, now: float, key: int) -> None:
        """Draw the frame, skipping the redraw while the scene is unchanged.

        ``refresh()`` runs every frame either way: it is a no-op on
        unchanged content, and the test suite indexes frames by time, so
        the per-frame cadence must hold.
        """
        max_y, max_x = stdscr.getmaxyx()
        if max_x < NEED_W or max_y < NEED_H:
            self._draw_too_small(stdscr, max_x, max_y)
            return
        glide_frac = self._spawn_glide_fraction(now)
        self._expire_spin_flash(now)
        scene = self._scene_key(now, max_x, max_y, glide_frac)
        if scene == self.prev_scene and key == -1 and now >= self.effects.shake_until:
            # Nothing changed since the last drawn frame: keep the current
            # virtual screen.
            stdscr.refresh()
            return
        self.prev_scene = scene
        self._draw_scene(stdscr, now, max_x, max_y, glide_frac)
        self._draw_modals(stdscr, max_x, max_y)
        stdscr.refresh()

    def _draw_too_small(self, stdscr: curses.window, max_x: int, max_y: int) -> None:
        """The 'terminal too small' notice instead of the game."""
        stdscr.erase()
        try:
            stdscr.addstr(
                1, 1, f"Terminal too small — need {NEED_W}×{NEED_H}, got {max_x}×{max_y}"
            )
        except curses.error:
            pass
        stdscr.refresh()

    def _spawn_glide_fraction(self, now: float) -> float | None:
        """Progress of the in-flight spawn glide (0..1), or None.

        While the new piece is still in flight the live piece is hidden
        and drawn by the glide instead; once the duration has elapsed the
        piece simply appears at its grid position.
        """
        if self.anim_start is None or self.engine.game_over:
            return None
        elapsed = now - self.anim_start
        if elapsed >= SPAWN_ANIM_SECONDS:
            self.anim_start = None
            return None
        return min(1.0, elapsed / SPAWN_ANIM_SECONDS)

    def _expire_spin_flash(self, now: float) -> None:
        # Checked before the scene key so the expiry frame flips the key
        # and redraws once without the flash.
        if (
            self.effects.spin_flash is not None
            and now - self.effects.spin_flash[1] >= SPIN_FLASH_SECONDS
        ):
            self.effects.spin_flash = None

    def _scene_key(
        self, now: float, max_x: int, max_y: int, glide_frac: float | None
    ) -> tuple[object, ...]:
        """Every value the drawn frame depends on — the static-scene key.

        engine.version (P5) bumps on every observable engine change —
        moves, rotations, drops, holds, gravity steps, locks, clears,
        spawns, pause flips, and game over — so one counter covers the
        board, stats panel, and game-over modal; the rest covers layout,
        effects, and modals. Any term flipping makes the frame dirty.
        """
        return (
            max_x, max_y,  # resize / layout
            self.engine.version,  # every engine state change (incl. game_over/paused)
            tuple(
                (tx, row, int((now - born) * 2.5))
                for tx, row, born in self.effects.floaters
            ),  # floater drift, same quantization as the draw below
            self.effects.spin_flash,
            glide_frac,
            now < self.effects.shake_until,  # the unshake frame must redraw
            self.menu, self.menu_cursor, self.typed_name, self.name_awaiting,  # modal content
            self.new_best, self.rank,
            self.state.settings.ghost, self.state.settings.hold,
            self.state.settings.theme,  # live recolor on switch (P12)
            # Sprint countdown (P11): the displayed second changes once per
            # second even though the engine's version only bumps on
            # gravity/locks — quantize it so the TIME row stays fresh.
            int(self.engine.time_left)
            if self.engine.sprint and self.engine.time_left is not None
            else None,
            self.state.best() if self.engine.game_over else 0,
        )

    def _draw_scene(
        self,
        stdscr: curses.window,
        now: float,
        max_x: int,
        max_y: int,
        glide_frac: float | None,
    ) -> None:
        """Draw the game without modals: board, floaters, panels, glide."""
        # Layout: one centered block — stats panel (left), board (center),
        # HOLD/NEXT column (right).
        block_x = max(0, (max_x - NEED_W) // 2)
        bx = block_x + STATS_W + PANEL_GAP  # board origin
        by = max(1, (max_y - (BOARD_H + 7)) // 2)
        sidebar_x_offset = BOARD_W_DRAWN + 4  # HOLD/NEXT x = board bx + 26

        stdscr.erase()

        # Board shake: a 1-cell jitter for a couple of frames after a hard
        # drop or a big clear.
        ox, oy = self.effects.shake(now)

        draw_board(
            stdscr, self.engine, bx + ox, by + oy,
            show_ghost=self.state.settings.ghost, hide_live=glide_frac is not None,
        )

        # Floating score text, drifting up out of the board.
        for text, row, born in self.effects.floaters:
            fy = by + oy + row - int((now - born) * 2.5)
            fx = bx + ox + CELL_OFF + max(0, (BOARD_INNER_W - len(text)) // 2)
            try:
                stdscr.addstr(fy, fx, text, curses.A_REVERSE)
            except curses.error:
                pass

        # T-spin corner flash: the four diagonals of the T's center.
        if self.effects.spin_flash is not None:
            (cx, cy), _ = self.effects.spin_flash
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                px, py = cx + dx, cy + dy
                if not (0 <= px < BOARD_W and 0 <= py < BOARD_H):
                    continue
                try:
                    stdscr.addstr(
                        by + oy + py, bx + ox + CELL_OFF + px * BOARD_PITCH, "▓▓", curses.A_REVERSE
                    )
                except curses.error:
                    pass

        draw_stats_panel(stdscr, self.engine, by, block_x, self.new_best)
        draw_sidebar(stdscr, self.engine, self.state, by, bx + sidebar_x_offset)

        # Spawn glide: drawn last among the non-modal elements, so it can
        # legitimately overlap the board's right wall while flying in.
        if glide_frac is not None:
            _draw_spawn_glide(stdscr, self.engine, glide_frac, bx, by, ox, oy)

        # Replay tag (P9): shown while the input log is being re-run.
        if self.replay_engine is not None:
            try:
                stdscr.addstr(by + BOARD_H + 3, bx, "REPLAY", curses.A_DIM)
            except curses.error:
                pass

    def _draw_modals(self, stdscr: curses.window, max_x: int, max_y: int) -> None:
        """The topmost layer: game-over / sprint / help / settings / pause."""
        if self.engine.game_over:
            if self.engine.sprint:
                draw_modal(
                    stdscr,
                    build_sprint_modal(self.engine, self.sprint_new_best, self.sprint_best),
                    max_x,
                    max_y,
                )
            else:
                draw_modal(
                    stdscr,
                    build_game_over_modal(
                        self.engine, self.state.best(), self.rank, self.typed_name,
                        self.name_awaiting, self.seed,
                    ),
                    max_x,
                    max_y,
                )
        elif self.menu == "help":
            draw_modal(stdscr, build_help_modal(), max_x, max_y)
        elif self.menu == "settings":
            draw_modal(
                stdscr, build_settings_modal(self.state.settings, self.menu_cursor),
                max_x, max_y,
            )
        elif self.menu == "scores":
            draw_modal(stdscr, build_scores_modal(self.state), max_x, max_y)
        elif self.engine.paused:
            draw_modal(stdscr, build_pause_modal(), max_x, max_y)


__all__ = ["SPAWN_ANIM_SECONDS", "Effects", "Session"]
