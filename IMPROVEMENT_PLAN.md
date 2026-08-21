# Terminal Tetris — Improvement Plan (2026-07-21)

> **GOAL (verbatim from owner):** "Set a Goal and work through every item in this
> plan to ensure we do the complete work."
>
> Every actionable item below (P1–P13) must be completed, quality-gated, and
> verified through the MCP play-test server. "Skip" items are explicit
> non-goals and are NOT part of the goal.

## Context & verdict

Terminal Tetris is a curses-based Tetris (Python 3.12, `uv`, `just`) with a pure,
fully tested engine and a thin curses UI. The engine already implements SRS
rotation with super-kicks, 7-bag randomizer, hold with swap lockout, lock delay
(0.5 s, 15-reset cap), T-spin full/mini detection + scoring, B2B, combos,
ghost piece, and guideline-style scoring.

**Performance verdict — do not chase CPU.** Measured 2026-07-21:

- Engine hot path (`ghost_y` + `_can_fall` + `full_rows`): **18.7 µs/op**
- 200 full simulated games (random input to game over): **0.12 s total** (0.58 ms/game)

The only real performance problem is *perceived*: frame pacing (a fixed
`time.sleep(FRAME)` drifts against actual frame cost) and input latency
(ESC delay already fixed at 25 ms; DAS/ARR hardcoded).

**Code verdict.** `main.py` is a 1020-line monolith; settings handling has
triplicated per-key if-chains; the render-skip scene key is a fragile 15-field
tuple; two legacy shims remain (`scores.py`, `game.py` facade); README is
stale; no CI.

## Global rules (all work)

- Repo: `/mnt/ai/theobjectivedad/pi`. Python 3.12 (`uv`), task runner `just`.
- **Quality gate (must be green before any commit/merge):**
  `just check` (= `ruff check src tests` + `mypy --strict src/tetris`) **and**
  `uv run pytest`.
- Line length 88 (ruff). All public functions fully type-annotated (mypy --strict).
- Engine purity: NO terminal/file I/O in `engine.py`, `pieces.py`, `scoring.py`,
  `board.py`, `settings.py`. All persistence through `state.py` only.
- `stats.STAT_LABELS` strings are the MCP parser contract — never change existing
  label strings; additive labels are OK if the MCP parser is updated in the same
  change.
- UI tests (`tests/test_ui.py`) drive the real `game_loop` with a fake curses
  screen + `FakeTime` (mocked `time.monotonic`/`time.sleep`). Preserve that
  pattern; never spawn real terminals in tests. Note: `FakeTime.sleep(dt)`
  advances its clock by `dt`, so drift-corrected sleeps remain test-stable.
- Commits: imperative, descriptive; after each milestone; no GPG signing;
  working tree left clean.
- **MCP play-test policy:** after every merged wave, (1) `mcp({connect: "tetris"})`
  to force a reload if `mcp/server.py` changed (the game itself is a fresh
  subprocess on each `tetris_start`, so game-code changes need no restart),
  (2) ensure `mcp/server.py` `KEYS` map covers every new player-facing key,
  (3) smoke test: `tetris_start` → `tetris_stats` → send a spread of keys
  (arrows, space, c, z, x, s, h, ?, p, esc, r) → `tetris_screen` → verify no
  traceback/blank screen → `tetris_stop`. Isolated scores via
  `TETRIS_SCORES=/tmp/tetris-mcp-scores.json` (already in `.mcp.json`).

## Phases & wave plan (parallelization)

| Wave | Items | Owner | Parallel? |
|---|---|---|---|
| 1 | P1, P10 (docs+CI) · P5, P7a, P9a (engine core) · P4a, P8, P12a (settings/state) | 3 sub-agents (worktrees) | yes — disjoint files |
| 2 | P2 (frame pacing) · P4b, P7b, P12b (main.py wiring) · P5b (scene key) | main agent | merge + integrate |
| 3 | P3 (180° rotation) · P9 (replays) | 1 sub-agent + main agent | yes — different regions |
| 4 | P11 (sprint mode) | 1 sub-agent | — |
| 5 | P6 (main.py decomposition) | main agent | — |
| 6 | P13 (shim cleanup) · final README sync · final gate + MCP smoke | main agent | — |

