"""Player settings — pure data, no I/O.

Holds the tunable options (start level, ghost piece, hold, sound, screen
shake, DAS/ARR, theme) plus the ``OPTIONS`` metadata the settings menu
iterates over. ``state.py`` is responsible for persisting these to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

from .pieces import MAX_START_LEVEL

#: The type of every setting value: int (start level), float (DAS/ARR),
#: bool (toggles), or str (theme name).
SettingValue = int | float | bool | str


@dataclass
class Settings:
    start_level: int = 1
    ghost: bool = True  # drop shadow (ghost piece)
    hold: bool = True
    sound: bool = True
    shake: bool = True  # screen shake on hard drop
    das: float = 0.17  # directional auto-shift delay, seconds
    arr: float = 0.04  # auto-repeat rate, seconds per step
    theme: str = "classic"


@dataclass(frozen=True)
class Option:
    key: str  # Settings field name
    label: str  # settings-menu display label
    values: tuple[int | float | bool | str, ...]  # allowed values in menu order


OPTIONS: tuple[Option, ...] = (
    Option("start_level", "start level", tuple(range(1, MAX_START_LEVEL + 1))),
    Option("ghost", "drop shadow", (False, True)),
    Option("hold", "hold piece", (False, True)),
    Option("sound", "sound", (False, True)),
    Option("shake", "screen shake", (False, True)),
    Option("das", "DAS delay", (0.05, 0.10, 0.15, 0.17, 0.20, 0.25, 0.30, 0.40)),
    Option("arr", "ARR rate", (0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10)),
    Option("theme", "theme", ("classic", "mono", "vivid")),
)

_OPTIONS_BY_KEY: dict[str, Option] = {opt.key: opt for opt in OPTIONS}


def option(key: str) -> Option:
    """Return the option for a Settings field name.

    Raises:
        KeyError: if ``key`` is not a known Settings field.
    """
    try:
        return _OPTIONS_BY_KEY[key]
    except KeyError:
        raise KeyError(key) from None


def value_of(settings: Settings, key: str) -> SettingValue:
    """Return the current value of the named setting (KeyError if unknown)."""
    option(key)  # KeyError if the key is not a known Settings field
    return cast("SettingValue", getattr(settings, key))


def with_value(settings: Settings, key: str, value: SettingValue) -> Settings:
    """A NEW Settings with ``key`` set to ``value`` (KeyError if unknown).

    The input instance is left untouched.
    """
    option(key)  # KeyError if the key is not a known Settings field
    changes: dict[str, Any] = {key: value}
    return replace(settings, **changes)


def cycle(settings: Settings, key: str, direction: int = 1) -> Settings:
    """A NEW Settings with ``key`` advanced one step in ``direction``.

    Steps wrap around within the option's allowed values (1 = next,
    -1 = previous); the input instance is left untouched.
    """
    opt = option(key)
    current = value_of(settings, key)
    try:
        index = opt.values.index(current)
    except ValueError:  # value outside the menu (should not happen): restart
        index = 0
    index = (index + direction) % len(opt.values)
    return with_value(settings, key, opt.values[index])


def format_value(value: SettingValue) -> str:
    """Menu display for a value: bools as on/off, everything else verbatim."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value)


def coerce_setting(key: str, raw: object) -> SettingValue | None:
    """Type-aware coercion of a raw setting value; never raises.

    Returns the coerced value, or ``None`` meaning "keep the current
    value": ``start_level`` must be an int (bools rejected) and is clamped
    to 1..MAX_START_LEVEL; bool options must be bool; float options must
    be float (bool/int rejected) and exactly one of the option's allowed
    values; str options must be exactly one of the allowed values;
    unknown keys are ignored.
    """
    opt = _OPTIONS_BY_KEY.get(key)
    if opt is None:
        return None
    if key == "start_level":
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return max(1, min(MAX_START_LEVEL, raw))
    expected = opt.values[0]
    if isinstance(expected, bool):
        return raw if isinstance(raw, bool) else None
    if isinstance(expected, float):
        return raw if isinstance(raw, float) and raw in opt.values else None
    return raw if isinstance(raw, str) and raw in opt.values else None


def from_dict(data: dict[str, object]) -> Settings:
    """Build Settings from a plain dict, never raising.

    Unknown keys are ignored; wrong-typed, out-of-range, or out-of-set
    values fall back to the field default (start_level is clamped to
    1..MAX_START_LEVEL).
    """
    result = Settings()
    for key, raw in data.items():
        value = coerce_setting(key, raw)
        if value is not None:
            result = with_value(result, key, value)
    return result


__all__ = [
    "OPTIONS",
    "Option",
    "SettingValue",
    "Settings",
    "coerce_setting",
    "cycle",
    "format_value",
    "from_dict",
    "option",
    "value_of",
    "with_value",
]
