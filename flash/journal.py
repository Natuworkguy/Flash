"""The journal: a few lines on each day, written by Flash from the chats
it had with the user, and rolled up into weeks and months.

Memory keeps facts; the journal keeps what happened. Once a day is
over, the chats it held (in the browser and the terminal alike) are
read and written up as a handful of bullets: what the user worked on,
what was decided, what was left open. A finished week gets a summary
of its days, and a finished month of its own, so "what was I doing in
March?" has an answer that fits in a model's window.

The sparks keeper writes it, the one Flash that holds the floor, so
two open Flashes never write the same day. A day with no chats gets no
entry. Entries are Markdown files under ~/.flash/journal, to read,
edit or delete like any other note.
"""

import contextlib
import json
import os
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .paths import FLASH_DIR

SETTING = "JOURNAL"

# Days looked back over for ones not written yet: on the first run, and
# after Flash was not open for a while.
CATCH_UP_DAYS = 7

# What a day's write-up reads: each side of an exchange cut to this,
# each chat to CHAT_CHARS, and the whole day to DAY_CHARS, for a small
# local model's window.
SAID_CHARS = 400
CHAT_CHARS = 3000
DAY_CHARS = 14000

# The most entries a search answers with.
FOUND_MOST = 8

DAY_PROMPT = """\
Write the user's journal entry for {day}, from their chats with Flash \
that day, below. Note what they worked on, what got decided or done, \
what they learned, and what was left open: one bullet each, at most \
eight, by topic rather than by chat. Write in the second person and \
the past tense ("You fixed ..."), plainly, keeping the names, numbers \
and places that matter. Leave out small talk and anything about Flash \
itself. Reply with the bullets only."""

ROLLUP_PROMPT = """\
Below are the user's journal entries for {span}. Write the summary of \
that {kind}: the main things they worked on and what came of them, at \
most eight bullets, in the second person and the past tense. Keep \
names, numbers and decisions; leave out the day-to-day detail. Reply \
with the bullets only."""

_KEY_RE = re.compile(r"^\d{4}-(?:\d{2}-\d{2}|W\d{2}|\d{2})$")


def enabled() -> bool:
    return (os.environ.get(SETTING) or "1").strip().lower() \
        not in ("0", "off", "false", "no")


def folder() -> Path:
    return FLASH_DIR / "journal"


def _state_file() -> Path:
    return folder() / "state.json"


def _state() -> dict:
    try:
        found = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(partial, path)


# --- Where each entry lives ------------------------------------------------


def _local_day(when: float) -> date:
    """The day WHEN fell on, here."""

    return datetime.fromtimestamp(when).astimezone().date()


def _today() -> date:
    return datetime.now().astimezone().date()


def _day_words(day: date) -> str:
    """Thursday 29 October 2026, the day put together by hand, since
    strftime has no day without a leading zero on Windows."""

    return f"{day:%A} {day.day} {day:%B %Y}"


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_days(key: str) -> list[date]:
    year, week = int(key[:4]), int(key[6:])
    monday = date.fromisocalendar(year, week, 1)
    return [monday + timedelta(days=n) for n in range(7)]


def path_of(key: str) -> Path:
    """The file for KEY: a day (2026-10-29), a week (2026-W44) or a
    month (2026-10)."""

    if not _KEY_RE.match(key):
        raise ValueError(f"{key!r} is not a day, a week or a month, like "
                         "2026-10-29, 2026-W44 or 2026-10.")
    kind = "weeks" if "W" in key else "days" if key.count("-") == 2 \
        else "months"
    return folder() / kind / f"{key}.md"


def heading(key: str) -> str:
    if "W" in key:
        days = week_days(key)
        return (f"Week {int(key[6:])}, {key[:4]} "
                f"({days[0].day} {days[0]:%B} to {days[-1].day} "
                f"{days[-1]:%B})")
    if key.count("-") == 2:
        return _day_words(date.fromisoformat(key))
    return f"{date(int(key[:4]), int(key[5:]), 1):%B %Y}"


def read(key: str) -> str:
    try:
        return path_of(key).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(key: str, bullets: str) -> Path:
    path = path_of(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {heading(key)}\n\n{bullets.strip()}\n",
                    encoding="utf-8")
    return path


def entries() -> list[str]:
    """Every entry's key, newest first: days, then weeks, then months."""

    found = []
    for kind in ("days", "weeks", "months"):
        with contextlib.suppress(OSError):
            found += sorted((p.stem for p in (folder() / kind).glob("*.md")),
                            reverse=True)
    return found


# --- What a day held ---------------------------------------------------------


