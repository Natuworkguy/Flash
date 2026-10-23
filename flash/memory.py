"""Persistent memory for Flash: facts saved across sessions.

The user's own memory, in one file, goes with every chat. A project
has a memory of its own besides (its conventions, decisions, where
things are), which goes with every chat in that project. Each function
takes PATH for which: the user's by default, a project's from
project_path.
"""

import contextlib
import re
import threading
from pathlib import Path
from typing import Optional

MEMORY_PATH = Path.home() / ".flash_memory.md"

# Moving in from another assistant: the user pastes this into a chat
# with it, and pastes back the code block it answers with. Each line
# becomes one entry as written, with no model in between to reword,
# drop, or invent anything.
IMPORT_PROMPT = """\
I'm moving to another AI assistant and want to bring what you know \
about me. List every memory you have stored about me, and everything \
you've learned about me from our past conversations.

Put all of it in one code block, one fact per line, each line starting \
with "- ". No headings, dates, or blank lines. Write each fact so it \
makes sense on its own, and keep my own words where you can.

Refer to me as "The User".

Cover:
- How I want you to respond: tone, format, length, and anything I've \
told you to always or never do.
- Who I am: name, location, job, and interests.
- What I'm working on: projects, goals, and topics that come up again.
- The languages, frameworks, and tools I use, and how I like to use them.
- Corrections I've made to your answers or behavior.
- Anything else you've kept about me.

Don't summarize, merge, or skip entries."""

MAX_IMPORTED = 200
MAX_ENTRY_CHARS = 500

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.DOTALL)
_MARKER_RE = re.compile(r"^(?:[-*•+]|\d+[.)])\s+")
_DATE_RE = re.compile(r"^\[[^\]]{1,40}\]\s*[-:–]?\s*")


def _file(path: Optional[Path]) -> Path:
    # Looked up each time, so a test that moves MEMORY_PATH moves it.
    return MEMORY_PATH if path is None else path


def _write(path: Optional[Path], entries: list[str]) -> None:
    target = _file(path)
    if not entries:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"- {e}" for e in entries) + "\n",
                      encoding="utf-8")


def load_memory(path: Optional[Path] = None) -> str:
    """Return the saved memory content, or "" if none exists."""

    target = _file(path)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8").strip()


def list_memory(path: Optional[Path] = None) -> list[str]:
    """Return every saved entry, in order, without the bullet prefix."""

    return [
        line.lstrip("- ").strip()
        for line in load_memory(path).splitlines()
        if line.strip()
    ]


def add_memory(entry: str, path: Optional[Path] = None) -> str:
    """Append one fact as a bullet point in the memory file."""

    entry = entry.strip()
    if not entry:
        return "Nothing to remember."

    entries = list_memory(path)
    entries.append(entry)
    _write(path, entries)
    return f"Remembered: {entry}"


def _check_index(entries: list[str], index: int) -> None:
    if index < 1 or index > len(entries):
        count = len(entries)
        verb = "is" if count == 1 else "are"
        raise IndexError(
            f"No memory at index {index}. There {verb} {count} saved "
            "(indices start at 1)."
        )


def edit_memory(index: int, entry: str,
                path: Optional[Path] = None) -> str:
    """Replace entry `index` (1-based) with `entry`, kept to one line.

    Raises IndexError if there is no entry at that index, and ValueError
    if `entry` is blank: forgetting is what removes one.
    """

    entry = " ".join(entry.split())
    if not entry:
        raise ValueError("A memory cannot be empty. Forget it instead.")

    entries = list_memory(path)
    _check_index(entries, index)

    old = entries[index - 1]
    entries[index - 1] = entry
    _write(path, entries)
    return f'Changed memory "{old}" to "{entry}".'


def forget_memory(index: int, path: Optional[Path] = None) -> str:
    """Delete one memory entry by its 1-based index (1 = first saved).

    Raises IndexError if there is no entry at that index.
    """

    entries = list_memory(path)
    _check_index(entries, index)

    removed = entries.pop(index - 1)
    _write(path, entries)

    return f'Deleted memory "{removed}".'


def search_memory(phrase: str,
                  path: Optional[Path] = None) -> list[tuple[int, str]]:
    """Return (1-based index, entry) for every entry containing `phrase`."""

    phrase = phrase.strip().lower()
    if not phrase:
        return []

    return [
        (i, entry)
        for i, entry in enumerate(list_memory(path), start=1)
        if phrase in entry.lower()
    ]


def parse_import(text: str) -> list[str]:
    """The facts in what another assistant answered IMPORT_PROMPT with.

    Only what is inside its code blocks, when it used any, so the
    chatter around them is left out. Each line is one fact, with its
    bullet, number, or date taken off. Headings and labels ending in a
    colon are skipped, and so is a fact already listed.
    """

    fenced = _FENCE_RE.findall(text or "")
    body = "\n".join(fenced) if fenced else (text or "")

    facts: list[str] = []
    seen: set[str] = set()

    for line in body.splitlines():
        fact = line.strip()
        if not fact or fact.startswith("#"):
            continue
        fact = _DATE_RE.sub("", _MARKER_RE.sub("", fact))
        fact = " ".join(fact.split())[:MAX_ENTRY_CHARS]
        if not fact or (fact.endswith(":") and len(fact) < 60):
            continue
        if fact.lower() in seen:
            continue
        seen.add(fact.lower())
        facts.append(fact)
        if len(facts) >= MAX_IMPORTED:
            break

    return facts


def import_memory(text: str,
                  path: Optional[Path] = None) -> tuple[list[str], int]:
    """Save every new fact in TEXT. Returns (the ones added, how many
    were already saved)."""

    entries = list_memory(path)
    known = {entry.lower() for entry in entries}
    facts = parse_import(text)
    added = [fact for fact in facts if fact.lower() not in known]

    if added:
        _write(path, entries + added)

    return added, len(facts) - len(added)


# --- A project's memory ---------------------------------------------------

_PROJECT_ID = re.compile(r"^[\w-]{1,64}$")
_in_project = threading.local()


def project_path(project_id: str) -> Path:
    """Where project PROJECT_ID's memory is kept. Raises ValueError for
    an id that is not one."""

    from .workspace import store  # deferred: workspace is the web's

    if not _PROJECT_ID.match(project_id or ""):
        raise ValueError(f"{project_id!r} is not a project")
    return store() / "project-memory" / f"{project_id}.md"


@contextlib.contextmanager
def in_project(project_id: str):
    """What runs in here is a chat in project PROJECT_ID ("" for none):
    remember, recall and forget can reach its memory."""

    before = getattr(_in_project, "id", "")
    _in_project.id = project_id or ""
    try:
        yield
    finally:
        _in_project.id = before


def current_project() -> str:
    """The project the chat running on this thread is in, or ""."""

    return getattr(_in_project, "id", "")
