"""Side questions: /btw, asked while Flash works, or any time.

A side question is answered from what the conversation already holds,
with no tools, on a thread of its own, so the work in progress goes on
undisturbed. Neither the question nor its answer joins the
conversation: it is a quick word on the side, and the model working on
the real thing never hears of it.
"""

from collections.abc import Callable
from typing import Any, Optional

from .dashes import DashGuard

PROMPT = (
    "The user has asked a quick question on the side, while you work on "
    "something else for them, or between requests. Answer it briefly and "
    "directly, from what you know and what the conversation below holds. "
    "You have no tools here and can't look anything up or change "
    "anything, and this answer doesn't change the work in progress, so "
    "don't offer to do work or say you will. If the conversation "
    "doesn't hold what the question needs, say so in a sentence."
)

# How much of the conversation comes along, newest first: enough for
# "why did you pick that file?", not a second copy of a long chat.
HISTORY_CHARS = 24_000
WORKING_CHARS = 2_000

USAGE = "Ask something after /btw, like /btw what does this function return?"


def question_of(line: str) -> Optional[str]:
    """The question in LINE if it is a /btw, or None if it is not one:
    "" for a /btw with nothing after it."""

    stripped = line.strip()
    if stripped != "/btw" and not stripped.startswith("/btw "):
        return None
    return stripped[len("/btw"):].strip()


def _text(message: dict) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else ""


def context(
    history: list[dict],
    question: str,
    working_on: str = "",
    so_far: str = "",
) -> list[dict]:
    """The messages to ask QUESTION with: the side-question prompt, the
    end of HISTORY's words (not its tool traffic or images), what is
    being worked on now, if anything, and the question."""

    kept: list[dict] = []
    room = HISTORY_CHARS
    for message in reversed(history):
        role = message.get("role")
        text = _text(message).strip()
        if role not in ("user", "assistant") or not text:
            continue
        if len(text) > room:
            if not kept:
                kept.append({"role": role, "content": text[-room:]})
            break
        kept.append({"role": role, "content": text})
        room -= len(text)
    kept.reverse()

    messages = [{"role": "system", "content": PROMPT}, *kept]
    if working_on:
        note = f"Right now you are working on: {working_on.strip()}"
        if so_far.strip():
            note += (
                "\nWhat you have written of the reply so far: "
                + so_far.strip()[-WORKING_CHARS:]
            )
        messages.append({"role": "system", "content": note})
    messages.append({"role": "user", "content": question})
    return messages


def ask(
    client: Any,
    model: str,
    messages: list[dict],
    options: Optional[dict] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """The answer, streamed to ON_TEXT a piece at a time as it comes."""

    guard = DashGuard()
    said: list[str] = []
    parts = client.chat(
        model=model, messages=messages, options=options or {}, stream=True,
    )
    for part in parts:
        message = getattr(part, "message", None)
        piece = getattr(message, "content", "") or ""
        piece = guard.feed(piece) if piece else ""
        if piece:
            said.append(piece)
            if on_text is not None:
                on_text(piece)
    rest = guard.flush()
    if rest:
        said.append(rest)
        if on_text is not None:
            on_text(rest)
    return "".join(said).strip()
