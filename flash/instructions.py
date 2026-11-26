"""Custom instructions: how the user wants every reply, in their words.

Set in the web UI's Settings, or with /instructions in the terminal, and
kept in ~/.flash/instructions.md. They go into Flash's system prompt on
every turn, in the terminal and the browser alike. Unlike memories,
which are facts Flash keeps about the user and their work, these are
standing orders about the replies themselves: "use metric", "no bullet
points", "answer in Spanish".
"""

import os
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

MAX_CHARS = 4000

# The file a repository keeps its notes for coding agents in: how to
# build it, how to run its tests, the conventions to keep. Read from the
# folder the turn works in, or the nearest folder above it, up to the
# top of its repository.
AGENTS_FILES = ("AGENTS.md", "FLASH.md")

# Enough for a thorough one; past it the rest is cut, since the file
# goes with every message.
MAX_AGENTS_CHARS = 12000

AGENTS_PROMPT = """=== The project's notes for agents ===
From {path}. Whoever keeps this project wrote these for anyone working
on it: how to build and test it, and the conventions to keep. Follow
them for work in this folder, unless the user asks otherwise:
{text}"""

PROMPT = """=== How the user wants your replies ===
The user's own standing instructions for every reply. Follow them,
unless they ask for something else in the moment:
{text}"""


def path() -> Path:
    return FLASH_DIR / "instructions.md"


def read() -> str:
    try:
        return path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save(text: str) -> str:
    """Replace them with TEXT ("" clears them); what is kept."""

    text = str(text or "").strip()
    if len(text) > MAX_CHARS:
        raise ValueError(
            f"Custom instructions are {len(text)} characters; the most is "
            f"{MAX_CHARS}, since they go with every message."
        )
    if not text:
        path().unlink(missing_ok=True)
        return ""
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(text + "\n", encoding="utf-8")
    return text


def prompt_block() -> str:
    text = read()
    return PROMPT.format(text=text) if text else ""


def agents_file(folder: Optional[Path] = None) -> Optional[Path]:
    """The AGENTS.md for FOLDER (the current one by default): its own,
    or the nearest above it, stopping at the top of its repository and
    never looking above the home folder."""

    try:
        here = Path(folder or os.getcwd()).resolve()
    except OSError:
        return None
    home = Path.home().resolve()
    for place in (here, *here.parents):
        if place == home.parent or place in home.parents:
            break
        for name in AGENTS_FILES:
            found = place / name
            if found.is_file():
                return found
        if (place / ".git").exists() or place == home:
            break
    return None


def agents_block(folder: Optional[Path] = None) -> str:
    found = agents_file(folder)
    if found is None:
        return ""
    try:
        text = found.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > MAX_AGENTS_CHARS:
        text = text[:MAX_AGENTS_CHARS] + "\n[the rest of the file is cut]"
    return AGENTS_PROMPT.format(path=found, text=text)
