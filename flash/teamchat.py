"""A team's group chat: the user and the team's sparks, in one room.

Every team has one. The user writes in it; the sparks it @mentions
answer, or its leads when it names no one, and a spark can @mention a
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

import contextlib
import json
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from . import paths, pings, sparks
from .tidy import tidy

# How many entries a team's chat keeps, oldest dropped first.
MAX_ENTRIES = 300
# What is said on calls is kept apart from that, so a long call does not
# push the chat itself out: this many of its lines, oldest first.
MAX_CALL_ENTRIES = 1500
# A call nobody has been heard on, or beaten for, in this long is over:
# its page was closed, or its device lost.
CALL_STALE = 5 * 60
# How long a spark's call to the user rings before it is missed.
RING_SECONDS = 45
# How long a spark waits after calling the user before it may call again,
# unless it works in UltraAuto.
CALL_USER_GAP = 30 * 60
# How many replies from sparks one message of the user's can set off,
# counting those a spark's @mention brings in.
REPLY_LIMIT = 4
# How much of the chat a spark reads before it answers.
CONTEXT_ENTRIES = 30
ENTRY_CHARS = 1500
# How much of a message a spark is quoted when told of a reaction to it.
REACTED_CHARS = 160
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
# A voice call: one entry in the chat, its messages kept apart.
CALL = "call"
# Where a call is: ringing (a spark calling the user), calling (the user
# calling, not picked up yet), live, or over, as ended (it was talked
# on), unanswered (the user's, never picked up), cancelled (it never got
# going), missed or declined (a spark's, not picked up).
OPEN_STATES = ("ringing", "calling", "live")

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
- @user pings the user, with a sound, wherever they are. Use it only
  when you need them now: a sign-off, a question only they can answer,
  or what you called them about when they didn't pick up.
- Do not speak for the user or for another spark.
- Real work goes in a shift: call take_on, then say you are on it.
- While a shift of yours runs, the room can change it: call steer_shift
  with what to do differently, and it takes that from its next step, or
  stop_shift when it is no longer wanted or needed.
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
- To fix a message of yours (a mistake, or news that has changed since
  you posted it), call edit_message with its number and what it should
  say now, rather than posting it again. Everyone can still see what it
  said before. When the edit says it all, answer with only {quiet}.
""".strip()

# A private chat with one spark, a DM: a room like a team's, keyed
# DM_PREFIX and the spark's id (team ids are bare hex, so never collide),
# with only that spark in it, which answers every message.
DM_PREFIX = "dm-"

DM_PROMPT = """
=== You are in a private chat with the user ===
This is a one-on-one chat between the user and you, {name} ({handle}):
no other spark sees it.

You are shown the latest of the chat, then asked to post your message.
- Write as yourself, like a direct message: short, plain, friendly. A
  few sentences at most, unless you were asked for more.
- Answer what the user said, from what you know and have found.
- Real work goes in a shift: call take_on, then say you are on it.
- While a shift of yours runs, the user can change it here: call
  steer_shift with what to do differently, or stop_shift when it is no
  longer wanted or needed.
- You can call send_message to post a quick "on it, checking" before
  you look into something; your answer is posted after it. When it
  already said it all, answer with only {quiet}.
- Each message starts with its number, as #12. To react to one with an
  emoji, call react with its number; to fix a message of yours, call
  edit_message. When that says it all, answer with only {quiet}.
""".strip()


def dm_id(spark_id: str) -> str:
    """The key of the private chat with the spark SPARK_ID."""

    return DM_PREFIX + spark_id


def is_dm(team_id: str) -> bool:
    return str(team_id or "").startswith(DM_PREFIX)


def _dm_spark(team_id: str) -> Optional[sparks.Spark]:
    return sparks.find(team_id[len(DM_PREFIX):]) if is_dm(team_id) else None


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

# How many of its own messages a spark may edit in one turn, and how many
# versions of one message are kept.
MAX_EDITED = 3
MAX_VERSIONS = 20

EDIT_MESSAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_message",
        "description": (
            "Change a message you posted in the team chat, by its number: "
            "fix a mistake, or bring it up to date. What it said before "
            "stays there to see. Only your own messages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Its number, as in 12.",
                },
                "text": {
                    "type": "string",
                    "description": "What it says now, the whole of it.",
                },
            },
            "required": ["message", "text"],
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


def off_change(listener: Callable[[str], None]) -> None:
    """Stop calling LISTENER."""

    with contextlib.suppress(ValueError):
        _listeners.remove(listener)


def _changed(team_id: str) -> None:
    for listener in list(_listeners):
        try:
            listener(team_id)
        except Exception:  # noqa: BLE001, S110
            pass  # a page that is gone never stops the chat


def _folder() -> Path:
    return sparks.sparks_dir() / "teamchat"


def _path(team_id: str) -> Path:
    return _folder() / f"{team_id}.json"


# What another Flash writes in a chat (a terminal's, or the keeper's
# while this one serves the page) is only on disk: this one looks every
# WATCH_SECONDS, and hears of each chat whose file changed. Its own
# writes it knows of already, by the file each left.
WATCH_SECONDS = 1.0
_written: dict[str, tuple[int, int]] = {}
_watcher: Optional[threading.Thread] = None


def _stamp(path: Path) -> tuple[int, int]:
    found = path.stat()
    return found.st_mtime_ns, found.st_size


