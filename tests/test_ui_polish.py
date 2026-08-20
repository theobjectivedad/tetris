"""UI-polish acceptance tests — the spec for the in-flight ``main.py`` work.

A colleague is implementing four UI-polish features in ``src/tetris/main.py``
in parallel with this file:

* ESC-key modal navigation (close the help/settings/scores modals, pause the
  game in open play, unpause from the PAUSED modal) — including the
  regression that a lone ESC must not swallow the key that follows it.
* a HIGH SCORES modal (``h`` opens it; the game is paused while it is up;
  lists the top 10 entries).
* game-over name entry: a top-10 score gets an ``ENTER YOUR NAME:`` prompt in
  the GAME OVER modal; printable chars append (max 10), Backspace deletes,
  Enter (or R/Q) commits, and the modal then shows the ranked
  ``★ #n — NAME ★`` line.
* aligned modal builders: ``build_scores_modal``, ``build_settings_modal``,
  ``build_help_modal`` and ``build_game_over_modal`` with fixed-width rows.

These tests are the acceptance spec. They intentionally FAIL against the
current ``main.py`` (ESC is inert, there is no ``h`` key, there is no name
entry, and the new/changed builders do not exist); they must pass
unmodified once the implementation lands.

The ``build_*`` functions are imported inside the unit tests that need them
so the game_loop-driven tests still collect while ``main.py`` lacks them.
"""

import json
import re
from pathlib import Path

from test_ui import (
    FakeScreen,
    grid_to_text_for_frame,
    piece_cols,
    piece_cols_from_grid,
    run_game,
)

from tetris import main
from tetris.settings import Settings
from tetris.state import GameState

ESC = 27
ENTER = 13
BACKSPACE = 127


def _frames_text(scr: FakeScreen, lo: int, hi: int) -> str:
    """Join the rendered frames ``[lo, hi)`` into one text blob."""
    return "\n".join(grid_to_text_for_frame(f, scr.cols) for f in scr.frames[lo:hi])


def _final_text(scr: FakeScreen) -> str:
    """Flatten the run's final frame into text."""
    return grid_to_text_for_frame(scr.frames[-1], scr.cols)


def _seed_scores(path: Path, entries: list[dict[str, object]]) -> None:
    """Write a unified state file (``{"scores": [...], "settings": {}}``)."""
    path.write_text(json.dumps({"scores": entries, "settings": {}}))


def _over_tetris(monkeypatch, score: int = 5000, once: bool = False) -> None:
    """Rig ``main.Tetris`` so the game starts already over at ``score``.

    With ``once=True`` only the first instance is rigged (later games —
    e.g. after an R replay — run normally).
    """
    created: list[main.Tetris] = []

    class OverTetris(main.Tetris):
        def __init__(self, start_level: int = 1) -> None:
            super().__init__(start_level=start_level)
            created.append(self)
            if once and len(created) > 1:
                return
            self.game_over = True
            self.score = score

    monkeypatch.setattr(main, "Tetris", OverTetris)


# -- A. ESC navigation --------------------------------------------------------


def test_esc_closes_help_modal(monkeypatch, tmp_path) -> None:
    """ESC closes the HELP modal: it is open mid-run and gone by the end."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("?")), (0.6, ESC)], duration=1.0)
    assert "HELP" not in _final_text(scr)


def test_esc_closes_settings_modal(monkeypatch, tmp_path) -> None:
    """ESC closes the SETTINGS modal (as Q does, without quitting)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("s")), (0.6, ESC)], duration=1.0)
    assert "SETTINGS" not in _final_text(scr)


def test_esc_closes_scores_modal(monkeypatch, tmp_path) -> None:
    """ESC closes the HIGH SCORES modal opened with h."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("h")), (0.6, ESC)], duration=1.0)
    assert "HIGH SCORES" not in _final_text(scr)


def test_esc_in_play_pauses(monkeypatch, tmp_path) -> None:
    """A lone ESC in open play pauses the game (PAUSED modal, t 0.4-0.9s)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ESC)], duration=2.0)
    assert "PAUSED" in _frames_text(scr, 20, 40)


