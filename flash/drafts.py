"""Drafts: a message going out in the user's name, theirs to change before
it goes.

An email or an iMessage always asks before it is sent. The question
comes with the message as a draft: the web page draws it as fields the
user edits in place before pressing Send, and the terminal opens it in
their editor when they answer e. What they send is what goes, word for
word, and the model is told it was changed, so it does not send its own
version again.

The editor is $VISUAL or $EDITOR, else nano, vim or vi (Notepad on
Windows). One that opens a window and returns at once, as VS Code does,
needs its wait flag: EDITOR="code --wait".
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# What each kind of draft holds, in the order it is shown.
FIELDS: dict[str, tuple[str, ...]] = {
    "email": ("to", "cc", "subject", "body"),
    "imessage": ("text",),
}

# The fields that are one line; the rest keep their line breaks.
ONE_LINE = ("to", "cc", "subject")

MAX_CHARS = {
    "to": 2000, "cc": 2000, "subject": 998, "body": 100_000, "text": 20_000,
}

_HEADER = re.compile(r"^\s*(to|cc|subject)\s*:(.*)$", re.IGNORECASE)


class DraftError(Exception):
    """A draft that could not be edited here."""


def clean(draft: object, kind: str = "") -> Optional[dict]:
    """DRAFT, as a page or an editor gave it back, kept to its kind's
    fields as strings of a sane length. None when it is not a draft."""

    if not isinstance(draft, dict):
        return None
    kind = kind or str(draft.get("kind") or "")
    if kind not in FIELDS:
        return None
    out = {"kind": kind}
    for key in FIELDS[kind]:
        value = draft.get(key)
        value = "" if value is None else str(value)
        if key in ONE_LINE:
            value = " ".join(value.split())
        else:
            value = value.replace("\r\n", "\n").strip("\n")
        out[key] = value[:MAX_CHARS[key]]
    return out


def changed(before: dict, after: Optional[dict]) -> Optional[dict]:
    """AFTER, when the user changed anything of BEFORE; else None."""

    kind = str(before.get("kind") or "")
    after = clean(after, kind) if after else None
    if after is None:
        return None
    for key in FIELDS[kind]:
        if after[key].strip() != str(before.get(key) or "").strip():
            return after
    return None


def as_text(draft: dict) -> str:
    """DRAFT as the file the user's editor opens: for an email its To,
    Cc and Subject lines, a blank line and the body; for an iMessage the
    text alone."""

    if draft.get("kind") == "imessage":
        return str(draft.get("text") or "")
    return (
        f"To: {draft.get('to') or ''}\n"
        f"Cc: {draft.get('cc') or ''}\n"
        f"Subject: {draft.get('subject') or ''}\n"
        f"\n{draft.get('body') or ''}"
    )


def from_text(text: str, before: dict) -> dict:
    """TEXT, the file the user saved, read back as a draft of BEFORE's
    kind. An email's header lines are read from the top until the first
    line that is not one; a header taken out keeps what BEFORE had."""

    kind = str(before.get("kind") or "")
    text = str(text or "").replace("\r\n", "\n")
    if kind == "imessage":
        return clean({"text": text.strip()}, kind) or {"kind": kind}

    fields = dict(before)
    lines = text.split("\n")
    at = 0
    while at < len(lines):
        match = _HEADER.match(lines[at])
        if not match:
            break
        fields[match.group(1).lower()] = match.group(2).strip()
        at += 1
    if at < len(lines) and not lines[at].strip():
        at += 1
    fields["body"] = "\n".join(lines[at:]).strip()
    return clean(fields, kind) or {"kind": kind}


def described(draft: dict) -> str:
    """DRAFT the way the tool's result shows a message about to go."""

    if draft.get("kind") == "imessage":
        return str(draft.get("text") or "")
    head = f"To: {draft.get('to') or ''}"
    if draft.get("cc"):
        head += f"\nCc: {draft['cc']}"
    return f"{head}\nSubject: {draft.get('subject') or ''}\n\n" + str(
        draft.get("body") or "")


def editor_command() -> list[str]:
    """The user's editor, as a command to run a file's path with; [] when
    this computer has none Flash can find."""

    said = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    if said.strip():
        return shlex.split(said, posix=os.name != "nt")
    if os.name == "nt":
        return ["notepad"]
    for name in ("nano", "vim", "vi"):
        if shutil.which(name):
            return [name]
    return []


def edit(draft: dict) -> dict:
    """DRAFT, opened in the user's editor and read back once they close
    it. DraftError when there is no editor, or it would not start."""

    command = editor_command()
    if not command:
        raise DraftError(
            "No editor to open it in. Set EDITOR in ~/.flash.env, say "
            "EDITOR=nano.")
    handle, path = tempfile.mkstemp(prefix="flash-draft-", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(as_text(draft) + "\n")
        try:
            subprocess.call([*command, path])
        except OSError as exc:
            raise DraftError(f"{command[0]} would not start: {exc}") from exc
        text = Path(path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return from_text(text, draft)
