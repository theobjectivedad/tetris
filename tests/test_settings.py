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
)


def test_defaults() -> None:
    s = Settings()
    assert s.start_level == 1
    assert s.ghost is True
    assert s.hold is True
    assert s.sound is True
    assert s.shake is True


def test_options_shape() -> None:
    assert len(OPTIONS) == 5
    assert [o.key for o in OPTIONS] == ["start_level", "ghost", "hold", "sound", "shake"]
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
    with pytest.raises(KeyError):
        option("bogus")


def test_value_of() -> None:
    s = Settings(start_level=9, hold=False)
    assert value_of(s, "start_level") == 9
    assert value_of(s, "hold") is False
    assert value_of(s, "ghost") is True
    with pytest.raises(KeyError):
        value_of(s, "bogus")


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


def test_from_dict_full() -> None:
    s = from_dict(
        {
            "start_level": 7,
            "ghost": False,
            "hold": False,
            "sound": True,
            "shake": False,
        }
    )
    assert (s.start_level, s.ghost, s.hold, s.sound, s.shake) == (
        7,
        False,
        False,
        True,
        False,
    )


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


def test_format_value() -> None:
    assert format_value(True) == "on"
    assert format_value(False) == "off"
    assert format_value(5) == "5"
    assert format_value(1) == "1"  # int 1 is not a bool
