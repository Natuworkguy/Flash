"""Chat titles the model writes: a few words for what a chat is about,
in place of its first message cut short.

Off until turned on, in the web UI's Settings or with MODEL_TITLES=1 in
~/.flash.env. Written once, after a chat's first reply, and never over
a name the user gave the chat themselves.
"""

import os
import re
from typing import Any, Optional

SETTING = "MODEL_TITLES"

PROMPT = (
    "Write a title for this chat: two to six words that name what it is "
    "about, the way a heading would. Plain words in the chat's language, "
    "no quotes, no emoji, nothing at the end like a full stop. Reply with "
    "the title and nothing else."
)

# How much of the first message and reply the model is shown.
SHOWN_CHARS = 2000


def enabled() -> bool:
    return (os.environ.get(SETTING) or "").strip().lower() in (
        "1", "on", "true", "yes",
    )


def clean(text: str, limit: int) -> str:
    """The title in what the model said: its first line, without the
    quotes, marks and labels models wrap one in, cut at a word to
    LIMIT. "" when nothing usable is left."""

    line = next(
        (ln for ln in (text or "").splitlines() if ln.strip()), "",
    )
    line = re.sub(r"^\s*(?:#+\s*|[-*]\s+)", "", line)
    line = re.sub(r"^\s*(?:chat\s+)?title\s*:\s*", "", line, flags=re.I)
    line = line.replace("**", "").replace("__", "").replace("`", "")
    line = line.strip().strip("\"'“”‘’").strip()
    line = re.sub(r"[\s.!?:;,]+$", "", line)
    line = " ".join(line.split())
    if len(line) > limit:
        cut = line[:limit + 1]
        line = cut[:cut.rfind(" ")] if " " in cut else line[:limit]
    return line.strip()


def write(
    client: Any, model: str, asked: str, answered: str, limit: int,
    options: Optional[dict] = None,
) -> str:
    """A title for the chat that opened with ASKED and its reply
    ANSWERED, or "" when the model gave none."""

    shown = f"The first message:\n{asked.strip()[:SHOWN_CHARS]}"
    if answered.strip():
        shown += f"\n\nThe reply:\n{answered.strip()[:SHOWN_CHARS]}"
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": shown},
        ],
        options=options or {},
        stream=False,
    )
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    content = (
        message.get("content") if isinstance(message, dict)
        else getattr(message, "content", "")
    )
    return clean(str(content or ""), limit)