def test_esc_acting_is_snappy(monkeypatch, tmp_path) -> None:
    """Regression: a lone ESC must act within ~120ms, not the old ~1s.

    Before the fix, a bare ESC on a real pty took ~1.1s to act: ncurses
    waits its 1000ms default ESCDELAY after a bare ESC before returning
    it, and the 150ms app-level ESC_TTL added on top. This harness models
    the app level on the 0.02s frame grid: a lone ESC pressed at t 0.3s
    must have the PAUSED modal visible on a frame within 120ms of the
    press (frames 18-20 = t 0.36-0.40s). With the old ESC_TTL = 0.15 the
    pending ESC was only emitted at t 0.46s (frame 23), so this window
    fails on the old code; with ESC_TTL = 0.06 the ESC is emitted at
    t 0.36-0.38s (frames 18-19)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ESC)], duration=2.0)
    assert "PAUSED" in _frames_text(scr, 18, 21)


def test_static_modal_keeps_frame_cadence_and_content(monkeypatch, tmp_path) -> None:
    """Perf invariant around the static-scene skip: while the settings
    modal sits open with no input, (a) the loop still refreshes every
    frame — the suite indexes frames by time (i * 0.02s), so the cadence
    must hold even when erase/draw are skipped — and (b) the rendered
    content is identical from the first open frame to the last (no
    drift, no stale redraw artifacts)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("s"))], duration=2.0)
    texts = [grid_to_text_for_frame(f, scr.cols) for f in scr.frames]
    # The modal opens at t=0.3 (frame 15) and stays open to the end of
    # the run (the terminating q at t=2.0 closes it on the final frame).
    open_idx = [i for i, t in enumerate(texts) if "SETTINGS" in t]
    assert open_idx, "settings modal never rendered"
    first, last = open_idx[0], open_idx[-1]
    assert last - first >= 40, "modal did not stay open (drift? early close?)"
    # Every frame while the modal is open renders the identical content.
    assert all(t == texts[first] for t in texts[first + 1 : last + 1])


def test_esc_while_paused_resumes(monkeypatch, tmp_path) -> None:
    """ESC while the PAUSED modal is up resumes the game: PAUSED is shown
    while paused (t 0.4-0.9s) and gone after the ESC at t 0.6."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("p")), (0.6, ESC)], duration=2.0)
    assert "PAUSED" in _frames_text(scr, 20, 40)
    assert "PAUSED" not in _frames_text(scr, 55, 80)


def test_lone_esc_and_next_key_both_delivered(monkeypatch, tmp_path) -> None:
    """Regression: the old KeyReader swallowed the key following a lone ESC.
    The ESC at t 0.4 must pause the game, and the 'p' at t 0.8 must still be
    delivered (unpausing shortly after)."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.4, ESC), (0.8, ord("p"))], duration=2.0)
    # The 'p' (t 0.8) is processed on the frame at t 0.8, so the pause must
    # still be visible on the frames before it — a window, not one frame.
    assert "PAUSED" in _frames_text(scr, 20, 40)
    # And the 'p' was not swallowed: the game is resumed well after it.
    assert "PAUSED" not in _frames_text(scr, 55, 80)


def test_split_arrow_sequence_still_works(monkeypatch, tmp_path) -> None:
    """A split arrow sequence (ESC [ C) must still reassemble into one RIGHT
    key — the new lone-ESC handling must not pause on its 27 byte."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    base = run_game(events=[])
    base_cols = piece_cols(base)

    # ESC, '[', 'C' delivered one frame apart (split across reads).
    split = run_game(events=[(0.5, 27), (0.52, 0x5B), (0.54, ord("C"))])
    assert piece_cols(split) == {c + 1 for c in base_cols}


# -- B. HIGH SCORES modal -----------------------------------------------------


def test_scores_modal_lists_top_10_with_names(monkeypatch, tmp_path) -> None:
    """h opens the HIGH SCORES modal listing exactly the top 10 of 12
    seeded entries: header, the top-10 names, ESC close; A1/A2 cut."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    # Ai has score 100*i; stored highest-first (as record() maintains).
    entries = [
        {"name": f"A{i}", "score": 100 * i, "lines": 1, "level": 1,
         "date": "2026-07-01 10:00"}
        for i in range(12, 0, -1)
    ]
    _seed_scores(path, entries)

    scr = run_game(events=[(0.3, ord("h"))], duration=1.0)
    text = _frames_text(scr, 40, 60)
    assert "HIGH SCORES" in text
    assert "NAME" in text  # header row
    assert "ESC close" in text
    for i in range(3, 13):  # top-10 entries: scores 300..1200
        assert f"A{i}" in text
    # The two lowest entries (A1/A2) must be cut from the top-10 listing.
    # Match the full 10-char name field so "A1" can't hit inside "A12".
    assert "A1" + " " * 8 not in text
    assert "A2" + " " * 8 not in text


