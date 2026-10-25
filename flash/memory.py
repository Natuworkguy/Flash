"""Persistent memory for Flash: facts saved across sessions.

The user's own memory, in one file, goes with every chat. A project
has a memory of its own besides (its conventions, decisions, where
things are), which goes with every chat in that project. Each function
takes PATH for which: the user's by default, a project's from
project_path.
"""

import contextlib
import hashlib
import json
import re
import shutil
import threading
import time
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


# --- When each was added, and how often it was recalled -----------------
#
# Kept beside the memory file, by each entry's words, so a tidy-up can
# tell an entry nobody has needed in months from one recalled every day.
# Entries saved before it was kept have none.


def _key(entry: str) -> str:
    return hashlib.sha256(" ".join(entry.lower().split()).encode()) \
        .hexdigest()[:16]


def _meta_file(path: Optional[Path]) -> Path:
    target = _file(path)
    return target.with_name(target.name + ".meta.json")


def _meta(path: Optional[Path]) -> dict:
    try:
        found = json.loads(_meta_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _save_meta(path: Optional[Path], meta: dict) -> None:
    keep = {_key(e) for e in list_memory(path)}
    meta = {k: v for k, v in meta.items() if k in keep}
    with contextlib.suppress(OSError):
        target = _meta_file(path)
        if not meta:
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _noted(path: Optional[Path], entry: str,
           like: Optional[list[str]] = None) -> None:
    """ENTRY was saved now; or written from LIKE, whose history it takes:
    the earliest added, every recall."""

    meta = _meta(path)
    now = time.time()
    olds = [meta.get(_key(e), {}) for e in like or []]
    added = min([float(o.get("added") or now) for o in olds] + [now])
    meta[_key(entry)] = {
        "added": added,
        "recalled": sum(int(o.get("recalled") or 0) for o in olds),
        "last": max([float(o.get("last") or 0) for o in olds] + [0.0]),
    }
    _save_meta(path, meta)


def usage(path: Optional[Path] = None) -> list[dict]:
    """Each entry, in order, with when it was added and how often and
    when it was last recalled (0 for never, or for before it was kept)."""

    meta = _meta(path)
    out = []
    for entry in list_memory(path):
        found = meta.get(_key(entry), {})
        out.append({
            "entry": entry,
            "added": float(found.get("added") or 0),
            "recalled": int(found.get("recalled") or 0),
            "last": float(found.get("last") or 0),
        })
    return out


def backup(path: Optional[Path] = None) -> Optional[Path]:
    """A copy of the memory as it is now, before something tidies it:
    the newest MAX_BACKUPS are kept. Where it went, or None."""

    source = _file(path)
    if not source.exists():
        return None
    # Beside where it lives: the user's in ~/.flash, as Flash's own are.
    folder = (source.parent / ".flash" if source == MEMORY_PATH
              else source.parent) / "memory-backups"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / time.strftime(f"{source.stem}-%Y%m%d-%H%M%S.md")
        shutil.copyfile(source, copy)
        for old in sorted(folder.glob(f"{source.stem}-*.md"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)
    except OSError:
        return None
    return copy


MAX_BACKUPS = 20


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
    _noted(path, entry)
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
    _noted(path, entry, like=[old])
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


def merge_memories(indexes: list[int], entry: str,
                   path: Optional[Path] = None) -> str:
    """Put ENTRY in place of the entries at INDEXES (1-based, two or
    more), where the first of them was: one entry for what they said."""

    entry = " ".join(str(entry or "").split())
    if not entry:
        raise ValueError("Say what the merged memory is.")
    entries = list_memory(path)
    wanted = sorted({int(i) for i in indexes})
    if len(wanted) < 2:
        raise ValueError("Merging takes two entries or more.")
    for index in wanted:
        _check_index(entries, index)
    olds = [entries[i - 1] for i in wanted]
    kept = [e for n, e in enumerate(entries, start=1) if n not in wanted]
    kept.insert(wanted[0] - 1, entry)
    _write(path, kept)
    _noted(path, entry, like=olds)
    return f'Merged {len(olds)} memories into "{entry}".'


def search_memory(phrase: str,
                  path: Optional[Path] = None) -> list[tuple[int, str]]:
    """Return (1-based index, entry) for every entry containing `phrase`,
    and count each as recalled."""

    phrase = phrase.strip().lower()
    if not phrase:
        return []

    found = [
        (i, entry)
        for i, entry in enumerate(list_memory(path), start=1)
        if phrase in entry.lower()
    ]
    if found:
        meta = _meta(path)
        now = time.time()
        for _, entry in found:
            had = meta.setdefault(_key(entry), {})
            had["recalled"] = int(had.get("recalled") or 0) + 1
            had["last"] = now
        _save_meta(path, meta)
    return found


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
