"""What Flash writes, tidied the way the user asked in Settings.

Two switches, both kept in ~/.flash.env like Flash's other settings:

- REMOVE_EM_DASHES, on unless set to off: em and en dashes and the
  horizontal bar go, by the rules in dashes.undash (a comma where a dash
  sets a phrase off, a hyphen between numbers or words, a bullet at the
  start of a list item; code kept as written).
- REMOVE_EMOJIS, off unless set to on: emoji characters go, with the
  space they leave behind.

Only what Flash produces passes through here: its replies, as they
stream and when finished, the arguments of every tool call it makes,
and spark reports and messages. Nothing it reads or fetches does.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .dashes import DashGuard, undash

DASHES_SETTING = "REMOVE_EM_DASHES"
EMOJIS_SETTING = "REMOVE_EMOJIS"
_OFF = ("off", "0", "false", "no")
_ON = ("on", "1", "true", "yes")

# One emoji: a pictograph, or a keycap, or a flag, with what modifies it
# (variation selector, skin tone) and anything joined to it by a
# zero-width joiner. A joiner on its own is left alone, since some
# scripts need it.
_PICT = (
    "\U0001F000-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55"
    "\u231A\u231B\u23E9-\u23FA\u2934\u2935\u3030\u303D\u3297\u3299"
)
_ONE = (
    rf"(?:[0-9#*]\uFE0F?\u20E3|[{_PICT}]"
    r"(?:\uFE0F|[\U0001F3FB-\U0001F3FF])*)"
)
_EMOJI = rf"{_ONE}(?:\u200D{_ONE})*\uFE0F?"
_RUN = rf"(?:{_EMOJI})+"
# A run between words takes one side's space with it; one opening or
# closing a line takes its space too; any other just goes.
_EMOJI_RES = (
    re.compile(rf"[ \t]+{_RUN}(?=[ \t]|$)", re.MULTILINE),
    re.compile(rf"^([ \t]*){_RUN}[ \t]*", re.MULTILINE),
    re.compile(_RUN),
)


def _flag(name: str, default: bool) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if value in _OFF:
        return False
    if value in _ON:
        return True
    return default


def dashes_on() -> bool:
    return _flag(DASHES_SETTING, True)


def emojis_on() -> bool:
    return _flag(EMOJIS_SETTING, False)


def settings() -> dict:
    """Both switches, as Settings shows them."""

    return {"dashes": dashes_on(), "emojis": emojis_on()}


def set_setting(which: str, on: bool) -> dict:
    """Turn "dashes" or "emojis" removal on or off, and keep it."""

    from .envfile import set_env_var
    from .paths import ENV_PATH

    name = {"dashes": DASHES_SETTING, "emojis": EMOJIS_SETTING}[which]
    value = "on" if on else "off"
    os.environ[name] = value
    set_env_var(ENV_PATH, name, value)
    return settings()


def unemoji(text: str) -> str:
    """TEXT with its emojis taken out."""

    if not text or text.isascii():
        return text
    first, line, rest = _EMOJI_RES
    text = first.sub("", text)
    text = line.sub(r"\1", text)
    return rest.sub("", text)


def tidy(text: str) -> str:
    """TEXT as the settings ask for it."""

    if not isinstance(text, str) or not text:
        return text
    if dashes_on():
        text = undash(text)
    if emojis_on():
        text = unemoji(text)
    return text


def tidy_args(value: Any) -> Any:
    """A tool call's arguments, every string in them tidied."""

    if isinstance(value, str):
        return tidy(value)
    if isinstance(value, dict):
        return {k: tidy_args(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tidy_args(v) for v in value]
    if isinstance(value, tuple):
        return tuple(tidy_args(v) for v in value)
    return value


class OutGuard:
    """tidy for a reply that arrives a piece at a time, with the same
    feed, flush and text as DashGuard, which it uses when dashes go."""

    def __init__(self) -> None:
        self._dashes = dashes_on()
        self._emojis = emojis_on()
        self._guard = DashGuard() if self._dashes else None
        self._raw: list[str] = []

    def feed(self, piece: str) -> str:
        if self._guard is not None:
            piece = self._guard.feed(piece)
        else:
            self._raw.append(piece)
        return unemoji(piece) if self._emojis else piece

    def flush(self) -> str:
        rest = self._guard.flush() if self._guard is not None else ""
        return unemoji(rest) if self._emojis else rest

    def text(self) -> str:
        if self._guard is not None:
            text = self._guard.text()
        else:
            text = "".join(self._raw)
        return unemoji(text) if self._emojis else text
