"""The audit log: everything a spark did, kept for good.

Every shift a spark starts and ends, every tool it calls with what it
was given and what came back, every step the user approved or refused,
and every change made to it, appended to a log of its own that nothing
in Flash ever rewrites. Each entry carries the hash of the one before,
so a log edited by hand afterwards says so when it is checked.

As Paperclip's audit trail is: every conversation traced, every
decision explained.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

# How much of a tool's arguments and result an entry keeps: enough to
# see what it did, not a copy of every file it read.
FIELD_CHARS = 2000
# How many entries a read hands back at most, newest last.
READ_LIMIT = 500
GENESIS = "0" * 64


def _cut(value):
    """VALUE, its long strings shortened, so one entry stays readable."""

    if isinstance(value, str):
        if len(value) > FIELD_CHARS:
            more = len(value) - FIELD_CHARS
            return value[:FIELD_CHARS] + f" [... {more} more]"
        return value
    if isinstance(value, dict):
        return {str(k): _cut(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cut(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _cut(str(value))


def _digest(previous: str, entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((previous + body).encode("utf-8")).hexdigest()


def _last_hash(path: Path) -> str:
    """The hash of PATH's last entry, read from its end."""

    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 64 * 1024))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return GENESIS
    for line in reversed(tail.splitlines()):
        try:
            return str(json.loads(line)["hash"])
        except (ValueError, KeyError, TypeError):
            continue
    return GENESIS


def record(folder: Path, log_name: str, kind: str, /, **fields) -> dict:
    """Append an entry of KIND to the log called LOG_NAME in FOLDER. The
    caller holds whatever lock keeps two writers apart."""

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{log_name}.jsonl"
    entry = {"at": time.time(), "kind": kind, **_cut(fields)}
    previous = _last_hash(path)
    entry["prev"] = previous
    entry["hash"] = _digest(previous, entry)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read(
    folder: Path, name: str, limit: Optional[int] = READ_LIMIT,
) -> list[dict]:
    """The log called NAME, oldest first: its last LIMIT entries."""

    path = folder / f"{name}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if limit is not None:
        lines = lines[-limit:]
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def raw(folder: Path, name: str) -> str:
    """The whole log as it is on disk, for export."""

    try:
        return (folder / f"{name}.jsonl").read_text(encoding="utf-8")
    except OSError:
        return ""


def verify(folder: Path, name: str) -> tuple[bool, int]:
    """Whether the log called NAME is as it was written: (True, entries)
    when every entry's hash holds, or (False, the line that breaks)."""

    path = folder / f"{name}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True, 0
    previous = GENESIS
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
            stated = entry.pop("hash")
        except (ValueError, KeyError, TypeError, AttributeError):
            return False, number
        if entry.get("prev") != previous or _digest(previous, entry) != stated:
            return False, number
        previous = stated
    return True, len(lines)