def test_scores_modal_empty_board(monkeypatch, tmp_path) -> None:
    """With no scores stored the modal shows the empty-board line."""
    monkeypatch.setenv("TETRIS_SCORES", str(tmp_path / "state.json"))

    scr = run_game(events=[(0.3, ord("h"))], duration=1.0)
    assert "No scores yet" in _frames_text(scr, 40, 60)


def test_build_scores_modal_row_format(tmp_path) -> None:
    """build_scores_modal: exact header and row formats — 39-char entry
    rows in score-desc order, em dash for an empty name, blank + footer."""
    from tetris.main import build_scores_modal

    path = tmp_path / "state.json"
    entries = [
        {"name": "ZED", "score": 900, "lines": 1, "level": 3,
         "date": "2026-07-01 10:00"},
        {"name": "", "score": 500, "lines": 1, "level": 2,
         "date": "2026-07-01 10:00"},
        {"name": "BOB", "score": 100, "lines": 1, "level": 1,
         "date": "2026-07-01 10:00"},
    ]
    _seed_scores(path, entries)

    m = build_scores_modal(GameState(path))
    assert m.title == "HIGH SCORES"
    assert len(m.lines) == 6  # header + 3 rows + "" + footer
    assert m.lines[0] == f"{'#':>2}  {'NAME':<10}{'SCORE':>9}{'LVL':>4}  DATE"
    for line in m.lines[1:4]:
        assert len(line) == 39
    assert m.lines[1] == f"{1:>2}  {'ZED':<10}{900:>9,}{3:>4}  {'2026-07-01'}"
    assert m.lines[2] == f"{2:>2}  {'—':<10}{500:>9,}{2:>4}  {'2026-07-01'}"
    assert m.lines[4] == ""
    assert m.lines[5] == "ESC close"


def test_scores_modal_opens_with_h_and_pauses(monkeypatch, tmp_path) -> None:
    """h opens the scores modal and the game is frozen while it is open:
    the piece's columns in a mid-run frame equal a fresh no-menu run's."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))

    by = max(1, (40 - 27) // 2)  # must match main.game_loop's layout math
    bx = (60 - 60) // 2 + 20  # board origin: stats (16) + gap (4)

    with_scores = run_game(events=[(0.3, ord("h"))], duration=2.0)
    assert "HIGH SCORES" in _frames_text(with_scores, 40, 60)
    paused_cols = piece_cols_from_grid(with_scores.frames[50], by, bx)

    fresh = run_game(events=[], duration=0.3)
    assert paused_cols == piece_cols(fresh)


# -- C. Name entry at game over -------------------------------------------------


def test_name_entry_flow(monkeypatch, tmp_path) -> None:
    """Name entry: chars append, Backspace (127) deletes, Enter commits.
    Mid-entry the modal shows ``ENTER YOUR NAME: AD█``; after Enter the
    ranked line ``★ #1 — AD ★``; the entry is saved to the state file."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    _over_tetris(monkeypatch, score=5000)  # fresh file -> rank 0

    # A D A, backspace -> "AD", Enter commits.
    scr = run_game(
        events=[
            (0.5, ord("A")),
            (0.6, ord("D")),
            (0.7, ord("A")),
            (0.8, BACKSPACE),
            (0.9, ENTER),
        ],
        duration=1.5,
    )
    # Backspace (t 0.8) applied, Enter (t 0.9) not yet: 5-frame window
    # around frame 43 (t 0.85).
    assert "ENTER YOUR NAME: AD█" in _frames_text(scr, 41, 46)
    # After Enter the committed ranked line is shown (window around t 1.0).
    assert "★ #1 — AD ★" in _frames_text(scr, 48, 53)

    data = json.loads(path.read_text())
    assert data["scores"][0]["name"] == "AD"


