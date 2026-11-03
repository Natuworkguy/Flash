"""Apple Reminders, on a Mac: listed, added to and ticked off, through
AppleScript. Nothing to sign in to; macOS asks once to let Flash control
Reminders.

Due dates cross as seconds from 2001-01-01 00:00 on this computer's
clock, as Apple Calendar's do (see calendars), so no date text is read in
the user's locale.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import computer

TIMEOUT = 60
MOST = 200
# A wall-clock time, with no zone on purpose: AppleScript's have none.
_WALL_2001 = datetime(2001, 1, 1)  # noqa: DTZ001
_BASE = (
    "set b to current date",
    "set day of b to 1",
    "set year of b to 2001",
    "set month of b to January",
    "set time of b to 0",
)
_TEXT = (
    "on t(x)",
    'if x is missing value then return ""',
    "return x as text",
    "end t",
    "on s(x, b)",
    'if x is missing value then return ""',
    "return (x - b) as text",
    "end s",
)
_LIST = (
    *_TEXT,
    "on run argv",
    *_BASE,
    "set fs to character id 31",
    "set rs to character id 30",
    'set out to ""',
    'tell application "Reminders"',
    'if item 1 of argv is "" then',
    "set ls to every list",
    "else",
    "set ls to {list (item 1 of argv)}",
    "end if",
    "repeat with l in ls",
    "set ln to name of l",
    'if item 2 of argv is "true" then',
    "set rems to every reminder of l",
    "else",
    "set rems to (every reminder of l whose completed is false)",
    "end if",
    "repeat with r in rems",
    "set out to out & ln & fs & (id of r) & fs & my t(name of r) & fs & "
    "my s(due date of r, b) & fs & (completed of r) & fs & "
    "my t(body of r) & rs",
    "end repeat",
    "end repeat",
    "end tell",
    "return out",
    "end run",
)
_LISTS = (
    'set out to ""',
    'tell application "Reminders"',
    "repeat with l in lists",
    "set out to out & (name of l) & (character id 30)",
    "end repeat",
    "end tell",
    "return out",
)
_ADD = (
    "on run argv",
    *_BASE,
    'tell application "Reminders"',
    'if item 1 of argv is "" then',
    "set l to default list",
    "else",
    "set l to list (item 1 of argv)",
    "end if",
    "set r to make new reminder at end of reminders of l with properties "
    "{name:item 2 of argv, body:item 3 of argv}",
    'if item 4 of argv is not "" then',
    "set due date of r to b + (item 4 of argv as number)",
    "end if",
    "return (name of l) & (character id 31) & (id of r)",
    "end tell",
    "end run",
)
_COMPLETE = (
    "on run argv",
    'tell application "Reminders"',
    "set r to reminder id (item 1 of argv)",
    "set completed of r to true",
    "return name of r",
    "end tell",
    "end run",
)


class RemindersError(Exception):
    """Something Reminders would not do, in words."""


def available() -> bool:
    return computer.system() == "mac"


def _run(script: tuple, *argv: str) -> str:
    if not available():
        raise RemindersError("Apple Reminders is only on a Mac.")
    command = ["osascript"]
    for line in script:
        command += ["-e", line]
    try:
        return computer._run([*command, *argv], timeout=TIMEOUT)
    except computer.ComputerError as exc:
        if computer._not_allowed(exc, "Reminders"):
            raise RemindersError(
                "macOS has to let the app Flash runs in (Terminal, iTerm "
                "and the like) control Reminders: System Settings > "
                "Privacy & Security > Automation, and Reminders.") from None
        raise RemindersError(f"Reminders said no: {exc}") from None


def _records(text: str) -> list[list[str]]:
    return [r.split("\x1f") for r in text.split("\x1e") if r.strip()]


def _seconds(moment: datetime) -> int:
    wall = moment.astimezone().replace(tzinfo=None)
    return int((wall - _WALL_2001).total_seconds())


def _moment(seconds: str) -> str:
    if not seconds.strip():
        return ""
    wall = _WALL_2001 + timedelta(seconds=float(seconds))
    return wall.astimezone().isoformat(timespec="minutes")


def lists() -> list[str]:
    return [r[0] for r in _records(_run(_LISTS))]


def reminders(in_list: str = "", done: bool = False) -> list[dict]:
    """The reminders in IN_LIST (every list, if left out): only those not
    done, unless DONE."""

    out = []
    for row in _records(_run(_LIST, str(in_list or "").strip(),
                             "true" if done else "false")):
        if len(row) < 6:
            continue
        name, rid, title, due, completed, notes = row[:6]
        try:
            due = _moment(due)
        except ValueError:
            due = ""
        out.append({"id": rid, "title": title, "list": name, "due": due,
                    "done": completed == "true", "notes": notes[:500]})
    return out[:MOST]


def add(title: str, in_list: str = "", due: Any = "",
        notes: str = "") -> dict:
    from .calendars import CalendarError, parse_time

    title = " ".join(str(title or "").split())[:300]
    if not title:
        raise RemindersError("Give the reminder a title.")
    when = ""
    if due:
        try:
            when = str(_seconds(parse_time(due)))
        except CalendarError as exc:
            raise RemindersError(str(exc)) from None
    said = _run(_ADD, str(in_list or "").strip(), title, str(notes or ""),
                when)
    name, _, rid = said.partition("\x1f")
    return {"id": rid, "title": title, "list": name,
            "due": _moment(when) if when else "", "done": False}


def complete(reminder_id: str) -> str:
    reminder_id = str(reminder_id or "").strip()
    if not reminder_id:
        raise RemindersError("Give the reminder's id, from list.")
    return _run(_COMPLETE, reminder_id)


def describe(found: dict) -> str:
    bits = [("[done] " if found.get("done") else "") + found["title"]]
    if found.get("due"):
        bits.append(f"due {found['due']}")
    bits.append(f"in {found['list']}")
    bits.append(f"[{found['id']}]")
    return " ".join(bits)
