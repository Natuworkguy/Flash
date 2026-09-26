"""The learning loop: what Flash carries from one session to the next.

Two halves. What was learned before goes into the system prompt: saved
memory, and the list of skills. And after enough work, a review runs
in the background over what just happened and writes down what is
worth keeping, as a new or corrected skill or a remembered fact, so
the next session starts out knowing it.

The design follows Hermes Agent's. The review is a separate pass after
the reply has gone out, never part of the user's turn, and it only
runs once there has been enough work to learn from: SKILL_REVIEW_AFTER
tool calls since the last one, or MEMORY_REVIEW_EVERY messages.
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

import ollama

from . import context, skills
from .memory import list_memory
from .theme import capture_tool_output

DEFAULT_SKILL_REVIEW_AFTER = 10
DEFAULT_MEMORY_REVIEW_EVERY = 10

# Memory is sent on every turn, so only this much of it is. The newest
# entries win, and the model is told how many older ones it can still
# reach with recall.
MEMORY_PROMPT_CHARS = 2200

# How much of the conversation the review reads, from the end. A local
# model reads a prompt at around a hundred tokens a second, so the
# whole of a long session would keep it busy for minutes.
REVIEW_INPUT_CHARS = 12000

MAX_REVIEW_ROUNDS = 6

# The tools the review may call. No shell, no files, no forget: it
# works unattended, so it gets nothing it could do damage with.
REVIEW_TOOL_NAMES = ("skill_view", "skill_manage", "remember", "recall")

NOTHING = "Nothing to save."

MEMORY_GUIDE = """\
Memory is for facts about the user and their environment: who they \
are, how they like to work, project conventions, paths and settings \
that matter. Save a fact with remember, once, in one short line. Use \
recall first to check it is not already saved."""

SKILL_GUIDE = """\
A skill is the procedure for a class of task, done the way this user \
wants it: the steps in order, the commands that work, and the pitfalls \
that cost time, each with one clause of why. A future session should \
be able to follow it and get it right the first time.

Signals that call for a skill update, any one is enough:
- The user corrected your approach, format, tone, or length. Put the \
lesson in the skill for that kind of task.
- A non-obvious fix, workaround, or sequence of steps worked.
- A skill used in this conversation was wrong, missing a step, or out \
of date. Patch it.

How:
- Prefer patching an existing skill that covers the task over creating \
one. Call skill_view on it first and copy old_string from what it \
returns.
- Name a new skill for the kind of task, never for today's instance of \
it. If the name only fits today's task, do not create it.
- Fix a wrong line in place; do not append a correction under it.
- Write rules, not a story of this session: no dates, ticket numbers, \
or quotes.

Do not save:
- Failures from missing tools, packages, or credentials. The user can \
fix those; they are not rules.
- Claims that a tool or feature does not work.
- Attempts that never worked. If nothing worked, save nothing.
- One-off tasks that will not come up again.
- Skills marked as the user's own: you may not change those."""

REVIEW_SYSTEM_PROMPT = f"""\
=== Learning review ===
You are Flash, reviewing a conversation you just had, to learn from it. \
Nobody is watching this pass and it gets no reply, so do not address \
the user. Use the tools to save what is worth keeping, then answer with \
one short line saying what you saved, or exactly "{NOTHING}" if nothing \
was worth it. Saving nothing is right when the conversation was routine."""

# No example skill or fact anywhere in these prompts: a model shown one
# tends to save the example rather than what it learned.
SKILLS_TASK = "Review the conversation above and update the skill library."
MEMORY_TASK = "Review the conversation above and save anything worth " \
    "remembering about the user or their environment."
BOTH_TASK = "Review the conversation above: update the skill library, " \
    "and save anything worth remembering about the user or their " \
    "environment."


