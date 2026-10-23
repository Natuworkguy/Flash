"""The terminal's conversations, kept so they can be picked up again.

Each is a file under ~/.flash/sessions: what the model saw, a title (its
first message), where it was started, and its forks. A fork is another
way the conversation went from some turn on; the one showing is the
conversation, and the others are kept aside, whole, to switch back to.
/resume picks one up, /fork starts a fork, and /forks shows them as a
tree.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from .paths import FLASH_DIR

TITLE_CHARS = 60
LABEL_CHARS = 60
MOST_KEPT = 200
IMAGE_NOTE = "[an image was attached]"
MAIN = "main"
ID_RE = re.compile(r"^[0-9a-f]{8}$")


class SessionError(ValueError):
    """A fork or session that is not there, and why."""


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    cwd: str = field(default_factory=lambda: os.getcwd())
    messages: list = field(default_factory=list)
    # Its forks, by id: where each came from, and, for every fork but the
    # one showing, what it was, kept aside. Empty until it is forked.
    forks: dict = field(default_factory=dict)
    fork: str = ""


def folder() -> Path:
    return FLASH_DIR / "sessions"


def _path(session_id: str) -> Path:
    if not ID_RE.match(session_id):
        raise SessionError(f"No conversation {session_id!r}.")
    return folder() / f"{session_id}.json"


def _plain(message: Any) -> Optional[dict]:
    """MESSAGE as it can be kept: images become a note."""

    if not isinstance(message, dict):
        return None
    kept = {k: v for k, v in message.items() if k != "images"}
    if message.get("images"):
        kept["content"] = f"{kept.get('content') or ''}\n{IMAGE_NOTE}".strip()
    try:
        json.dumps(kept)
    except (TypeError, ValueError):
        kept = json.loads(json.dumps(kept, default=str))
    return kept


def _said(message: dict) -> str:
    return " ".join(str(message.get("content") or "").split())


def is_turn(message: dict, notes: tuple[str, ...] = ()) -> bool:
    """Whether MESSAGE is one the user sent, starting a turn: not a note
    Flash put in among a turn's steps."""

    text = str(message.get("content") or "")
    return message.get("role") == "user" and not any(
        text.startswith(note) for note in notes if note)


def save(session: Session, messages: list) -> Session:
    """Keep SESSION as MESSAGES has it now."""

    session.messages = [m for m in map(_plain, messages) if m]
    if not session.title:
        first = next((m for m in messages if isinstance(m, dict)
                      and m.get("role") == "user" and _said(m)), None)
        if first:
            title = _said(first)
            session.title = title if len(title) <= TITLE_CHARS \
                else title[:TITLE_CHARS - 1].rstrip() + "…"
    session.updated = time.time()
    if not session.messages and not session.forks:
        return session
    path = _path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(asdict(session)), encoding="utf-8")
    os.replace(partial, path)
    _trim()
    return session


