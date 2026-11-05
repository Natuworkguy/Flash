"""Working notes: what Flash writes down for itself on a long task.

On a long task the history fills, and compaction squeezes the turns that
no longer fit into one short summary, each time again. Details are lost
that way: a path found an hour ago, a decision and why, a test that
fails for a reason already worked out. So Flash keeps notes as it goes,
with the notes tool: findings, decisions, what is done and what is
left. They are its own, word for word, and every compaction carries
them over whole beside the summary, however many times the chat is
compacted.

Each chat has its own notes. The web page's are kept on disk with the
chat; the terminal's last as long as its conversation.
"""

import contextlib
import json
import re
import threading
from pathlib import Path
from typing import Optional

# How long the notes may grow: past it, the oldest lines go first.
MAX_CHARS = 6000
MAX_LINE = 400
HEADER = ("Your working notes, kept word for word (the notes tool "
          "changes them):")

_local = threading.local()
_lock = threading.Lock()
_held: dict[str, list[str]] = {}


def _key() -> str:
    key = getattr(_local, "key", "")
    return key or f"thread-{threading.get_ident()}"


@contextlib.contextmanager
def in_chat(key: str, kept: bool = True):
    """What runs in here writes the notes of chat KEY. KEPT: they are
    saved on disk, for a chat that outlives the process."""

    before = (getattr(_local, "key", ""), getattr(_local, "kept", False))
    _local.key, _local.kept = key or "", kept
    try:
        yield
    finally:
        _local.key, _local.kept = before


def _path(key: str) -> Optional[Path]:
    if not re.fullmatch(r"[\w-]{1,80}", key or ""):
        return None
    from .workspace import store  # deferred: workspace is the web's

    return store() / "notes" / f"{key}.json"


def lines(key: Optional[str] = None) -> list[str]:
    """Chat KEY's notes (this thread's chat's by default), a line each."""

    key = key or _key()
    with _lock:
        if key in _held:
            return list(_held[key])
    found: list[str] = []
    path = _path(key)
    if path is not None:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                found = [str(line) for line in data if str(line).strip()]
    with _lock:
        _held.setdefault(key, found)
        return list(_held[key])


def _save(key: str, notes: list[str], kept: bool) -> None:
    # Over the limit, the oldest lines go.
    while notes and sum(len(line) + 1 for line in notes) > MAX_CHARS:
        notes.pop(0)
    with _lock:
        _held[key] = list(notes)
    path = _path(key) if kept else None
    if path is None:
        return
    with contextlib.suppress(OSError):
        if notes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(notes, ensure_ascii=False),
                            encoding="utf-8")
        else:
            path.unlink(missing_ok=True)


def _clean(text: str) -> list[str]:
    out = []
    for line in str(text or "").replace("\\n", "\n").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if line:
            out.append(line[:MAX_LINE])
    return out


def change(add: str = "", rewrite: Optional[str] = None,
           drop: str = "") -> list[str]:
    """This chat's notes changed: ADD's lines put at the end, REWRITE in
    place of them all ("" clears them), or the lines with DROP in them
    taken out. The notes after."""

    key, kept = _key(), bool(getattr(_local, "kept", False)) and \
        bool(getattr(_local, "key", ""))
    notes = lines(key) if rewrite is None else []
    if rewrite:
        notes = _clean(rewrite)
    if drop.strip():
        gone = drop.strip().lower()
        notes = [line for line in notes if gone not in line.lower()]
    for line in _clean(add):
        if line not in notes:
            notes.append(line)
    _save(key, notes, kept)
    return lines(key)


def clear(key: Optional[str] = None) -> None:
    """Chat KEY's notes gone (this thread's chat's by default): the
    conversation started over, or the chat was deleted."""

    key = key or _key()
    with _lock:
        _held.pop(key, None)
    path = _path(key)
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def block(key: Optional[str] = None) -> str:
    """The notes as a compaction carries them over: "" with none."""

    notes = lines(key)
    if not notes:
        return ""
    return HEADER + "\n" + "\n".join(f"- {line}" for line in notes)


def with_notes(summary: str, key: Optional[str] = None) -> str:
    """SUMMARY, with the notes after it, word for word."""

    notes = block(key)
    return f"{summary.strip()}\n\n{notes}" if notes else summary