def _stamps() -> dict[str, tuple[int, int]]:
    out = {}
    with contextlib.suppress(OSError):
        for path in _folder().glob("*.json"):
            with contextlib.suppress(OSError):
                out[path.stem] = _stamp(path)
    return out


def _look(seen: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """The chats on disk now; each changed since SEEN by another Flash
    is passed on to the listeners, as if it changed here."""

    now = _stamps()
    for team_id, stamp in now.items():
        if seen.get(team_id) != stamp and _written.get(team_id) != stamp:
            _changed(team_id)
    return now


def _watch() -> None:
    seen = _stamps()
    while True:
        time.sleep(WATCH_SECONDS)
        seen = _look(seen)


def watch() -> None:
    """Hear of what other Flashes write in the team chats, from now on:
    once per process, for one that shows them live."""

    global _watcher

    with _busy_lock:
        if _watcher is not None and _watcher.is_alive():
            return
        _watcher = threading.Thread(
            target=_watch, daemon=True, name="teamchat-watch",
        )
        _watcher.start()


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
    paths.replace(temp, path)
    with contextlib.suppress(OSError):
        _written[team_id] = _stamp(path)


def _change(team_id: str, change: Callable[[dict], object]):
    """Read TEAM_ID's chat, CHANGE it, write it back, under the sparks'
    lock; what CHANGE returned."""

    with sparks._held():
        data = _read(team_id)
        out = change(data)
        _write(team_id, data)
    _changed(team_id)
    return out


def _on_call(entry: dict) -> bool:
    """Whether ENTRY was said on a call (not the call's own entry)."""

    return bool(entry.get("call")) and entry.get("kind") != CALL


def _add(data: dict, entry: dict) -> dict:
    data["seq"] += 1
    entry = {"id": data["seq"], "at": time.time(), **entry}
    data["entries"].append(entry)
    # The chat and its calls' lines each keep their own latest.
    said = [e for e in data["entries"] if _on_call(e)]
    rest = [e for e in data["entries"] if not _on_call(e)]
    drop = {id(e) for e in said[:-MAX_CALL_ENTRIES]} | \
        {id(e) for e in rest[:-MAX_ENTRIES]}
    if drop:
        data["entries"] = [e for e in data["entries"] if id(e) not in drop]
    return entry


def _live(entries: list) -> list:
    """ENTRIES without the messages a retry replaced: gone from the chat
    for the page, the terminal and every spark's prompt alike."""

    return [e for e in entries if not e.get("retried")]


def _shown(entries: list) -> list:
    """ENTRIES as the chat shows them: live, without hidden prompts."""

    return [e for e in _live(entries) if not e.get("hidden")]


def _state(entry: dict) -> str:
    """Where the call ENTRY is, for entries from before calls had one."""

    state = entry.get("state")
    if state:
        return state
    return "ended" if entry.get("ended") else "live"


def _settle(data: dict, now: Optional[float] = None) -> bool:
    """End the calls in DATA that are over without anyone saying so: a
    spark's that rang out, and those nobody has been heard on for
    CALL_STALE. Whether any was."""

    now = time.time() if now is None else now
    changed = False
    for entry in data["entries"]:
        if entry.get("kind") != CALL or _state(entry) not in OPEN_STATES:
            continue
        state = _state(entry)
        if state == "ringing":
            if now - float(entry.get("at") or now) < RING_SECONDS:
                continue
            entry.update(state="missed",
                         ended=float(entry.get("at") or now) + RING_SECONDS)
        else:
            last = float(entry.get("beat") or entry.get("at") or now)
            if now - last < CALL_STALE:
                continue
            entry.update(state="ended" if entry.get("connected")
                         else "unanswered", ended=last)
        data["seq"] += 1
        entry["changed"] = data["seq"]
        changed = True
    return changed


def _settled(team_id: str) -> dict:
    """TEAM_ID's chat, with any call that is over marked so."""

    data = _read(team_id)
    if _settle(dict(data, entries=[dict(e) for e in data["entries"]])):
        _change(team_id, _settle)
        data = _read(team_id)
    return data


def _call_entry(data: dict, call_id: str) -> Optional[dict]:
    return next((e for e in data["entries"] if e.get("kind") == CALL
                 and e.get("call") == call_id), None)


def live_calls() -> list[dict]:
    """Every call going on now, in every chat, ringing calls aside: what
    another Flash can take over. Each with its chat's id as "team"."""

    found = []
    with contextlib.suppress(OSError):
        for path in _folder().glob("*.json"):
            with contextlib.suppress(Exception):
                for entry in _settled(path.stem)["entries"]:
                    if entry.get("kind") == CALL and \
                            _state(entry) in ("calling", "live"):
                        found.append({"team": path.stem, **entry})
    return found


def open_call(team_key: str) -> Optional[dict]:
    """The call going on in TEAM_KEY's chat now, ringing or live."""

    team = _team(team_key)
    data = _settled(team.id)
    return next((e for e in reversed(data["entries"])
                 if e.get("kind") == CALL and _state(e) in OPEN_STATES),
                None)


def start_call(team_key: str, holder: str = "") -> dict:
    """A voice call begun now by the user, with the spark of a private
    chat or with a team: its one entry in the chat, whose "call" id the
    call's messages carry. A call still open there is over first: only
    one goes on at a time. HOLDER: the Flash it is on (a page, a
    terminal), so another can take it over."""

    team = _team(team_key)

    def change(data: dict) -> dict:
        now = time.time()
        _settle(data, now)
        for entry in data["entries"]:
            if entry.get("kind") == CALL and _state(entry) in OPEN_STATES:
                last = float(entry.get("beat") or entry.get("at") or now)
                entry.update(
                    ended=last, state="ended" if entry.get("connected")
                    else "missed" if _state(entry) == "ringing"
                    else "unanswered")
                data["seq"] += 1
                entry["changed"] = data["seq"]
        return _add(data, {
            "kind": CALL, "call": uuid.uuid4().hex[:12], "ended": 0,
            "state": "calling", "from": USER, "beat": now,
            **({} if is_dm(team.id) else {"group": True}),
            **({"holder": holder[:80]} if holder else {}),
        })

    return _change(team.id, change)


# What each step of a call does to it, from the state it must be in.
_STEPS = {
    "beat": OPEN_STATES,
    "connected": ("calling", "live"),
    "answer": ("ringing",),
    "decline": ("ringing",),
    "end": OPEN_STATES,
    "cancel": OPEN_STATES,
    # Another Flash takes the call over: it goes on there, not here.
    "take": ("calling", "live"),
}


def call_step(team_key: str, call_id: str, step: str,
              holder: str = "") -> Optional[dict]:
    """Move the call CALL_ID in TEAM_KEY's chat on: beat (still there),
    connected (first answer heard), answer or decline (a spark's call to
    the user), end (hung up), cancel (it never got going) or take
    (HOLDER, another Flash, takes it over). Its entry, or None if there
    is no such call. A step it is past is ignored: ending a call twice
    keeps the first end.

    HOLDER says which Flash the step comes from. One the call has moved
    away from changes nothing: its hang-up, as it lets go, does not end
    the call where it went."""

    if step not in _STEPS:
        raise TeamChatError(f"Unknown call step {step!r}.")
    team = _team(team_key)
    talked: list[bool] = []

    def change(data: dict) -> Optional[dict]:
        now = time.time()
        _settle(data, now)
        entry = _call_entry(data, call_id)
        if entry is None:
            return None
        state = _state(entry)
        holder_now = str(entry.get("holder") or "")
        if holder and holder_now and holder != holder_now and \
                step in ("beat", "connected", "end", "cancel"):
            return dict(entry)
        if state not in _STEPS[step]:
            if step == "answer":
                raise TeamChatError("That call is over: it rang out, or "
                                    "was declined.")
            return entry
        entry["beat"] = now
        if holder and step in ("answer", "connected", "take") and (
                step == "take" or not holder_now):
            entry["holder"] = holder[:80]
        if step == "take":
            entry["taken"] = now
        if step == "connected" and not entry.get("connected"):
            entry.update(connected=now, state="live")
        elif step == "answer":
            entry.update(connected=now, state="live")
        elif step == "decline":
            entry.update(state="declined", ended=now)
        elif step in ("end", "cancel"):
            entry["ended"] = now
            entry["state"] = (
                "cancelled" if step == "cancel" and not entry.get("connected")
                else "ended" if entry.get("connected")
                else "missed" if state == "ringing" else "unanswered")
            said = [e for e in data["entries"] if e.get("call") == call_id
                    and e.get("kind") == USER and not e.get("hidden")]
            talked.append(entry["state"] == "ended" and len(said) >= 2)
        if step != "beat":
            data["seq"] += 1
            entry["changed"] = data["seq"]
        return dict(entry)

    entry = _change(team.id, change)
    if talked and talked[0]:
        # Once a real conversation is over, it is summed up in the chat.
        threading.Thread(target=_recap, args=(team.id, call_id),
                         daemon=True).start()
    return entry


def end_call(team_key: str, call_id: str) -> Optional[dict]:
    """Mark the call CALL_ID in TEAM_KEY's chat over, now: its entry, or
    None if there is no such call. Ending it twice keeps the first end."""

    return call_step(team_key, call_id, "end")


RECAP_PROMPT = (
    "(The voice call just ended. Post one short message in the chat "
    "summing up what you talked about: what was decided, and anything "
    "you said you would do. A few lines, written, not spoken. If nothing "
    "worth keeping was said, answer with only {quiet}.)"
)


def _recap(team_id: str, call_id: str) -> None:
    """After a call: the spark (a team's leads) sums it up in the chat."""

    with contextlib.suppress(Exception):
        message = say(team_id, RECAP_PROMPT.format(quiet=QUIET), hidden=True)
        answer(team_id, {**message, "recap": call_id})


def call_log(team_key: str, call_id: str) -> list[dict]:
    """What was said on the call CALL_ID, in order: who, and what."""

    team = _team(team_key)
    by_id = {s.id: s for s in sparks.all_sparks()}
    out = []
    for entry in _live(_read(team.id)["entries"]):
        if entry.get("call") != call_id or entry.get("kind") == CALL \
                or entry.get("hidden"):
            continue
        spark = by_id.get(entry.get("spark", ""))
        out.append({
            "at": entry.get("at", 0), "id": entry.get("id", 0),
            "who": USER if entry.get("kind") == USER else "spark",
            "spark": entry.get("spark", ""),
            "name": "You" if entry.get("kind") == USER
            else getattr(spark, "name", "") or entry.get("name") or "A spark",
            "text": str(entry.get("text") or ""),
            "failed": bool(entry.get("failed")),
        })
    return out


# Whether sparks may call the user: on unless switched off.
CALLS_SETTING = "SPARK_CALLS"
CALLS_OFF = ("The user turned off calls from sparks. If it can't wait, "
             "@user them in your report or in a message instead.")


def calls_allowed() -> bool:
    """Whether sparks may call the user. Read from the env file, so the
    keeper's sparks go by what was set in another Flash."""

    from .notify import _saved

    return _saved(CALLS_SETTING).strip().lower() not in (
        "0", "off", "false", "no")


def ring(spark: sparks.Spark, reason: str) -> dict:
    """SPARK calls the user, for REASON: a call that rings wherever Flash
    is open (or on the desktop when only the keeper is), RING_SECONDS
    long. Its entry in their private chat."""

    if not calls_allowed():
        raise TeamChatError(CALLS_OFF)
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        raise TeamChatError("Say why you are calling.")
    room = dm_id(spark.id)

    def change(data: dict) -> dict:
        now = time.time()
        _settle(data, now)
        busy = next((e for e in data["entries"] if e.get("kind") == CALL
                     and _state(e) in OPEN_STATES), None)
        if busy is not None:
            raise TeamChatError("You are on a call with the user already.")
        last = max((float(e.get("at") or 0) for e in data["entries"]
                    if e.get("kind") == CALL and e.get("from") == SPARK),
                   default=0)
        # In UltraAuto a spark is trusted to call as often as it needs.
        if sparks.mode_of(spark) != "ultra" and now - last < CALL_USER_GAP:
            wait = int((CALL_USER_GAP - (now - last)) // 60) + 1
            raise TeamChatError(
                f"You called the user {int((now - last) // 60)} minutes "
                f"ago. Wait {wait} more minutes, or message them instead.")
        return _add(data, {
            "kind": CALL, "call": uuid.uuid4().hex[:12], "ended": 0,
            "state": "ringing", "from": SPARK, "spark": spark.id,
            "name": spark.name, "reason": reason, "beat": now,
        })

    entry = _change(room, change)
    from . import sparkcalls  # deferred: it imports this module

    sparkcalls.rang(room, entry)
    return entry


def _team(key: str) -> sparks.Team:
    if is_dm(key):
        spark = _dm_spark(key)
        if spark is None:
            raise TeamChatError("That spark is gone.")
        # A room of its own, as a team of one.
        return sparks.Team(id=dm_id(spark.id), name=spark.name,
                           colour=spark.colour)
    team = sparks.find_team(key)
    if team is None:
        raise TeamChatError(f"There is no team called {key}.")
    return team


def history(team_key: str, since: int = 0) -> dict:
    """TEAM_KEY's chat for the page: the entries after SINCE, those
    before it reacted to since, who is typing, and how many came in since
    the user last looked."""

    team = _team(team_key)
    data = _settled(team.id)
    now = time.time()
    typing = [
        {"spark": sid, "doing": str(t.get("doing") or "")}
        for sid, t in data["typing"].items()
        if isinstance(t, dict) and now - float(t.get("at") or 0) < TYPING_STALE
    ]
    return {
        "team": team.id, "seq": data["seq"],
        "entries": _shown(
            [e for e in data["entries"] if e.get("id", 0) > since]
        ),
        # Those it has, but changed since: their reactions, as now.
        "changed": [
            e for e in data["entries"]
            if since and 0 < e.get("id", 0) <= since
            and e.get("changed", 0) > since
        ],
        "typing": typing,
        "unread": _unread(data),
        # Its sparks are still at the last message: a call waits on.
        "answering": team.id in _busy,
    }


def _unread(data: dict) -> int:
    """What the sparks said since the user looked: not the user's own
    messages, nor news of what the user did to the team."""

    return sum(
        1 for e in _live(data["entries"])
        if e.get("at", 0) > data["read"] and (
            (e.get("kind") not in (USER, CALL)
             and not (e.get("kind") == EVENT and not e.get("spark"))
             # Heard on a call, not waiting in the chat.
             and not e.get("call"))
            # A spark's call the user missed is waiting for them.
            or (e.get("kind") == CALL and e.get("from") == SPARK
                and _state(e) == "missed"))
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


def _seen(team_id: str, spark: sparks.Spark, entries: list) -> None:
    """SPARK has read ENTRIES: the user's messages among them are marked
    read by it, with when, for the page's ticks. Writes nothing when it
    had read them all already."""

    ids = {
        e.get("id") for e in entries
        if e.get("kind") == USER and spark.id not in (e.get("seen") or {})
    }
    if not ids:
        return

    def change(data: dict) -> None:
        now = time.time()
        for entry in data["entries"]:
            if entry.get("id") not in ids:
                continue
            seen = entry.get("seen")
            entry["seen"] = seen = seen if isinstance(seen, dict) else {}
            if spark.id in seen:
                continue
            seen[spark.id] = now
            # As a reaction does: a page that has it sees it changed.
            data["seq"] += 1
            entry["changed"] = data["seq"]

    _change(team_id, change)


def read_by(entry: dict) -> list[str]:
    """The sparks that have read ENTRY, the user's message, by ID, the
    first to read it first."""

    seen = entry.get("seen")
    if not isinstance(seen, dict):
        return []
    return [i for i, _ in sorted(seen.items(), key=lambda kv: kv[1])]


def event(team_id: str, spark: Optional[sparks.Spark], what: str,
          text: str = "", files: Optional[list] = None,
          whom: str = "") -> Optional[dict]:
    """Put news in TEAM_ID's chat: WHAT happened (report, failed,
    joined), by SPARK, with TEXT, the start of what it said, and WHOM it
    was for, the sparks that handed it the work, if any did."""

    if not team_id or sparks.find_team(team_id) is None:
        return None
    text = " ".join(str(text or "").split())
    if len(text) > EVENT_CHARS:
        text = text[:EVENT_CHARS].rstrip() + " [...]"
    by = {"spark": spark.id, "name": spark.name} if spark else {}
    if files:
        by["files"] = list(files)
    if whom:
        by["for"] = whom
    return _change(team_id, lambda data: _add(data, {
        "kind": EVENT, "what": what, "text": text, **by,
    }))


def _members(team_id: str) -> list[sparks.Spark]:
    if is_dm(team_id):
        spark = _dm_spark(team_id)
        return [spark] if spark else []
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


def say(
    team_key: str, text: str, reply_to: int = 0,
    files: Optional[list] = None, hidden: bool = False, call: str = "",
) -> dict:
    """Post what the user wrote in TEAM_KEY's chat, as a reply to the
    message numbered REPLY_TO if one is given, with the uploaded FILES
    it carries (as the page shows them). HIDDEN: the sparks answer it,
    but it is left out of the chat's history (a call's opening hello).
    CALL: the id of the call it was said on, from start_call().
    The sparks' answers are answer()'s to make."""

    team = _team(team_key)
    text = str(text or "").strip()[:sparks.MESSAGE_CHARS]
    files = [
        dict(f) for f in files or [] if isinstance(f, dict) and f.get("id")
    ]
    if not text and not files:
        raise TeamChatError("Say something first.")
    if not _members(team.id):
        raise TeamChatError(
            f"{team.name} has no sparks yet: put one on it to talk to."
        )

    def change(data: dict) -> dict:
        entry = {"kind": USER, "text": text}
        if hidden:
            entry["hidden"] = True
        if call:
            entry["call"] = call
            # Heard on the call: it is still going.
            on = _call_entry(data, call)
            if on is not None and _state(on) in OPEN_STATES:
                on["beat"] = time.time()
        if files:
            entry["attachments"] = files
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


def _attached_note(entry: dict) -> str:
    """The files the user attached to ENTRY, named by path for a spark's
    tools to open, as the main chat names them to the model; "" for
    none."""

    from . import workspace

    paths = []
    for f in entry.get("attachments") or []:
        path = workspace.upload_path(str(f.get("id", "")))
        if path is not None:
            paths.append(str(path))
    return f" [Attached file: {', '.join(paths)}]" if paths else ""


def _said_line(entry: dict, by_id: dict) -> str:
    text = " ".join(str(entry.get("text") or "").split())
    if len(text) > ENTRY_CHARS:
        text = text[:ENTRY_CHARS].rstrip() + " [...]"
    kind = entry.get("kind")
    if kind == CALL:
        return _call_line(entry)
    if entry.get("call"):
        text = "[on the call] " + text
    if kind == USER:
        text += _attached_note(entry)
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
        return f"{who}{' (edited)' if entry.get('versions') else ''}: {text}"
    what = {
        "report": "filed a report", "failed": "could not finish a shift",
        "joined": "joined the team", "left": "left the team",
    }.get(entry.get("what", ""), entry.get("what", ""))
    if entry.get("for"):
        what += f" for {entry['for']}"
    return f"({who} {what}{': ' + text if text else ''})"


def _call_line(entry: dict) -> str:
    """A call's entry, as a spark reads it in the chat."""

    state = _state(entry)
    with_ = "the user and the team" if entry.get("group") else "the user"
    if entry.get("from") == SPARK:
        reason = f" about: {entry['reason']}" if entry.get("reason") else ""
        if state == "ringing":
            return f"(You are calling the user{reason}; it is ringing)"
        if state == "missed":
            return (f"(You called the user{reason}; they did not pick "
                    "up. If it still needs them, you can @user them with "
                    "it)")
        if state == "declined":
            return f"(You called the user{reason}; they declined)"
    if state in ("calling", "live"):
        return f"(A voice call with {with_}, going on now)"
    if state == "unanswered":
        return "(The user called; the call was not picked up)"
    if state == "cancelled":
        return "(A call that never got going)"
    start = float(entry.get("connected") or entry.get("at") or 0)
    seconds = max(0, float(entry.get("ended") or start) - start)
    length = f"{round(seconds / 60)} min" if seconds >= 90 \
        else f"{int(seconds)} s"
    return f"(A voice call with {with_}, {length})"


def _view(team: sparks.Team, spark: sparks.Spark, entries: list) -> str:
    by_id = {s.id: s for s in sparks.all_sparks()}
    lines = [f"#{e.get('id', 0)} {_line(e, by_id)}" for e in entries]
    room = "Your private chat with the user" if is_dm(team.id) \
        else f"{team.name}'s chat"
    return (
        f"=== {room}: its latest {len(lines)} messages ===\n"
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

    posted = _change(team_id, change)
    # An @user in it pings the user.
    pings.ping(spark, str(entry.get("text") or ""), "team", team_id)
    return posted


def _sending(team_id: str, spark: sparks.Spark, sent: list[dict],
             call: str = ""):
    """send_message, for SPARK: posted now, and kept in SENT."""

    def send(args: dict) -> str:
        # Posted as it is sent, so its dashes go here, as an answer's do.
        text = tidy(
            str(args.get("text", ""))
        ).strip()[:sparks.MESSAGE_CHARS]
        if not text or _quiet(text):
            return "Error: the message was empty."
        if len(sent) >= MAX_SENT:
            return (
                f"Error: that is {MAX_SENT} messages this turn already. "
                "Put the rest in your answer."
            )
        sent.append(_post(
            team_id, spark, {"text": text, **({"call": call} if call else {})},
        ))
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
    moved: list[bool] = []

    def change(data: dict) -> dict:
        entry = next(
            (e for e in data["entries"] if e.get("id") == entry_id), None,
        )
        if entry is None:
            raise TeamChatError("That message is no longer in the chat.")
        reactions = _reactions(entry)
        whose = reactions.get(emoji, [])
        adding = by not in whose if on is None else on
        if adding != (by in whose):
            moved.append(adding)
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

    entry = _change(team.id, change)
    if moved:
        _tell_author(team, entry, emoji, by, moved[0])
    return entry


def edit(team_key: str, entry_id: int, text: str, by: str) -> dict:
    """Change what the message numbered ENTRY_ID says to TEXT, as BY, the
    spark that posted it. What it said before is kept, with when, for
    anyone to look back at. The message, as it is now."""

    team = _team(team_key)
    text = tidy(str(text or "")).strip()[:sparks.MESSAGE_CHARS]
    if not text or _quiet(text):
        raise TeamChatError("A message cannot be edited to nothing.")

    def change(data: dict) -> dict:
        entry = next(
            (e for e in data["entries"] if e.get("id") == entry_id), None,
        )
        if entry is None or entry.get("retried"):
            raise TeamChatError("That message is no longer in the chat.")
        if entry.get("kind") != SPARK or entry.get("spark") != by:
            raise TeamChatError("Only your own messages can be edited.")
        if entry.get("failed"):
            raise TeamChatError("A message that failed cannot be edited.")
        if text == entry.get("text"):
            raise TeamChatError("That is what it says already.")
        versions = list(entry.get("versions") or [])
        versions.append({
            "text": entry.get("text", ""),
            "at": entry.get("edited") or entry.get("at"),
        })
        entry["versions"] = versions[-MAX_VERSIONS:]
        entry["text"] = text
        entry["edited"] = time.time()
        # As a reaction does: a page that has it sees it changed.
        data["seq"] += 1
        entry["changed"] = data["seq"]
        return dict(entry)

    return _change(team.id, change)


def _editing(team_id: str, spark: sparks.Spark, edited: list[int]):
    """edit_message, for SPARK: its own messages only."""

    def put(args: dict) -> str:
        if len(edited) >= MAX_EDITED:
            return f"Error: that is {MAX_EDITED} edits this turn already."
        try:
            entry_id = int(str(args.get("message", "")).strip().lstrip("#"))
            entry = edit(team_id, entry_id, str(args.get("text", "")),
                         spark.id)
        except ValueError as exc:  # TeamChatError is one too
            if isinstance(exc, TeamChatError):
                return f"Error: {exc}"
            return "Error: give the message's number, as in 12."
        edited.append(entry["id"])
        sparks.tool_line(f"EditMessage(#{entry['id']})")
        return "(edited: it shows what it says now, and what it said before)"

    return put


def _tell_author(
    team: sparks.Team, entry: dict, emoji: str, by: str, on: bool,
) -> None:
    """Tell the spark that said ENTRY, a message or a report, that BY
    put EMOJI on it, or took it off (ON false); not when it was its own."""

    author = entry.get("spark")
    if not author or author == by or entry.get("kind") == USER:
        return
    if entry.get("kind") == EVENT and entry.get("what") not in (
        "report", "failed",
    ):
        return  # news of joining or leaving is not something it said
    if by == USER:
        who = "The user"
    else:
        found = sparks.find(by)
        who = found.name if found else "A spark"
    text = " ".join(str(entry.get("text") or "").split())
    if len(text) > REACTED_CHARS:
        text = text[:REACTED_CHARS].rstrip() + "…"
    what = "report" if entry.get("kind") == EVENT else "message"
    sparks.reacted(
        author, emoji, who, f'your {what} #{entry.get("id", 0)}, "{text}"',
        f"{team.name}'s chat", f"{team.id}:{entry.get('id', 0)}:{emoji}:{by}",
        on,
    )


END_CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "end_call",
        "description": (
            "Hang up the voice call you are on with the user, once the "
            "conversation is over: they said bye, or that was all. Say "
            "your goodbye in your answer as well."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

# Told to a spark answering the user on a one-to-one voice call.
CALL_PROMPT = """
You are on a voice call with the user: what you answer is read out to
them. Keep it short and spoken, with no lists or formatting. When the
conversation is over (they say bye, or that is all), say goodbye and
call end_call to hang up. A picture with what they say is what their
camera or screen shows now."""

GROUP_CALL_PROMPT = """
You are on a group voice call with the user and your team: what you
answer is read out to them in your own voice, after the others'. Keep it
short and spoken, with no lists or formatting, and only say something
when you have something to add. When the user says bye, or that is all,
say goodbye and call end_call to hang up for everyone."""

# Said on a call in place of an answer that failed: the error itself is
# kept in the chat, for the Call Log.
CALL_FAILED = ("Sorry, something went wrong on my side, so I couldn't "
               "answer that.")
# The most pictures a spark is shown with one message.
MAX_PICTURES = 2


def _ending(ended: list[bool]):
    """end_call: the call hangs up once this turn's answer is read."""

    def end(args: dict) -> str:
        ended.append(True)
        sparks.tool_line("EndCall()")
        return "(the call hangs up after your answer)"

    return end


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


def _ultra_tools() -> tuple[str, ...]:
    """In UltraAuto, every tool a shift has that can work now: commands,
    edits, email and iMessages too."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    working = {t["function"]["name"] for t in flash_tools.available_tools()}
    return tuple(n for n in flash_tools.SPARK_TOOL_NAMES if n in working)


def _reply(
    team: sparks.Team, spark: sparks.Spark, client=None, call=False,
) -> list[dict]:
    """SPARK's messages in TEAM's chat, made now and posted: those it
    sent as it worked, then its answer. [] if it chose to stay quiet.
    CALL: the user is on a voice call with it, which it can end; the
    call's id, its messages are kept as the call's."""

    from . import agent as subagents
    from . import tools as flash_tools  # deferred: avoids a module cycle

    call_id = call if isinstance(call, str) else ""
    steps: list[str] = []
    kit = sparks.ChatKit(spark)
    sent: list[dict] = []
    reacted: list[str] = []
    edited: list[int] = []
    tools = {
        **kit.tools, "send_message": _sending(team.id, spark, sent, call_id),
        "react": _reacting(team.id, spark, reacted),
        "edit_message": _editing(team.id, spark, edited),
    }
    schemas = [*kit.schemas, SEND_MESSAGE_TOOL, REACT_TOOL,
               EDIT_MESSAGE_TOOL]
    ended: list[bool] = []
    call = bool(call)
    if call:
        tools["end_call"] = _ending(ended)
        schemas.append(END_CALL_TOOL)
    others = [s for s in _members(team.id) if s.id != spark.id]
    # A team can have several leads, equals at the top of it.
    leads = _leads(team.id)
    lead_word = ", a lead" if len(leads) > 1 else ", the lead"
    roster = "\n".join(
        f"- {s.name} ({s.handle})"
        + (f", {s.title}" if s.title else "")
        + (lead_word if any(s.id == lead.id for lead in leads) else "")
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
        room = DM_PROMPT if is_dm(team.id) else TEAM_CHAT_PROMPT
        system = sparks.chat_prompt(
            spark, host, model, flash_tools.CURRENT_DATE_PROMPT,
        ) + "\n\n" + room.format(
            team=team.name, name=spark.name, handle=spark.handle,
            roster=roster, quiet=QUIET,
        ) + ((CALL_PROMPT if is_dm(team.id) else GROUP_CALL_PROMPT)
             if call else "")
        entries = _live(_settled(team.id)["entries"])[-CONTEXT_ENTRIES:]
        _seen(team.id, spark, entries)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _view(team, spark, entries)},
        ]
        # What the user's camera or screen showed with their latest
        # message, for a model that can see it.
        pictures = _pictures(entries, host, model)
        if pictures:
            messages[-1]["images"] = pictures
        # In UltraAuto an answer here gets every tool a shift has, and
        # what would ask is told yes: nobody is here to answer it.
        ultra = sparks.mode_of(spark) == "ultra"
        with sparks.answer_from(lambda question: "y") if ultra \
                else contextlib.nullcontext():
            text = sparks._work(
                spark, messages, tools, schemas, steps,
                lambda doing: _set_typing(team.id, spark.id, doing),
                sparks.MAX_CHAT_ROUNDS, sparks.CHAT_LAST_WORD, client,
                names=_ultra_tools() if ultra else None,
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
            **({"spoken": CALL_FAILED} if call else {}),
        }
    kit.apply()

    if ended:
        # The page hangs up once it has read this one out.
        entry = {**(entry or {"text": "Bye!", "steps": steps}),
                 "end_call": True}
    if entry is not None:
        if call_id:
            entry = {**entry, "call": call_id}
        return [*sent, _post(team.id, spark, entry, done=True)]
    _set_typing(team.id, spark.id, "")
    if not sent and not reacted and not edited:
        sparks.log(spark.id, "team_chat_quiet", team=team.name)
    return sent


IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _pictures(entries: list, host: str, model: str) -> list[str]:
    """The pictures attached to the user's latest message among ENTRIES,
    base64, when MODEL can see: what a call's camera or screen showed."""

    import base64

    from . import workspace
    from .sysprompt import model_sees_images

    latest = next((e for e in reversed(entries) if e.get("kind") == USER
                   and not e.get("hidden")), None)
    if latest is None:
        return []
    paths = []
    for f in latest.get("attachments") or []:
        path = workspace.upload_path(str(f.get("id", "")))
        if path is not None and path.suffix.lower() in IMAGE_TYPES:
            paths.append(path)
    if not paths or not model_sees_images(host, model):
        return []
    out = []
    for path in paths[:MAX_PICTURES]:
        with contextlib.suppress(OSError):
            out.append(base64.b64encode(path.read_bytes()).decode("ascii"))
    return out


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
    call = message.get("call") or False
    replied = _replied_to(team.id, message)
    if replied is not None and all(q.id != replied.id for q in queue):
        # The one replied to answers first, then any it named besides.
        queue.insert(0, replied)
    if not queue:
        # Named no one: the team's leads answer, each of them, as equals.
        queue = _leads(team.id)
        limit = max(limit, len(queue))
    posted: list[dict] = []
    turns = 0
    while queue and turns < limit:
        spark = sparks.find(queue.pop(0).id)
        if spark is None or spark.id in answered:
            continue
        answered.add(spark.id)
        turns += 1
        # One turn, and any messages in it: one quiet brings no one in.
        said = _reply(team, spark, client, call=call)
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
    thread: those it @mentions, else the team's leads, then whoever their
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


def retry(team_key: str, entry_id: int, client=None) -> dict:
    """Have the spark whose message ENTRY_ID is answer again, as a retry
    of a reply that went wrong: the old one leaves the chat, and the
    spark writes another from the chat as it is without it. Answered on
    a thread of its own, the page's way; the old message, as marked."""

    team = _team(team_key)

    def mark(data: dict) -> dict:
        entry = next(
            (e for e in data["entries"] if e.get("id") == entry_id), None,
        )
        if entry is None or entry.get("retried"):
            raise TeamChatError("That message is no longer in the chat.")
        if entry.get("kind") != SPARK:
            raise TeamChatError("Only a spark's message can be retried.")
        spark = next((s for s in _members(team.id)
                      if s.id == entry.get("spark")), None)
        if spark is None:
            raise TeamChatError(
                f"{entry.get('name') or 'That spark'} is not on the team "
                "now, so it cannot answer again."
            )
        entry["retried"] = True
        # As a reaction does: a page that has it sees it changed.
        data["seq"] += 1
        entry["changed"] = data["seq"]
        return dict(entry)

    with _busy_lock:
        if team.id in _busy:
            raise TeamChatError(
                "The team is still answering. Try again once it is done."
            )
        _busy.add(team.id)
    try:
        old = _change(team.id, mark)
    except BaseException:
        with _busy_lock:
            _busy.discard(team.id)
        raise

    def again() -> None:
        try:
            spark = sparks.find(old["spark"])
            if spark is not None:
                _reply(team, spark, client)
        finally:
            with _busy_lock:
                _busy.discard(team.id)
                waiting = _waiting.pop(team.id, None)
            # A message sent while it answered again is answered now.
            if waiting is not None:
                answer(team.id, waiting, client)

    threading.Thread(target=again, daemon=True).start()
    return old


def send(
    team_key: str, text: str, client=None, reply_to: int = 0,
    files: Optional[list] = None, call=False, hidden: bool = False,
) -> dict:
    """Post what the user wrote, a reply to REPLY_TO if given, with
    FILES attached, and have the sparks answer it in the background: the
    page's way. CALL: said on a voice call, which the spark can end: the
    call's id from start_call(), its messages kept as the call's.
    The user's message, as posted."""

    message = say(
        team_key, text, reply_to, files, hidden=hidden,
        call=call if isinstance(call, str) else "",
    )
    heard = {**message, "call": call or True} if call else message
    threading.Thread(
        target=answer, args=(team_key, heard, client), daemon=True,
    ).start()
    return message


def prompt_block(spark: sparks.Spark) -> str:
    """The latest of SPARK's team chat, for its prompt: "" with no team,
    or nothing said yet."""

    blocks = [b for b in (_team_block(spark), _dm_block(spark)) if b]
    return "\n\n".join(blocks)


def _dm_block(spark: sparks.Spark) -> str:
    """The latest of SPARK's private chat with the user: "" if none."""

    room = dm_id(spark.id)
    entries = _shown(_settled(room)["entries"])[-PROMPT_ENTRIES:]
    if not entries:
        return ""
    with contextlib.suppress(OSError):
        _seen(room, spark, entries)
    by_id = {s.id: s for s in sparks.all_sparks()}
    lines = []
    for entry in entries:
        line = _line(entry, by_id)
        if len(line) > PROMPT_CHARS:
            line = line[:PROMPT_CHARS].rstrip() + " [...]"
        lines.append(f"#{entry.get('id', 0)} {line}")
    return (
        "=== The latest in your private chat with the user ===\n"
        + "\n".join(lines)
    )


def _team_block(spark: sparks.Spark) -> str:
    if not spark.team:
        return ""
    entries = _shown(_read(spark.team)["entries"])[-PROMPT_ENTRIES:]
    if not entries:
        return ""
    # What is in its prompt it reads, on a shift as in a chat.
    with contextlib.suppress(OSError):
        _seen(spark.team, spark, entries)
    by_id = {s.id: s for s in sparks.all_sparks()}
    lines = []
    for entry in entries:
        line = _line(entry, by_id)
        if len(line) > PROMPT_CHARS:
            line = line[:PROMPT_CHARS].rstrip() + " [...]"
        lines.append(f"#{entry.get('id', 0)} {line}")
    return (
        "=== The latest in your team's group chat (what the user says "
        "there is meant for the whole team; to point at a message in a "
        "report, write its number, like #12, and the user can open it) "
        "===\n" + "\n".join(lines)
    )