def _cut(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _web_exchanges(chat: dict) -> list[tuple[float, str, str]]:
    """CHAT's exchanges, each (when, asked, answered). A reply's time is
    its stats line's; one with none takes the chat's last change."""

    out: list[tuple[float, str, str]] = []
    asked, answered = "", ""
    for event in chat.get("log") or []:
        kind = event.get("type")
        if kind == "user":
            if asked or answered:
                out.append((0.0, asked, answered))
            asked, answered = str(event.get("text") or ""), ""
        elif kind == "assistant" and event.get("text"):
            answered = f"{answered} {event['text']}".strip()
        elif kind == "stats" and (asked or answered):
            out.append((float(event.get("at") or 0), asked, answered))
            asked, answered = "", ""
    if asked or answered:
        out.append((0.0, asked, answered))
    updated = float(chat.get("updated") or 0)
    return [(when or updated, a, b) for when, a, b in out]


def _transcript(title: str, exchanges: list[tuple[str, str]]) -> str:
    lines = [f"Chat: {title or 'Untitled'}"]
    for asked, answered in exchanges:
        if asked:
            lines.append(f"User: {_cut(asked, SAID_CHARS)}")
        if answered:
            lines.append(f"Flash: {_cut(answered, SAID_CHARS)}")
    text = "\n".join(lines)
    return text if len(text) <= CHAT_CHARS else \
        text[:CHAT_CHARS] + "\n[the rest of this chat is cut]"


def gather(day: date, state: Optional[dict] = None) -> tuple[str, dict]:
    """What DAY's chats said, as one transcript, and the terminal
    sessions it read, by id, with how many of their messages: "" when
    there were none."""

    from . import sessions, workspace

    state = state if state is not None else _state()
    parts: list[str] = []
    for chat in sorted(workspace.load_chats(),
                       key=lambda c: float(c.get("created") or 0)):
        said = [(a, b) for when, a, b in _web_exchanges(chat)
                if when and _local_day(when) == day]
        if said:
            parts.append(_transcript(str(chat.get("title") or ""), said))

    # The terminal keeps no time on each message: what a session added
    # since it was last journaled goes in the day it last changed.
    read_up_to = state.get("sessions") or {}
    reached: dict[str, int] = {}
    for found in sessions.listing():
        if _local_day(float(found.get("updated") or 0)) != day:
            continue
        with contextlib.suppress(sessions.SessionError, OSError, ValueError):
            session = sessions.load(str(found.get("id")))
            messages = [m for m in session.messages if isinstance(m, dict)]
            new = messages[int(read_up_to.get(session.id) or 0):]
            said, asked = [], ""
            for message in new:
                if message.get("role") == "user":
                    asked = str(message.get("content") or "")
                elif message.get("role") == "assistant" and \
                        message.get("content"):
                    said.append((asked, str(message["content"])))
                    asked = ""
            if said:
                parts.append(_transcript(session.title, said))
            reached[session.id] = len(messages)

    text = "\n\n".join(parts)
    if len(text) > DAY_CHARS:
        text = text[:DAY_CHARS] + "\n[the rest of the day is cut]"
    return text, reached


# --- Writing it ---------------------------------------------------------


def _ask(client: Any, model: str, system: str, user: str,
         options: Optional[dict] = None) -> str:
    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        options=options or {}, stream=False,
    )
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    content = (message.get("content") if isinstance(message, dict)
               else getattr(message, "content", ""))
    return _bullets(str(content or ""))


def _bullets(text: str) -> str:
    """The bullets in what the model said, each "- " led, and nothing
    around them: no heading, no "Here is your entry"."""

    out = []
    for line in text.splitlines():
        line = line.strip()
        found = re.match(r"^(?:[-*•+]|\d+[.)])\s+(.*)$", line)
        if found and found.group(1).strip():
            out.append(f"- {found.group(1).strip()}")
    return "\n".join(out)


def write_day(client: Any, model: str, day: date,
              options: Optional[dict] = None) -> Optional[Path]:
    """Write DAY's entry from its chats. None when it had none, or the
    model wrote nothing usable."""

    state = _state()
    text, reached = gather(day, state)
    if not text:
        return None
    bullets = _ask(client, model, DAY_PROMPT.format(day=_day_words(day)),
                   text, options)
    if not bullets:
        return None
    path = _write(day.isoformat(), bullets)
    state.setdefault("sessions", {}).update(reached)
    _save_state(state)
    return path


