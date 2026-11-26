"""A sense of time, for the sparks: what time it is now, and how long ago
something happened, in words a model reads the way a person would.

A spark works for days at a time, in a keeper that may have started at
login, so the date it is told is read afresh every time it is woken,
never kept from when Flash started. Everything here is in this
computer's own time, the user's, and in plain English whatever the
computer's language: the prompts the sparks read are English.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def local(at: Optional[float] = None) -> datetime:
    """AT, a time.time(), as this computer's local time; now for None."""

    at = time.time() if at is None else float(at)
    return datetime.fromtimestamp(at, timezone.utc).astimezone()


def part_of_day(hour: int) -> str:
    """How an hour reads: "in the morning", "at night"."""

    if 5 <= hour < 12:
        return "in the morning"
    if 12 <= hour < 17:
        return "in the afternoon"
    if 17 <= hour < 21:
        return "in the evening"
    return "at night"


def zone(when: datetime) -> str:
    """WHEN's time zone, as "PST, UTC-08:00"."""

    offset = when.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "-" if minutes < 0 else "+"
    hours, mins = divmod(abs(minutes), 60)
    utc = f"UTC{sign}{hours:02d}:{mins:02d}"
    name = (when.tzname() or "").strip()
    return f"{name}, {utc}" if name and name != "UTC" else utc


def clock(at: float) -> str:
    """AT as a 24-hour clock time: "14:05"."""

    return local(at).strftime("%H:%M")


def short_day(at: float, now: Optional[float] = None) -> str:
    """AT's day, short: "Tue 24 Nov", with its year when it is not this
    one's."""

    when, today = local(at), local(now)
    day = f"{DAYS[when.weekday()][:3]} {when.day} {MONTHS[when.month - 1][:3]}"
    return day if when.year == today.year else f"{day} {when.year}"


def _days_apart(at: float, now: Optional[float]) -> int:
    return (local(at).date() - local(now).date()).days


def day_words(at: float, now: Optional[float] = None) -> str:
    """AT's day, as said from NOW: "today", "yesterday", "tomorrow",
    else "Tue 24 Nov"."""

    apart = _days_apart(at, now)
    return {0: "today", -1: "yesterday", 1: "tomorrow"}.get(
        apart, short_day(at, now))


def when(at: float, now: Optional[float] = None) -> str:
    """AT as said from NOW: "today at 14:05", "yesterday at 09:00",
    "Tue 24 Nov at 09:00"."""

    day = day_words(at, now)
    return f"{'on ' if day[0].isupper() else ''}{day} at {clock(at)}"


def span(seconds: float) -> str:
    """A length of time in words: "40 minutes", "5 hours", "3 days"."""

    minutes = max(0, int(seconds // 60))
    if minutes < 90:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours} hours"
    days = round(hours / 24)
    if days < 14:
        return f"{days} days"
    weeks = round(days / 7)
    if days < 60:
        return f"{weeks} weeks"
    return f"{round(days / 30)} months"


def ago(at: float, now: Optional[float] = None) -> str:
    """How long ago AT was: "just now", "5 minutes ago", "3 days ago"."""

    now = time.time() if now is None else now
    seconds = now - float(at)
    if seconds < 60:
        return "just now"
    return f"{span(seconds)} ago"


def stamp(at: float, now: Optional[float] = None) -> str:
    """The stamp put before a message to say when it was sent:
    "[Thu 26 Nov 14:05]"."""

    return f"[{short_day(at, now)} {clock(at)}]"


def day_header(at: float, now: Optional[float] = None) -> str:
    """The line a chat shows above a day's messages: "--- Today,
    Thursday 26 November ---"."""

    day = local(at)
    long_day = f"{DAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
    if day.year != local(now).year:
        long_day += f" {day.year}"
    near = {0: "Today", -1: "Yesterday"}.get(_days_apart(at, now))
    return f"--- {near + ', ' if near else ''}{long_day} ---"


def now_block(now: Optional[float] = None) -> str:
    """What time it is, for a spark's prompt, read as it is built: the
    day, the date, the time, the part of the day and the time zone."""

    when_now = local(now)
    day = DAYS[when_now.weekday()]
    date = f"{when_now.day} {MONTHS[when_now.month - 1]} {when_now.year}"
    hour = when_now.strftime("%H:%M")
    return (
        "=== The time now ===\n"
        f"It is {day} {date}, {hour} {part_of_day(when_now.hour)}, the "
        f"user's own time ({zone(when_now)}).\n"
        f"As a local time for a tool that takes one: "
        f"{when_now.strftime('%Y-%m-%dT%H:%M')}.\n"
        "This is read afresh each time you are woken, so it is right even "
        "when Flash has run for days: treat it as the present. Work out "
        "\"today\", \"tomorrow\", \"in 2 hours\" and how long ago something "
        "was from it. When a search is time-sensitive, put THIS year "
        f"({when_now.year}) into the query, never a year recalled from "
        "training data."
    )


# A stamp a model copied to the start of its own message, from the
# stamps on the ones it read: "[14:05]", "[Thu 26 Nov 14:05]", maybe
# after the message's number.
_COPIED_STAMP = re.compile(
    r"^\s*(?:#\d+\s+)?\[(?:[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}"
    r"(?: \d{4})? )?\d{1,2}:\d{2}\]\s*"
)


def unstamp(text: str) -> str:
    """TEXT without a time stamp copied to its start."""

    return _COPIED_STAMP.sub("", str(text or ""), count=1)
