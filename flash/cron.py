"""Set times for sparks: "9am weekdays", "mon 8:30", or a cron line.

A spark normally runs every so often: every 2 hours, daily. Some jobs
belong at a time of day instead, a brief before work, a check after the
nightly build, and "daily" drifts with whenever the last shift ended.
These are those: kept as a standard five-field cron line (minute, hour,
day of month, month, day of week), in this machine's local time, and
written however the user says them.

As OpenClaw's cron jobs do.
"""

from __future__ import annotations

import datetime as dt
import re
import time

# How far ahead next_after() looks before deciding a schedule never
# fires (the 30th of February, say). Leap days need four years.
LOOK_AHEAD_DAYS = 366 * 4 + 1

_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),
)

_DAYS = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}
_DAY_NAMES = (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday",
)
_MONTHS = {
    name: number for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"), 1,
    )
}

_CRON_FIELD = re.compile(r"^[\d*/,\-a-z]+$")


class CronError(ValueError):
    """A schedule that could not be read, with what to write instead."""


# --- Cron lines ----------------------------------------------------------


def _number(text: str, low: int, high: int, names: dict) -> int:
    value = names.get(text[:3]) if text[:1].isalpha() else None
    if value is None:
        if not text.isdigit():
            raise CronError(f"{text!r} is not a number")
        value = int(text)
    if low == 0 and high == 6 and value == 7:
        value = 0  # Sunday, either end of the week
    if not low <= value <= high:
        raise CronError(f"{value} is outside {low} to {high}")
    return value


def _field(text: str, low: int, high: int, names: dict) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, by = part.partition("/")
            if not by.isdigit() or int(by) < 1:
                raise CronError(f"{by!r} is not a step")
            step = int(by)
        if part == "*":
            first, last = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            first = _number(a, low, high, names)
            last = _number(b, low, high, names)
            if names is _DAYS and last == 0 and b != "0":
                last = 6 if first else 0
            if last < first:
                raise CronError(f"{part!r} runs backwards")
        else:
            first = _number(part, low, high, names)
            last = high if step > 1 else first
        out.update(range(first, last + 1, step))
    return out


def fields(expr: str) -> list[set[int]]:
    """EXPR's five fields, each as the values it allows."""

    parts = expr.split()
    if len(parts) != 5:
        raise CronError("a cron line has five fields")
    named = ({}, {}, {}, _MONTHS, _DAYS)
    return [
        _field(part, low, high, names)
        for part, (_, low, high), names in zip(parts, _FIELDS, named)
    ]


def _is_cron(text: str) -> bool:
    parts = text.split()
    return len(parts) == 5 and all(_CRON_FIELD.match(p) for p in parts)


# --- Words ---------------------------------------------------------------

_TIME = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$"
)
_ORDINAL = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)$")
_FILLER = {"at", "on", "every", "each", "and", "the", "of", "in", "o'clock"}


def _clock(word: str) -> tuple[int, int] | None:
    """(hour, minute) from "9am", "9:30", "21:05", "noon", or a bare hour
    on the 24-hour clock ("at 9"); None if not one."""

    if word == "noon":
        return 12, 0
    if word == "midnight":
        return 0, 0
    match = _TIME.match(word)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    half = (match.group(3) or "").replace(".", "")
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _tokens(text: str) -> list[str]:
    text = text.lower().replace(",", " ").replace("&", " ")
    # "9 am" is one time, not a number and a word.
    text = re.sub(r"(\d)\s+(am|pm|a\.m\.|p\.m\.)", r"\1\2", text)
    return [w for w in text.split() if w not in _FILLER]


def _day(word: str) -> int | None:
    word = word.rstrip("s") if len(word) > 3 else word
    for name, number in _DAYS.items():
        full = _DAY_NAMES[number].lower()
        if word in (name, full, full[:4]):
            return number
    return None


def _from_words(text: str) -> str:
    times: list[tuple[int, int]] = []
    weekdays: set[int] = set()
    monthdays: set[int] = set()
    for word in _tokens(text):
        clock = _clock(word)
        if clock:
            times.append(clock)
            continue
        if word in ("daily", "day", "days", "everyday", "nightly"):
            continue
        if word in ("weekday", "weekdays"):
            weekdays.update(range(1, 6))
            continue
        if word in ("weekend", "weekends"):
            weekdays.update((0, 6))
            continue
        if word == "monthly":
            monthdays.add(1)
            continue
        day = _day(word)
        if day is not None:
            weekdays.add(day)
            continue
        if "-" in word and all(_day(w) is not None for w in word.split("-")):
            first, last = (_day(w) for w in word.split("-"))
            weekdays.update(range(first, last + 1) if first <= last
                            else [*range(first, 7), *range(0, last + 1)])
            continue
        ordinal = _ORDINAL.match(word)
        if ordinal and 1 <= int(ordinal.group(1)) <= 31:
            monthdays.add(int(ordinal.group(1)))
            continue
        if word == "month":
            continue
        raise CronError(f"could not read {word!r}")

    if not times:
        raise CronError("say a time of day, like 9am or 17:30")
    minutes = {m for _, m in times}
    if len(minutes) > 1:
        raise CronError(
            "times on one schedule share their minutes: 9am and 5pm, or "
            "9:30 and 17:30, not 9:00 and 17:30"
        )
    hours = _join({h for h, _ in times})
    day = _join(monthdays) if monthdays else "*"
    week = _join(weekdays) if weekdays else "*"
    return f"{minutes.pop()} {hours} {day} * {week}"


