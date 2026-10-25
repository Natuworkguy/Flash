"""@user: a spark calling on the user.

A spark writes @user in its report, or in a message in its team's chat,
when it needs the user: a sign-off, a question only they can answer,
news that cannot wait. The words stay where they were written; besides,
the user is pinged: the open page plays a ping and says who it was, and
extensions that follow "mention" hear of it (progress.py), so Isola can
show a banner. Pings share the agent's notification limit (notify.py),
so a spark that writes @user over and over cannot bury the user in
them; past it, the words are still there to read.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable
from typing import Any

# The name a spark calls the user by. No spark may take it.
USER = "user"
TEXT_CHARS = 280

_USER_RE = re.compile(r"(?<![\w@/.])@user(?![\w./-])", re.I)

_listeners: list[Callable[[dict], None]] = []
_lock = threading.Lock()


def mentions_user(text: str) -> bool:
    """Whether TEXT says @user."""

    return bool(_USER_RE.search(text or ""))


def on_ping(listener: Callable[[dict], None]) -> None:
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)


def off_ping(listener: Callable[[dict], None]) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def _line(text: str) -> str:
    """What the ping says: the words around the @user, on one line."""

    flat = " ".join(str(text or "").split())
    found = _USER_RE.search(flat)
    start = 0
    if found and found.start() > TEXT_CHARS // 2:
        # The @user far down a long report: from the sentence it is in.
        cut = max(flat.rfind(". ", 0, found.start()),
                  flat.rfind("? ", 0, found.start()),
                  flat.rfind("! ", 0, found.start()))
        start = cut + 2 if cut >= 0 else found.start()
    line = flat[start:]
    return line if len(line) <= TEXT_CHARS \
        else line[:TEXT_CHARS - 1].rstrip() + "…"


def ping(spark: Any, text: str, where: str, team: str = "") -> bool:
    """SPARK wrote TEXT, in its report (WHERE "report") or in TEAM's
    chat ("team"): if it says @user, ping the user. Whether it did."""

    from . import briefing, notify, progress

    if not mentions_user(text):
        return False
    if notify.take_notify_turn():
        return False
    said = {
        "spark": spark.id, "name": spark.name, "handle": spark.handle,
        "colour": spark.colour, "where": where, "team": team,
        "text": _line(text),
    }
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        with contextlib.suppress(Exception):
            listener(dict(said))
    progress.tell("mention", **said)
    # And in the briefing, for when it was missed.
    with contextlib.suppress(Exception):
        briefing.notice("ping", f"{spark.name} mentioned you", said["text"],
                        spark=spark.id, who=spark.name, colour=spark.colour,
                        team=team, where=where)
    return True
