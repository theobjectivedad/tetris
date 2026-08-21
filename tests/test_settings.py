"""Tests for the pure settings model (tetris.settings)."""

from __future__ import annotations

import pytest

from tetris.pieces import MAX_START_LEVEL
from tetris.settings import (
    OPTIONS,
    Settings,
    cycle,
    format_value,
    from_dict,
    option,
    value_of,
    with_value,
)


def test_defaults() -> None:
    s = Settings()
    assert s.start_level == 1
    assert s.ghost is True
    assert s.hold is True
    assert s.sound is True
    assert s.shake is True
    assert s.das == 0.17
    assert s.arr == 0.04
    assert s.theme == "classic"


def test_options_shape() -> None:
    assert len(OPTIONS) == 9
    assert [o.key for o in OPTIONS] == [
        "start_level",
        "ghost",
        "hold",
        "sound",
        "shake",
        "das",
        "arr",
        "theme",
        "mode",
    ]
    # Every option key must be a real Settings field.
    for o in OPTIONS:
        assert o.key in Settings.__dataclass_fields__
    # Every option's values must contain the field's current default, so the
    # menu can always display and step from the loaded value.
    s = Settings()
    for o in OPTIONS:
        assert value_of(s, o.key) in o.values


def test_option_lookup() -> None:
    assert option("start_level").key == "start_level"
    assert option("start_level").values == tuple(range(1, MAX_START_LEVEL + 1))
    assert option("ghost").label == "drop shadow"
    assert option("ghost").values == (False, True)
    assert option("das").label == "DAS delay"
    assert option("das").values == (0.05, 0.10, 0.15, 0.17, 0.20, 0.25, 0.30, 0.40)
    assert option("arr").label == "ARR rate"
    assert option("arr").values == (0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10)
    assert option("theme").label == "theme"
    assert option("theme").values == ("classic", "mono", "vivid")
    # Every menu label fits the fixed 14-column label field.
    for o in OPTIONS:
        assert len(o.label) <= 14
    with pytest.raises(KeyError):
        option("bogus")


def test_value_of() -> None:
    s = Settings(start_level=9, hold=False)
    assert value_of(s, "start_level") == 9
    assert value_of(s, "hold") is False
    assert value_of(s, "ghost") is True
    assert value_of(s, "das") == 0.17
    assert value_of(s, "arr") == 0.04
    assert value_of(s, "theme") == "classic"
    s2 = Settings(das=0.30, arr=0.08, theme="vivid")
    assert value_of(s2, "das") == 0.30
    assert value_of(s2, "arr") == 0.08
    assert value_of(s2, "theme") == "vivid"
    with pytest.raises(KeyError):
        value_of(s, "bogus")


def test_with_value_round_trips() -> None:
    base = Settings()
    out = with_value(base, "theme", "mono")
    assert out.theme == "mono"
    assert base.theme == "classic"  # original untouched
    assert with_value(base, "das", 0.25).das == 0.25
    assert with_value(base, "arr", 0.01).arr == 0.01
    assert with_value(base, "start_level", 15).start_level == 15
    assert with_value(base, "shake", False).shake is False
    # Every allowed value applies cleanly through the helper.
    for o in OPTIONS:
        for v in o.values:
            assert with_value(Settings(), o.key, v).__dict__[o.key] == v
    with pytest.raises(KeyError):
        with_value(base, "bogus", 1)


def test_cycle_start_level_wraps() -> None:
    low = Settings(start_level=1)
    assert cycle(low, "start_level", 1).start_level == 2
    assert cycle(low, "start_level", -1).start_level == MAX_START_LEVEL
    high = Settings(start_level=MAX_START_LEVEL)
    assert cycle(high, "start_level", 1).start_level == 1
    assert cycle(high, "start_level", -1).start_level == MAX_START_LEVEL - 1
    mid = Settings(start_level=10)
    assert cycle(mid, "start_level", 1).start_level == 11
    assert cycle(mid, "start_level", -1).start_level == 9


def test_cycle_bool_wraps() -> None:
    on = Settings(ghost=True)
    off = Settings(ghost=False)
    # values are (False, True): from True both directions wrap to False,
    # from False both wrap to True.
    assert cycle(on, "ghost", 1).ghost is False
    assert cycle(on, "ghost", -1).ghost is False
    assert cycle(off, "ghost", 1).ghost is True
    assert cycle(off, "ghost", -1).ghost is True
    with pytest.raises(KeyError):
        cycle(Settings(), "bogus", 1)


def test_cycle_returns_new_instance() -> None:
    s = Settings(sound=True)
    out = cycle(s, "sound", 1)
    assert out is not s
    assert s.sound is True  # original untouched
    assert out.sound is False


def test_cycle_das_wraps() -> None:
    # values: 0.05, 0.10, 0.15, 0.17, 0.20, 0.25, 0.30, 0.40
    mid = Settings(das=0.17)
    assert cycle(mid, "das", 1).das == 0.20
    assert cycle(mid, "das", -1).das == 0.15
    assert cycle(Settings(das=0.40), "das", 1).das == 0.05  # forward wrap
    assert cycle(Settings(das=0.05), "das", -1).das == 0.40  # backward wrap
    # Other fields survive a cycle of a different key.
    out = cycle(Settings(das=0.40, theme="mono"), "das", 1)
    assert (out.das, out.theme) == (0.05, "mono")


