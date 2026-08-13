"""Persistent memory for Flash: facts saved across sessions."""

from pathlib import Path

MEMORY_PATH = Path.home() / ".flash_memory.md"


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


def forget_memory(index: int) -> str:
    """Delete one memory entry by its 1-based index (1 = first saved).

    Raises IndexError if there is no entry at that index.
    """

    entries = list_memory()
    if index < 1 or index > len(entries):
        count = len(entries)
        verb = "is" if count == 1 else "are"
        raise IndexError(
            f"No memory at index {index}. There {verb} {count} saved "
            "(indices start at 1)."
        )

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