def _setting(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def skill_review_after() -> int:
    """Tool calls between skill reviews; 0 turns them off."""

    return _setting("SKILL_REVIEW_AFTER", DEFAULT_SKILL_REVIEW_AFTER)


def memory_review_every() -> int:
    """Messages between memory reviews; 0 turns them off."""

    return _setting("MEMORY_REVIEW_EVERY", DEFAULT_MEMORY_REVIEW_EVERY)


# --- What goes into the system prompt ------------------------------------


def memory_block() -> str:
    """Saved memory for the system prompt, newest first to be cut."""

    entries = list_memory()

    if not entries:
        return ""

    kept: list[str] = []
    used = 0

    for entry in reversed(entries):
        line = f"- {entry}"
        if used + len(line) > MEMORY_PROMPT_CHARS:
            break
        kept.insert(0, line)
        used += len(line) + 1

    older = len(entries) - len(kept)
    note = (
        f"\n({older} older entries are not shown; recall searches them.)"
        if older else ""
    )

    return (
        "=== Memory ===\n"
        "Facts saved in past sessions. Use them without being asked.\n"
        + "\n".join(kept)
        + note
    )


_snapshot: Optional[str] = None


def prompt_block() -> str:
    """Memory and skills for the system prompt, as of session start.

    Frozen until refresh(): Ollama reuses its work on a prompt whose
    start has not changed, and a local model rereads a changed one at a
    hundred tokens a second. So what is saved during a session reaches
    the prompt in the next one, or after /clear.
    """

    global _snapshot

    if _snapshot is None:
        _snapshot = "\n\n".join(
            part for part in (memory_block(), skills.listing()) if part
        )

    return _snapshot


def refresh() -> None:
    global _snapshot
    _snapshot = None


# --- The background review -----------------------------------------------


@dataclass
class Review:
    """One background pass, and what it did."""

    skills: bool
    memory: bool
    started: float = field(default_factory=time.time)
    actions: list[str] = field(default_factory=list)
    reply: str = ""
    error: str = ""
    done: bool = False


@dataclass
class _State:
    tool_calls: int = 0
    turns: int = 0
    running: Optional[Review] = None
    finished: list[Review] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)


_state = _State()
_lock = threading.Lock()

# ai.py swaps in its own, so the review asks for the same window.
chat_options: Callable[[], dict] = dict


def reset() -> None:
    """Start the counts over, as a new session does."""

    global _state
    cancel()
    with _lock:
        _state = _State()


def running() -> bool:
    with _lock:
        return _state.running is not None


def _task(review: Review) -> str:
    if review.skills and review.memory:
        return BOTH_TASK
    return SKILLS_TASK if review.skills else MEMORY_TASK


def review_messages(review: Review, messages: list[dict]) -> list[dict]:
    """What the review model is sent."""

    guides = [REVIEW_SYSTEM_PROMPT]
    if review.skills:
        guides.append(SKILL_GUIDE)
        guides.append(
            skills.listing() or "=== Skills ===\nNo skills saved yet."
        )
    if review.memory:
        guides.append(MEMORY_GUIDE)
        guides.append(memory_block() or "=== Memory ===\nNothing saved yet.")

    conversation = context.transcript(messages)
    if len(conversation) > REVIEW_INPUT_CHARS:
        conversation = (
            "(earlier conversation cut)\n"
            + conversation[-REVIEW_INPUT_CHARS:]
        )

    return [
        {"role": "system", "content": "\n\n".join(guides)},
        {
            "role": "user",
            "content": (
                "=== Conversation ===\n"
                f"{conversation}\n"
                "=== End of conversation ===\n\n"
                f"{_task(review)}"
            ),
        },
    ]


def _schemas(names: tuple[str, ...]) -> list[dict[str, Any]]:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    return [t for t in flash_tools.tools if t["function"]["name"] in names]


def _allowed(review: Review) -> tuple[str, ...]:
    names = []
    if review.skills:
        names += ["skill_view", "skill_manage"]
    if review.memory:
        names += ["remember", "recall"]
    return tuple(names)