def _rollup(client: Any, model: str, key: str, days: list[date],
            kind: str, options: Optional[dict]) -> Optional[Path]:
    written = [(d, read(d.isoformat())) for d in days]
    written = [(d, text) for d, text in written if text]
    if not written:
        return None
    body = "\n\n".join(text for _, text in written)
    bullets = _ask(client, model, ROLLUP_PROMPT.format(
        span=heading(key), kind=kind), body, options)
    return _write(key, bullets) if bullets else None


def _month_days(year: int, month: int) -> list[date]:
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    return [first + timedelta(days=n) for n in range((after - first).days)]


def catch_up(client: Any, model: str, today: Optional[date] = None,
             options: Optional[dict] = None,
             doing: Optional[Callable[[str], None]] = None) -> list[Path]:
    """Write every day before TODAY (within CATCH_UP_DAYS) that has no
    entry yet, then every finished week and month among them that has
    none. What was written."""

    today = today or _today()
    state = _state()
    quiet = set(state.get("quiet") or [])
    written: list[Path] = []
    days = [today - timedelta(days=n) for n in range(CATCH_UP_DAYS, 0, -1)]
    for day in days:
        key = day.isoformat()
        if key in quiet or path_of(key).exists():
            continue
        if doing:
            doing(f"Writing the journal for {_day_words(day)}")
        path = write_day(client, model, day, options)
        if path is None:
            state = _state()
            state["quiet"] = sorted({*state.get("quiet", []), key})[-60:]
            _save_state(state)
        else:
            written.append(path)

    for week in sorted({week_key(day) for day in days}):
        if week_days(week)[-1] >= today or path_of(week).exists():
            continue
        path = _rollup(client, model, week, week_days(week), "week", options)
        if path:
            written.append(path)
    for year, month in sorted({(day.year, day.month) for day in days}):
        key = f"{year}-{month:02d}"
        if (year, month) >= (today.year, today.month) or \
                path_of(key).exists():
            continue
        path = _rollup(client, model, key, _month_days(year, month),
                       "month", options)
        if path:
            written.append(path)
    return written


def tick(client_for: Callable[[], Any], model: str,
         options: Optional[dict] = None,
         today: Optional[date] = None) -> list[Path]:
    """Once a day, from the sparks keeper: catch the journal up, unless
    it was turned off, no model is set, or it already ran today."""

    today = today or _today()
    if not enabled() or not model:
        return []
    state = _state()
    if state.get("checked") == today.isoformat():
        return []
    # Marked first: a model that is down is not asked again every
    # minute, and the days it missed are written tomorrow.
    state["checked"] = today.isoformat()
    state["at"] = time.time()
    _save_state(state)
    return catch_up(client_for(), model, today, options)


# --- Reading it back ---------------------------------------------------------


def search(query: str) -> list[tuple[str, list[str]]]:
    """Entries with every word of QUERY in them, newest first, each with
    its lines that have any of them."""

    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    if not words:
        return []
    found = []
    for key in entries():
        text = read(key)
        lowered = text.lower()
        if all(w in lowered for w in words):
            lines = [ln for ln in text.splitlines()[1:]
                     if any(w in ln.lower() for w in words)]
            found.append((key, lines))
        if len(found) >= FOUND_MOST:
            break
    return found


def listing(limit: int = 14) -> str:
    keys = entries()
    if not keys:
        return ("The journal is empty. Flash writes a day up once it is "
                "over, from the chats it held.")
    days = [k for k in keys if k.count("-") == 2][:limit]
    rest = [k for k in keys if k.count("-") != 2]
    lines = [f"- {k}: {heading(k)}" for k in days + rest]
    return "Journal entries, newest first:\n" + "\n".join(lines)


def answer(action: str = "list", key: str = "", query: str = "") -> str:
    """The journal tool: list, read KEY, or search for QUERY."""

    action = (action or "list").strip().lower()
    if action == "read":
        key = key.strip()
        try:
            text = read(key)
        except ValueError as exc:
            return f"Error: {exc}"
        if text:
            return text
        if key.count("-") == 1 and "W" not in key:
            # A month not rolled up yet: its days, as they are.
            year, month = int(key[:4]), int(key[5:])
            days = [read(d.isoformat()) for d in _month_days(year, month)]
            days = [d for d in days if d]
            if days:
                return "\n\n".join(days)
        if "W" in key:
            days = [read(d.isoformat()) for d in week_days(key)]
            days = [d for d in days if d]
            if days:
                return "\n\n".join(days)
        return f"There is no journal entry for {key}."
    if action == "search":
        found = search(query)
        if not found:
            return f"Nothing in the journal mentions {query!r}."
        return "\n\n".join(
            f"{heading(key)} ({key}):\n" + "\n".join(lines)
            for key, lines in found)
    return listing()
