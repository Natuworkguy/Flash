"""Timers and alarms, as a phone's clock has them.

A timer counts down from a length ("10 minutes for the pasta") and can be
paused, resumed or given another minute. An alarm goes off at a time of
day, once or on chosen days of the week. Either rings when it comes due:
the computer plays a tone, a desktop notification goes up, the web page
and the watch show it, until it is stopped or snoozed (nine minutes, as
alarm clocks have it). One left ringing stops by itself after ten
minutes, and is noted in the briefing as missed.

Kept in ~/.flash/clock.json, so they survive a restart. A thread here
watches the time to the second while anything is set; it starts the
first time anything asks.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from . import paths

SNOOZE_MINUTES = 9
RING_LIMIT = 10 * 60
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_lock = threading.RLock()
_thread: Optional[threading.Thread] = None
_listeners: list[Callable[[dict], None]] = []


class ClockError(ValueError):
    pass


# ---------------------------------------------------------------- storage

def _file():
    return paths.FLASH_DIR / "clock.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- reading what people say

_UNITS = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}


def parse_length(text: Any) -> float:
    """Seconds in "10m", "1h30m", "90s", "10 minutes", "1:30" (m:s) or
    "1:02:00" (h:m:s), or a bare number of minutes."""

    s = str(text or "").strip().lower()
    if not s:
        raise ClockError("How long? Say '10m', '1h30m' or '90s'.")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s) * 60
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", s):
        parts = [int(p) for p in s.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    total, found = 0.0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", s.replace("and", " ")):
        if unit not in _UNITS:
            raise ClockError(f"Can't read {text!r} as a length.")
        total += float(amount) * _UNITS[unit]
        found = True
    if not found or total <= 0:
        raise ClockError(f"Can't read {text!r} as a length.")
    return total


def parse_time_of_day(text: Any) -> tuple[int, int]:
    """(hour, minute) from "7", "7:30", "07:30", "7.30am", "6:45 pm",
    "18:00", "noon", "midnight"."""

    s = str(text or "").strip().lower().replace(".", ":")
    if s in ("noon", "midday"):
        return 12, 0
    if s == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?", s)
    if not m:
        raise ClockError(f"Can't read {text!r} as a time. Say '7:30', '6:45pm' or '18:00'.")
    h, mi, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half:
        if not 1 <= h <= 12:
            raise ClockError(f"{text!r} isn't a time.")
        h = (h % 12) + (12 if half.startswith("p") else 0)
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ClockError(f"{text!r} isn't a time.")
    return h, mi


def parse_days(text: Any) -> list[str]:
    """[] (once), or the days an alarm repeats: "weekdays", "weekends",
    "every day", "mon,wed,fri", "Tuesdays and Thursdays"."""

    if isinstance(text, list):
        text = ",".join(str(t) for t in text)
    s = str(text or "").strip().lower()
    if not s or s in ("once", "none", "no"):
        return []
    if s in ("daily", "every day", "everyday", "all"):
        return list(DAYS)
    if s in ("weekdays", "workdays", "week days"):
        return DAYS[:5]
    if s in ("weekends", "weekend"):
        return DAYS[5:]
    out = []
    for word in re.findall(r"[a-z]+", s):
        for d in DAYS:
            if word.startswith(d):
                if d not in out:
                    out.append(d)
                break
    if not out:
        raise ClockError(f"Can't read {text!r} as days. Say 'weekdays' or 'mon,wed'.")
    return [d for d in DAYS if d in out]


def _next_alarm(hour: int, minute: int, days: list[str], after: float) -> float:
    base = datetime.fromtimestamp(after)
    for add in range(0, 8):
        day = base + timedelta(days=add)
        at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at.timestamp() <= after:
            continue
        if days and DAYS[at.weekday()] not in days:
            continue
        return at.timestamp()
    return (base + timedelta(days=1)).timestamp()


# ---------------------------------------------------------------- setting and changing

def add_timer(length: Any, label: str = "", now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    seconds = parse_length(length) if not isinstance(length, (int, float)) else float(length)
    if not 1 <= seconds <= 7 * 86400:
        raise ClockError("A timer runs from a second to a week.")
    item = {"id": "t" + uuid.uuid4().hex[:6], "kind": "timer", "label": str(label or "").strip()[:80],
            "length": seconds, "ends": now + seconds, "left": None, "made": now,
            "ringing": None}
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    _changed()
    return item


def add_alarm(at: Any, label: str = "", days: Any = "", now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    h, m = parse_time_of_day(at)
    every = parse_days(days)
    item = {"id": "a" + uuid.uuid4().hex[:6], "kind": "alarm", "label": str(label or "").strip()[:80],
            "hour": h, "minute": m, "days": every, "on": True,
            "next": _next_alarm(h, m, every, now), "ringing": None, "snoozed": None}
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    _changed()
    return item


def _edit(item_id: str, change: Callable[[dict, float], Optional[bool]]) -> dict:
    now = time.time()
    with _lock:
        items = _load()
        for i, it in enumerate(items):
            if it.get("id") == item_id:
                drop = change(it, now)
                if drop:
                    items.pop(i)
                _save(items)
                break
        else:
            raise ClockError(f"No timer or alarm {item_id!r}.")
    if it.get("ringing") is None:
        _quiet_if_none()
    _changed()
    return it


def cancel(item_id: str) -> dict:
    return _edit(item_id, lambda it, now: True)


def stop(item_id: str) -> dict:
    """Stop one ringing: a timer is done with; an alarm waits for its next
    day, or turns off if it rang once."""

    def change(it, now):
        it["ringing"] = None
        if it["kind"] == "timer":
            return True
        it["snoozed"] = None
        if it["days"]:
            it["next"] = _next_alarm(it["hour"], it["minute"], it["days"], now)
        else:
            it["on"] = False
        return False
    return _edit(item_id, change)


def snooze(item_id: str, minutes: float = SNOOZE_MINUTES) -> dict:
    def change(it, now):
        it["ringing"] = None
        if it["kind"] == "timer":
            it["ends"], it["left"] = now + minutes * 60, None
            it["length"] = max(it["length"], minutes * 60)
        else:
            it["snoozed"] = now + minutes * 60
        return False
    return _edit(item_id, change)


def pause(item_id: str) -> dict:
    def change(it, now):
        if it["kind"] != "timer":
            raise ClockError("Only a timer pauses.")
        if it["left"] is None and it["ringing"] is None:
            it["left"] = max(0.0, it["ends"] - now)
        return False
    return _edit(item_id, change)


def resume(item_id: str) -> dict:
    def change(it, now):
        if it["kind"] == "timer" and it["left"] is not None:
            it["ends"], it["left"] = now + it["left"], None
        return False
    return _edit(item_id, change)


def add_time(item_id: str, seconds: float = 60) -> dict:
    def change(it, now):
        if it["kind"] != "timer":
            raise ClockError("Only a timer takes more time.")
        if it["left"] is not None:
            it["left"] += seconds
        elif it["ringing"] is not None:
            it["ringing"], it["ends"] = None, now + seconds
        else:
            it["ends"] += seconds
        it["length"] += seconds
        return False
    return _edit(item_id, change)


def toggle(item_id: str, on: bool) -> dict:
    def change(it, now):
        if it["kind"] != "alarm":
            raise ClockError("Only an alarm turns on and off.")
        it["on"] = bool(on)
        it["snoozed"] = None
        if on:
            it["next"] = _next_alarm(it["hour"], it["minute"], it["days"], now)
        return False
    return _edit(item_id, change)


# ---------------------------------------------------------------- what the page and the watch show

def _due(it: dict) -> Optional[float]:
    if it["kind"] == "timer":
        return None if it["left"] is not None else it["ends"]
    if not it.get("on"):
        return None
    return it["snoozed"] or it["next"]


def describe(it: dict, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    out = dict(it)
    if it["kind"] == "timer":
        left = it["left"] if it["left"] is not None else max(0.0, it["ends"] - now)
        out["remaining"] = 0.0 if it["ringing"] else left
        out["progress"] = 1.0 if it["ringing"] else max(0.0, min(1.0, 1 - left / max(1.0, it["length"])))
        out["paused"] = it["left"] is not None
        out["title"] = it["label"] or f"{_length_words(it['length'])} timer"
    else:
        due = _due(it)
        out["remaining"] = None if due is None else max(0.0, due - now)
        out["title"] = it["label"] or "Alarm"
        out["time"] = f"{it['hour']:02d}:{it['minute']:02d}"
        out["repeat"] = _days_words(it["days"])
    out["due"] = _due(it)
    return out


def state(now: Optional[float] = None) -> dict:
    """Everything set, soonest first, the ringing ones first of all, with
    the time Flash read it at (so a clock elsewhere can count on its own)."""

    _ensure()
    now = time.time() if now is None else now
    with _lock:
        items = [describe(it, now) for it in _load()]
    items.sort(key=lambda d: (d["ringing"] is None, d["due"] is None, d["due"] or 0))
    return {"now": now, "items": items, "ringing": [d for d in items if d["ringing"] is not None]}


def _length_words(seconds: float) -> str:
    s = int(round(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    parts = []
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if sec and not h:
        parts.append(f"{sec} s")
    return " ".join(parts) or "0 s"


def _days_words(days: list[str]) -> str:
    if not days:
        return "Once"
    if days == DAYS:
        return "Every day"
    if days == DAYS[:5]:
        return "Weekdays"
    if days == DAYS[5:]:
        return "Weekends"
    return ", ".join(d.capitalize() for d in days)


# ---------------------------------------------------------------- ringing

def on_change(fn: Callable[[dict], None]) -> None:
    """FN(state) each time anything is set, changed, rings or stops."""
    if fn not in _listeners:
        _listeners.append(fn)


def _changed() -> None:
    _ensure()
    st = None
    for fn in list(_listeners):
        with contextlib.suppress(Exception):
            st = st or state()
            fn(st)


def tick(now: Optional[float] = None) -> list[dict]:
    """Ring whatever has come due; stop what has rung too long. The rung."""

    now = time.time() if now is None else now
    rang, missed = [], []
    with _lock:
        items = _load()
        changed = False
        for it in items:
            due = _due(it)
            if it.get("ringing") is None and due is not None and due <= now:
                it["ringing"] = now
                if it["kind"] == "alarm":
                    it["snoozed"] = None
                rang.append(it)
                changed = True
            elif it.get("ringing") is not None and now - it["ringing"] > RING_LIMIT:
                missed.append(dict(it))
                it["ringing"] = None
                if it["kind"] == "alarm":
                    if it["days"]:
                        it["next"] = _next_alarm(it["hour"], it["minute"], it["days"], now)
                    else:
                        it["on"] = False
                changed = True
        if missed:
            items = [it for it in items if not (it["kind"] == "timer" and it["id"] in {m["id"] for m in missed})]
        if changed:
            _save(items)
    for it in rang:
        _announce(it)
    for it in missed:
        with contextlib.suppress(Exception):
            from . import briefing
            briefing.notice("notify", "Missed", describe(it, now)["title"] + " rang for ten minutes.")
    if rang or missed:
        _changed()
        _ring_sound()
    return rang


def _announce(it: dict) -> None:
    title = describe(it)["title"]
    body = "Time's up." if it["kind"] == "timer" else f"It's {it['hour']:02d}:{it['minute']:02d}."
    with contextlib.suppress(Exception):
        from .notify import desktop
        desktop(title, body)


_sound: Optional[threading.Thread] = None


def ringing_now() -> bool:
    with _lock:
        return any(it.get("ringing") is not None for it in _load())


def _ring_sound() -> None:
    """The computer's speakers ring while anything does."""

    global _sound
    if _sound is not None and _sound.is_alive():
        return

    def run():
        try:
            from .sparkcalls import ringtone
        except Exception:  # noqa: BLE001
            return
        while ringing_now():
            with contextlib.suppress(Exception):
                ringtone(ringing_now)
            time.sleep(.5)

    _sound = threading.Thread(target=run, name="clock-ring", daemon=True)
    _sound.start()


