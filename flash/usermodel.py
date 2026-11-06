"""The user model: a living profile of the person Flash works with.

Memory holds single facts, one line each. The profile is the picture they
add up to, kept in a few short sections: who the user is, how they work,
how they like replies, what they know, what they are working on, and
where they are headed. The background review rewrites a section when a
conversation shows something lasting about it; every chat starts with the
profile, so Flash knows who it is talking to without being told again.

The user can read and edit all of it in Settings, and a section of their
own, Your notes, is theirs alone: Flash reads it and never writes it. The
setting USER_MODEL (on unless turned off) stops the updates; what is
written stays until the user clears it.
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

from . import paths
from .paths import FLASH_DIR

SETTING = "USER_MODEL"
# key, heading, and what goes in it (for the review)
SECTIONS = (
    ("who", "Who they are",
     "their role, background and situation"),
    ("work", "How they work",
     "their tools, habits, and how they like work done"),
    ("replies", "How they like replies",
     "length, tone, format, and what bothers them"),
    ("knows", "What they know",
     "where they are expert, and where they are new"),
    ("now", "What they are working on",
     "their current projects and what each is"),
    ("goals", "Where they are headed",
     "what they are aiming for, near and far"),
)
NOTES = ("notes", "Your notes")
SECTION_CHARS = 700        # one section, at most
PROMPT_CHARS = 2400        # the profile in the system prompt, at most
TITLE = "# About you"


def path() -> Path:
    return FLASH_DIR / "user-profile.md"


def enabled() -> bool:
    """Whether Flash keeps the profile up to date: on unless turned off."""

    from .notify import _saved

    return _saved(SETTING).strip().lower() not in ("0", "off", "false", "no")


def set_enabled(on: bool) -> None:
    from .envfile import set_env_var
    from .paths import ENV_PATH

    os.environ[SETTING] = "1" if on else "0"
    set_env_var(ENV_PATH, SETTING, os.environ[SETTING])


def _keys() -> dict[str, str]:
    """Each heading, lower case, to its section's key."""

    return {heading.lower(): key for key, heading, _ in SECTIONS} | {
        NOTES[1].lower(): NOTES[0]}


def text() -> str:
    try:
        return path().read_text(encoding="utf-8")
    except OSError:
        return ""


def parse(raw: str) -> dict[str, str]:
    """The sections in RAW, by key. Headings it does not know are kept
    with the section above them, so nothing the user wrote is lost."""

    keys, found, current, lines = _keys(), {}, None, []

    def close():
        if current is not None:
            body = "\n".join(lines).strip()
            if body:
                found[current] = (found.get(current, "") + "\n" + body).strip()

    for line in (raw or "").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading and heading.group(1).lower() in keys:
            close()
            current, lines = keys[heading.group(1).lower()], []
        elif current is None and line.startswith("# "):
            continue
        else:
            if current is None:
                current, lines = NOTES[0], []
            lines.append(line)
    close()
    return found


def render(sections: dict[str, str]) -> str:
    parts = [TITLE]
    for key, heading, _ in (*SECTIONS, (*NOTES, "")):
        body = (sections.get(key) or "").strip()
        if body:
            parts.append(f"## {heading}\n\n{body}")
    return "\n\n".join(parts) + "\n"


def load() -> dict[str, str]:
    return parse(text())


def _write(sections: dict[str, str]) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    partial.write_text(render(sections), encoding="utf-8")
    paths.replace(partial, target)


def save_text(raw: str) -> dict[str, str]:
    """The user's own edit of the whole profile."""

    sections = parse(raw)
    _write(sections)
    return sections


def update(section: str, content: str) -> str:
    """Flash's rewrite of one section, whole. Never Your notes."""

    from . import guard

    key = (section or "").strip().lower()
    names = {k for k, _, _ in SECTIONS}
    if key == NOTES[0]:
        return ("Not updated: Your notes are the user's own; leave them "
                "as they are.")
    if key not in names:
        return (f"Error: no section {section!r}. Sections: "
                f"{', '.join(sorted(names))}.")
    content = "\n".join(line.rstrip()
                        for line in str(content or "").strip().splitlines())
    found = guard.signs(content)
    if found:
        return ("Not updated: it reads like instructions to an assistant "
                f"({found[0]}), which is not a description of the user.")
    with contextlib.suppress(Exception):
        from . import vault

        content = vault.scrub(content)
    if len(content) > SECTION_CHARS:
        return (f"Not updated: keep a section under {SECTION_CHARS} "
                f"characters (this is {len(content)}). Say it shorter.")
    sections = load()
    if content:
        sections[key] = content
    else:
        sections.pop(key, None)
    _write(sections)
    heading = dict((k, h) for k, h, _ in SECTIONS)[key]
    return f"Updated {heading}." if content else f"Cleared {heading}."


def updated() -> float:
    try:
        return path().stat().st_mtime
    except OSError:
        return 0.0


def prompt_block() -> str:
    """The profile for the system prompt, cut to fit: each section loses
    its end before any section is dropped."""

    sections = load()
    if not sections:
        return ""
    order = [(h, sections[k]) for k, h, _ in (*SECTIONS, (*NOTES, ""))
             if sections.get(k)]
    share = max(160, PROMPT_CHARS // len(order))
    body = []
    for heading, content in order:
        if len(content) > share:
            content = content[:share - 1].rstrip() + "…"
        body.append(f"{heading}:\n{content}")
    return ("=== About the user ===\n"
            "Who you are working with, as you have come to know them and "
            "as they put it in Your notes. Let it shape how you work and "
            "reply, without mentioning it.\n\n" + "\n\n".join(body))


GUIDE = """\
The user profile below is your picture of the user, in sections. Update \
a section with update_user_model when this conversation showed something \
lasting about it that the section does not say, or says wrongly: rewrite \
the whole section, short lines, merged with what it already says, keeping \
what is still true. Describe the person, not this conversation: no dates, \
no quotes, nothing that only mattered today. Your notes are the user's \
own words; never change them, and let them win over what you would \
write. Most conversations change nothing here.

Sections: """ + "; ".join(f"{k} ({h}: {what})" for k, h, what in SECTIONS)


def review_block() -> str:
    sections = load()
    shown = render(sections).strip() if sections else "(empty so far)"
    return "=== User profile ===\n" + shown


def age() -> str:
    stamp = updated()
    if not stamp:
        return ""
    days = int((time.time() - stamp) // 86400)
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"
