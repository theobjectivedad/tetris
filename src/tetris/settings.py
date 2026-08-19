"""Player settings — pure data, no I/O.

Holds the tunable options (start level, ghost piece, hold, sound, screen
shake) plus the ``OPTIONS`` metadata the settings menu iterates over.
``state.py`` is responsible for persisting these to disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pieces import MAX_START_LEVEL


@dataclass
class Settings:
    start_level: int = 1
    ghost: bool = True  # drop shadow (ghost piece)
    hold: bool = True
    sound: bool = True
    shake: bool = True  # screen shake on hard drop


@dataclass(frozen=True)
class Option:
    key: str  # Settings field name
    label: str  # settings-menu display label
    values: tuple[int | bool, ...]  # allowed values in menu order


OPTIONS: tuple[Option, ...] = (
    Option("start_level", "start level", tuple(range(1, MAX_START_LEVEL + 1))),
    Option("ghost", "drop shadow", (False, True)),
    Option("hold", "hold piece", (False, True)),
    Option("sound", "sound", (False, True)),
    Option("shake", "screen shake", (False, True)),
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


def value_of(settings: Settings, key: str) -> int | bool:
    """Return the current value of the named setting (KeyError if unknown)."""
    key = option(key).key
    if key == "start_level":
        return settings.start_level
    if key == "ghost":
        return settings.ghost
    if key == "hold":
        return settings.hold
    if key == "sound":
        return settings.sound
    return settings.shake


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
    value: int | bool = opt.values[index]
    if key == "start_level":
        new_level = value if isinstance(value, int) else 1
        return Settings(
            start_level=new_level,
            ghost=settings.ghost,
            hold=settings.hold,
            sound=settings.sound,
            shake=settings.shake,
        )
    new_bool = value if isinstance(value, bool) else False
    return Settings(
        start_level=settings.start_level,
        ghost=new_bool if key == "ghost" else settings.ghost,
        hold=new_bool if key == "hold" else settings.hold,
        sound=new_bool if key == "sound" else settings.sound,
        shake=new_bool if key == "shake" else settings.shake,
    )


def format_value(value: int | bool) -> str:
    """Menu display for a value: bools as on/off, ints verbatim."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value)


def _coerce(key: str, raw: object) -> int | bool | None:
    """Coerce a raw settings value; ``None`` means 'keep the default'."""
    if key == "start_level":
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return max(1, min(MAX_START_LEVEL, raw))
    if isinstance(raw, bool):
        return raw
    return None


def from_dict(data: dict[str, object]) -> Settings:
    """Build Settings from a plain dict, never raising.

    Unknown keys are ignored; wrong-typed or out-of-range values fall back
    to the field default (start_level is clamped to 1..MAX_START_LEVEL).
    """
    result = Settings()
    for key, raw in data.items():
        if key not in _OPTIONS_BY_KEY:
            continue
        value = _coerce(key, raw)
        if value is not None:
            setattr(result, key, value)
    return result


__all__ = ["OPTIONS", "Option", "Settings", "cycle", "format_value", "from_dict", "option", "value_of"]