def _join(values: set[int]) -> str:
    """VALUES as a cron field, runs of three or more as ranges: 1-5,0."""

    out: list[str] = []
    run: list[int] = []
    for value in sorted(values) + [-2]:
        if run and value != run[-1] + 1:
            out.append(
                f"{run[0]}-{run[-1]}" if len(run) >= 3
                else ",".join(map(str, run))
            )
            run = []
        run.append(value)
    return ",".join(out)


def parse(text) -> str:
    """A cron line for TEXT: "9am weekdays", "mon, thu 08:30", "the 1st
    at 9am", "noon daily", or a cron line as is. Raises CronError."""

    text = " ".join(str(text or "").split())
    if not text:
        raise CronError("the schedule was empty")
    if _is_cron(text.lower()):
        expr = text.lower()
    else:
        expr = _from_words(text)
    fields(expr)  # raises on anything out of range
    if next_after(expr, time.time()) is None:
        raise CronError("that schedule never comes round")
    return expr


# --- When ----------------------------------------------------------------


def next_after(expr: str, after: float) -> float | None:
    """The first time EXPR fires after AFTER, in local time, or None if
    it never does."""

    minutes, hours, days, months, weekdays = fields(expr)
    # Cron's rule: when both the day of the month and the day of the
    # week are narrowed, a day matching either one counts.
    days_any = len(days) == 31
    weekdays_any = len(weekdays) == 7

    # Local time on purpose: "9am" is 9am on this machine's clock,
    # whatever its offset that day, which mktime() works out.
    start = (int(after) // 60 + 1) * 60
    today = time.localtime(start)
    first = dt.date(today.tm_year, today.tm_mon, today.tm_mday)
    for offset in range(LOOK_AHEAD_DAYS):
        day = first + dt.timedelta(days=offset)
        if day.month not in months:
            continue
        weekday = (day.weekday() + 1) % 7  # cron counts from Sunday
        on_day = day.day in days
        on_week = weekday in weekdays
        if days_any or weekdays_any:
            if not (on_day and on_week):
                continue
        elif not (on_day or on_week):
            continue
        for hour in sorted(hours):
            for minute in sorted(minutes):
                when = time.mktime(
                    (day.year, day.month, day.day, hour, minute, 0, 0, 0, -1)
                )
                if when >= start:
                    return when
    return None


def shortest_gap(expr: str, after: float, count: int = 48) -> float:
    """The fewest seconds between two of EXPR's next COUNT firings after
    AFTER: how often it can run, at its busiest."""

    gap = float("inf")
    when = next_after(expr, after)
    for _ in range(count):
        if when is None:
            break
        later = next_after(expr, when)
        if later is None:
            break
        gap = min(gap, later - when)
        when = later
    return gap


def _at(hour: int, minute: int) -> str:
    if minute == 0 and hour in (0, 12):
        return "midnight" if hour == 0 else "noon"
    half = "am" if hour < 12 else "pm"
    shown = hour % 12 or 12
    return f"{shown}{half}" if minute == 0 else f"{shown}:{minute:02d}{half}"


def _list(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def words(expr: str) -> str:
    """How EXPR reads: "at 9am on weekdays", "at 8:30am on Mondays"."""

    try:
        minutes, hours, days, months, weekdays = fields(expr)
    except CronError:
        return f"on the schedule {expr}"
    if len(hours) == 24 and len(minutes) <= 4:
        out = (
            "on the hour" if minutes == {0}
            else f"at {_list([f':{m:02d}' for m in sorted(minutes)])} "
            "past every hour"
        )
        gaps = {b - a for a, b in zip(sorted(minutes), sorted(minutes)[1:])}
        if len(minutes) > 1 and len(gaps) == 1 and 60 % gaps.pop() == 0 \
                and min(minutes) < 60 // len(minutes):
            out = f"every {60 // len(minutes)} minutes"
    elif len(minutes) > 4 or len(hours) > 6:
        return f"on the schedule {expr}"
    else:
        out = "at " + _list(
            [_at(h, m) for h in sorted(hours) for m in sorted(minutes)]
        )
    if len(weekdays) < 7:
        if weekdays == set(range(1, 6)):
            out += " on weekdays"
        elif weekdays == {0, 6}:
            out += " on weekends"
        else:
            out += " on " + _list([f"{_DAY_NAMES[d]}s" for d in sorted(
                weekdays, key=lambda d: (d + 6) % 7,
            )])
    if len(days) < 31:
        def nth(n: int) -> str:
            suffix = "th" if 10 <= n % 100 <= 20 else {
                1: "st", 2: "nd", 3: "rd",
            }.get(n % 10, "th")
            return f"{n}{suffix}"
        joined = _list([nth(d) for d in sorted(days)])
        out += (" or" if len(weekdays) < 7 else "") + f" on the {joined}"
    if len(months) < 12:
        names = [dt.date(2000, m, 1).strftime("%B") for m in sorted(months)]
        out += f" in {_list(names)}"
    elif len(weekdays) == 7 and len(days) == 31 and len(hours) < 24:
        out += " every day"
    return out