def load(session_id: str) -> Session:
    try:
        raw = json.loads(_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SessionError(f"No conversation {session_id!r}.") from None
    names = {f.name for f in fields(Session)}
    return Session(**{k: v for k, v in raw.items() if k in names})


def listing() -> list[dict]:
    """Every kept conversation, the latest first."""

    found = []
    for path in folder().glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        messages = raw.get("messages") or []
        found.append({
            "id": raw.get("id", path.stem), "title": raw.get("title") or "",
            "updated": float(raw.get("updated") or 0),
            "cwd": raw.get("cwd") or "",
            "turns": sum(1 for m in messages if m.get("role") == "user"),
            "forks": len(raw.get("forks") or {}),
        })
    found.sort(key=lambda s: -s["updated"])
    return found


def remove(session_id: str) -> bool:
    try:
        _path(session_id).unlink()
    except (OSError, SessionError):
        return False
    return True


def _trim() -> None:
    for old in listing()[MOST_KEPT:]:
        remove(old["id"])


# --- Forks ---------------------------------------------------------------


def _park(session: Session, messages: list) -> None:
    session.forks[session.fork]["messages"] = [
        m for m in map(_plain, messages) if m]


def fork(session: Session, messages: list, back: int = 0,
         notes: tuple[str, ...] = ()) -> tuple[str, list]:
    """Fork SESSION, BACK turns before now (0: from here). The way it
    went is kept as the fork it was; the new fork goes on from there.
    Returns the new fork's id and the messages it starts with."""

    turns = [i for i, m in enumerate(messages) if is_turn(m, notes)]
    if back < 0 or back > len(turns):
        raise SessionError(
            f"There {'is' if len(turns) == 1 else 'are'} only "
            f"{len(turns)} turn{'' if len(turns) == 1 else 's'} to go back.")
    keep = len(messages) if back == 0 else turns[-back]
    if not session.forks:
        session.forks[MAIN] = {"id": MAIN, "parent": "", "label": "Main",
                               "created": session.created}
        session.fork = MAIN
    _park(session, messages)
    kept = list(messages[:keep])
    last = next((_said(m) for m in reversed(kept)
                 if m.get("role") in ("user", "assistant") and _said(m)), "")
    made = uuid.uuid4().hex[:6]
    session.forks[made] = {
        "id": made, "parent": session.fork, "created": time.time(),
        "label": (last[:LABEL_CHARS] or "From the start"),
    }
    session.fork = made
    save(session, kept)
    return made, kept


def switch(session: Session, messages: list, fork_id: str) -> list:
    """Show fork FORK_ID, keeping the one showing aside: its messages."""

    found = session.forks.get(fork_id)
    if found is None:
        raise SessionError(f"There is no fork {fork_id!r}. /forks lists "
                           "them.")
    if fork_id == session.fork:
        return messages
    _park(session, messages)
    now = list(found.pop("messages", None) or [])
    session.fork = fork_id
    save(session, now)
    return now


def delete_fork(session: Session, messages: list, fork_id: str) -> list:
    """Let fork FORK_ID go, as the web's do: its own forks move up a
    level, the one showing goes back to where it came from, and with one
    fork left there are none. The messages now showing."""

    gone = session.forks.get(fork_id)
    if gone is None:
        raise SessionError(f"There is no fork {fork_id!r}.")
    parent = gone.get("parent", "")
    kids = sorted((f for f in session.forks.values()
                   if f.get("parent") == fork_id),
                  key=lambda f: f.get("created", 0))
    if not parent and kids:
        parent = kids[0]["id"]
        kids[0]["parent"] = ""
    for kid in kids:
        if kid["id"] != parent:
            kid["parent"] = parent
    if fork_id == session.fork:
        showing = parent if parent in session.forks else next(
            f for f in session.forks if f != fork_id)
        messages = list(session.forks[showing].pop("messages", None) or [])
        session.fork = showing
    del session.forks[fork_id]
    if len(session.forks) < 2:
        session.forks.clear()
        session.fork = ""
    save(session, messages)
    return messages


def tree(session: Session, messages: list) -> list[dict]:
    """The forks in tree order, each with its depth, the lines to draw
    before it, how many turns it has, and whether it is showing."""

    kids: dict[str, list[dict]] = {}
    for f in session.forks.values():
        kids.setdefault(f.get("parent", ""), []).append(f)
    for group in kids.values():
        group.sort(key=lambda f: f.get("created", 0))
    rows: list[dict] = []

    def walk(f: dict, lead: str, last: bool, depth: int) -> None:
        showing = f["id"] == session.fork
        said = messages if showing else f.get("messages") or []
        rows.append({
            "id": f["id"], "label": f.get("label", ""), "depth": depth,
            "lead": lead + ("" if depth == 0 else
                            "└─ " if last else "├─ "),
            "turns": sum(1 for m in said if m.get("role") == "user"),
            "last": next((_said(m)[:LABEL_CHARS] for m in reversed(said)
                          if m.get("role") == "assistant" and _said(m)), ""),
            "current": showing,
        })
        below = kids.get(f["id"], [])
        for i, kid in enumerate(below):
            walk(kid, lead + ("" if depth == 0 else
                              "   " if last else "│  "),
                 i == len(below) - 1, depth + 1)

    roots = [f for f in session.forks.values()
             if f.get("parent", "") not in session.forks]
    for root in sorted(roots, key=lambda f: f.get("created", 0)):
        walk(root, "", True, 0)
    return rows