Scope guards per wave prevent merge conflicts; each later wave starts from the
updated main.

---

## P1 — Fix README docs drift  *(wave 1, agent)*

README.md is wrong in at least 6 places. Verified deltas:

1. "two-piece next queue" → **5-piece queue** (`QUEUE_LEN = 5`, NEXT box shows 5).
2. "top 5" high scores → **top 10** (`GameState.MAX = 10`).
3. `~/.local/share/terminal-tetris/scores.json` → unified **`state.json`**
   (scores + settings; `$TETRIS_SCORES` override) — see `state.py`.
4. "79 pytest unit tests" → real count (currently 189; re-count at final sync).
5. Game-over stats claim "time" — **no time is tracked yet**; modal shows
   Score / Lines / Level / Pieces / rank. (Becomes true after P7; re-verify
   at final sync.)
6. Layout section describes the old structure. Mirror `AGENTS.md` layout:
   `pieces.py` (SRS data), `scoring.py`, `board.py`, `engine.py` (pure engine),
   `settings.py`, `state.py` (only I/O module), `stats.py`, `main.py` (curses
   UI), `mcp/` (play-test server), `tests/`.
7. Controls table missing: **Z** (rotate CCW), **?** (help), **S** (settings),
   **H** (scores), **ESC** (back/close/pause). (X for 180° added at P3.)
8. Features list: verify every claim against code; add settings menu, help
   dialog, high-scores dialog with name entry, spawn-glide animation, board
   shake, floating score popups where missing.

Acceptance: zero claims in README that contradict the shipped code.
Scope: `README.md` (+ `src/tetris/mcp/README.md` if stale). No code changes.

## P2 — Frame pacing: drift-corrected 50 fps  *(wave 2, main agent)*

`game_loop` currently ends each iteration with `time.sleep(FRAME)` — actual
cadence is `frame_cost + 20 ms`, jittery on slow terminals.

Design: capture `frame_start = time.monotonic()` at the top of the loop
iteration; after all work (both the static-scene-skip path and the full-draw
path) sleep `max(0.0, FRAME - (time.monotonic() - frame_start))`.

`FakeTime.monotonic()` is controlled by tests so `elapsed ≈ 0` and
`sleep(FRAME)` behaves exactly as today — the UI test suite must pass
unchanged.

Acceptance: `uv run pytest` green; wall-clock cadence measured steady
(50 fps) while playing.

## P3 — 180° rotation (X key) for J/L/S/Z/T  *(wave 3, agent)*

The one missing guideline feature. Design:

- `pieces.py`: add `KICKS_180_JLSTZ: dict[tuple[int, int], list[tuple[int, int]]]`
  for the 180° transitions `(0,2)`, `(2,0)`, `(1,3)`, `(3,1)` (and the
  reverse pairs share the same offset list), SRS y-up convention (the engine
  already flips dy when applying kicks):

      (0, 0), (1, 0), (-1, 0), (0, 1), (1, 1)

  **I piece: no 180° rotation** (guideline excludes it — its 180° is a
  no-op modulo the 4×4 box row). NOTE: this table is the guideline table as
  implemented in guideline-compliant clients; the official PDF was not
  reachable from this network — re-verify against
  `Tetris-Guideline v4.12+` §180° Rotation if/when accessible, and
  unit-test the behavior regardless.
- `engine.py`: new method `rotate_180(self, now: float | None = None) -> bool`:
  - I piece → always `False`.
  - `new_rot = (self.piece.rot + 2) % 4`; try each kick via `_collides(q, new_rot)`;
    on success: update piece, `_register_shift(now)` (180° is a rotate action:
    it refreshes lock delay under the same `LOCK_RESET_MAX` cap).
- `main.py`: `X` / `x` key → `rotate_180(now)` under the same
  `ROTATE_COOLDOWN` throttle as other rotations. Help modal: add
  "X rotate 180" row. `mcp/server.py`: add `"x": b"x"` to `KEYS`.
