"""Teaching: what the user is learning, kept as a graph of small facts.

When someone asks Flash to teach them something, the subject is cut
into propositions: single claims that can each be checked with one
question ("a derivative is the slope of a function at a point"). Each
names the ones it rests on, so the graph says what to teach first.

Every answer the user gives to a practice question moves an estimate of
whether they know that proposition, by knowledge tracing: a right answer
raises it, less so when guessing would have got it right, and a wrong
one lowers it, less so when it could have been a slip. Something known
comes back for review after a day, then two, four, and so on, and a
wrong answer brings it back the next day.

The plan is what to do next: reviews that are due, then the weakest
propositions whose groundwork is known. Only the agent uses this, as a
tool: there is nothing to open.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

from . import workspace

FILE = "tutor.json"
DAY = 86400
# Knowledge tracing: what it is thought known to start, the chance of
# learning it from one practice, of guessing it right, and of slipping.
START, LEARN, GUESS, SLIP = 0.2, 0.15, 0.2, 0.1
KNOWN = 0.9
MOST_INTERVAL_DAYS = 120
_lock = threading.Lock()


class TutorError(ValueError):
    """A request the graph cannot carry out, in words."""


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60]


def _load() -> dict:
    found = workspace._read(FILE, {})
    return found if isinstance(found, dict) else {}


def subjects() -> list[str]:
    return sorted(_load())


def _subject(data: dict, subject: str) -> dict:
    key = _key(subject)
    if not key or key not in data:
        raise TutorError(f"There is no subject {subject!r}. Add "
                         "propositions to it first.")
    return data[key]


def add(subject: str, propositions: list[dict]) -> list[str]:
    """Add PROPOSITIONS to SUBJECT: each {id, text, needs: [ids]}. Ids
    already there have their text and needs replaced, and what is known
    of them kept."""

    key = _key(subject)
    if not key:
        raise TutorError("Name the subject.")
    with _lock:
        data = _load()
        held = data.setdefault(key, {"name": subject, "props": {}})
        props = held["props"]
        added = []
        for given in propositions or []:
            pid = _key(given.get("id") or given.get("text"))
            text = str(given.get("text") or "").strip()
            if not pid or not text:
                continue
            old = props.get(pid, {})
            props[pid] = {"text": text,
                          "needs": [_key(n) for n in given.get("needs") or []
                                    if _key(n) and _key(n) != pid],
                          "known": old.get("known", START),
                          "seen": old.get("seen", 0),
                          "right": old.get("right", 0),
                          "streak": old.get("streak", 0),
                          "due": old.get("due", 0.0)}
            added.append(pid)
        missing = sorted({n for p in props.values() for n in p["needs"]}
                         - set(props))
        if missing:
            raise TutorError("These are needed but not given: "
                             + ", ".join(missing) + ".")
        if _cycle(props):
            raise TutorError("The propositions need each other in a loop.")
        workspace._write(FILE, data)
    return added


def _cycle(props: dict) -> bool:
    state: dict[str, int] = {}

    def visit(pid: str) -> bool:
        if state.get(pid) == 1:
            return True
        if state.get(pid) == 2:
            return False
        state[pid] = 1
        if any(visit(n) for n in props[pid]["needs"]):
            return True
        state[pid] = 2
        return False

    return any(visit(pid) for pid in props)


def trace(known: float, right: bool) -> float:
    """What is thought known after one answer."""

    if right:
        seen = known * (1 - SLIP) / (known * (1 - SLIP) + (1 - known) * GUESS)
    else:
        seen = known * SLIP / (known * SLIP + (1 - known) * (1 - GUESS))
    return seen + (1 - seen) * LEARN


def measure(subject: str, pid: str, right: bool,
            now: Optional[float] = None) -> dict:
    """Record one practice answer to proposition PID."""

    now = time.time() if now is None else now
    with _lock:
        data = _load()
        props = _subject(data, subject)["props"]
        prop = props.get(_key(pid))
        if prop is None:
            raise TutorError(f"There is no proposition {pid!r} in "
                             f"{subject!r}.")
        prop["known"] = round(trace(prop["known"], right), 4)
        prop["seen"] += 1
        prop["right"] += int(right)
        prop["streak"] = prop["streak"] + 1 if right else 0
        days = min(MOST_INTERVAL_DAYS, 2 ** max(0, prop["streak"] - 1)) \
            if right else 1
        prop["due"] = now + days * DAY
        workspace._write(FILE, data)
    return {"id": _key(pid), "known": prop["known"],
            "is_known": prop["known"] >= KNOWN, "review_in_days": days}


def plan(subject: str, limit: int = 5, now: Optional[float] = None
         ) -> list[dict]:
    """What to do next: reviews that are due, then the least known
    propositions whose groundwork is known."""

    now = time.time() if now is None else now
    props = _subject(_load(), subject)["props"]
    due, ready = [], []
    for pid, prop in props.items():
        if prop["known"] >= KNOWN:
            if prop["due"] and prop["due"] <= now:
                due.append(("review", pid, prop))
        elif all(props[n]["known"] >= KNOWN for n in prop["needs"]):
            ready.append(("teach" if not prop["seen"] else "practice",
                          pid, prop))
    due.sort(key=lambda d: d[2]["due"])
    ready.sort(key=lambda r: (r[2]["known"], len(r[2]["needs"])))
    return [{"do": do, "id": pid, "text": prop["text"],
             "known": prop["known"]}
            for do, pid, prop in (due + ready)[:limit]]


def describe(subject: str) -> dict[str, Any]:
    """How far the user is: how many propositions are known, and the
    graph, groundwork first."""

    held = _subject(_load(), subject)
    props = held["props"]
    order: list[str] = []

    def place(pid: str) -> None:
        if pid in order:
            return
        for need in props[pid]["needs"]:
            place(need)
        order.append(pid)

    for pid in sorted(props):
        place(pid)
    known = sum(1 for p in props.values() if p["known"] >= KNOWN)
    return {"subject": held["name"], "known": known, "of": len(props),
            "propositions": [{"id": pid, "text": props[pid]["text"],
                              "needs": props[pid]["needs"],
                              "known": props[pid]["known"]}
                             for pid in order]}


def forget(subject: str) -> None:
    with _lock:
        data = _load()
        _subject(data, subject)
        data.pop(_key(subject))
        workspace._write(FILE, data)
