"""The agent loop: one, for every place Flash runs one.

A turn in the terminal, a turn in the web UI, a sub-agent, a spark's
shift and the learning review each ask the model, run the tools it
asks for, and ask again until it answers. They differ in how they ask
(streamed to a page, printed to a terminal, quietly on a thread) and in
which tools they offer, not in the rules, so the rules live here:

- How many rounds a turn gets (MAX_TOOL_ROUNDS, 50 unless set), and
  what happens when it runs out: the steps the model just asked for are
  run, not dropped, and then it is asked, with no tools, for its answer
  from everything it found. A half-written "Now let me run the tests:"
  is never shown as the answer.
- A reply with neither words nor a tool call is asked for again, twice.
- Code changed and not checked is sent back, once, to run the project's
  checks (verify.Watch), wherever the change was made.
- Each tool's output is cut to the output limit (tools.trim_tool_output),
  which the model can raise with more_output when a long log matters.

Callers give it three things: ask, which asks the model and returns a
Reply; call, which runs one tool and returns its output; and, if they
need one, after_round, which adds what came in during the round (images
a tool attached, a message the user sent to steer the turn).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DEFAULT_ROUNDS = 50
EMPTY_RETRIES = 2

EMPTY_NUDGE = (
    "Your last reply was empty. Answer now, or call the tool you need next."
)


def limit_message(ran: int) -> str:
    """Told to the model when its rounds are used up."""

    return (
        f"You have used all {ran} tool rounds this turn allows, and the "
        "steps you last asked for have been run: their results are above. "
        "Answer now from everything the tools found, with no more tool "
        "calls. Say what is done, what is not, and what to run next to "
        "finish, so the user can tell you to carry on."
    )


def max_rounds() -> int:
    """A turn's rounds: MAX_TOOL_ROUNDS, else DEFAULT_ROUNDS."""

    try:
        return max(1, int(os.environ.get("MAX_TOOL_ROUNDS") or DEFAULT_ROUNDS))
    except ValueError:
        return DEFAULT_ROUNDS


@dataclass
class Reply:
    """One answer from the model."""

    content: str = ""
    thinking: str = ""
    calls: list = field(default_factory=list)   # (name, args) pairs
    stopped: bool = False                       # the user stopped it
    error: Optional[str] = None                 # the backend failed

    @property
    def empty(self) -> bool:
        return not self.content.strip() and not self.calls


@dataclass
class Result:
    reply: Reply
    rounds: int = 0
    tool_count: int = 0
    hit_limit: bool = False
    used: list = field(default_factory=list)


def ask_until_said(ask: Callable[..., Reply], messages: list, tools: bool,
                   retries: int = EMPTY_RETRIES,
                   on_retry: Optional[Callable[[int], None]] = None) -> Reply:
    """ASK, again with a nudge while the model says nothing at all."""

    reply = ask(messages, tools)
    for attempt in range(1, retries + 1):
        if reply.error or reply.stopped or not reply.empty:
            break
        if on_retry is not None:
            on_retry(attempt)
        reply = ask(messages + [{"role": "system", "content": EMPTY_NUDGE}],
                    tools)
    return reply


def run(
    messages: list,
    *,
    ask: Callable[[list, bool], Reply],
    call: Callable[[str, dict], Any],
    first: Optional[Reply] = None,
    rounds: Optional[int] = -1,
    after_round: Optional[Callable[[list, list], None]] = None,
    on_reply: Optional[Callable[[Reply], None]] = None,
    on_retry: Optional[Callable[[int], None]] = None,
    stopping: Optional[Callable[[], bool]] = None,
    trim: Optional[Callable[[str, str], str]] = None,
    check_code: bool = True,
    last_word: Optional[Callable[[int], str]] = None,
    retries: int = EMPTY_RETRIES,
) -> Result:
    """Run a turn to its answer.

    MESSAGES grows with each step's assistant message and tool results.
    ROUNDS is how many rounds of tool calls it gets: -1 for max_rounds(),
    None for no limit. FIRST is a reply already in hand (the terminal
    asks once before it knows whether tools are coming). ON_REPLY sees
    each reply that calls tools, before they run, so a page can show the
    words that came with them."""

    from . import tools as flash_tools
    from . import verify

    if rounds == -1:
        rounds = max_rounds()
    trim = trim or flash_tools.trim_tool_output
    flash_tools.reset_output_limit()
    watch = verify.Watch() if check_code else None
    result = Result(reply=first or ask_until_said(ask, messages, True, retries,
                                                  on_retry=on_retry))

    while True:
        reply = result.reply
        if reply.error or reply.stopped:
            return result
        if not reply.calls:
            # About to end with code changed and not checked: sent back,
            # once, to run the project's checks.
            nudge = watch.nudge() if watch is not None else ""
            if not nudge:
                return result
            if on_reply is not None and reply.content.strip():
                on_reply(reply)
            messages.append({"role": "assistant", "content": reply.content})
            messages.append({"role": "user", "content": nudge})
            result.reply = ask_until_said(ask, messages, True, retries,
                                          on_retry=on_retry)
            continue

        # The steps asked for are run, even the round past the limit:
        # what the model asked for is never dropped.
        out_of_rounds = rounds is not None and result.rounds + 1 >= rounds
        result.rounds += 1
        if on_reply is not None:
            on_reply(reply)
        messages.append({
            "role": "assistant", "content": reply.content,
            "tool_calls": [{"function": {"name": n, "arguments": a}}
                           for n, a in reply.calls],
        })
        for name, args in reply.calls:
            if stopping is not None and stopping():
                output = "Stopped by the user before this ran."
            else:
                output = call(name, args)
                if name != "reason":
                    result.tool_count += 1
                    result.used.append(name)
            if watch is not None:
                watch.saw(name, args, output)
            messages.append({"role": "tool", "content": trim(str(output), name),
                             "tool_name": name})
        if after_round is not None:
            after_round(messages, reply.calls)
        if stopping is not None and stopping():
            result.reply = Reply(stopped=True)
            return result
        if out_of_rounds:
            result.hit_limit = True
            messages.append({"role": "system", "content": (
                last_word(result.rounds) if last_word is not None
                else limit_message(result.rounds))})
            result.reply = ask_until_said(ask, messages, False, retries,
                                          on_retry=on_retry)
            # Tools were not offered: any it named anyway are not run.
            result.reply.calls = []
            return result
        result.reply = ask_until_said(ask, messages, True, retries,
                                      on_retry=on_retry)
