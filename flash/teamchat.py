"""A team's group chat: the user and the team's sparks, in one room.

Every team has one. The user writes in it; the sparks it @mentions
answer, or its lead when it names no one, and a spark can @mention a
teammate to bring it in too, a few replies at most for each message of
the user's, so two sparks never talk on forever. Around the talk, the
team's news lands in it as it happens: a report filed, a shift that
failed, a spark that joined. And the latest of it is in every member's
prompt, on shifts as in chats, so what the user says to the room
reaches the whole team.

It is kept in a file of its own for each team, under the sparks'
folder, read and written under the sparks' own lock, so a keeper
running elsewhere can post its news while an open Flash shows it.
"""

from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from . import sparks

# How many entries a team's chat keeps, oldest dropped first.
MAX_ENTRIES = 300
# How many replies from sparks one message of the user's can set off,
# counting those a spark's @mention brings in.
REPLY_LIMIT = 4
# How much of the chat a spark reads before it answers.
CONTEXT_ENTRIES = 30
ENTRY_CHARS = 1500
EVENT_CHARS = 280
# How much of it every member carries in its prompt.
PROMPT_ENTRIES = 6
PROMPT_CHARS = 300
# How much of the message a reply is to it carries, for the page's quote
# and for the sparks reading it.
QUOTE_CHARS = 600
# A spark marked typing for longer than this has gone quiet: a Flash
# that quit half way through an answer.
TYPING_STALE = 10 * 60

USER = "user"
SPARK = "spark"
EVENT = "event"

TEAM_CHAT_PROMPT = """
=== You are in your team's group chat ===
This is {team}'s group chat: the user and every spark on the team, in
one conversation. You are {name} ({handle}). The others on the team:
{roster}

You are shown the latest of the chat, then asked to post your message.
- Write as yourself, like a message in a group chat: short, plain,
  friendly. A few sentences at most, unless you were asked for more.
- Answer what was said to you, or to the team, from what you know and
  have found. Do not repeat what a teammate already said.
- To bring a teammate in, @mention them by handle in your message, and
  they answer after you. Only do that when they are really needed.
- Do not speak for the user or for another spark.
- Real work goes in a shift: call take_on, then say you are on it.
- You need not answer. When you have nothing worth adding (it was not
  for you, a teammate already said it, or there is nothing to say),
  stay quiet: answer with only {quiet}, and nothing is posted.
- You can send more than one message, as people do in a group chat:
  call send_message to post one now, say a quick "on it, checking"
  before you look into something, and your answer is posted after it.
  When send_message already said it all, answer with only {quiet}.
- Each message in the chat starts with its number, as #12. To react to
  one with an emoji, call react with its number: a thumbs up says you
  saw it or agree, with no message needed. When the reaction says it
  all, answer with only {quiet}.
""".strip()

# How many messages a spark may post in one turn with send_message, so
# one turn is a few lines in the chat, not a flood.
MAX_SENT = 4

SEND_MESSAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "send_message",
        "description": (
            "Post a message in the team chat now, before your answer: a "
            "quick word while you work, or one thought of several, each "
            f"its own message. At most {MAX_SENT} in a turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message, in a sentence or a few.",
                },
            },
            "required": ["text"],
        },
    },
}

# The reactions the page offers first. Any one emoji will do.
QUICK_REACTIONS = ("\U0001f44d", "❤️", "\U0001f602", "\U0001f389",
                   "\U0001f440", "\U0001f64f", "\U0001f525", "✅")
# How many different reactions one message can carry, and how long one
# can be: an emoji made of several, a family or a flag, runs to a few.
MAX_REACTIONS = 12
REACTION_CHARS = 16
# How many reactions a spark may put on in one turn.
MAX_REACTED = 3

REACT_TOOL = {
    "type": "function",
    "function": {
        "name": "react",
        "description": (
            "React to a message in the team chat with an emoji, by its "
            "number: a thumbs up to say you saw it, a party popper for "
            "good news. It posts no message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "integer",
                    "description": "The message's number, as in #12.",
                },
                "emoji": {
                    "type": "string",
                    "description": "One emoji.",
                },
            },
            "required": ["message", "emoji"],
        },
    },
}

