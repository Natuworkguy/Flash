"""Reminders Flash keeps itself: "remind me at 6 to call Sam".

Not Apple Reminders (see reminders.py): these live in
~/.flash/timers.json, survive restarts, and when one comes due Flash
shows a desktop notification and puts it in the briefing, the same way
notify_user does. The sparks keeper fires them, so only the one Flash
holding the floor does, once.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from . import paths


def _file():
    return paths.FLASH_DIR / "timers.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict)]


def _save(timers: list[dict]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(timers, indent=1), encoding="utf-8")
    tmp.replace(path)


def parse_when(when: str, now: Optional[float] = None) -> float:
    """An ISO local time (2026-11-03T18:00) or "in 20m" / "in 2h" /
    "in 1d" / "in 90s". Raises ValueError on anything else."""

    text = str(when or "").strip().lower()
    now = time.time() if now is None else now
    if text.startswith("in "):
        text = text[3:].replace(" ", "")
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        unit, amount = text[-1:], text[:-1]
        if unit in units and amount.replace(".", "", 1).isdigit():
            return now + float(amount) * units[unit]
        raise ValueError(
            f"Can't read {when!r}: say 'in 20m', 'in 2h' or a time.")
    try:
        return datetime.fromisoformat(str(when).strip()).timestamp()
    except ValueError:
        raise ValueError(
            f"Can't read {when!r}: give a time like 2026-11-03T18:00, "
            "or 'in 20m'."
        ) from None


def add(text: str, when: str, now: Optional[float] = None) -> dict:
    text = str(text or "").strip()
    if not text:
        raise ValueError("Say what to remind the user of.")
    now = time.time() if now is None else now
    at = parse_when(when, now)
    if at <= now:
        raise ValueError("That time has already passed.")
    timer = {"id": uuid.uuid4().hex[:8], "text": text, "at": at, "made": now}
    timers = _load()
    timers.append(timer)
    _save(timers)
    return timer


def upcoming() -> list[dict]:
    return sorted(_load(), key=lambda t: t.get("at", 0))


def cancel(timer_id: str) -> Optional[dict]:
    timers = _load()
    for timer in timers:
        if timer.get("id") == str(timer_id).strip():
            timers.remove(timer)
            _save(timers)
            return timer
    return None


def _remind(timer: dict) -> None:
    from . import briefing
    from .notify import desktop

    with contextlib.suppress(Exception):
        desktop("Reminder", timer["text"])
    with contextlib.suppress(Exception):
        briefing.notice("notify", "Reminder", timer["text"])


def fire_due(now: Optional[float] = None) -> list[dict]:
    """Remind of every timer whose time has come, and forget them."""

    now = time.time() if now is None else now
    timers = _load()
    due = [t for t in timers if float(t.get("at", 0)) <= now]
    if not due:
        return []
    _save([t for t in timers if t not in due])
    for timer in due:
        _remind(timer)
    return due


def words(timer: dict) -> str:
    at = datetime.fromtimestamp(timer["at"]).astimezone()
    when = at.strftime("%a %d %b %H:%M")
    return f"{timer['id']}: {when}: {timer['text']}"


def tool(action: str = "list", text: str = "", when: str = "",
         id: str = "", **_: Any) -> str:
    """The remind_me tool's work, as text for the model."""

    action = str(action or "list").strip().lower()
    try:
        if action == "add":
            return "Set. " + words(add(text, when))
        if action == "cancel":
            gone = cancel(id)
            if gone is None:
                return f"No reminder {id!r}."
            return f"Cancelled {words(gone)}"
        if action == "list":
            items = upcoming()
            if not items:
                return "No reminders set."
            return "\n".join(map(words, items))
    except (ValueError, OSError) as err:
        return f"Error: {err}"
    return "Error: action is add, list or cancel."