def test_name_entry_caps_at_ten(monkeypatch, tmp_path) -> None:
    """Names are capped at 10 chars: typing 15 chars commits the first 10."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    _over_tetris(monkeypatch, score=5000)

    letters = [ord(c) for c in "abcdefghijklmno"]  # 15 chars
    events = [(0.2 + i * 0.05, key) for i, key in enumerate(letters)]
    events.append((1.0, ENTER))  # a..o land at t 0.2..0.9; commit at 1.0

    run_game(events=events, duration=1.5)

    data = json.loads(path.read_text())
    name = data["scores"][0]["name"]
    assert len(name) == 10
    assert name == "abcdefghij"


def test_name_entry_r_commits_and_replays(monkeypatch, tmp_path) -> None:
    """R during name entry commits the typed name first, then starts a
    fresh game: the state file holds "TE" and late frames show a new
    game (SCORE 0) with the GAME OVER modal gone."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    _over_tetris(monkeypatch, score=5000, once=True)  # replayed game runs real

    scr = run_game(
        events=[(0.5, ord("T")), (0.6, ord("E")), (0.7, ord("r"))],
        duration=1.5,
    )
    late = _frames_text(scr, 55, 80)
    assert "SCORE  0" in late
    assert "GAME OVER" not in late

    data = json.loads(path.read_text())
    assert data["scores"][0]["name"] == "TE"


def test_no_name_entry_for_unranked_score(monkeypatch, tmp_path) -> None:
    """A score outside the top 10 gets no name entry: the game-over modal
    shows the best-score line instead of ENTER YOUR NAME."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("TETRIS_SCORES", str(path))
    # 10 existing scores far above the rigged 5000 -> the game ranks out.
    entries = [
        {"name": f"B{i}", "score": 1_000_000 + i, "lines": 1, "level": 9,
         "date": "2026-07-01 10:00"}
        for i in range(10)
    ]
    _seed_scores(path, entries)
    _over_tetris(monkeypatch, score=5000)

    scr = run_game(events=[], duration=1.5)
    early = _frames_text(scr, 5, 40)
    assert "GAME OVER" in early
    assert "ENTER YOUR NAME" not in early
    assert "ENTER YOUR NAME" not in _frames_text(scr, 45, 75)
    assert "Best:   1,000,009" in early


# -- D. Alignment unit tests (no screen) ---------------------------------------


def test_settings_rows_fixed_width() -> None:
    """build_settings_modal: the five option rows are exactly 22 chars with
    the value right-aligned in the last 8 columns; blank + ESC footer."""
    from tetris.main import build_settings_modal

    m = build_settings_modal(Settings(), 0)
    assert m.title == "SETTINGS"
    assert all(len(line) == 22 for line in m.lines[0:5])
    assert m.lines[0] == f"{'start level':<14}{1:>8}"
    assert m.lines[0][14:22] == "       1"
    assert m.lines[1] == f"{'drop shadow':<14}{'on':>8}"
    assert m.lines[1][14:22] == "on".rjust(8)
    assert m.lines[5] == ""
    assert "ESC close" in m.lines[6]


def test_help_modal_content() -> None:
    """build_help_modal: the key legend, the SCORING reference section, and
    the version line — every row within 35 chars, 32-dash separators."""
    from tetris.main import build_help_modal

    m = build_help_modal()
    assert m.title == "HELP"
    text = "\n".join(m.lines)
    for needle in ("S settings", "Q quit", "SCORING", "COMBO", "B2B", "SPINS"):
        assert needle in text
    assert re.search(r"v\d", text)
    assert all(len(line) <= 35 for line in m.lines)
    # Separator row: 32 dashes (ASCII '-' or box-drawing '\u2500').
    assert any(line in ("-" * 32, "\u2500" * 32) for line in m.lines)


def test_game_over_modal_lines() -> None:
    """build_game_over_modal: fixed Score/Lines/Pieces rows, the rank/name
    row variants, and the Best row only for an unranked game."""
    from tetris.main import build_game_over_modal

    t = main.Tetris()
    t.score = 1234

    m = build_game_over_modal(t, 999, None)
    assert m.title == "GAME OVER"
    assert m.lines[0] == f"Score   {t.score:,}"
    assert any("R replay" in line for line in m.lines)
    assert f"Best:   {999:,}" in m.lines

    m2 = build_game_over_modal(t, 999, 2, name="BOB", name_awaiting=False)
    assert "★ #3 — BOB ★" in m2.lines
    assert not any(line.startswith("Best:") for line in m2.lines)

    m3 = build_game_over_modal(t, 999, 2, name="BO", name_awaiting=True)
    assert "ENTER YOUR NAME: BO█" in m3.lines