- Tests: 180° in free space flips in place (0↔2, 1↔3); I piece refused;
  floor/wall kick cases from the table; lock-delay reset on 180°; UI test:
  X key rotates (fake-screen, pattern from `tests/test_ui.py`).

Acceptance: quality gate green; MCP smoke: X visibly rotates a T on the board.

## P4 — DAS/ARR as settings  *(wave 1a + wave 2b)*

Input feel is the most-tuned knob in Tetris; currently hardcoded
(`DAS = 0.17`, `ARR = 0.04` in `main.py`).

- **P4a (agent, wave 1 — `settings.py`/`state.py` only):** new `Settings`
  fields `das: float = 0.17`, `arr: float = 0.04` (specs in P8/P12 below).
- **P4b (main agent, wave 2 — `main.py`):** `KeyReader` takes das/arr per
  call (or reads them in the loop): replace the constants in
  `on_direction` / `auto_direction` with the session's setting values
  (`state.settings.das` / `.arr`). Keep the constants as fallback defaults.
  Help modal: note the new options live under `S`.
- Tests: settings-side tests in wave 1; a UI test that a custom ARR changes
  the number of streamed moves over a held window (fake time) — or at least
  assert the loop reads settings (unit level is fine).

Acceptance: changing DAS/ARR in the settings menu measurably changes
streaming behavior; gate green.

## P5 — Engine version counter (robust render-skip)  *(wave 1a + wave 2b)*

`main.py`'s static-scene key is a 15-field tuple whose correctness depends on
remembering every mutation site (the in-code comment admits the coupling).

- **P5a (agent, wave 1 — engine only):** `Tetris.version: int`, starts 0,
  bumped on every observable change: successful `move`/`rotate`/`rotate_180`/
  `soft_drop`/`hard_drop`/`hold`, gravity step in `tick`, `_lock`,
  `_commit_clears`, `game_over` set, and `paused` flips (make `paused` a
  property with a bumping setter so `t.paused = X` keeps working).
- **P5b (main agent, wave 2):** scene key collapses to
  `t.version` + piece position + effects + menu/settings state.
- Tests: version bumps per action; failed move does NOT bump.

Acceptance: UI tests green (render-skip still triggers on static frames);
scene tuple no longer enumerates engine internals.

## P6 — Decompose `main.py` (1020 lines)  *(wave 5, main agent)*

Pure refactor — behavior and rendering identical, **all existing tests pass
unchanged** (at most trivial import updates in tests).

Target layout (flat modules, matching the existing package style):

- `src/tetris/ui_input.py` — `KeyReader`, ESC-sequence reassembly,
  DAS/ARR/rotate-cooldown constants.
- `src/tetris/ui_render.py` — colors/init (`init_colors`, `build_attrs`,
  THEMES application), board/panel/box/preview/modal draw functions,
  `Modal` + modal builders, drawn-geometry constants
  (`BOARD_PITCH`, `NEED_W`, …).
- `src/tetris/ui_session.py` — `Session`: owns `Tetris`, `GameState`,
  `KeyReader`, `Effects`, modal/menu state, name entry; methods
  `on_frame(stdscr, now)` (input → tick → effects → render),
  `reset_game`, `open_menu`/`close_menu`, `commit_name`.
- `main.py` shrinks to: `game_loop` (curses init, session loop, quit),
  `main()`; keep re-exports needed by tests (`BOARD_H`, etc.).

Constraints: `from tetris import main` + `main.game_loop(stdscr)` signatures
unchanged; FakeScreen protocol unchanged; stats panel pixel positions
unchanged (MCP contract); `STAT_LABELS` untouched.

Acceptance: `just check` + full pytest green; `main.py` < ~150 lines; MCP
smoke green (screen text identical in shape to pre-refactor for a scripted
key run — compare a few frames via `tetris_screen`).

## P7 — Session stats + game time  *(wave 1a + wave 2b + wave 6c)*