def _call(review: Review, name: str, args: dict) -> str:
    """Run one of the review's tools, keeping a note of what changed."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    if name not in _allowed(review):
        return f"Unknown tool: {name}."

    if name == "skill_manage":
        args.pop("managed_only", None)
        result = flash_tools.skill_manage_tool(**args, managed_only=True)
    else:
        result = flash_tools.run_tool((name, args))

    succeeded = (
        (name == "skill_manage" and not result.startswith("Error"))
        or (name == "remember" and result.startswith("Remembered"))
    )
    if succeeded:
        review.actions.append(result)

    return result


def _stream(client, model: str, messages: list, tools: list) -> Any:
    """One chat call, streamed so a cancel can cut it off between chunks.

    Closing a stream is what tells Ollama to stop, which matters on a
    machine that can only run one generation at a time: the user's own
    turn would otherwise wait behind this one.
    """

    content = []
    calls: list = []

    for chunk in client.chat(
        model=model,
        messages=messages,
        tools=tools,
        options=chat_options(),
        stream=True,
    ):
        if _state.cancel.is_set():
            raise InterruptedError("review cancelled")
        message = getattr(chunk, "message", None)
        content.append(getattr(message, "content", "") or "")
        calls.extend(getattr(message, "tool_calls", None) or [])

    return "".join(content), calls


def _run(review: Review, messages: list[dict], host: str, model: str):
    try:
        client = ollama.Client(host=host)
        tools = _schemas(_allowed(review))
        chat = review_messages(review, messages)
        reply = ""

        # Tool output goes to a sink, never the terminal: this thread
        # would otherwise print into the middle of the user's prompt.
        with capture_tool_output(lambda *_event: None):
            for _round in range(MAX_REVIEW_ROUNDS):
                reply, calls = _stream(client, model, chat, tools)
                if not calls:
                    break

                named = [
                    (
                        call.function.name,
                        dict(call.function.arguments or {}),
                    )
                    for call in calls
                ]
                chat.append({
                    "role": "assistant",
                    "content": reply,
                    "tool_calls": [
                        {"function": {"name": n, "arguments": a}}
                        for n, a in named
                    ],
                })
                for name, args in named:
                    try:
                        result = _call(review, name, args)
                    except TypeError as exc:
                        result = f"Error: {exc}"
                    chat.append(
                        {"role": "tool", "content": result, "tool_name": name}
                    )

        review.reply = reply.strip()
    except InterruptedError:
        review.error = "cancelled"
    except Exception as exc:  # noqa: BLE001
        review.error = f"{exc.__class__.__name__}: {exc}"
    finally:
        review.done = True
        with _lock:
            if _state.running is review:
                _state.running = None
            if review.error == "cancelled" and not review.actions:
                # Nothing was learned, so the work it was reviewing is
                # still owed a review, and the counts carry on from it.
                if review.skills:
                    _state.tool_calls += skill_review_after()
                if review.memory:
                    _state.turns += memory_review_every()
            else:
                _state.finished.append(review)


def after_turn(
    messages: list[dict],
    tool_calls: int,
    *,
    host: str,
    model: str,
    start: Callable[..., Any] = threading.Thread,
) -> Optional[Review]:
    """Count a finished turn, and start a review if one is due."""

    skill_every = skill_review_after()
    memory_every = memory_review_every()

    with _lock:
        _state.tool_calls += tool_calls
        _state.turns += 1

        if _state.running is not None or not model:
            return None

        due_skills = bool(skill_every) and _state.tool_calls >= skill_every
        due_memory = bool(memory_every) and _state.turns >= memory_every

        if not (due_skills or due_memory):
            return None

        if due_skills:
            _state.tool_calls = 0
        if due_memory:
            _state.turns = 0

        review = Review(skills=due_skills, memory=due_memory)
        _state.running = review
        _state.cancel.clear()

    worker = start(
        target=_run,
        args=(review, [dict(m) for m in messages], host, model),
        daemon=True,
    )
    worker.start()
    return review


def cancel() -> None:
    """Stop a running review, so the user's next turn is not kept
    waiting behind it. It is owed again after the next turn."""

    _state.cancel.set()


def news() -> list[str]:
    """What finished reviews learned, once each, for the user to see."""

    with _lock:
        finished, _state.finished = _state.finished, []

    lines = []
    for review in finished:
        if review.actions:
            lines.extend(review.actions)
        elif review.error:
            lines.append(f"Learning review failed: {review.error}")

    return lines
