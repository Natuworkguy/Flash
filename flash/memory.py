"""Persistent memory for Flash: facts saved across sessions."""

import re
from pathlib import Path

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


def load_memory() -> str:
    """Return the saved memory content, or "" if none exists."""

    if not MEMORY_PATH.exists():
        return ""
    return MEMORY_PATH.read_text(encoding="utf-8").strip()


def list_memory() -> list[str]:
    """Return every saved entry, in order, without the bullet prefix."""

    return [
        line.lstrip("- ").strip()
        for line in load_memory().splitlines()
        if line.strip()
    ]


def add_memory(entry: str) -> str:
    """Append one fact as a bullet point in the memory file."""

    entry = entry.strip()
    if not entry:
        return "Nothing to remember."

    entries = list_memory()
    entries.append(entry)
    MEMORY_PATH.write_text(
        "\n".join(f"- {e}" for e in entries) + "\n", encoding="utf-8"
    )
    return f"Remembered: {entry}"


def _check_index(entries: list[str], index: int) -> None:
    if index < 1 or index > len(entries):
        count = len(entries)
        verb = "is" if count == 1 else "are"
        raise IndexError(
            f"No memory at index {index}. There {verb} {count} saved "
            "(indices start at 1)."
        )


def edit_memory(index: int, entry: str) -> str:
    """Replace entry `index` (1-based) with `entry`, kept to one line.

    Raises IndexError if there is no entry at that index, and ValueError
    if `entry` is blank: forgetting is what removes one.
    """

    entry = " ".join(entry.split())
    if not entry:
        raise ValueError("A memory cannot be empty. Forget it instead.")

    entries = list_memory()
    _check_index(entries, index)

    old = entries[index - 1]
    entries[index - 1] = entry
    MEMORY_PATH.write_text(
        "\n".join(f"- {e}" for e in entries) + "\n", encoding="utf-8"
    )
    return f'Changed memory "{old}" to "{entry}".'


def forget_memory(index: int) -> str:
    """Delete one memory entry by its 1-based index (1 = first saved).

    Raises IndexError if there is no entry at that index.
    """

    entries = list_memory()
    _check_index(entries, index)

    removed = entries.pop(index - 1)

    if entries:
        MEMORY_PATH.write_text(
            "\n".join(f"- {e}" for e in entries) + "\n", encoding="utf-8"
        )
    else:
        MEMORY_PATH.unlink(missing_ok=True)

    return f'Deleted memory "{removed}".'


def search_memory(phrase: str) -> list[tuple[int, str]]:
    """Return (1-based index, entry) for every entry containing `phrase`."""

    phrase = phrase.strip().lower()
    if not phrase:
        return []

    return [
        (i, entry)
        for i, entry in enumerate(list_memory(), start=1)
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


def import_memory(text: str) -> tuple[list[str], int]:
    """Save every new fact in TEXT. Returns (the ones added, how many
    were already saved)."""

    entries = list_memory()
    known = {entry.lower() for entry in entries}
    facts = parse_import(text)
    added = [fact for fact in facts if fact.lower() not in known]

    if added:
        MEMORY_PATH.write_text(
            "\n".join(f"- {e}" for e in entries + added) + "\n",
            encoding="utf-8",
        )

    return added, len(facts) - len(added)