- **P7a (agent, wave 1 — engine):** `play_time: float` accumulated in
  `tick()` (design: count real elapsed time when not paused, including clear
  flashes; NOT during pause/game over; no jump on resume because the
  last-tick timestamp keeps advancing while paused). Exposed via
  `snapshot()` as `"time": float` (widen `snapshot` return annotation to
  `dict[str, int | bool | float]`; propagate to `stats.sidebar_stats`).
- **P7b (main agent, wave 2):** game-over modal gains `Time MM:SS` (format
  helper in `stats.py` or the modal builder); track `best_combo` in the
  engine (max of running combo) and show it too.
- **P7c (main agent, wave 6):** persist `time` and `best_combo` on the
  high-score entry (`state.record` signature extension — additive JSON keys;
  legacy entries without them stay valid).

Acceptance: engine tests for time accumulation (pause/frozen/resume cases);
game-over modal shows time; entry JSON round-trips.

## P8 — Settings genericity (kill the if-chains)  *(wave 1, agent)*

`settings.value_of`, `settings.cycle`, and `state.update_settings` each
contain per-key if/else chains. Design:

- `Option.values` becomes the single source of truth (already is for menu
  order); widen to `tuple[int | bool | str, ...]`.
- `value_of(settings, key) -> int | bool | str`: validate via `option(key)`
  then `getattr(settings, key)` — no per-key branching.
- Generic application: `with_value(settings, key, value) -> Settings`
  via `dataclasses.replace(settings, **{key: value})` — used by `cycle`
  and by `state.update_settings` (after type-aware coercion).
- Coercion rules (shared, in `settings.py`): `start_level` int clamped
  1..MAX_START_LEVEL (bool rejected); bools must be bool; floats must be
  float **and in the option's allowed values** (exact membership — JSON
  round-trips 0.17 exactly); str must be in allowed values; unknown keys
  ignored; wrong type → keep current/default (never raise).
- `format_value`: `True→"on"`, `False→"off"`, str passthrough, int→str.

Behavior must stay bit-identical for existing options (cycle wrap order,
clamping, ignore-unknown). Acceptance: existing + new settings/state tests
green; no per-key if/else chain remains outside the `_coerce` metadata.

## P9 — Replays: seed + input log + playback  *(wave 3, main agent)*

The engine is already deterministic with an injected RNG — replays are cheap.

- **Determinism (P9a, wave 1, part of engine-core agent):** `Tetris.__init__`
  always uses `self._rng = rng or random.Random()`; `_refill` always shuffles
  with `self._rng` (drop the global-`random` branch). Tests that seeded the
  global RNG get an explicit `rng=random.Random(seed)` instead.
- **Session (wave 3):** the UI creates one `seed` per game
  (`int(time.time()) % 2**32` — stable across restarts of a session is not
  required; document it) and `Tetris(rng=random.Random(seed),
  start_level=...)`. Every **accepted-intent** player action is logged as
  `(t_rel, token)` with tokens `L R U Z D S H C X`
  (S = soft drop, H = hard drop; log the attempt, not the result — rejected
  attempts replay identically).
- **Save:** on game over, append
  `{seed, start_level, das, arr, ts, score, lines, events}` to
  `replays.json` next to `state.json` (via `state.py` — the only I/O module),
  keeping the 5 most recent. Save every completed game.
