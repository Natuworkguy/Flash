"""Preset replies: the few taps that answer a message, shown under a
question.

A spark's message in the team chat can bring its own, up to three short
answers ("Sounds right", "Something's off"), given with send_message.
One that ends on a question a yes or a no answers gets Yes, No and Not
now without them. The page shows them under the chat's newest message
only, and a tap sends the words as the user's own message.
"""

from __future__ import annotations

import re

YES_NO = ("Yes", "No", "Not now")
MAX_PRESETS = 3
PRESET_CHARS = 40

# The words a question a yes or a no answers starts with.
_YES_NO_START = re.compile(
    r"(should|shall|do|does|did|can|could|would|will|is|are|was|were|"
    r"may|might|want|have|has|ok|okay|any)\b", re.IGNORECASE)


def asks_yes_or_no(text: str) -> bool:
    """Whether TEXT ends on a question a yes answers: its last sentence
    asks, starts like "Should I" or "Do you", and offers no choice of
    two ("this or that?")."""

    text = re.sub(r"[*_`>#]+", "", str(text or "")).strip()
    if not text.endswith("?"):
        return False
    last = re.split(r"(?<=[.!?])\s+|\n+", text[:-1])[-1].strip()
    return bool(_YES_NO_START.match(last)) and not re.search(
        r"\bor\b", last, re.IGNORECASE)


def clean(replies: object) -> list[str]:
    """REPLIES, as a model gave them, kept to a few short one-line
    answers, each once."""

    if not isinstance(replies, (list, tuple)):
        return []
    kept: list[str] = []
    for reply in replies:
        if not isinstance(reply, str):
            continue
        reply = " ".join(reply.split())[:PRESET_CHARS].strip()
        if reply and reply.lower() not in (k.lower() for k in kept):
            kept.append(reply)
        if len(kept) == MAX_PRESETS:
            break
    return kept


def for_message(text: str, own: object = None) -> list[str]:
    """The presets to offer under a message saying TEXT: OWN when it
    gave some, Yes, No and Not now when it asks a yes or no question,
    else none."""

    return clean(own) or (list(YES_NO) if asks_yes_or_no(text) else [])
