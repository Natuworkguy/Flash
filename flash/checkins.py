"""What the user wants to hear about, how often, and when not at all.

One setting, in the user's own words: "Tell me about new issues on
flash as they come in, the weather each morning, and nothing about
newsletters." Every spark's shift reads it before it decides whether
what it found is worth a word, and how soon. Beside it, quiet hours: a
span of the night (or any span) when nothing pings, notifies or calls.
What the sparks find meanwhile waits in their reports, and the first
check-in after the quiet ends brings it.

Kept in one small file under Flash's folder, so the keeper's sparks and
an open Flash read the same thing.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

MOST_CHARS = 2000
_lock = threading.Lock()


class CheckinError(ValueError):
    """Quiet hours Flash could not read."""


def _path() -> Path:
    # Looked up here, not bound at import, so a test can point FLASH_DIR
    # at a temporary home.
    return FLASH_DIR / "checkins.json"


def _load() -> dict:
    try:
        found = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- What to tell the user about --------------------------------------


def interests() -> str:
    """What the user wants to hear about, and how often, as they put it."""

    return str(_load().get("interests") or "").strip()


def set_interests(text: str) -> str:
    text = str(text or "").strip()[:MOST_CHARS]
    with _lock:
        data = _load()
        data["interests"] = text
        _save(data)
    return text


# --- Quiet hours ----------------------------------------------------------

_TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
_SPAN = re.compile(
    rf"^\s*(?:from\s+)?{_TIME}\s*(?:-|–|—|to|until|till)\s*{_TIME}\s*$",
    re.I,
)
_OFF = ("", "off", "none", "no", "never")


def _minute(hour: str, minute: Optional[str], half: Optional[str]) -> int:
    h, m = int(hour), int(minute or 0)
    half = (half or "").lower().replace(".", "")
    if half:
        if not 1 <= h <= 12:
            raise CheckinError(f"{hour}{half} is not a time of day.")
        h = h % 12 + (12 if half == "pm" else 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise CheckinError(f"{hour}:{minute or '00'} is not a time of day.")
    return h * 60 + m


def parse_quiet(text: str) -> Optional[tuple[int, int]]:
    """Quiet hours as minutes after midnight, (start, end), from words
    like "10pm-7am", "22:00 to 07:00" or "11:30pm until 6": None for
    off. A span past midnight wraps."""

    text = str(text or "").strip().lower()
    if text in _OFF:
        return None
    found = _SPAN.match(text)
    if not found:
        raise CheckinError(
            "Say quiet hours as a span, like 10pm-7am or 22:00 to 07:00, "
            "or off."
        )
    h1, m1, a1, h2, m2, a2 = found.groups()
    # "10-7pm" and "10pm-7": a half given once counts for the other
    # only where it makes a sensible night.
    if a2 and not a1:
        a1 = "pm" if a2.startswith("a") and int(h1) <= 12 else a2
    if a1 and not a2 and int(h2) <= 12:
        a2 = "am" if a1.startswith("p") else a1
    start, end = _minute(h1, m1, a1), _minute(h2, m2, a2)
    if start == end:
        raise CheckinError("Quiet hours need a start and an end that differ.")
    return start, end


def _clock(minute: int) -> str:
    h, m = divmod(minute, 60)
    half = "am" if h < 12 else "pm"
    h = h % 12 or 12
    return f"{h}{half}" if not m else f"{h}:{m:02d}{half}"


def quiet_words(span: Optional[tuple[int, int]] = None) -> str:
    """Quiet hours as the user would say them: "10pm to 7am", or ""."""

    span = quiet_hours() if span is None else span
    return f"{_clock(span[0])} to {_clock(span[1])}" if span else ""


def quiet_hours() -> Optional[tuple[int, int]]:
    raw = _load().get("quiet")
    if isinstance(raw, list) and len(raw) == 2:
        try:
            return int(raw[0]) % 1440, int(raw[1]) % 1440
        except (TypeError, ValueError):
            return None
    return None


def set_quiet_hours(text: str) -> Optional[tuple[int, int]]:
    span = parse_quiet(text)
    with _lock:
        data = _load()
        data["quiet"] = list(span) if span else None
        _save(data)
    return span


def _local(now: Optional[float]) -> dt.datetime:
    return dt.datetime.now() if now is None else dt.datetime.fromtimestamp(now)


def is_quiet(now: Optional[float] = None) -> bool:
    """Whether it is quiet hours now, in this computer's time."""

    span = quiet_hours()
    if not span:
        return False
    at = _local(now)
    minute = at.hour * 60 + at.minute
    start, end = span
    return start <= minute < end if start < end else (
        minute >= start or minute < end)


def quiet_left(now: Optional[float] = None) -> int:
    """Seconds until quiet hours end: 0 when it is not quiet now."""

    if not is_quiet(now):
        return 0
    at = _local(now)
    end = quiet_hours()[1]
    ends = at.replace(hour=end // 60, minute=end % 60, second=0,
                      microsecond=0)
    if ends <= at:
        ends += dt.timedelta(days=1)
    return max(1, int((ends - at).total_seconds()))


# --- What a check-in reads ------------------------------------------------


def prompt_block(now: Optional[float] = None) -> str:
    """The user's words on what to tell them about and when not to, for
    a spark's shift: "" when they set neither."""

    said, span = interests(), quiet_hours()
    if not said and not span:
        return ""
    lines = ["=== What the user wants to hear about ==="]
    if said:
        lines += [
            "In their words:", said,
            "Hold what you find up against this: tell them about what it "
            "names, as often as it says and no more often; leave out "
            "what it says to leave out; and when it asks for a digest, "
            "keep the pieces in your notes until it is time.",
        ]
    if span:
        lines.append(
            f"Quiet hours: {quiet_words(span)}, this computer's time. "
            + ("It is quiet hours NOW: do not write @user and do not "
               "notify or call; keep what you find for your report, and "
               "the user reads it after."
               if is_quiet(now) else
               "It is not quiet hours now.")
        )
    return "\n".join(lines)