def test_cycle_arr_wraps() -> None:
    # values: 0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10 (gap at 0.05)
    mid = Settings(arr=0.04)
    assert cycle(mid, "arr", 1).arr == 0.06
    assert cycle(mid, "arr", -1).arr == 0.03
    assert cycle(Settings(arr=0.10), "arr", 1).arr == 0.01  # forward wrap
    assert cycle(Settings(arr=0.01), "arr", -1).arr == 0.10  # backward wrap


def test_cycle_theme_wraps() -> None:
    # values: classic, mono, vivid
    assert cycle(Settings(theme="classic"), "theme", 1).theme == "mono"
    assert cycle(Settings(theme="classic"), "theme", -1).theme == "vivid"  # wrap
    assert cycle(Settings(theme="mono"), "theme", 1).theme == "vivid"
    assert cycle(Settings(theme="mono"), "theme", -1).theme == "classic"
    assert cycle(Settings(theme="vivid"), "theme", 1).theme == "classic"  # wrap


def test_cycle_mode_wraps() -> None:
    # values: classic, sprint (P11)
    assert cycle(Settings(mode="classic"), "mode", 1).mode == "sprint"
    assert cycle(Settings(mode="classic"), "mode", -1).mode == "sprint"  # wrap
    assert cycle(Settings(mode="sprint"), "mode", 1).mode == "classic"  # wrap
    assert cycle(Settings(mode="sprint"), "mode", -1).mode == "classic"


def test_mode_coercion() -> None:
    # str option: only allowed values accepted, everything else kept as-is.
    assert from_dict({"mode": "sprint"}).mode == "sprint"
    assert from_dict({"mode": "marathon"}).mode == "classic"  # invalid -> default
    assert from_dict({"mode": 42}).mode == "classic"  # wrong type -> default


def test_from_dict_full() -> None:
    s = from_dict(
        {
            "start_level": 7,
            "ghost": False,
            "hold": False,
            "sound": True,
            "shake": False,
            "das": 0.20,
            "arr": 0.08,
            "theme": "vivid",
        }
    )
    assert (
        s.start_level,
        s.ghost,
        s.hold,
        s.sound,
        s.shake,
        s.das,
        s.arr,
        s.theme,
    ) == (7, False, False, True, False, 0.20, 0.08, "vivid")


def test_from_dict_partial() -> None:
    s = from_dict({"start_level": 3})
    assert s.start_level == 3
    assert s.ghost is True
    assert s.hold is True
    assert s.sound is True
    assert s.shake is True


def test_from_dict_wrong_types_fall_back_to_defaults() -> None:
    s = from_dict({"start_level": "high", "ghost": 1, "shake": "yes"})
    assert s.start_level == 1
    assert s.ghost is True
    assert s.shake is True


def test_from_dict_out_of_range_clamps() -> None:
    assert from_dict({"start_level": 99}).start_level == MAX_START_LEVEL
    assert from_dict({"start_level": 0}).start_level == 1
    assert from_dict({"start_level": -5}).start_level == 1


def test_from_dict_ignores_unknown_keys() -> None:
    s = from_dict({"bogus": 123, "start_level": 2})
    assert s.start_level == 2
    assert not hasattr(s, "bogus")


def test_from_dict_bad_new_values_keep_defaults() -> None:
    # str where a float is expected, int where a float is expected, and an
    # unknown theme name all fall back to the field default (unknown keys
    # like "das" in the wrong type never raise).
    s = from_dict({"das": "fast", "arr": 1, "theme": "neon"})
    assert s.das == 0.17
    assert s.arr == 0.04
    assert s.theme == "classic"


def test_from_dict_bool_where_float_expected() -> None:
    assert from_dict({"das": True}).das == 0.17
    assert from_dict({"das": False}).das == 0.17
    assert from_dict({"arr": True}).arr == 0.04


def test_from_dict_float_out_of_set_keeps_default() -> None:
    # 0.5 / 0.12 are floats but not allowed DAS/ARR values; exact
    # membership is required (JSON round-trips the allowed floats exactly).
    assert from_dict({"das": 0.5}).das == 0.17
    assert from_dict({"arr": 0.12}).arr == 0.04
    assert from_dict({"das": 0.17}).das == 0.17
    assert from_dict({"arr": 0.06}).arr == 0.06


def test_from_dict_bool_0_does_not_coerce_to_bool_option() -> None:
    # 0.0 == False in Python: membership must not leak across types.
    assert from_dict({"ghost": 0.0}).ghost is True  # default kept, not True
    assert from_dict({"sound": 1.0}).sound is True  # default kept


def test_from_dict_out_of_range_start_level_still_clamps() -> None:
    assert from_dict({"start_level": 9999}).start_level == MAX_START_LEVEL


def test_format_value_strings_and_floats() -> None:
    assert format_value("classic") == "classic"
    assert format_value("mono") == "mono"
    assert format_value(0.17) == "0.17"
    assert format_value(0.04) == "0.04"
    assert format_value(0.2) == "0.2"


def test_format_value() -> None:
    assert format_value(True) == "on"
    assert format_value(False) == "off"
    assert format_value(5) == "5"
    assert format_value(1) == "1"  # int 1 is not a bool
