"""Spawn animation tests: each new piece glides from the head of the NEXT box
into its spawn position over ~150ms.

Drives the real game_loop through the shared harness in ``test_ui`` (fake
screen + fake clock). The board geometry is derived from the rendered frames
themselves — the board's top border is the only 22-wide run of "█" on the
screen — so these tests do not depend on the FakeScreen row count.
"""

import random

from test_ui import grid_to_text_for_frame, run_game

from tetris import engine

# Drawn width of the board block (walls included).
BOARD_DRAWN_W = 22


def _board_origin(frame: dict[tuple[int, int], str]) -> tuple[int, int] | None:
    """(top_row, left_col) of the board's top border in a frame, or None.

    The top border is the topmost row containing a run of at least
    BOARD_DRAWN_W consecutive "█"; nothing else on screen draws a solid run
    that wide (the sidebar boxes use "│" / "─" borders).
    """
    rows: dict[int, list[int]] = {}
    for (y, x), ch in frame.items():
        if ch == "█":
            rows.setdefault(y, []).append(x)
    for y in sorted(rows):
        cols = sorted(rows[y])
        run_start = cols[0]
        for i, c in enumerate(cols):
            if i == 0 or c != cols[i - 1] + 1:
                if i > 0 and cols[i - 1] - run_start + 1 >= BOARD_DRAWN_W:
                    return y, run_start
                run_start = c
        if cols[-1] - run_start + 1 >= BOARD_DRAWN_W:
            return y, run_start
    return None


def _gap_has_block(frame: dict[tuple[int, int], str], bx: int) -> bool:
    """True if a solid block is drawn in the 4-col gap between the board's
    right wall (col bx + BOARD_DRAWN_W - 1) and the sidebar box's left
    border (col bx + BOARD_DRAWN_W + 4). The NEXT previews sit inside the
    sidebar box, so in a settled frame this gap is always empty."""
    lo = bx + BOARD_DRAWN_W
    hi = lo + 3
    return any(ch == "█" for (y, x), ch in frame.items() if lo <= x <= hi)


def _top_cell_area_has_block(
    frame: dict[tuple[int, int], str], by_row: int, bx: int
) -> bool:
    """True if a solid block is drawn in the top of the board's cell area.

    The window (rows by+2..by+5, cols bx+2..bx+18) excludes every position
    the 1-col board walls / top border can occupy during a hard-drop shake
    (ox/oy in {-1, 0, 1}), and the ghost is "▒", not "█". A freshly spawned
    piece (grid x=3..6, y<=2) always covers this area when drawn at its
    grid position."""
    return any(
        ch == "█"
        for (y, x), ch in frame.items()
        if by_row + 2 <= y <= by_row + 5 and bx + 2 <= x <= bx + 18
    )


def test_spawn_seq_starts_at_one_and_increments() -> None:
    """The engine exposes a pure spawn counter the UI can key off."""
    assert engine.Tetris().spawn_seq == 1
    t = engine.Tetris(rng=random.Random(7))
    assert t.spawn_seq == 1
    t.hard_drop()  # locks the piece and spawns the next one
    assert t.spawn_seq == 2


def test_mid_flight_piece_is_drawn_in_gap(monkeypatch) -> None:
    """Mid-glide, the live piece is hidden from the board and the flying
    piece crosses the gap between the board's right wall and the sidebar."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_spawn_anim_mid.json")
    scr = run_game(events=[(0.5, ord(" "))], duration=2.0)

    origin = _board_origin(scr.frames[-1])  # a settled late frame
    assert origin is not None, "board top border not found"
    by_row, bx = origin

    # The hard drop lands at t≈0.5 (20ms frame grid; ±1 frame of clock
    # drift), and the 0.15s glide runs until ≈0.65.
    window = [i for i in range(len(scr.frames)) if 0.52 <= i * 0.02 <= 0.62]
    # (a) The flying piece crosses the board/sidebar gap.
    assert any(_gap_has_block(scr.frames[i], bx) for i in window), (
        "flying piece never drawn in the board/sidebar gap "
        f"(frame dump: {grid_to_text_for_frame(scr.frames[window[2]], scr.cols)})"
    )
    # (b) The live piece is hidden while it is in flight: at least one
    # mid-flight frame has the top of the cell area free of solid blocks
    # (without the animation the just-spawned piece is drawn at its grid
    # position on every frame).
    assert any(
        not _top_cell_area_has_block(scr.frames[i], by_row, bx) for i in window
    ), "live piece not hidden from the board during the glide"


def test_settled_piece_is_not_drawn_in_gap(monkeypatch) -> None:
    """After the glide, the piece sits at its grid position: the gap is
    empty (no double-draw) while the live piece is on the board."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_spawn_anim_settled.json")
    scr = run_game(events=[(0.5, ord(" "))], duration=2.0)

    origin = _board_origin(scr.frames[-1])
    assert origin is not None, "board top border not found"
    by_row, bx = origin

    for i, f in enumerate(scr.frames):
        if not 0.7 < i * 0.02 < 0.9:
            continue
        assert not _gap_has_block(f, bx), f"gap occupied at t≈{i * 0.02:.2f}"
        # The live piece sits at its grid position in the top of the board
        # (the just-spawned piece, y<=2), so the empty gap above is a
        # genuine "no double-draw" signal.
        assert _top_cell_area_has_block(f, by_row, bx), (
            "live piece not visible at its grid position"
        )


def test_first_piece_glides_from_next_box(monkeypatch) -> None:
    """The very first piece also glides in from the NEXT box head."""
    monkeypatch.setenv("TETRIS_SCORES", "/tmp/test_tetris_spawn_anim_first.json")
    scr = run_game(events=[], duration=1.0)

    origin = _board_origin(scr.frames[-1])
    assert origin is not None, "board top border not found"
    by_row, bx = origin

    window = [i for i in range(len(scr.frames)) if 0.02 <= i * 0.02 <= 0.12]
    assert any(_gap_has_block(scr.frames[i], bx) for i in window), (
        "first piece never drawn in the gap during its glide "
        f"(frame dump: {grid_to_text_for_frame(scr.frames[3], scr.cols)})"
    )
    assert any(
        not _top_cell_area_has_block(scr.frames[i], by_row, bx) for i in window
    ), "first piece not hidden from the board during its glide"
