"""Documents kept up to date as the agents edit them.

A document shown in the web UI's side panel is a copy of a file. When an
agent (the chat's, a sub-agent, a spark) edits or writes that file, the
panel is told at once and shows the change going in. Without autonomous
mode, the change waits for the user's yes first, and the panel shows it
as a proposal they can allow or deny in place.

The tools report here; whoever draws the panel listens.
"""

from __future__ import annotations

import re
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Optional

# A gap left to be filled in: "[insert the Q3 numbers here]", "[add a
# summary]", "[fill in the date]". Square brackets, one line, starting
# with what to do. A Markdown link, "[Add to calendar](...)", is not one.
PLACEHOLDER_RE = re.compile(
    r"\[(?:insert|add|put|write|fill in|fill)\b[^\[\]\n]{0,200}\](?!\()",
    re.IGNORECASE,
)

Listener = Callable[[str, str], None]

_listeners: list[weakref.WeakMethod] = []
_lock = threading.Lock()
_pending = threading.local()


def placeholders(text: str) -> list[str]:
    """The gaps in TEXT left to be filled in, in order."""

    return PLACEHOLDER_RE.findall(text or "")


def _key(path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path)


def listen(method: Listener) -> None:
    """Call METHOD (a bound method) with (path, text) whenever a file
    changes. Held weakly, so a listener that is gone stops being
    called."""

    with _lock:
        _listeners[:] = [ref for ref in _listeners if ref() is not None]
        _listeners.append(weakref.WeakMethod(method))


def changed(path, text: str) -> None:
    """A tool wrote PATH, which now holds TEXT."""

    _pending.now = None
    key = _key(path)
    with _lock:
        alive = [ref() for ref in _listeners]
    for listener in alive:
        if listener is None:
            continue
        # A panel that could not be told never stops the write.
        try:
            listener(key, text)
        except Exception:  # noqa: BLE001, S110  # nosec B110
            pass


def propose(path, text: str) -> None:
    """A tool is about to ask whether PATH may become TEXT: the next
    question this thread asks is about it."""

    _pending.now = (_key(path), text)


def take_proposal() -> Optional[tuple[str, str]]:
    """The change this thread's question is about, (path, text), once."""

    found = getattr(_pending, "now", None)
    _pending.now = None
    return found
