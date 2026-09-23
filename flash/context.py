"""Deciding how much of the conversation the model still gets to see.

The old rule was a flat cap: six messages, three thousand characters,
oldest deleted first. That is under a thousand tokens of conversation on
a model with a hundred and twenty thousand, and it cut wherever the
count happened to land, which on a turn that called tools meant leaving
a tool result whose call had already been deleted.

This keeps two rules instead. Work in *blocks*, a user message and every
assistant and tool message that answered it, and drop whole blocks, so a
tool result and the call that produced it are always either both present
or both gone. And size the history against the model's real context
window rather than a constant, so a 128k model is not run as if it were
a 2k one.

What falls off the end is handed back to the caller rather than thrown
away, so `ai.py` can have the model summarize it into a few lines and
keep the thread of a long session instead of losing it.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

# Characters per token. Real tokenizers average nearer 4 on prose and
# worse on code and JSON, which is most of what a tool result holds.
# Guessing low means trimming slightly early, which costs a little
# history; guessing high means overflowing the window, which costs the
# whole request, so this leans low on purpose.
CHARS_PER_TOKEN = 3.5

# Roughly the role framing and delimiters a chat template adds per
# message, independent of its text.
TOKENS_PER_MESSAGE = 4

# An image is worth far more than its JSON length suggests.
TOKENS_PER_IMAGE = 800

# When the backend will not say how big the window is.
DEFAULT_CONTEXT = 8192

# Never hand the history the entire window: the system prompt, the
# tool schemas, and the reply all have to fit beside it.
MIN_HISTORY_TOKENS = 1024

# Fraction of whatever is left after the system prompt and the reply
# that history may use. The rest absorbs the tool schemas and the fact
# that the estimate above is an estimate.
HISTORY_SHARE = 0.75

SUMMARY_MARKER = "=== Summary of earlier conversation ==="


def estimate_tokens(text: str) -> int:
    """About how many tokens `text` costs."""

    if not text:
        return 0

    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: dict) -> int:
    """About how many tokens one message costs, images included."""

    total = TOKENS_PER_MESSAGE + estimate_tokens(message.get("content") or "")

    for call in message.get("tool_calls") or []:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        total += estimate_tokens(str(function.get("name", "")))
        total += estimate_tokens(str(function.get("arguments", "")))

    total += TOKENS_PER_IMAGE * len(message.get("images") or [])

    return total


def total_tokens(messages: list[dict]) -> int:
    """About how many tokens a whole message list costs."""

    return sum(message_tokens(message) for message in messages)


def history_budget(
    context_window: Optional[int],
    *,
    system_tokens: int = 0,
    output_tokens: int = 0,
) -> int:
    """How many tokens the conversation history may use.

    Everything the request carries besides the history, the system
    prompt and the space the reply needs, comes off the top first.
    """

    window = context_window or DEFAULT_CONTEXT
    spare = window - system_tokens - output_tokens

    return max(MIN_HISTORY_TOKENS, int(spare * HISTORY_SHARE))


def blocks(messages: list[dict]) -> list[list[dict]]:
    """Split the history into user-led blocks.

    A block is one user message and everything that answered it. Any
    messages before the first user message (a summary of what was
    trimmed earlier, say) form a block of their own at the front.
    """

    grouped: list[list[dict]] = []

    for message in messages:
        if message.get("role") == "user" or not grouped:
            grouped.append([message])
        else:
            grouped[-1].append(message)

    return grouped


@dataclass
class Trimmed:
    """What survived the budget, and what did not."""

    kept: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    tokens: int = 0

    @property
    def lost(self) -> bool:
        return bool(self.dropped)


def _shrink_last(block: list[dict], budget: int) -> list[dict]:
    """Cut a single oversized block down, worst offender first.

    Only reached when one exchange alone will not fit, which in
    practice means a tool returned something enormous. The user message
    that started it and the model's last word are what the next turn
    actually needs, so the tool traffic in the middle goes first.
    """

    kept = list(block)

    while len(kept) > 1 and total_tokens(kept) > budget:
        # Walk forward to the first tool result, which is the oldest
        # and so the least likely to still matter.
        for index, message in enumerate(kept):
            if message.get("role") == "tool":
                del kept[index]
                # The call that asked for it is now unanswered, which
                # some chat templates reject outright.
                if index and kept[index - 1].get("tool_calls"):
                    del kept[index - 1]
                break
        else:
            # Nothing left to cut but the conversation itself.
            del kept[1 if len(kept) > 1 else 0]

    return kept


def trim(messages: list[dict], budget: int) -> Trimmed:
    """Fit `messages` into `budget`, dropping whole blocks from the front.

    The newest block always survives, even when it alone is over
    budget, because dropping it would throw away the request the model
    is answering right now.
    """

    if not messages:
        return Trimmed()

    grouped = blocks(messages)
    kept_blocks = list(grouped)
    dropped: list[dict] = []

    while len(kept_blocks) > 1:
        flat = [message for block in kept_blocks for message in block]
        if total_tokens(flat) <= budget:
            break
        dropped.extend(kept_blocks.pop(0))

    kept = [message for block in kept_blocks for message in block]

    if total_tokens(kept) > budget and len(kept_blocks) == 1:
        shrunk = _shrink_last(kept_blocks[0], budget)
        # Compared by identity, not value: a block can hold two equal
        # messages, and `not in` would then report neither as dropped.
        survived = {id(message) for message in shrunk}
        dropped.extend(
            message for message in kept if id(message) not in survived
        )
        kept = shrunk

    return Trimmed(kept=kept, dropped=dropped, tokens=total_tokens(kept))


def transcript(messages: list[dict]) -> str:
    """The messages as plain text, for handing to a summarizer."""

    lines = []

    for message in messages:
        role = message.get("role", "?")
        body = (message.get("content") or "").strip()

        calls = message.get("tool_calls") or []
        if calls:
            names = ", ".join(
                str(call.get("function", {}).get("name", "?"))
                for call in calls
                if isinstance(call, dict)
            )
            body = (body + f"\n[called: {names}]").strip()

        if message.get("role") == "tool":
            role = f"tool:{message.get('tool_name', '?')}"

        if body:
            lines.append(f"{role}: {body}")

    return "\n\n".join(lines)


SUMMARY_INSTRUCTION = """\
Summarize the conversation below so it can replace the original in a \
context window that has run out of room. Write it for yourself to read \
later, not for the user, and keep every detail a later turn would \
need: what the user asked for, decisions made and the reasons for \
them, files and paths touched and what changed in each, commands run \
and what they returned, facts established, and anything still \
unfinished. Drop pleasantries, restatements, and anything already \
superseded. Write plain prose or short bullets, no preamble, under 400 \
words. Reply with the summary alone."""


def summary_request(messages: list[dict]) -> list[dict]:
    """The messages to send a model to summarize what is being dropped."""

    return [
        {"role": "system", "content": SUMMARY_INSTRUCTION},
        {"role": "user", "content": transcript(messages)},
    ]


def summary_message(text: str) -> dict:
    """Wrap a summary so it survives as the head of the history."""

    return {
        "role": "user",
        "content": f"{SUMMARY_MARKER}\n{text.strip()}",
    }


def is_summary(message: dict) -> bool:
    """Whether this message is a summary this module produced."""

    return SUMMARY_MARKER in (message.get("content") or "")


def merge_summary(messages: list[dict], summary: str) -> list[dict]:
    """Put `summary` at the head of `messages`, replacing an older one.

    Two summaries in a row would compound: the second summarizes a
    history that already began with the first, so keeping both means
    paying for the same conversation twice.
    """

    body = [message for message in messages if not is_summary(message)]

    if not summary.strip():
        return body

    return [summary_message(summary), *body]
