"""Custom instructions: how the user wants every reply, in their words.

Set in the web UI's Settings, or with /instructions in the terminal, and
kept in ~/.flash/instructions.md. They go into Flash's system prompt on
every turn, in the terminal and the browser alike. Unlike memories,
which are facts Flash keeps about the user and their work, these are
standing orders about the replies themselves: "use metric", "no bullet
points", "answer in Spanish".
"""

from pathlib import Path

from .paths import FLASH_DIR

MAX_CHARS = 4000

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
