"""Flash's soul: who it is, in the user's own words.

~/.flash/SOUL.md is a page the user writes about the Flash they want: a
name, a personality, a tone, how it treats them, what it cares about. It
goes into every conversation, in the terminal and the web UI alike, as
who Flash is. It shapes how Flash talks and carries itself, never what
is true or what Flash is allowed to do: the rules above it still hold.

As OpenClaw's SOUL.md does. Empty or missing, Flash is itself.
"""

from __future__ import annotations

from pathlib import Path

from .paths import FLASH_DIR

# A soul is a page, not a book: past this it is cut, and it says so.
SOUL_CHARS = 6000

# What a new soul starts as, to write over: an example, all commented out
# so it changes nothing until the user writes their own.
STARTER = """<!--
This is Flash's soul: who it is, in your words. It goes into every
conversation. Write it as you would describe a person. Anything inside
these comment marks is ignored, so delete them and write your own.

For example:

Your name is Ember. You're warm, a little playful, and straight with me.
You call me Nathan. Keep it short unless I ask for more.
You care about doing things properly: you'd rather check than guess.
When something goes wrong, say so plainly and then fix it.
-->
"""

PROMPT = """=== Your soul ===
The user wrote this about who you are. Be this in every reply: your name,
your manner, your tone. It shapes how you talk, never what is true or what
you are allowed to do, so everything above still holds.

{soul}"""


def soul_path() -> Path:
    # FLASH_DIR is looked up here, not bound at import, so a test can
    # point it at a temporary home.
    return FLASH_DIR / "SOUL.md"


def read() -> str:
    """The soul as the user wrote it, or "" when there is none."""

    try:
        return soul_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write(text: str) -> str:
    """Keep TEXT as the soul. Empty removes it. Returns what was kept."""

    text = str(text or "").replace("\r\n", "\n").strip()
    path = soul_path()
    if not text:
        path.unlink(missing_ok=True)
        return ""
    if len(text) > SOUL_CHARS:
        raise ValueError(
            f"A soul is a page, not a book: keep it under {SOUL_CHARS} "
            "characters."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def _meant(text: str) -> str:
    """TEXT without its HTML comments: what the user actually wrote."""

    out = []
    while "<!--" in text:
        before, _, rest = text.partition("<!--")
        out.append(before)
        _, closed, text = rest.partition("-->")
        if not closed:
            text = ""
    out.append(text)
    return "".join(out).strip()


def prompt_block() -> str:
    """The soul for the system prompt, or "" when there is none."""

    soul = _meant(read())[:SOUL_CHARS]
    return PROMPT.format(soul=soul) if soul else ""