def _quiet_if_none() -> None:
    pass        # the ring thread checks ringing_now() itself and ends


def _ensure() -> None:
    """The watcher, once, while this process runs."""

    global _thread
    if _thread is not None and _thread.is_alive():
        return

    def run():
        while True:
            with contextlib.suppress(Exception):
                tick()
            time.sleep(1.0)

    _thread = threading.Thread(target=run, name="clock", daemon=True)
    _thread.start()


# ---------------------------------------------------------------- the tool Flash uses

def words(d: dict) -> str:
    if d["kind"] == "timer":
        if d["ringing"]:
            return f"{d['id']}: {d['title']}, ringing now"
        left = _length_words(d["remaining"])
        return f"{d['id']}: {d['title']}, {left} left" + (" (paused)" if d["paused"] else "")
    when = "ringing now" if d["ringing"] else (
        "off" if not d.get("on") else f"{d['time']} {d['repeat'].lower()}")
    return f"{d['id']}: {d['title']} at {d['time']}, {when}"


def tool(action: str = "list", length: str = "", at: str = "", days: str = "", label: str = "",
         id: str = "", minutes: float = SNOOZE_MINUTES, **_: Any) -> str:
    """The clock tool's work, as text for the model."""

    action = str(action or "list").strip().lower()
    try:
        if action == "timer":
            return "Timer set. " + words(describe(add_timer(length, label)))
        if action == "alarm":
            return "Alarm set. " + words(describe(add_alarm(at, label, days)))
        if action in ("cancel", "delete"):
            return "Cancelled " + words(describe(cancel(id)))
        if action == "stop":
            stop(id)
            return f"Stopped {id}."
        if action == "snooze":
            snooze(id, float(minutes or SNOOZE_MINUTES))
            return f"Snoozed {id} for {int(float(minutes or SNOOZE_MINUTES))} minutes."
        if action == "pause":
            return "Paused " + words(describe(pause(id)))
        if action == "resume":
            return "Resumed " + words(describe(resume(id)))
        if action == "add_time":
            return "Added. " + words(describe(add_time(id, parse_length(length or "1m"))))
        if action in ("on", "off"):
            return ("On. " if action == "on" else "Off. ") + words(describe(toggle(id, action == "on")))
        st = state()
        if not st["items"]:
            return "No timers or alarms set."
        return "\n".join(words(d) for d in st["items"])
    except ClockError as exc:
        return f"Couldn't: {exc}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "clock",
        "description": (
            "Timers and alarms, like a phone's clock. A timer counts down a "
            "length and rings; an alarm rings at a time of day, once or on "
            "chosen days. Ringing shows on the computer, the web page and "
            "the watch until stopped or snoozed. Use this for 'set a timer "
            "for 10 minutes', 'wake me at 7', 'alarm weekdays at 6:45'. For a "
            "note at a time ('remind me to call Sam at 6') use remind_me."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["timer", "alarm", "list", "cancel", "stop", "snooze", "pause", "resume",
                                    "add_time", "on", "off"]},
                "length": {"type": "string", "description": "For a timer or add_time: '10m', '1h30m', '90s'."},
                "at": {"type": "string", "description": "For an alarm: '7:30', '6:45pm', '18:00'."},
                "days": {"type": "string",
                         "description": "For an alarm that repeats: 'weekdays', 'weekends', 'every day', "
                                        "'mon,wed,fri'. Leave out for once."},
                "label": {"type": "string", "description": "What it's for: 'Pasta', 'Gym'."},
                "id": {"type": "string", "description": "Which one, for cancel, stop, snooze, pause, resume, "
                                                        "add_time, on, off (from list)."},
                "minutes": {"type": "number", "description": "Snooze length, 9 if left out."},
            },
            "required": ["action"],
        },
    },
}