# What a spark answers in the team chat to stay quiet. Only there: in a
# chat with the user or on a shift, a spark always answers.
QUIET = "NO_REPLY"


def _quiet(text: str) -> bool:
    """Whether TEXT is a spark choosing not to answer."""

    said = " ".join(str(text or "").split()).strip(" .!*_`\"'").upper()
    return said in (QUIET, QUIET.replace("_", " "), QUIET.replace("_", ""))


_listeners: list[Callable[[str], None]] = []
_busy: set[str] = set()
# The latest message for each team sent while its last was answered.
_waiting: dict[str, dict] = {}
_busy_lock = threading.Lock()


class TeamChatError(ValueError):
    """A message that could not be sent as asked."""


def on_change(listener: Callable[[str], None]) -> None:
    """Call LISTENER with a team's id whenever its chat changes."""

    if listener not in _listeners:
        _listeners.append(listener)


def _changed(team_id: str) -> None:
    for listener in list(_listeners):
        try:
            listener(team_id)
        except Exception:  # noqa: BLE001, S110
            pass  # a page that is gone never stops the chat


def _path(team_id: str) -> Path:
    return sparks.sparks_dir() / "teamchat" / f"{team_id}.json"


def _read(team_id: str) -> dict:
    try:
        data = json.loads(_path(team_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
    typing = data.get("typing") if isinstance(data.get("typing"), dict) else {}
    return {
        "seq": int(data.get("seq") or 0), "entries": entries,
        "typing": typing, "read": float(data.get("read") or 0),
    }


def _write(team_id: str, data: dict) -> None:
    path = _path(team_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _change(team_id: str, change: Callable[[dict], object]):
    """Read TEAM_ID's chat, CHANGE it, write it back, under the sparks'
    lock; what CHANGE returned."""

    with sparks._held():
        data = _read(team_id)
        out = change(data)
        _write(team_id, data)
    _changed(team_id)
    return out


def _add(data: dict, entry: dict) -> dict:
    data["seq"] += 1
    entry = {"id": data["seq"], "at": time.time(), **entry}
    data["entries"].append(entry)
    del data["entries"][:-MAX_ENTRIES]
    return entry


def _team(key: str) -> sparks.Team:
    team = sparks.find_team(key)
    if team is None:
        raise TeamChatError(f"There is no team called {key}.")
    return team


def history(team_key: str, since: int = 0) -> dict:
    """TEAM_KEY's chat for the page: the entries after SINCE, those
    before it reacted to since, who is typing, and how many came in since
    the user last looked."""

    team = _team(team_key)
    data = _read(team.id)
    now = time.time()
    typing = [
        {"spark": sid, "doing": str(t.get("doing") or "")}
        for sid, t in data["typing"].items()
        if isinstance(t, dict) and now - float(t.get("at") or 0) < TYPING_STALE
    ]
    return {
        "team": team.id, "seq": data["seq"],
        "entries": [e for e in data["entries"] if e.get("id", 0) > since],
        # Those it has, but changed since: their reactions, as now.
        "changed": [
            e for e in data["entries"]
            if since and 0 < e.get("id", 0) <= since
            and e.get("changed", 0) > since
        ],
        "typing": typing,
        "unread": _unread(data),
    }


def _unread(data: dict) -> int:
    """What the sparks said since the user looked: not the user's own
    messages, nor news of what the user did to the team."""

    return sum(
        1 for e in data["entries"]
        if e.get("kind") != USER and e.get("at", 0) > data["read"]
        and not (e.get("kind") == EVENT and not e.get("spark"))
    )


def unread(team_id: str) -> int:
    return _unread(_read(team_id))


def mark_read(team_key: str) -> bool:
    """Mark TEAM_KEY's chat read: whether there was anything to. With
    nothing new it writes nothing, so a page that marks it each time it
    looks does not set itself off again."""

    team = _team(team_key)
    if not unread(team.id):
        return False

    def change(data: dict) -> None:
        data["read"] = time.time()

    _change(team.id, change)
    return True


def event(team_id: str, spark: Optional[sparks.Spark], what: str,
          text: str = "", files: Optional[list] = None) -> Optional[dict]:
    """Put news in TEAM_ID's chat: WHAT happened (report, failed,
    joined), by SPARK, with TEXT, the start of what it said."""

    if not team_id or sparks.find_team(team_id) is None:
        return None
    text = " ".join(str(text or "").split())
    if len(text) > EVENT_CHARS:
        text = text[:EVENT_CHARS].rstrip() + " [...]"
    by = {"spark": spark.id, "name": spark.name} if spark else {}
    if files:
        by["files"] = list(files)
    return _change(team_id, lambda data: _add(data, {
        "kind": EVENT, "what": what, "text": text, **by,
    }))


def _members(team_id: str) -> list[sparks.Spark]:
    return sparks.members(team_id)


def _leads(team_id: str) -> list[sparks.Spark]:
    """The team's sparks that report to no one on it: its leads."""

    found = _members(team_id)
    ids = {s.id for s in found}
    return [s for s in found if s.reports_to not in ids]


def _named(
    team_id: str, text: str, skip: set[str], everyone: bool = False,
) -> list[sparks.Spark]:
    """The team's sparks TEXT @mentions, but those in SKIP. With
    EVERYONE, an @everyone in it names the whole team, its leads first:
    the user's way to ask them all; a spark's own @everyone names no
    one."""

    team = _members(team_id)
    ids = {s.id for s in team}
    leads = _leads(team_id)
    order = leads + [s for s in team if all(s.id != lead.id for lead in leads)]
    return [
        s for s in sparks.mentioned(text, everyone, order)
        if s.id in ids and s.id not in skip
    ]


def _quote(entry: dict) -> dict:
    """What a reply keeps of the message it answers: whose it is, and
    the start of what it said."""

    text = " ".join(str(entry.get("text") or "").split())
    if len(text) > QUOTE_CHARS:
        text = text[:QUOTE_CHARS].rstrip() + " [...]"
    quote = {"id": entry.get("id", 0), "kind": entry.get("kind", ""),
             "text": text}
    if entry.get("spark"):
        quote["spark"] = entry["spark"]
        quote["name"] = entry.get("name", "")
    if entry.get("kind") == EVENT:
        quote["what"] = entry.get("what", "")
    return quote


def say(team_key: str, text: str, reply_to: int = 0) -> dict:
    """Post what the user wrote in TEAM_KEY's chat, as a reply to the
    message numbered REPLY_TO if one is given. The sparks' answers are
    answer()'s to make."""

    team = _team(team_key)
    text = str(text or "").strip()[:sparks.MESSAGE_CHARS]
    if not text:
        raise TeamChatError("Say something first.")
    if not _members(team.id):
        raise TeamChatError(
            f"{team.name} has no sparks yet: put one on it to talk to."
        )

    def change(data: dict) -> dict:
        entry = {"kind": USER, "text": text}
        if reply_to:
            to = next(
                (e for e in data["entries"] if e.get("id") == reply_to), None,
            )
            if to is None:
                raise TeamChatError(
                    "That message is no longer in the chat to reply to."
                )
            entry["reply"] = _quote(to)
        data["read"] = time.time()
        return _add(data, entry)

    return _change(team.id, change)


def _line(entry: dict, by_id: dict) -> str:
    line = _said_line(entry, by_id)
    reactions = _reactions(entry)
    if not reactions:
        return line
    names = {USER: "the user"}
    return line + " [reactions: " + "; ".join(
        f"{emoji} " + ", ".join(
            names.get(who) or getattr(by_id.get(who), "name", "a spark")
            for who in whose
        )
        for emoji, whose in reactions.items()
    ) + "]"


def _said_line(entry: dict, by_id: dict) -> str:
    text = " ".join(str(entry.get("text") or "").split())
    if len(text) > ENTRY_CHARS:
        text = text[:ENTRY_CHARS].rstrip() + " [...]"
    kind = entry.get("kind")
    if kind == USER:
        reply = entry.get("reply")
        if not reply:
            return f"The user: {text}"
        whose = (
            "their own message" if reply.get("kind") == USER
            else f"{reply.get('name') or 'a spark'}'s "
            + ("news" if reply.get("kind") == EVENT else "message")
        )
        return (
            f"The user, replying to {whose} \"{reply.get('text', '')}\": "
            f"{text}"
        )
    spark = by_id.get(entry.get("spark", ""))
    if kind == EVENT and not entry.get("spark"):
        # News about the team itself, not one of its sparks.
        return f"(The user {entry.get('what', '')} the team)"
    name = entry.get("name") or (spark.name if spark else "A spark")
    who = f"{name} ({spark.handle})" if spark else name
    if kind == SPARK:
        return f"{who}: {text}"
    what = {
        "report": "filed a report", "failed": "could not finish a shift",
        "joined": "joined the team", "left": "left the team",
    }.get(entry.get("what", ""), entry.get("what", ""))
    return f"({who} {what}{': ' + text if text else ''})"


def _view(team: sparks.Team, spark: sparks.Spark, entries: list) -> str:
    by_id = {s.id: s for s in sparks.all_sparks()}
    lines = [f"#{e.get('id', 0)} {_line(e, by_id)}" for e in entries]
    return (
        f"=== {team.name}'s chat: its latest {len(lines)} messages ===\n"
        + "\n\n".join(lines)
        + "\n=== End of the chat ===\n\n"
        f"Post your message in the chat now, as {spark.name}."
    )


def _set_typing(team_id: str, spark_id: str, doing: str) -> None:
    def change(data: dict) -> None:
        if doing:
            data["typing"][spark_id] = {"at": time.time(), "doing": doing}
        else:
            data["typing"].pop(spark_id, None)

    _change(team_id, change)


def _post(team_id: str, spark: sparks.Spark, entry: dict,
          done: bool = False) -> dict:
    """Put ENTRY, SPARK's, in the chat; DONE takes it off the typing."""

    def change(data: dict) -> dict:
        if done:
            data["typing"].pop(spark.id, None)
        return _add(data, {
            "kind": SPARK, "spark": spark.id, "name": spark.name, **entry,
        })

    return _change(team_id, change)


def _sending(team_id: str, spark: sparks.Spark, sent: list[dict]):
    """send_message, for SPARK: posted now, and kept in SENT."""

    def send(args: dict) -> str:
        text = str(args.get("text", "")).strip()[:sparks.MESSAGE_CHARS]
        if not text or _quiet(text):
            return "Error: the message was empty."
        if len(sent) >= MAX_SENT:
            return (
                f"Error: that is {MAX_SENT} messages this turn already. "
                "Put the rest in your answer."
            )
        sent.append(_post(team_id, spark, {"text": text}))
        sparks.tool_line(f"SendMessage({text[:60]})")
        return "(posted in the chat)"

    return send


def _reactions(entry: dict) -> dict[str, list[str]]:
    found = entry.get("reactions")
    if not isinstance(found, dict):
        return {}
    return {
        str(emoji): [str(w) for w in whose]
        for emoji, whose in found.items()
        if isinstance(whose, list) and whose
    }


def _emoji(text: str) -> str:
    """TEXT, if it is an emoji to react with; else TeamChatError."""

    said = str(text or "").strip()
    if (
        not said or len(said) > REACTION_CHARS
        # Emoji, their joiners and variations are all past U+2000; a
        # word is not, nor a space.
        or any(ord(c) < 0x2000 or c.isspace() for c in said)
        or not any(unicodedata.category(c) == "So" for c in said)
    ):
        raise TeamChatError("A reaction is an emoji.")
    return said


def react(
    team_key: str, entry_id: int, emoji: str, by: str = USER,
    on: Optional[bool] = None,
) -> dict:
    """Put EMOJI on the message numbered ENTRY_ID in TEAM_KEY's chat, as
    BY, the user or a spark's id, or take it off: as ON says, or, without
    it, off if BY has it on already. The message, as it is now."""

    team = _team(team_key)
    emoji = _emoji(emoji)

    def change(data: dict) -> dict:
        entry = next(
            (e for e in data["entries"] if e.get("id") == entry_id), None,
        )
        if entry is None:
            raise TeamChatError("That message is no longer in the chat.")
        reactions = _reactions(entry)
        whose = reactions.get(emoji, [])
        adding = by not in whose if on is None else on
        if adding and by not in whose:
            if emoji not in reactions and len(reactions) >= MAX_REACTIONS:
                raise TeamChatError(
                    f"That message has {MAX_REACTIONS} reactions already."
                )
            reactions[emoji] = [*whose, by]
        elif not adding and by in whose:
            whose = [w for w in whose if w != by]
            if whose:
                reactions[emoji] = whose
            else:
                reactions.pop(emoji)
        if reactions:
            entry["reactions"] = reactions
        else:
            entry.pop("reactions", None)
        # The chat's count moves on, so a page that has the message
        # sees it changed; no message is ever given the number it took.
        data["seq"] += 1
        entry["changed"] = data["seq"]
        return dict(entry)

    return _change(team.id, change)


def _reacting(team_id: str, spark: sparks.Spark, reacted: list[str]):
    """react, for SPARK: its reaction put on, never taken off."""

    def put(args: dict) -> str:
        if len(reacted) >= MAX_REACTED:
            return f"Error: that is {MAX_REACTED} reactions this turn already."
        emoji = str(args.get("emoji", ""))
        try:
            entry_id = int(str(args.get("message", "")).strip().lstrip("#"))
            entry = react(team_id, entry_id, emoji, spark.id, on=True)
        except ValueError as exc:  # TeamChatError is one too
            if isinstance(exc, TeamChatError):
                return f"Error: {exc}"
            return "Error: give the message's number, as in 12."
        reacted.append(emoji)
        sparks.tool_line(f"React(#{entry['id']} {emoji.strip()})")
        return "(reacted)"

    return put


def _reply(
    team: sparks.Team, spark: sparks.Spark, client=None,
) -> list[dict]:
    """SPARK's messages in TEAM's chat, made now and posted: those it
    sent as it worked, then its answer. [] if it chose to stay quiet."""

    from . import agent as subagents
    from . import tools as flash_tools  # deferred: avoids a module cycle

    steps: list[str] = []
    kit = sparks.ChatKit(spark)
    sent: list[dict] = []
    reacted: list[str] = []
    tools = {
        **kit.tools, "send_message": _sending(team.id, spark, sent),
        "react": _reacting(team.id, spark, reacted),
    }
    schemas = [*kit.schemas, SEND_MESSAGE_TOOL, REACT_TOOL]
    others = [s for s in _members(team.id) if s.id != spark.id]
    roster = "\n".join(
        f"- {s.name} ({s.handle})"
        + (f", {s.title}" if s.title else "")
        + (", the lead" if not sparks.lead_of(s) else "")
        for s in others
    ) or "(no one else yet)"
    _set_typing(team.id, spark.id, "Thinking")
    entry: Optional[dict] = None
    try:
        model = sparks.model_of(spark)
        if not model:
            raise RuntimeError("no model is set, so it could not answer")
        if sparks.over_budget(spark):
            raise RuntimeError(sparks.out_of_budget(spark))
        host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
        system = sparks.chat_prompt(
            spark, host, model, flash_tools.CURRENT_DATE_PROMPT,
        ) + "\n\n" + TEAM_CHAT_PROMPT.format(
            team=team.name, name=spark.name, handle=spark.handle,
            roster=roster, quiet=QUIET,
        )
        entries = _read(team.id)["entries"][-CONTEXT_ENTRIES:]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _view(team, spark, entries)},
        ]
        text = sparks._work(
            spark, messages, tools, schemas, steps,
            lambda doing: _set_typing(team.id, spark.id, doing),
            sparks.MAX_CHAT_ROUNDS, sparks.CHAT_LAST_WORD, client,
            audit_as="team chat",
        )
        said = "" if not text or _quiet(text) else text
        # Its answer only repeating the message it just sent is not
        # another message.
        if sent and said.strip() == sent[-1].get("text", "").strip():
            said = ""
        if said or kit.documents:
            entry = {"text": said or "I wrote this up.", "steps": steps}
            if kit.documents:
                entry["files"] = list(kit.documents)
    except Exception as exc:  # noqa: BLE001
        entry = {
            "steps": steps, "failed": True,
            "text": f"I could not answer: {exc.__class__.__name__}: {exc}",
        }
    kit.apply()

    if entry is not None:
        return [*sent, _post(team.id, spark, entry, done=True)]
    _set_typing(team.id, spark.id, "")
    if not sent and not reacted:
        sparks.log(spark.id, "team_chat_quiet", team=team.name)
    return sent


def _replied_to(team_id: str, message: dict) -> Optional[sparks.Spark]:
    """The spark whose message, or news, MESSAGE replies to, if it is
    still on the team: a reply calls on it as an @mention would."""

    spark_id = (message.get("reply") or {}).get("spark", "")
    if not spark_id:
        return None
    return next((s for s in _members(team_id) if s.id == spark_id), None)


def _round(team: sparks.Team, message: dict, client=None) -> list[dict]:
    answered: set[str] = set()
    queue = _named(team.id, message.get("text", ""), answered, everyone=True)
    # @everyone: each of them gets its turn, past the usual few replies.
    limit = REPLY_LIMIT
    if sparks.mentions_everyone(message.get("text", "")):
        limit = max(REPLY_LIMIT, len(queue))
    replied = _replied_to(team.id, message)
    if replied is not None and all(q.id != replied.id for q in queue):
        # The one replied to answers first, then any it named besides.
        queue.insert(0, replied)
    if not queue:
        queue = _leads(team.id)[:1]
    posted: list[dict] = []
    turns = 0
    while queue and turns < limit:
        spark = sparks.find(queue.pop(0).id)
        if spark is None or spark.id in answered:
            continue
        answered.add(spark.id)
        turns += 1
        # One turn, and any messages in it: one quiet brings no one in.
        said = _reply(team, spark, client)
        posted += said
        for entry in said:
            if not entry.get("failed"):
                queue += [
                    s for s in _named(team.id, entry["text"], answered)
                    if all(q.id != s.id for q in queue)
                ]
    return posted


def answer(team_key: str, message: dict, client=None) -> list[dict]:
    """The sparks' answers to MESSAGE, the user's, made now on this
    thread: those it @mentions, else the team's lead, then whoever their
    answers bring in, REPLY_LIMIT at most. A message sent while another
    is being answered waits its turn, and is answered next."""

    team = _team(team_key)
    with _busy_lock:
        if team.id in _busy:
            _waiting[team.id] = message
            return []
        _busy.add(team.id)
    posted: list[dict] = []
    try:
        while True:
            posted += _round(team, message, client)
            with _busy_lock:
                message = _waiting.pop(team.id, None)
                if message is None:
                    _busy.discard(team.id)
                    return posted
    except BaseException:
        with _busy_lock:
            _busy.discard(team.id)
            _waiting.pop(team.id, None)
        raise


def send(
    team_key: str, text: str, client=None, reply_to: int = 0,
) -> dict:
    """Post what the user wrote, a reply to REPLY_TO if given, and have
    the sparks answer it in the background: the page's way. The user's
    message, as posted."""

    message = say(team_key, text, reply_to)
    threading.Thread(
        target=answer, args=(team_key, message, client), daemon=True,
    ).start()
    return message


def prompt_block(spark: sparks.Spark) -> str:
    """The latest of SPARK's team chat, for its prompt: "" with no team,
    or nothing said yet."""

    if not spark.team:
        return ""
    entries = _read(spark.team)["entries"][-PROMPT_ENTRIES:]
    if not entries:
        return ""
    by_id = {s.id: s for s in sparks.all_sparks()}
    lines = []
    for entry in entries:
        line = _line(entry, by_id)
        if len(line) > PROMPT_CHARS:
            line = line[:PROMPT_CHARS].rstrip() + " [...]"
        lines.append(f"- {line}")
    return (
        "=== The latest in your team's group chat (what the user says "
        "there is meant for the whole team) ===\n" + "\n".join(lines)
    )