- **UI:** game-over modal shows `Seed: <n>` and a `G` line ("G replay last
  game"). `G` starts a replay run: fresh engine with same seed/settings,
  events re-fed at their original timestamps (1× speed, real time), a dim
  "REPLAY" tag near the board; `ESC` aborts back to the game-over modal.
  `mcp/server.py`: add `"g"` to KEYS.
- **Tests (engine-level):** two engines with the same seed + same scripted
  `(now, action)` sequence produce identical final score/board/queue;
  a recorded-then-played sequence round-trips (unit, no real time).

Acceptance: play a game via MCP, note the seed at game over, press G, watch
the identical game replay; `just check` + pytest green.

## P10 — CI workflow  *(wave 1, agent)*

No `.github/workflows` exists; gates run only via local pre-commit hooks.

`.github/workflows/ci.yml`: ubuntu-latest; checkout; `astral-sh/setup-uv@v4`
(python 3.12); `uv sync` (verify in `pyproject.toml` which extras the tests
need — the MCP server requires `mcp` + `pyte`; if any test imports
`tetris.mcp`, dev deps must include them); then run the gate **directly**
(do not depend on `just` being installed):

```yaml
- run: uv run ruff check src tests
- run: uv run mypy --strict src/tetris
- run: uv run pytest
```

Acceptance: YAML valid; commands match `just check` + `just test` exactly.

## P11 — Sprint mode (10-line time attack)  *(wave 4, agent)*

Adds replay value. Design:

- **Settings:** `mode: str = "classic"`, values `("classic", "sprint")`,
  label "mode".
- **Engine:** sprint config: `SPRINT_LINES = 10`, `SPRINT_TIME = 180.0`.
  New state: `won: bool = False`, `time_left: float | None = None`
  (None in classic). `tick()`: countdown when not paused; `time_left <= 0`
  → game over (loss). `lines >= SPRINT_LINES` → `won = True`, game over
  (win). Snapshot exposes `won`, `time_left`, `sprint: bool`.
- **UI:** in sprint mode the stats panel shows `TIME` (M:SS, countdown) in
  place of the SPINS row — implement via `stats.panel_stats(snapshot,
  sprint)` (classic `STAT_LABELS` unchanged — MCP contract preserved).
  `time_left <= 30 s`: blink the TIME value. Game over: win → modal
  "SPRINT CLEARED" with `Time`, `Best time`, `Score`; loss → "TIME UP" with
  `Lines 7/10`.
- **Persistence:** sprint does NOT write the score high-score table (clean
  semantics). `state.py` gains `record_sprint(time_s: float) -> tuple[bool,
  float | None]` (True if new best) storing `{"sprint": {"best_time": float,
  "date": str}}` in the unified file (additive; tolerant read).
- **MCP:** update the stats parser to read TIME when present (additive label).
  `mcp/server.py` KEYS unchanged (no new keys).
- **Tests:** countdown to loss; win at 10 lines; no time write to score
  table; sprint best recorded and shown; classic mode unchanged (all existing
  tests green).

Acceptance: MCP playthrough: switch mode to sprint in settings, play to win
or lose, verify modal + best time persistence across a restart.

## P12 — Themes  *(wave 1a + wave 2b)*

- **P12a (agent, wave 1 — settings only):** `theme: str = "classic"`,
  values `("classic", "mono", "vivid")`, label "theme" (per P8 spec).
- **P12b (main agent, wave 2):** new `src/tetris/themes.py` (pure data):
  `THEMES: dict[str, dict[str, int]]` — name → {piece kind → curses base fg}
  plus per-theme text/highlight/flash pair assignments:
  - `classic` — current palette (I cyan, O yellow, T magenta, S green,
    Z red, J blue, L white).
  - `mono` — all cells white; dim variants via attrs; text white.
  - `vivid` — distinct bright remap (e.g. I blue, O yellow, T white,
    S green, Z red, J cyan, L magenta).
  `init_colors(theme: str)` builds pairs from the theme; changing the theme
  setting re-inits pairs + `build_attrs()` live (curses allows re-initing a
  pair). Border/bg pairs stay as-is (gray 231 / near-black 235) — themes only
  recolor pieces/text/highlights.
- Acceptance: cycle the theme in the settings menu while playing; screen
  recolors without restart; gate green; MCP smoke (screen parses).

## P13 — Legacy shim cleanup  *(wave 6, main agent)*

- Delete `src/tetris/scores.py` (re-export shim). Keep the `HighScores =
  GameState` alias in `state.py` (public name, 1 line).
- Retire the `src/tetris/game.py` facade: point all internal imports at the
  real modules (`engine`, `pieces`, `scoring`, `settings`, `state`) —
  `main.py`, `tests/`, `mcp/server.py`. (AGENTS.md's layout already omits
  both shims.) Breaking for external `from tetris.game import …` consumers —
  accepted; note in README/changelog at final sync.
- Re-count tests; update README's "Project layout" + test count.

Acceptance: no references to `tetris.scores` or `tetris.game` in
`src/`, `tests/`; gate green.

---

## Explicit non-goals (the "Skip" list)

- **Local 2-player multiplayer** — high complexity, dubious value for a
  solo terminal game.
- **CPU micro-optimization of rendering** — measured headroom is 4+ orders
  of magnitude beyond need; the scene-skip optimization is already in.
- **Exotic-terminal acrobatics** — ESC handling already fixed (25 ms
  `set_escdelay` + app-level reassembly).

## Definition of Done (whole plan)

- [ ] P1–P13 all implemented and merged
- [ ] `just check` + `uv run pytest` green on the final tree
- [ ] README re-synced against final behavior (counts, controls, layout,
      features incl. sprint/themes/replays/180°)
- [ ] MCP `KEYS` covers all player keys; full MCP smoke test green
- [ ] Every feature has engine- or UI-level test coverage
- [ ] Working tree clean; milestone commits with imperative messages

## Progress log

- [x] **P1** README docs drift fixed (queue 5, top-10, state.json, 195 tests,
      no time stat, correct layout, full controls table). — merged 02b3965
- [x] **P10** CI workflow `.github/workflows/ci.yml` (setup-uv@v4, ruff + mypy
      --strict + pytest). — merged 02b3965
- [x] **P2** Drift-corrected 50fps frame pacing (`sleep_to_frame`). — merged 6685d35
- [x] **P12a** Theme data module `themes.py` (classic/mono/vivid). — merged 6758d0b
- [x] **P8** Settings genericity (generic value_of/cycle/with_value, shared
      coerce_setting). — merged 5dda86c (208 tests)
- [x] **P4a** DAS/ARR + theme as settings (values + labels). — merged 5dda86c
- [x] **P4b/P12b** DAS/ARR + themes wired into main.py (live reader.das/arr,
      theme-driven init_colors, settings sync on change). — merged 9703051
- [x] **P5** Engine version counter (every observable mutation bumps `version`).
      — merged 9703051
- [x] **P7** Session stats + game time (`play_time`, `best_combo`, `time` on
      score entries, `best_combo` param on record, TIME in stats panel +
      game-over modal). — merged 9703051
- [x] **P9** Replays: per-game seed (P9a RNG made injectable/deterministic),
      timestamped input log saved to `replays.json` (5 most recent), `G` at
      game over replays the last saved game at 1× with a `REPLAY` tag, ESC
      aborts. Verified live via MCP (seed + G hint in modal, replay runs and
      restores the game-over screen). — merged 61abd53
- [ ] **P3** 180° rotation (X) — in progress (wave 3 agent)
- [ ] **P11** Sprint mode — pending (wave 4)
- [ ] **P6** Decompose main.py — pending (wave 5)
- [ ] **P13** Legacy shim cleanup — pending (wave 6)

### Lessons / gotchas discovered

- **G vs. name entry:** `g` is a valid high-score name character, so `G`
  replay must NOT be a shortcut *during* name entry (it would eat the typed
  letter). G replays only on the settled game-over screen (`not
  name_awaiting`), and the name-entry modal omits the G hint.
- **Rigged test Tetris subclasses** must accept the new `rng` kwarg
  (`main.py` now constructs `Tetris(rng=random.Random(seed), ...)`); all six
  custom `__init__(self, start_level=...)` overrides were widened to
  `__init__(self, rng=None, start_level=1)` and pass `rng` through to
  `super()`.
- **Replay timestamps are game-relative real time** (`now - game_start`). If
  the player idles before their first input (common when driving via MCP,
  where tool-call latency adds ~15-20s), the replay faithfully reproduces
  that idle (pieces fall by gravity) before the input burst. Correct, not a
  bug.
- **MCP `tetris_start` docstring / `KEYS`** must list new keys (`x`, `g`) and
  the server must be restarted to pick up `KEYS` changes (long-lived uvx
  process); game-code changes are picked up on the next `tetris_start`.
