"""Good morning: a spark that says good morning to the user each day, in
its chat with them, at the time they set; and, for a team lead (or any
spark asked to), with a rundown of what the user left unfinished.

What is unfinished is gathered here, so the spark need not go looking:
the checklists of the user's chats with steps still open, their open
Apple Reminders that are due by the end of today (on a Mac), the hires
waiting on their yes, and the work they handed to sparks that is not
done. Each morning message is sent once a day, never in quiet hours,
and not at all when Flash was off until well past the time.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

# How long after its time a good morning still makes sense: past this,
# it waits for tomorrow rather than arriving at lunch.
LATE_HOURS = 3
# Chats older than this are not looked through for open steps.
CHAT_DAYS = 14
MOST_LINES = 12

TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I)


def parse_time(text) -> str:
    """'7', '7:30', '7:30am', '19:05' as 'HH:MM'; '' or 'off' as ''."""

    text = str(text or "").strip().lower()
    if text in ("", "off", "none", "no", "false"):
        return ""
    found = TIME_RE.match(text)
    if not found:
        raise ValueError("a time of day, as 7:30 or 7:30am")
    hour, minute, half = int(found.group(1)), int(found.group(2) or 0), found.group(3)
    if half:
        if not 1 <= hour <= 12:
            raise ValueError("a time of day, as 7:30 or 7:30am")
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ValueError("a time of day, as 7:30 or 7:30am")
    return f"{hour:02d}:{minute:02d}"


def due(at: str, sent: str, now: float | None = None) -> bool:
    """Whether a good morning set for AT ('HH:MM'), last sent on the day
    SENT ('YYYY-MM-DD'), is due NOW: today's time has come, it is not
    long past, and today's is not sent yet."""

    if not at:
        return False
    when = datetime.fromtimestamp(time.time() if now is None else now)
    hour, minute = (int(x) for x in at.split(":"))
    start = when.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (sent != when.strftime("%Y-%m-%d") and start <= when
            < start + timedelta(hours=LATE_HOURS))


def today(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")


# --- What the user left unfinished -----------------------------------------

def open_chat_steps(now: float | None = None) -> list[str]:
    """Each recent chat whose last checklist has steps not done: its
    title and what is left."""

    from . import workspace

    now = time.time() if now is None else now
    out = []
    for saved in workspace.load_chats():
        if now - float(saved.get("updated") or 0) > CHAT_DAYS * 86400:
            continue
        plan = None
        for event in reversed(saved.get("log") or []):
            if isinstance(event, dict) and event.get("type") == "plan":
                plan = event.get("steps") or []
                break
        left = [s.get("text", "") for s in plan or [] if isinstance(s, dict) and s.get("status") != "done"]
        if not left:
            continue
        title = saved.get("title") or "A chat"
        out.append(f'Chat "{title}": {len(left)} of {len(plan)} steps left, next: {left[0]}')
    return out


def open_reminders(now: float | None = None) -> list[str]:
    """The user's open Apple Reminders due by the end of today, overdue
    first, on a Mac; none elsewhere."""

    from . import reminders

    try:
        if not reminders.available():
            return []
        found = reminders.reminders()
    except Exception:  # noqa: BLE001 - Reminders not allowed, or not there: none
        return []
    end = datetime.fromtimestamp(time.time() if now is None else now).replace(hour=23, minute=59)
    out = []
    for r in found:
        due_at = str(r.get("due") or "")
        try:
            when = datetime.fromisoformat(due_at).replace(tzinfo=None) if due_at else None
        except ValueError:
            when = None
        if when is not None and when > end:
            continue
        late = " (overdue)" if when is not None and when.date() < end.date() else ""
        out.append(f"Reminder: {r.get('title') or r.get('name') or 'untitled'}{late}")
    return out


def open_spark_work() -> list[str]:
    """Hires waiting on the user, and jobs they gave sparks not done."""

    from . import sparks

    out = []
    with_hires = sparks.hires()
    if with_hires:
        out.append(f"{len(with_hires)} hire{'s' if len(with_hires) != 1 else ''} waiting for your yes or no")
    for spark in sparks.all_sparks():
        jobs = [item for item in spark.inbox if item.get("job") and item.get("from") == "the user"]
        for job in jobs[:3]:
            out.append(f"{spark.name} has not finished what you gave it: {str(job.get('text'))[:120]}")
    return out


def unfinished(now: float | None = None) -> list[str]:
    lines = [*open_reminders(now), *open_chat_steps(now), *open_spark_work()]
    return lines[:MOST_LINES]


# --- The message ------------------------------------------------------------

NOTE = (
    "(It is {when}: time for your good morning to the user, here in your "
    "chat with them. Say good morning in your own words, as yourself: "
    "warm and short, a line or two, something about the day if you know "
    "it (what you have been working on for them, what is coming up). No "
    "list of tools, no questions you could answer yourself.{tasks})"
)

TASKS = (
    " Then, as their lead, tell them what they left unfinished, from "
    "this list (gathered for you just now), most pressing first, in a "
    "few short lines; offer to take one on if your team could:\n{lines}"
)

NO_TASKS = (
    " As their lead you looked: nothing is left unfinished on their "
    "lists today. Say so in a few words, it is good news."
)


def note(with_tasks: bool, now: float | None = None) -> str:
    when = datetime.fromtimestamp(time.time() if now is None else now).strftime("%A %d %B, %H:%M")
    tasks = ""
    if with_tasks:
        lines = unfinished(now)
        tasks = TASKS.format(lines="\n".join(f"- {line}" for line in lines)) if lines else NO_TASKS
    return NOTE.format(when=when, tasks=tasks)
