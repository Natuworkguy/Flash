"""Debates: /debate, two sides arguing a question out.

The first side takes the answer it finds strongest and argues for it;
the second argues the strongest case against, and each answers the
other, ROUNDS times. Then a third voice, the chat's own model, sums it
up: where they agreed, where they did not, which arguments held, and
what the answer turns on. Each side can be its own model
("/debate alpha:8b vs beta:8b: question"), or both the chat's.

No tools: they argue from what they know and what the conversation
holds. The question and the summary join the conversation, so it can
go on from there ("which side do you buy?").
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Optional

from . import btw

ROUNDS = 2
MOST_ROUNDS = 4
# How much of the chat comes along, as for a side question.
HISTORY_CHARS = 12_000

USAGE = ("Give a question after /debate, like /debate should I rewrite "
         "this in Rust? Two models: /debate alpha:8b vs beta:8b: question")

SIDES = ("For", "Against")

SIDE_PROMPT = """
You are one side of a debate, in front of the user, who wants to see a
question argued out before they decide. You are {me}; the other side is
{them}.
{stance}
- Argue, do not hedge: make your case as strongly as it honestly goes.
  Never invent facts, figures or sources; say so when you are unsure.
- Answer what the other side last said, point by point, before adding
  anything new. Concede a point that is right: it makes you credible.
- Keep each turn short: a few tight paragraphs or bullets at most.
- Do not address the user, sum up, or call it a tie; that is not yours.
""".strip()

FIRST = ("Open the debate: take the answer to the question you find most "
         "defensible, say it in one line, and argue for it.")
SECOND = ("Argue the strongest case against the other side's answer: a "
          "different answer, or why theirs fails.")

JUDGE_PROMPT = """
You have watched a debate between {a} and {b} on the user's question,
which the user may have steered along the way: weigh what they asked
for.
Sum it up for the user, plainly, with these headings:
**Where they agree**, **Where they disagree**, **What held up** (the
strongest arguments on each side, and any that fell apart),
**What it turns on** (the facts or priorities that decide it, so the
user can decide for themselves), and **My take**, in a sentence or two:
which way you lean and why, or what you would need to know.
Do not take a side in the other sections. Be brief.
""".strip()


def parse(text: str) -> tuple[str, list[str]]:
    """The question in "/debate ..." or after it, and the models named
    for the sides, if any: "a vs b: question"."""

    text = re.sub(r"^/debate\b", "", str(text or "").strip()).strip()
    # Model names have colons in them: a colon and a space ends them.
    found = re.match(r"^(\S+)\s+vs\.?\s+(\S+?):\s+(.+)$", text, re.S)
    if found:
        return found.group(3).strip(), [found.group(1), found.group(2)]
    return text, []


def _history(messages: list[dict]) -> list[dict]:
    kept: list[dict] = []
    room = HISTORY_CHARS
    for message in reversed(messages):
        content = message.get("content")
        text = content.strip() if isinstance(content, str) else ""
        if message.get("role") not in ("user", "assistant") or not text:
            continue
        if len(text) > room:
            break
        kept.append({"role": message["role"], "content": text})
        room -= len(text)
    kept.reverse()
    return kept


# A turn the user put in to steer it, among the sides' turns.
USER = -1


def _transcript(turns: list[dict], names: list[str]) -> str:
    def who(side: int) -> str:
        return "The user, steering the debate" if side == USER \
            else names[side]

    return "\n\n".join(f"{who(t['side'])}:\n{t['text']}" for t in turns)


def run(
    question: str,
    client: Any,
    models: list[str],
    judge: str,
    history: Optional[list[dict]] = None,
    rounds: int = ROUNDS,
    options: Optional[dict] = None,
    say: Optional[Callable[[dict], None]] = None,
    stopped: Callable[[], bool] = lambda: False,
    turns: Optional[list[dict]] = None,
    steers: Callable[[], list[str]] = lambda: [],
) -> dict:
    """Argue QUESTION out. MODELS are the two sides' (one for both, or
    two); JUDGE sums it up. TURNS goes on from a debate already had, for
    ROUNDS more. STEERS gives what the user has said to steer it since
    it last asked, taken in before each turn as a turn of theirs.

    SAY hears each piece as it comes: {"turn": n, "side": 0 or 1, "text":
    piece}, {"turn": n, "side": -1, "text": steer}, then {"summary":
    piece}. The whole debate, at the end: question, sides, turns,
    summary, and whether it was stopped."""

    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError(USAGE)
    models = (list(models) + list(models))[:2] if models else [judge] * 2
    same = models[0] == models[1]
    names = list(SIDES) if same else \
        [f"{SIDES[0]} ({models[0]})", f"{SIDES[1]} ({models[1]})"]
    say = say or (lambda event: None)
    rounds = max(1, min(int(rounds or ROUNDS), MOST_ROUNDS))
    context = _history(history or [])
    turns = [dict(t) for t in turns or []]

    def take_steers() -> None:
        for steer in steers():
            steer = " ".join(str(steer or "").split())
            if steer:
                say({"turn": len(turns), "side": USER, "text": steer})
                turns.append({"side": USER, "text": steer})

    for _ in range(rounds):
        for side in (0, 1):
            if stopped():
                break
            take_steers()
            spoken = sum(1 for t in turns if t["side"] != USER)
            stance = FIRST if not spoken else SECOND if spoken == 1 \
                else "Carry on: answer their last turn, and press your case."
            if turns and turns[-1]["side"] == USER:
                stance += (" The user has just steered the debate: take "
                           "what they said into account first.")
            system = SIDE_PROMPT.format(me=names[side], them=names[1 - side],
                                        stance=stance)
            asked = [{"role": "system", "content": system}, *context,
                     {"role": "user", "content": f"The question: {question}"
                      + (f"\n\nThe debate so far:\n\n"
                         f"{_transcript(turns, names)}" if turns else "")
                      + f"\n\nYour turn, {names[side]}."}]
            n = len(turns)
            text = btw.ask(client, models[side], asked, options,
                           on_text=lambda piece, n=n, side=side: say(
                               {"turn": n, "side": side, "text": piece}),
                           stopped=stopped)
            if text:
                turns.append({"side": side, "text": text})
    summary = ""
    if not stopped():
        take_steers()
    if any(t["side"] != USER for t in turns) and not stopped():
        asked = [{"role": "system",
                  "content": JUDGE_PROMPT.format(a=names[0], b=names[1])},
                 {"role": "user", "content": f"The question: {question}"
                  f"\n\nThe debate:\n\n{_transcript(turns, names)}"}]
        summary = btw.ask(client, judge, asked, options,
                          on_text=lambda piece: say({"summary": piece}),
                          stopped=stopped)
    return {"question": question, "sides": names, "models": models,
            "turns": turns, "summary": summary,
            "stopped": bool(stopped())}


def note(debate: dict) -> list[dict]:
    """What the conversation keeps of DEBATE, for it to go on from: the
    question, and the summary as the answer; stopped before one, what
    each side said."""

    head = (f"(A debate between {debate['sides'][0]} and "
            f"{debate['sides'][1]}, {len(debate['turns'])} turns")
    if debate.get("summary"):
        said = f"{head}. The summary:)\n\n{debate['summary']}"
    else:
        said = (f"{head}, stopped by the user before the end:)\n\n"
                + _transcript(debate["turns"], debate["sides"]))
    return [
        {"role": "user", "content": f"Debate this: {debate['question']}"},
        {"role": "assistant", "content": said},
    ]
