"""Sparks: small agents that keep working after the chat is over.

A sub-agent does one job and is gone. A spark is given a standing goal
instead, a name, and the lines it must not cross, and then works at it
on a schedule for as long as Flash is running, the terminal or the web
UI alike: "watch the issues on my repo and tell me about new bugs every
morning", "check the price of this every hour". Each run is a shift. A
shift gets the goal, what the spark wrote down for itself last time,
and everything the person has told it, then works with the same tools a
sub-agent has and files a report. A shift with nothing to say files a
quiet one, so a spark that watches for something rare is not a spark
that nags.

Sparks learn by being told. Feedback on a report is kept as a lesson,
and every later shift reads every lesson, so "only tell me about the
ones labelled bug" is said once.

Each spark is one JSON file in ~/.flash/sparks, written whole on every
change. One keeper runs the shifts one at a time: the model is usually
local, and two shifts at once would only make both slow. It runs inside
whichever Flash is open, or, with sparks always on, in a Flash of its
own that the operating system starts at login.
"""

import base64
import contextlib
import hashlib
import itertools
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

import ollama

from . import agent as subagents
from . import cron
from .dashes import undash
from .paths import ENV_PATH, FLASH_DIR
from .sysprompt import get_model_system_prompt
from .theme import (
    ERROR,
    answer_from,
    capture_tool_output,
    tool_line,
    tool_result,
)

# The bubbles a spark is drawn as, one picked for each new spark. Bright
# enough to read on both a dark and a light page.
COLOURS = (
    "#f2a65a",  # ember
    "#5ab0f2",  # sky
    "#7bd88f",  # mint
    "#c792ea",  # lilac
    "#ff7a90",  # coral
    "#ffd166",  # sun
    "#4fd1c5",  # lagoon
    "#a3b1ff",  # periwinkle
)

NAME_CHARS = 24
TITLE_CHARS = 40
GOAL_CHARS = 2000
BOUNDARY_CHARS = 1000
LESSON_CHARS = 400
NOTES_CHARS = 4000

MAX_LESSONS = 30
MAX_REPORTS = 40
# How many of its rated reports a shift is shown, and how much of each.
MAX_RATED = 6
RATED_CHARS = 280
MAX_STEPS = 40
# How many rounds of tools a shift gets before it is told to write its
# report from what it has: the user's to set (SPARK_SHIFT_ROUNDS), within
# these bounds. The user may also take the limit off
# (SPARK_SHIFT_UNLIMITED), and a shift then works until the model writes
# its report or the user stops it; the number is kept for when the limit
# goes back on.
SHIFT_ROUNDS_DEFAULT = 12
SHIFT_ROUNDS_MIN = 1
SHIFT_ROUNDS_MAX = 100

# Chat: how much of it is kept, how much of it the spark rereads, and
# how long it may work on one answer.
MESSAGE_CHARS = 4000
MAX_CHAT = 80
CHAT_CONTEXT = 20
MAX_CHAT_ROUNDS = 8
# An answer still "on its way" after this long belongs to a Flash that
# quit while making it.
REPLY_STALE_SECONDS = 600

# How often a spark may run. Every shift is a full agent run against the
# model, so the floor keeps a spark from hogging it.
MIN_EVERY_MINUTES = 15
MAX_EVERY_MINUTES = 7 * 24 * 60
DEFAULT_EVERY_MINUTES = 60

# How often the keeper looks for a spark that is due, and for one that
# another Flash changed.
TICK_SECONDS = 5.0

IDLE = "idle"
WORKING = "working"
FAILED = "failed"
# Stopped part way, until the user says yes or no to a step that asks.
WAITING = "waiting"

MAX_INBOX = 20
# How often, in ticks, a spark's watched folder is looked at, the most
# files looked at in one, and how long after a shift a change waits.
WATCH_EVERY_TICKS = 3
WATCH_FILES = 5000
WATCH_SETTLE_SECONDS = 60
# Folders a watch never looks inside: nobody's edits, only tools'.
WATCH_SKIP = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next",
}

# A shift that has nothing to say answers with this, and its report is
# kept without being called news.
NOTHING_NEW = "NOTHING NEW"

SPARK_PROMPT = """
=== You are a spark ===
You are {name} ({handle}){titled}, a spark: an agent that works on one standing
goal for this user, on a schedule, while they get on with other things.
This is one of your shifts. Nobody is watching it and nobody can answer
a question, so work on your own, then write your report.

Your goal:
{goal}

Stay inside these boundaries, whatever the goal seems to need:
{boundaries}

What the user has told you about how to do this (always follow it):
{lessons}

How the user rated your recent reports. Do more of what they liked and
less of what they did not: what you looked at, how much you said, how
you said it.
{rated}

Your notes from last time, written by you for you:
{notes}

Your last report:
{last}

This shift runs {every}; the last one was {since}.

How to work:
- Do what the goal asks now, with your tools. Do not only plan.
- A step that runs a command or changes a file waits for the user to
  say yes before it runs. Ask for it only when the goal needs it: the
  shift pauses there, and carries on with their answer.
- Call keep_notes with anything the next shift has to know: what you
  have already seen or reported, what you are waiting on. Your notes are
  replaced, not added to, so keep what still matters.
- End with your report to the user: short, what you found or did, and
  anything that needs them. Do not repeat news from your last report.
- If nothing is worth telling them, reply with exactly {nothing_new}
  and nothing else.
""".strip()

KEEP_NOTES_TOOL = {
    "type": "function",
    "function": {
        "name": "keep_notes",
        "description": (
            "Replace your notes, which your next shift reads first. Put "
            "in what it must know to carry on: what you have seen or "
            "already reported, what you are waiting for."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Your whole notes, in plain text.",
                },
            },
            "required": ["notes"],
        },
    },
}

ROUND_LIMIT_MESSAGE = (
    "You have used every tool round this shift gets. Write your report "
    "now from what you have. Do not call any more tools."
)


CHAT_PROMPT = """
=== You are a spark, talking with the user ===
You are {name} ({handle}){titled}, a spark: an agent that works on one standing
goal for this user, on a schedule ({every}). Right now the user is
talking to you directly, between your shifts.

Your goal:
{goal}

Stay inside these boundaries, whatever you are asked:
{boundaries}

What the user has told you about how to do this (always follow it):
{lessons}

Your notes, written by you for your next shift:
{notes}

Your recent reports, newest last:
{reports}

How to talk:
- Answer as yourself, in the first person: short, plain, friendly.
- Answer from what you know. When they want something checked now, use
  your tools and say what you found.
- When they tell you how to do your job differently, call learn, so
  every later shift follows it.
- When they give you a new goal or a new schedule, call set_goal or
  set_schedule, then say what changed.
- When something here matters to your next shift, call keep_notes.
- When they ask you to do something that takes real work ("actually,
  can you..."), call take_on with the job: it runs in a shift of yours
  now, in the background, with all your tools, and your report on it
  comes back to them. Then tell them you are on it. A quick look you
  can do here and now needs no shift.
""".strip()

CHAT_LAST_WORD = (
    "You have used every tool round this answer gets. Answer the user "
    "now from what you have. Do not call any more tools."
)


def _tool(name: str, description: str, param: str, about: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    param: {"type": "string", "description": about},
                },
                "required": [param],
            },
        },
    }


CHAT_TOOLS = [
    _tool(
        "learn",
        "Keep a lesson from the user about how to do your job. Every "
        "later shift reads it and follows it.",
        "lesson", "The lesson, as one short instruction to yourself.",
    ),
    _tool(
        "set_goal",
        "Replace your standing goal, when the user gives you a new one.",
        "goal", "The whole new goal.",
    ),
    _tool(
        "set_schedule",
        "Change how often your shifts run, when the user asks.",
        "every", "How often: 30m, 2h, daily, weekly (at least 15m), or set "
        "times: 9am weekdays, mon 8:30, the 1st at 9am, or a cron line.",
    ),
]


TAKE_ON_TOOL = {
    "type": "function",
    "function": {
        "name": "take_on",
        "description": (
            "Take on a job the user asks you for in this chat: it runs in "
            "a shift of yours that starts now, in the background, with all "
            "your tools, and your report on it comes back to the user here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "job": {
                    "type": "string",
                    "description": (
                        "The job, in full, as the shift will need it: what "
                        "to do, where, and what to report."
                    ),
                },
            },
            "required": ["job"],
        },
    },
}


class SparkError(ValueError):
    """A spark that could not be made or found as asked."""


@dataclass
class Report:
    """What one shift had to say."""

    at: float
    text: str
    quiet: bool = False
    failed: bool = False
    read: bool = False
    steps: list[str] = field(default_factory=list)
    feedback: str = ""
    # What the user thought of it: 1 liked, -1 disliked, 0 not said.
    rating: int = 0
    # It stopped to ask: the user's yes or no carries it on.
    approval: bool = False
    # Web chats that gave it a job, for the report to be posted into,
    # and the ones it has been posted into so far.
    chats: list[str] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)


@dataclass
class Message:
    """One line of a chat with a spark: the user's or the spark's."""

    at: float
    who: str  # "you" or "spark"
    text: str
    steps: list[str] = field(default_factory=list)
    failed: bool = False


@dataclass
class Spark:
    """One spark, as kept on disk."""

    id: str
    name: str
    goal: str
    # Its job title, as a teammate's: "Repo watcher". "" for none.
    title: str = ""
    boundaries: str = ""
    every: int = DEFAULT_EVERY_MINUTES
    # Set times instead of every so often: a cron line, "0 9 * * 1-5",
    # in local time. "" runs it every EVERY minutes.
    at: str = ""
    colour: str = COLOURS[0]
    created: float = field(default_factory=time.time)
    paused: bool = False
    status: str = IDLE
    activity: str = ""
    notes: str = ""
    lessons: list[str] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    last_run: float = 0.0
    next_run: float = 0.0
    runs: int = 0
    # A shift asked for now, ahead of the schedule, even while paused.
    asked: bool = False
    chat: list[Message] = field(default_factory=list)
    # The project it works on, by its ID in the web UI's projects, or "".
    project: str = ""
    # The model it runs on. "" for a spark from before one was chosen,
    # which runs on whatever Flash is set to.
    model: str = ""
    # A step that asks first, waiting on the user: what it is, and all
    # a shift needs to carry on from it. {} when nothing waits.
    pending: dict = field(default_factory=dict)
    # The user asked the shift running now to stop.
    stop_asked: bool = False
    # A folder whose changes start a shift, besides the schedule.
    watch: str = ""
    # Why the next shift starts early, for it to be told.
    why: str = ""
    # What other sparks handed it, for its next shift: {from, text, at}.
    inbox: list = field(default_factory=list)
    # When the answer being made now was started, or 0; and what it is
    # doing meanwhile.
    replying: float = 0.0
    reply_activity: str = ""

    @property
    def handle(self) -> str:
        return handle_of(self.name)

    @property
    def answering(self) -> bool:
        return bool(self.replying) and (
            time.time() - self.replying < REPLY_STALE_SECONDS
        )

    @property
    def unread(self) -> int:
        return sum(1 for r in self.reports if not r.read and not r.quiet)

    @property
    def waiting(self) -> bool:
        """Stopped on a step that asks, the user not having answered."""

        return self.status == WAITING and not self.pending.get("answer")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["handle"] = self.handle
        data["answering"] = self.answering
        data["waiting"] = self.waiting
        data["model_used"] = model_of(self)
        # What waits, as the user sees it: not the conversation behind it.
        data["pending"] = {
            k: self.pending[k] for k in ("label", "detail", "tool", "at")
            if k in self.pending
        }
        found = project_of(self)
        data["project"] = found.id if found else ""
        data["project_name"] = found.name if found else ""
        data["unread"] = self.unread
        data["schedule"] = schedule_words(self)
        return data


def _titled(spark: Spark) -> str:
    # What follows the name in "You are Scout (@scout-spark), ...".
    return f", the user's {spark.title}" if spark.title else ""


def _title(text) -> str:
    return " ".join(str(text or "").split())[:TITLE_CHARS]


def handle_of(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"@{slug or 'spark'}-spark"


# --- Schedule words ------------------------------------------------------

_UNITS = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
    "w": 10080, "week": 10080, "weeks": 10080,
}
_WORDS = {
    "hourly": 60, "daily": 1440, "nightly": 1440, "weekly": 10080,
    "hour": 60, "day": 1440, "week": 10080,
}
_EVERY_RE = re.compile(r"^(?:every\s+)?(\d+(?:\.\d+)?)?\s*([a-z]+)$")


def _read_every(text) -> Optional[int]:
    """Minutes from "30m", "2 hours", "daily" or 45; None if TEXT is not
    a length of time."""

    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return int(text)
    words = str(text or "").strip().lower()
    if not words:
        return DEFAULT_EVERY_MINUTES
    if words.isdigit():
        return int(words)
    if words in _WORDS or words.removeprefix("every ") in _WORDS:
        return _WORDS[words.removeprefix("every ")]
    match = _EVERY_RE.match(words)
    if not match or match.group(2) not in _UNITS:
        return None
    return int(float(match.group(1) or 1) * _UNITS[match.group(2)])


def parse_every(text) -> int:
    """Minutes between shifts, from "30m", "2 hours", "daily" or 45."""

    minutes = _read_every(text)
    if minutes is None:
        raise SparkError(
            f"Could not read {text!r} as a schedule. Try 30m, 2h, "
            "daily, or weekly."
        )
    if minutes < MIN_EVERY_MINUTES:
        raise SparkError(
            f"A spark runs at most every {MIN_EVERY_MINUTES} minutes."
        )
    return min(minutes, MAX_EVERY_MINUTES)


def parse_schedule(text) -> tuple[int, str]:
    """(minutes, at) for a spark's schedule, from every so often ("30m",
    "daily") or set times ("9am weekdays", "mon 8:30", a cron line). AT
    is "" for every so often; for set times, MINUTES is how often they
    come round at their busiest."""

    if _read_every(text) is not None:
        return parse_every(text), ""
    try:
        expr = cron.parse(text)
    except cron.CronError as exc:
        raise SparkError(
            f"Could not read {text!r} as a schedule ({exc}). Try 30m, 2h, "
            "daily, or set times: 9am weekdays, mon 8:30, or a cron line."
        ) from None
    gap = cron.shortest_gap(expr, time.time()) / 60
    if gap < MIN_EVERY_MINUTES:
        raise SparkError(
            f"A spark runs at most every {MIN_EVERY_MINUTES} minutes."
        )
    return int(min(gap, MAX_EVERY_MINUTES)), expr


def schedule_words(spark) -> str:
    """How SPARK's schedule reads: "every 2 hours", "at 9am on weekdays".
    SPARK is a spark, or a template's dict."""

    at = spark.get("at", "") if isinstance(spark, dict) else spark.at
    every = spark["every"] if isinstance(spark, dict) else spark.every
    return cron.words(at) if at else f"every {every_words(every)}"


def next_shift(spark: Spark, after: float) -> float:
    """When SPARK's next shift is due, its last having ended at AFTER."""

    if spark.at:
        try:
            when = cron.next_after(spark.at, after)
        except cron.CronError:
            when = None
        if when is not None:
            return when
    return after + spark.every * 60


def _schedule(spark: Spark, every: int, at: str) -> None:
    """Put SPARK on a new schedule, its next shift moved to match."""

    spark.every, spark.at = every, at
    if at:
        # Set times wait for the next of them, not for one long gone.
        spark.next_run = next_shift(spark, time.time())
    elif spark.last_run:
        spark.next_run = next_shift(spark, spark.last_run)


def every_words(minutes: int) -> str:
    """How a schedule reads: "30 minutes", "2 hours", "day"."""

    for size, unit in ((10080, "week"), (1440, "day"), (60, "hour")):
        if minutes % size == 0:
            count = minutes // size
            return unit if count == 1 else f"{count} {unit}s"
    return f"{minutes} minutes"


def _since(then: float) -> str:
    if not then:
        return "never: this is your first shift"
    minutes = int((time.time() - then) // 60)
    if minutes < 60:
        return f"{minutes} minutes ago"
    if minutes < 2880:
        return f"{minutes // 60} hours ago"
    return f"{minutes // 1440} days ago"


# --- Storage -------------------------------------------------------------

_lock = threading.RLock()
_listeners: list[Callable[[], None]] = []


def sparks_dir() -> Path:
    # FLASH_DIR is read here, not bound at import, so a test can point
    # it at a temporary home.
    return FLASH_DIR / "sparks"


def _path(spark_id: str) -> Path:
    return sparks_dir() / f"{spark_id}.json"


def _from_dict(data: dict) -> Spark:
    known = {f.name for f in fields(Spark)}
    kept = {k: v for k, v in data.items() if k in known}
    report_fields = {f.name for f in fields(Report)}
    kept["reports"] = [
        Report(**{k: v for k, v in r.items() if k in report_fields})
        for r in data.get("reports") or []
        if isinstance(r, dict)
    ]
    message_fields = {f.name for f in fields(Message)}
    kept["chat"] = [
        Message(**{k: v for k, v in m.items() if k in message_fields})
        for m in data.get("chat") or []
        if isinstance(m, dict)
    ]
    return Spark(**kept)


def _load(path: Path) -> Optional[Spark]:
    try:
        return _from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _save(spark: Spark) -> None:
    folder = sparks_dir()
    folder.mkdir(parents=True, exist_ok=True)
    data = asdict(spark)
    temp = folder / f".{spark.id}.tmp"
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, _path(spark.id))


_depth = threading.local()


@contextlib.contextmanager
def _held():
    """This process's lock, and the same across processes.

    A spark's file is read, changed, and written whole. The keeper
    started at login and an open Flash can both do that to one spark at
    once, a shift finishing while you chat with it, and without this
    one write would lose the other's change. Reentrant, so a change
    made while one is held does not wait on itself.
    """

    with _lock:
        if getattr(_depth, "n", 0):
            _depth.n += 1
            try:
                yield
            finally:
                _depth.n -= 1
            return
        handle = _lock_edits()
        _depth.n = 1
        try:
            yield
        finally:
            _depth.n = 0
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.close()  # closing it lets the lock go


def _lock_edits():
    try:
        sparks_dir().mkdir(parents=True, exist_ok=True)
        handle = open(sparks_dir() / ".edit.lock", "a+b")  # held till done
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue  # LK_LOCK gives up after ten seconds
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        handle.close()
        return None
    return handle


def _spark_files() -> list[Path]:
    # Not the dotfiles beside them: the keeper's heartbeat is JSON too.
    return [
        p for p in sparks_dir().glob("*.json") if not p.name.startswith(".")
    ]


def _changed() -> None:
    for listener in list(_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001, S110
            pass  # a listener never stops a shift


def on_change(listener: Callable[[], None]) -> None:
    """Call LISTENER whenever any spark changes: the web UI redraws."""

    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)


def all_sparks() -> list[Spark]:
    """Every spark, oldest first."""

    folder = sparks_dir()
    if not folder.is_dir():
        return []
    with _lock:
        found = [_load(p) for p in _spark_files()]
    return sorted((s for s in found if s), key=lambda s: s.created)


def find(key: str) -> Optional[Spark]:
    """A spark by its ID, name, or handle, ignoring case and the @."""

    key = str(key or "").strip().lower().lstrip("@")
    if not key:
        return None
    for spark in all_sparks():
        if key in (
            spark.id, spark.name.lower(), spark.handle.lstrip("@"),
            spark.handle.lstrip("@").removesuffix("-spark"),
        ):
            return spark
    return None


def _must_find(key: str) -> Spark:
    spark = find(key)
    if spark is None:
        raise SparkError(f"No spark called {key!r}.")
    return spark


def _edit(key: str, change: Callable[[Spark], None]) -> Spark:
    with _held():
        spark = _must_find(key)
        change(spark)
        _save(spark)
    _changed()
    return spark


# --- Projects ------------------------------------------------------------
#
# A spark can be given a project, one of the web UI's: a folder, with
# instructions for working in it. Its shifts are told where the folder
# is and read the instructions, and a chat with it starts in the
# project. A shift does not move the process into the folder, as a web
# chat does for its turn, since the keeper shares the process with
# whatever else is running; it is told to use full paths instead.


def model_of(spark: Spark, flash: str = "") -> str:
    """The model SPARK runs on: its own, or Flash's if it has none.
    FLASH, when given, is Flash's model as the caller has it."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    return spark.model or flash or flash_tools.MODEL_NAME or ""


def _model_name(model) -> str:
    return " ".join(str(model or "").split())[:200]


def project_of(spark: Spark):
    """SPARK's project, or None: none given, or the project is gone."""

    if not spark.project:
        return None
    from . import workspace  # deferred: only a spark with one needs it

    return workspace.project(spark.project)


def resolve_project(key: str) -> str:
    """The ID of the project KEY names, by ID or by name; "" for none."""

    key = " ".join(str(key or "").split())
    if key.lower() in ("", "none", "no project", "-"):
        return ""
    from . import workspace  # deferred: only a spark with one needs it

    for found in workspace.projects():
        if key == found.id or key.casefold() == found.name.casefold():
            return found.id
    raise SparkError(f"No project called {key!r}.")


def project_block(spark: Spark) -> str:
    """What a shift is told about SPARK's project, or ""."""

    found = project_of(spark)
    if found is None:
        return ""
    lines = [
        f"=== Your project: {found.name} ===",
        f"You work on the project in {found.path}. Relative paths do not "
        "start there, so give full paths, and cd into it first in shell "
        "commands.",
    ]
    if found.instructions:
        lines += ["", found.instructions]
    return "\n".join(lines)


def _watch_folder(path: str) -> str:
    """PATH as a folder to watch, in full; "" for none."""

    path = str(path or "").strip()
    if not path:
        return ""
    folder = Path(os.path.expanduser(path)).resolve()
    if not folder.is_dir():
        raise SparkError(f"{path} is not a folder on this computer.")
    return str(folder)


# --- Templates and sharing -----------------------------------------------
#
# A spark worth having is worth passing on. A template is a spark with
# nothing of anyone's in it: a name, a goal, a schedule, boundaries, and
# the lessons it was taught. Flash comes with a few, and any spark can be
# shared as a code to paste, which gives whoever adds it a copy of their
# own, looked over first.

TEMPLATES = [
    {
        "name": "Morning Brief",
        "title": "News editor",
        "blurb": "The news on your topics, every morning",
        "goal": (
            "Search the web for the most important news of the last day on "
            "the topics I care about, and give me a brief of five bullets "
            "at most, each with its link. My topics are in your lessons; "
            "if there are none, ask me for them in your report."
        ),
        "every": 1440,
        "boundaries": (
            "Only read and search. Never sign up for or buy anything."
        ),
    },
    {
        "name": "Repo Watch",
        "title": "Repo watcher",
        "blurb": "What changed in a git repo, and what is left undone",
        "goal": (
            "In my project's git repository, fetch from the remote and tell "
            "me about new commits on the main branch since your last report, "
            "and about uncommitted changes that have sat for more than a day."
        ),
        "every": 120,
        "boundaries": (
            "Only read. Never commit, push, pull, reset, stash, or change a "
            "file."
        ),
    },
    {
        "name": "Test Runner",
        "title": "Test keeper",
        "blurb": "Tells you when passing tests start failing",
        "goal": (
            "Run my project's tests. Tell me when a test that passed before "
            "fails now, with its name, the error, and the likely cause. Keep "
            "the list of what passed in your notes."
        ),
        "every": 360,
        "boundaries": (
            "Only run the tests. Never change code or install anything."
        ),
    },
    {
        "name": "Disk Guard",
        "title": "Disk custodian",
        "blurb": "Warns before a disk fills up",
        "goal": (
            "Check the free space on each mounted disk. When one has less "
            "than 10% free, tell me, with the biggest folders in my home "
            "folder."
        ),
        "every": 1440,
        "boundaries": "Never delete, move, or empty anything.",
    },
    {
        "name": "Dependency Check",
        "title": "Dependency auditor",
        "blurb": "Outdated packages, and which ones matter",
        "goal": (
            "Find my project's outdated dependencies, with the tool it uses "
            "(npm outdated, pip list --outdated, and so on), and tell me "
            "which have security fixes or major new versions."
        ),
        "every": 10080,
        "boundaries": "Only read. Never install, upgrade, or remove anything.",
    },
    {
        "name": "Page Watch",
        "title": "Page watcher",
        "blurb": "Tells you when a web page changes",
        "goal": (
            "Fetch the page at the address in your lessons and tell me when "
            "what it says has changed since last time, and how. Keep what it "
            "said in your notes. If there is no address, ask me for one."
        ),
        "every": 360,
        "boundaries": "Only read. Never fill in or submit anything.",
    },
    {
        "name": "Inbox",
        "title": "Inbox keeper",
        # Shown, when it is not, before one is made.
        "needs": "email",
        "blurb": "Sorts your email, says what needs you, drafts replies",
        "goal": (
            "Check my email with check_inbox for what has come in since "
            "your last shift: it covers every account I have connected. "
            "Keep the newest uid you have seen in each account in your "
            "notes, and skip anything at or below it. Read the ones that "
            "matter with read_email, giving the account each is in. Sort "
            "each new email: needs me, worth "
            "knowing, or ignorable (newsletters, receipts, notifications). "
            "Report the ones that need me first, one line each: who, what "
            "they want, and by when, with the email's subject as a link to "
            "it ([subject](link), from check_inbox); then the worth-knowing "
            "ones in a line or two, linked the same way; then how many you "
            "skipped. When one needs a reply you "
            "can write from what you know, draft it with send_email as a "
            "reply to its uid: I approve each one before it goes. If "
            "nothing new needs me, reply NOTHING NEW."
        ),
        "every": 30,
        "boundaries": (
            "Never delete, move, archive or mark email. Never sign up, "
            "unsubscribe, click links, or buy anything. Never send an email "
            "except as a reply I can approve."
        ),
    },
]

SHARE_PREFIX = "flash-spark:"


def share_code(key: str) -> str:
    """SPARK as a code to paste somewhere: its template, no more."""

    spark = _must_find(key)
    data = {
        "v": 1, "name": spark.name, "title": spark.title,
        "goal": spark.goal,
        "boundaries": spark.boundaries, "every": spark.every,
        "lessons": spark.lessons,
    }
    if spark.at:
        data["at"] = spark.at
    packed = base64.urlsafe_b64encode(
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return SHARE_PREFIX + packed.rstrip("=")


def read_code(code: str) -> dict:
    """What a share code holds, checked, to show before adding it."""

    code = "".join(str(code or "").split())
    if not code.startswith(SHARE_PREFIX):
        raise SparkError(
            f"That is not a spark's code: they start {SHARE_PREFIX}"
        )
    packed = code[len(SHARE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(
            packed + "=" * (-len(packed) % 4)
        ).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SparkError("That code is not whole. Copy all of it.") from None
    if not isinstance(data, dict) or not str(data.get("goal") or "").strip():
        raise SparkError("That code holds no spark.")
    lessons = data.get("lessons") or []
    read = {
        "name": " ".join(str(data.get("name") or "Spark").split())[
            :NAME_CHARS],
        "title": _title(data.get("title")),
        "goal": str(data["goal"]).strip()[:GOAL_CHARS],
        "boundaries": str(data.get("boundaries") or "").strip()[
            :BOUNDARY_CHARS],
        "every": parse_every(data.get("every") or DEFAULT_EVERY_MINUTES),
        "at": parse_schedule(data["at"])[1] if data.get("at") else "",
        "lessons": [
            " ".join(str(lesson).split())[:LESSON_CHARS]
            for lesson in lessons[:MAX_LESSONS] if str(lesson).strip()
        ] if isinstance(lessons, list) else [],
    }
    read["schedule"] = schedule_words(read)
    return read


def add_from(
    source: str, project: str = "", model: str = "", paused: bool = False,
) -> Spark:
    """A spark of your own, copied from a share code or a template named
    SOURCE. A name already taken gets a number."""

    found = next(
        (t for t in TEMPLATES
         if t["name"].casefold() == str(source).strip().casefold()),
        None,
    )
    data = dict(found) if found else read_code(source)
    name, number = data["name"], 2
    while find(handle_of(name)) is not None:
        name = f"{data['name'][:NAME_CHARS - 3]} {number}"
        number += 1
    spark = create(
        name, data["goal"], data.get("boundaries", ""),
        data.get("at") or data["every"],
        project, model=model, title=data.get("title", ""), paused=paused,
    )
    lessons = data.get("lessons") or []
    if lessons:
        spark = _edit(spark.id, lambda s: s.lessons.extend(lessons))
    return spark


def create(
    name: str, goal: str, boundaries: str = "", every="", project: str = "",
    watch: str = "", model: str = "", title: str = "", paused: bool = False,
) -> Spark:
    """Make a spark. Its first shift runs as soon as the keeper looks;
    made PAUSED, as soon as it is resumed."""

    name = " ".join(str(name or "").split())[:NAME_CHARS]
    goal = str(goal or "").strip()[:GOAL_CHARS]
    if not name:
        raise SparkError("A spark needs a name.")
    if not goal:
        raise SparkError("A spark needs a goal.")
    minutes, at = parse_schedule(every)
    project = resolve_project(project)
    watch = _watch_folder(watch)

    with _held():
        taken = {s.handle for s in all_sparks()}
        if handle_of(name) in taken:
            raise SparkError(f"There is already a spark called {name}.")
        spark = Spark(
            id=uuid.uuid4().hex[:8],
            name=name,
            title=_title(title),
            goal=goal,
            boundaries=str(boundaries or "").strip()[:BOUNDARY_CHARS],
            every=minutes,
            at=at,
            colour=COLOURS[len(taken) % len(COLOURS)],
            project=project,
            watch=watch,
            model=_model_name(model),
            paused=bool(paused),
        )
        if at:
            # Its first shift is at its first set time, not now.
            spark.next_run = next_shift(spark, time.time())
        _save(spark)
    _changed()
    wake()
    return spark


def update(key: str, **changes) -> Spark:
    """Change a spark's goal, boundaries, schedule, name, or title."""

    def change(spark: Spark) -> None:
        if "goal" in changes:
            goal = str(changes["goal"] or "").strip()[:GOAL_CHARS]
            if not goal:
                raise SparkError("A spark needs a goal.")
            spark.goal = goal
        if "boundaries" in changes:
            spark.boundaries = (
                str(changes["boundaries"] or "").strip()[:BOUNDARY_CHARS]
            )
        if "every" in changes:
            _schedule(spark, *parse_schedule(changes["every"]))
        if "project" in changes:
            spark.project = resolve_project(changes["project"])
        if "watch" in changes:
            spark.watch = _watch_folder(changes["watch"])
        if "model" in changes:
            spark.model = _model_name(changes["model"])
        if "title" in changes:
            spark.title = _title(changes["title"])
        if "name" in changes:
            name = " ".join(str(changes["name"] or "").split())[:NAME_CHARS]
            if not name:
                raise SparkError("A spark needs a name.")
            clash = find(handle_of(name))
            if clash and clash.id != spark.id:
                raise SparkError(f"There is already a spark called {name}.")
            spark.name = name

    return _edit(key, change)


def remove(key: str) -> Spark:
    with _held():
        spark = _must_find(key)
        _path(spark.id).unlink(missing_ok=True)
    _changed()
    return spark


def set_paused(key: str, paused: bool) -> Spark:
    def change(spark: Spark) -> None:
        spark.paused = paused
        if not paused and spark.next_run < time.time():
            # Back from a pause, it picks up now rather than at once for
            # every shift it missed.
            spark.next_run = time.time()

    spark = _edit(key, change)
    wake()
    return spark


def _add_lesson(spark: Spark, lesson: str) -> None:
    """LESSON on SPARK's list, once: told the same thing twice, it keeps
    it once."""

    if lesson.casefold() not in (known.casefold() for known in spark.lessons):
        spark.lessons.append(lesson)
        del spark.lessons[:-MAX_LESSONS]


def teach(key: str, lesson: str, report_at: Optional[float] = None) -> Spark:
    """Keep LESSON for every later shift, and hang it on a report."""

    lesson = " ".join(str(lesson or "").split())[:LESSON_CHARS]
    if not lesson:
        raise SparkError("Say what it should do differently.")

    def change(spark: Spark) -> None:
        _add_lesson(spark, lesson)
        for report in spark.reports:
            if report_at is not None and report.at == report_at:
                report.feedback = lesson
                report.read = True

    return _edit(key, change)


def rate(key: str, report_at: float, rating: int) -> Spark:
    """Like (1) or dislike (-1) the report made at REPORT_AT, or take
    that back (0). Later shifts are shown what was liked and what not."""

    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = 2
    if rating not in (-1, 0, 1):
        raise SparkError("A report is liked (1), disliked (-1), or 0.")

    def change(spark: Spark) -> None:
        for report in spark.reports:
            if report.at == report_at and not report.quiet:
                report.rating = rating
                report.read = True
                return
        raise SparkError(f"{spark.name} has no such report.")

    return _edit(key, change)


def rate_latest(key: str, rating: int) -> tuple[Spark, Report]:
    """Rate SPARK's latest report with something in it."""

    spark = _must_find(key)
    said = [r for r in spark.reports if not r.quiet and not r.failed]
    if not said:
        raise SparkError(f"{spark.name} has no report to rate yet.")
    spark = rate(spark.id, said[-1].at, rating)
    return spark, next(r for r in spark.reports if r.at == said[-1].at)


def rated_block(spark: Spark) -> str:
    """The reports the user liked or disliked, newest last, for a shift
    to learn from."""

    rated = [r for r in spark.reports if r.rating][-MAX_RATED:]
    if not rated:
        return "(none rated yet)"
    lines = []
    for report in rated:
        text = " ".join(report.text.split())
        if len(text) > RATED_CHARS:
            text = text[:RATED_CHARS].rstrip() + "…"
        line = f"- {'Liked' if report.rating > 0 else 'Disliked'}: {text}"
        if report.feedback:
            line += f"\n  They said: {report.feedback}"
        lines.append(line)
    return "\n".join(lines)


def forget_lesson(key: str, index: int) -> Spark:
    """Drop lesson INDEX, counted from 1."""

    def change(spark: Spark) -> None:
        if not 1 <= index <= len(spark.lessons):
            raise SparkError(f"{spark.name} has no lesson {index}.")
        del spark.lessons[index - 1]

    return _edit(key, change)


def mark_read(key: str) -> Spark:
    def change(spark: Spark) -> None:
        for report in spark.reports:
            report.read = True

    return _edit(key, change)


def unread_total() -> int:
    """What the Sparks badge counts: new reports, and steps waiting on a
    yes or a no."""

    return sum(s.unread + (1 if s.waiting else 0) for s in all_sparks())


# --- News, for the terminal ---------------------------------------------

_told: set[tuple[str, float]] = set()


def news() -> list[tuple[Spark, Report]]:
    """Unread reports not yet pointed out, each only once a session."""

    fresh = []
    for spark in all_sparks():
        for report in spark.reports:
            key = (spark.id, report.at)
            if report.read or report.quiet or key in _told:
                continue
            _told.add(key)
            fresh.append((spark, report))
    return fresh


# --- A shift -------------------------------------------------------------


def _prompt(spark: Spark, host: str, model: str, date_prompt: str) -> str:
    lessons = "\n".join(f"- {lesson}" for lesson in spark.lessons)
    last = next(
        (r.text for r in reversed(spark.reports) if not r.failed), ""
    )
    body = SPARK_PROMPT.format(
        name=spark.name,
        handle=spark.handle,
        titled=_titled(spark),
        rated=rated_block(spark),
        goal=spark.goal,
        boundaries=spark.boundaries or "(none beyond your usual care)",
        lessons=lessons or "(nothing yet)",
        notes=spark.notes or "(none yet)",
        last=last or "(none yet)",
        every=schedule_words(spark),
        since=_since(spark.last_run),
        nothing_new=NOTHING_NEW,
    )
    parts = [
        get_model_system_prompt(host, model), body, project_block(spark),
        team_block(spark), date_prompt,
    ]
    return "\n\n".join(part for part in parts if part)


def _set_activity(
    spark_id: str, activity: str, what: str = "activity",
) -> None:
    with _held():
        spark = find(spark_id)
        if spark is None:
            return
        setattr(spark, what, activity)
        _save(spark)
    _changed()


class NeedsApproval(Exception):
    """A shift reached a step that asks first: it stops, to carry on
    with the user's answer. LATER are the calls after it, not run."""

    def __init__(self, name: str, args: dict, later: list) -> None:
        super().__init__(name)
        # Not "args": an exception's own, which it makes a tuple.
        self.name, self.arguments, self.later = name, args, later


class Stopped(Exception):
    """The user asked the shift to stop."""


def describe(name: str, args: dict) -> tuple[str, str]:
    """A step that asks first, as the user is asked it: what it wants,
    and the detail to judge it by."""

    path = str(args.get("file_path") or args.get("path") or "")
    if name == "shell":
        return "Run a command", str(args.get("command", ""))
    if name == "write":
        body = str(args.get("content", ""))
        cut = body[:1200] + ("\n..." if len(body) > 1200 else "")
        return f"Write {path}", cut
    if name == "send_email":
        to = str(args.get("to") or "").strip()
        reply = str(args.get("reply_to") or "").strip()
        label = (
            f"Send an email to {to}" if to else
            f"Reply to email {reply}" if reply else "Send an email"
        )
        sender = str(args.get("account") or "").strip()
        if sender:
            label += f", from {sender}"
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "")
        cut = body[:1200] + ("\n..." if len(body) > 1200 else "")
        return label, (f"Subject: {subject}\n\n" if subject else "") + cut
    if name in ("edit", "multi_edit"):
        return f"Change {path}", json.dumps(
            {k: v for k, v in args.items() if k not in ("file_path", "path")},
            ensure_ascii=False, indent=1,
        )[:1200]
    return name, json.dumps(args, ensure_ascii=False)[:1200]


UNLIMITED_WORDS = ("unlimited", "none", "off", "infinite", "no limit", "∞")


def _saved(name: str) -> str:
    """NAME as the env file has it, else as this process does.

    From the file, as autonomous() reads it, so a keeper running in the
    background goes by what was set in another Flash.
    """

    from dotenv import dotenv_values

    try:
        saved = dotenv_values(ENV_PATH).get(name)
    except OSError:
        saved = None
    if saved is None or not str(saved).strip():
        saved = os.getenv(name, "")
    return str(saved or "").strip()


def shift_rounds_number() -> int:
    """The number of rounds a shift is limited to, while it is."""

    try:
        rounds = int(_saved("SPARK_SHIFT_ROUNDS"))
    except ValueError:
        return SHIFT_ROUNDS_DEFAULT
    return max(SHIFT_ROUNDS_MIN, min(SHIFT_ROUNDS_MAX, rounds))


def shift_rounds_unlimited() -> bool:
    return _saved("SPARK_SHIFT_UNLIMITED").lower() in ("1", "true", "on")


def shift_rounds() -> Optional[int]:
    """How many rounds of tools a shift gets, as the user last set it:
    None for no limit."""

    return None if shift_rounds_unlimited() else shift_rounds_number()


def _save_setting(name: str, value: str) -> None:
    from .envfile import set_env_var

    os.environ[name] = value
    set_env_var(ENV_PATH, name, value)


def set_shift_rounds_unlimited(unlimited: bool) -> Optional[int]:
    """Take the limit off every spark's shifts, or put it back."""

    _save_setting("SPARK_SHIFT_UNLIMITED", "1" if unlimited else "0")
    return shift_rounds()


def set_shift_rounds(value) -> Optional[int]:
    """Set how many rounds of tools a shift gets, for every spark: a
    number, which puts the limit back on, or "unlimited", which takes
    it off."""

    if str(value).strip().lower() in UNLIMITED_WORDS:
        return set_shift_rounds_unlimited(True)
    try:
        rounds = int(str(value).strip())
    except ValueError:
        raise SparkError(
            "Say how many rounds, as a number, or unlimited."
        ) from None
    if not SHIFT_ROUNDS_MIN <= rounds <= SHIFT_ROUNDS_MAX:
        raise SparkError(
            f"A shift gets between {SHIFT_ROUNDS_MIN} and "
            f"{SHIFT_ROUNDS_MAX} rounds of tools, or no limit."
        )
    _save_setting("SPARK_SHIFT_ROUNDS", str(rounds))
    _save_setting("SPARK_SHIFT_UNLIMITED", "0")
    return rounds


def autonomous() -> bool:
    """Whether autonomous mode is on, as the user last set it.

    From the env file, not this process's memory: the user may have
    switched it in another Flash, the terminal while this one serves the
    web UI say, and this one would not know. Only when the file does not
    say does this process's own setting count.
    """

    from dotenv import dotenv_values

    from . import tools as flash_tools  # deferred: avoids a module cycle

    try:
        saved = dotenv_values(ENV_PATH).get("NO_COMMAND_CONFIRMATION")
    except OSError:
        saved = None
    if saved is None or not str(saved).strip():
        return bool(flash_tools.NO_COMMAND_CONFIRMATION)
    try:
        return int(str(saved).strip()) > 0
    except ValueError:
        return bool(flash_tools.NO_COMMAND_CONFIRMATION)


HAND_OFF_TOOL = {
    "type": "function",
    "function": {
        "name": "hand_off",
        "description": (
            "Hand something to another of the user's sparks: a finding it "
            "should act on, or work that is its job, not yours. It starts "
            "a shift soon and is given your note."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spark": {
                    "type": "string",
                    "description": "Its name or handle, from the list.",
                },
                "note": {
                    "type": "string",
                    "description": "What it needs to know, on its own.",
                },
            },
            "required": ["spark", "note"],
        },
    },
}


def _handing_off(spark: Spark) -> Callable[[dict], str]:
    def hand(args: dict) -> str:
        note = " ".join(str(args.get("note", "")).split())[:LESSON_CHARS * 2]
        target = find(str(args.get("spark", "")))
        if target is None or target.id == spark.id:
            return "Error: no other spark by that name."
        if not note:
            return "Error: the note was empty."

        def give(other: Spark) -> None:
            other.inbox.append({"from": spark.name, "text": note,
                                "at": time.time()})
            del other.inbox[:-MAX_INBOX]
            other.asked = True

        _edit(target.id, give)
        wake()
        tool_line(f"HandOff({target.name})")
        tool_result(note)
        return f"(handed to {target.name}: it starts a shift soon)"

    return hand


# --- Asking each other ---------------------------------------------------
#
# A spark, or Flash, can ask another spark something and have its answer
# now: what it found this morning, whether it has seen this already. The
# one asked answers as itself, from what it knows, with no tools: so an
# answer is quick, never runs anything, and never asks a third spark,
# which could ask back. Work goes the other way, by hand_off or take_on.

CONSULT_PROMPT = """
=== {asker} is asking you ===
{asker}{who} is asking you something, between your shifts. Answer from
what you know: your goal, your notes, your reports, and what the user
has taught you. Be short and specific, as one teammate to another, and
give the facts it needs (names, numbers, links) rather than a summary
of them. If you do not know, say so plainly: do not guess, and do not
offer to look. You have no tools for this answer.
""".strip()

CONSULT_LAST_WORD = (
    "Answer now, from what you know. You have no tools for this answer."
)

ASK_SPARK_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_spark",
        "description": (
            "Ask another of the user's sparks something and get its answer "
            "now: what it has found, what it already reported, whether it "
            "has seen something. It answers from what it knows, without "
            "running anything. To give it work instead, use hand_off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spark": {
                    "type": "string",
                    "description": "Its name or handle, from the list.",
                },
                "question": {
                    "type": "string",
                    "description": "What to ask, on its own.",
                },
            },
            "required": ["spark", "question"],
        },
    },
}


def consult(
    key: str, question: str, asker: str = "Flash", who: str = "",
    client=None,
) -> tuple[Spark, str]:
    """KEY's answer to QUESTION, made now, from what it knows. ASKER is
    who asks, by name, and WHO what they are, for the spark to know."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    question = str(question or "").strip()[:MESSAGE_CHARS]
    if not question:
        raise SparkError("Ask it something.")
    spark = find(str(key or ""))
    if spark is None:
        names = ", ".join(s.name for s in all_sparks())
        raise SparkError(
            f"There is no spark called {key!r}."
            + (f" The user's sparks: {names}." if names else
               " The user has no sparks.")
        )
    host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
    prompt = "\n\n".join((
        chat_prompt(
            spark, host, model_of(spark), flash_tools.CURRENT_DATE_PROMPT,
        ),
        CONSULT_PROMPT.format(asker=asker, who=who),
    ))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
    ]
    text = _work(
        spark, messages, {}, [], [], lambda doing: None, 1,
        CONSULT_LAST_WORD, client, names=(),
    )
    return spark, text or "(It had nothing to say.)"


def _asking(spark: Spark) -> Callable[[dict], str]:
    """ask_spark, for SPARK to ask the others."""

    def ask_one(args: dict) -> str:
        target = find(str(args.get("spark", "")))
        if target is not None and target.id == spark.id:
            return "Error: that is you. Ask another spark."
        try:
            other, text = consult(
                str(args.get("spark", "")), str(args.get("question", "")),
                asker=spark.name, who=f" ({spark.handle}), another spark",
            )
        except SparkError as exc:
            return f"Error: {exc}"
        except Exception as e:  # noqa: BLE001
            return f"Error: it could not answer: {e.__class__.__name__}: {e}"
        tool_line(f"AskSpark({other.name})")
        tool_result(text)
        return f"{other.name} says: {text}"

    return ask_one


def roster_block() -> str:
    """The user's sparks, for Flash to ask or give work to; "" if none."""

    found = all_sparks()
    if not found:
        return ""
    lines = [
        "=== The user's sparks ===",
        "Agents that keep working on a goal on a schedule. ask_spark asks "
        "one something and gets its answer now; give_spark hands one a "
        "job, done in a shift that starts now, with its report coming "
        "back to the user.",
    ]
    for spark in found:
        first = spark.goal.splitlines()[0][:120]
        state = " (paused)" if spark.paused else ""
        lines.append(f"- {spark.name} ({spark.handle}){state}: {first}")
    return "\n".join(lines)


def team_block(spark: Spark) -> str:
    """The other sparks, for SPARK to hand work to; "" if there are none."""

    others = [s for s in all_sparks() if s.id != spark.id]
    if not others:
        return ""
    lines = [
        "=== The other sparks: hand_off sends one work, ask_spark asks "
        "one something ==="
    ]
    for other in others:
        first = other.goal.splitlines()[0][:120]
        lines.append(f"- {other.name} ({other.handle}): {first}")
    return "\n".join(lines)


# A model that says to try again: too many requests at once (Ollama's
# cloud runs only so many for an account, so a chat with a spark can
# take its own shift's turn), a rate limit, or a server briefly down. A
# shift waits these out rather than failing part way through.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_WORDS = re.compile(
    r"too many|concurrent|rate.?limit|overloaded|busy|try again|"
    r"temporarily|unavailable|timed? ?out",
    re.IGNORECASE,
)
RETRY_WAITS = (2, 5, 10, 20, 30)
RETRY_STEP_SECONDS = 0.5


def _passing(error: Exception) -> bool:
    """Whether ERROR, from the model, is worth asking again after."""

    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    if isinstance(error, ollama.ResponseError):
        return (
            getattr(error, "status_code", None) in RETRY_STATUSES
            or bool(RETRY_WORDS.search(str(error)))
        )
    return False


def _pause(seconds: float, stopping: Optional[Callable[[], bool]]) -> None:
    """Wait SECONDS, a little at a time, so Stop still stops."""

    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if stopping is not None and stopping():
            raise Stopped()
        time.sleep(min(RETRY_STEP_SECONDS, until - time.monotonic()))


def _ask_model(
    client, doing: Callable[[str], None],
    stopping: Optional[Callable[[], bool]], **request,
):
    """client.chat(**REQUEST), asked again after a refusal that says to
    try again, up to len(RETRY_WAITS) times."""

    for wait in (*RETRY_WAITS, None):
        try:
            return client.chat(**request)
        except Exception as error:  # re-raised unless it is passing
            if wait is None or not _passing(error):
                raise
            doing(f"The model is busy, trying again in {wait}s")
            _pause(wait, stopping)
    raise AssertionError("unreachable")


_EMAIL_WORDS = re.compile(
    r"e-?mail|inbox|check_inbox|read_email|send_email", re.IGNORECASE,
)


def _about_email(spark: Spark) -> bool:
    """Whether SPARK's job has to do with the user's email."""

    return bool(_EMAIL_WORDS.search(
        "\n".join([spark.goal, spark.boundaries, *spark.lessons])
    ))


def needs_email(spark: Spark) -> bool:
    """Whether SPARK can only do its job with the user's email."""

    return bool(re.search(
        r"check_inbox|read_email|send_email", spark.goal,
    ))


def _work(
    spark: Spark,
    messages: list[dict],
    own: dict[str, Callable[[dict], str]],
    own_tools: list[dict],
    steps: list[str],
    doing: Callable[[str], None],
    rounds: Optional[int],
    last_word: str,
    client=None,
    names: Optional[tuple[str, ...]] = None,
    gate: Optional[Callable[[str], bool]] = None,
    stopping: Optional[Callable[[], bool]] = None,
) -> str:
    """One agent run for SPARK: the model and its tools, back and forth,
    until it answers. OWN are the tools only a spark has, each answered
    here; NAMES the rest it may call, a sub-agent's by default. What it
    ran lands in STEPS, and DOING hears what it is up to. A call GATE
    says asks first raises NeedsApproval, and STOPPING raises Stopped
    between steps. Its last reply, without dashes."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    model = model_of(spark)
    if not model:
        raise RuntimeError("no model is set, so it could not run")
    host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
    client = client or ollama.Client(host=host)
    allowed = names if names is not None else subagents.allowed_tool_names()
    offered = flash_tools.available_tools()
    if not flash_tools.mail.configured() and _about_email(spark):
        # Its job is email, which is not connected: it gets the tools
        # anyway, so it is told so when it reaches for them, and says
        # so, rather than reporting that its tools are missing.
        offered = offered + flash_tools.EMAIL_TOOLS
    schemas = [
        t for t in offered if t["function"]["name"] in allowed
    ] + own_tools

    def record(kind: str, text: str, style: str) -> None:
        if kind == "line":
            steps.append(text)
            del steps[:-MAX_STEPS]
            doing(f"Running {text}")
        elif kind == "result" and style == ERROR and steps:
            steps[-1] += " (failed)"

    final = ""
    tool_calls: list = []
    with capture_tool_output(record):
        # No limit: it goes until the model has no more tools to call,
        # or the user stops it.
        for _ in range(rounds) if rounds is not None else itertools.count():
            if stopping is not None and stopping():
                raise Stopped()
            doing("Thinking")
            response = _ask_model(
                client, doing, stopping,
                model=model, messages=messages, tools=schemas,
                options=subagents.chat_options(),
            )
            message = getattr(response, "message", None)
            final = getattr(message, "content", "") or ""
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            if not tool_calls:
                break

            calls = [subagents._tool_call_name_args(c) for c in tool_calls]
            messages.append({
                "role": "assistant",
                "content": final,
                "tool_calls": [
                    {"function": {"name": n, "arguments": a}}
                    for n, a in calls
                ],
            })
            for at, (name, args) in enumerate(calls):
                if gate is not None and name in allowed and gate(name):
                    raise NeedsApproval(name, args, calls[at + 1:])
                result = _call(name, args, allowed, steps, own)
                messages.append({
                    "role": "tool",
                    "content": flash_tools.trim_tool_output(result, name),
                    "tool_name": name,
                })

    if tool_calls:
        doing("Writing")
        messages.append({"role": "system", "content": last_word})
        response = _ask_model(
            client, doing, stopping,
            model=model, messages=messages,
            options=subagents.chat_options(),
        )
        final = getattr(
            getattr(response, "message", None), "content", ""
        ) or ""

    return undash(final).strip()


def _keeping_notes(notes: list[str]):
    def keep(args: dict) -> str:
        notes[0] = str(args.get("notes", "")).strip()
        tool_line("KeepNotes()")
        return "(notes kept)"

    return keep


NOT_RUN = (
    "Not run: it came after a step that waited for the user's approval. "
    "Call it again if you still need it."
)


def take_on(key: str, job: str, chat: str = "") -> Spark:
    """A job the user gave SPARK in a chat, for a shift that starts now.
    CHAT is the web chat it came from, for the report to go back to."""

    job = str(job or "").strip()[:GOAL_CHARS]
    if not job:
        raise SparkError("Say what the job is.")

    def give(spark: Spark) -> None:
        spark.inbox.append({
            "from": "the user", "text": job, "at": time.time(),
            "job": True, "chat": chat,
        })
        del spark.inbox[:-MAX_INBOX]
        spark.asked = True

    spark = _edit(key, give)
    wake()
    return spark


def to_post() -> list[tuple[Spark, Report, str]]:
    """Each report of a job given in a web chat, with the chat, not yet
    posted into it."""

    return [
        (spark, report, chat)
        for spark in all_sparks()
        for report in spark.reports
        for chat in report.chats
        if chat not in report.posted
    ]


def posted(spark_id: str, report_at: float, chat: str) -> None:
    """Note that a report is in CHAT now, or that CHAT is gone."""

    def change(spark: Spark) -> None:
        for report in spark.reports:
            if report.at == report_at and chat not in report.posted:
                report.posted.append(chat)

    with contextlib.suppress(SparkError):
        _edit(spark_id, change)


def _opening(why: str, inbox: list) -> str:
    """What a shift is told first: to start, and why it started now."""

    parts = ["Start your shift."]
    if why:
        parts.append(f"It started early because: {why}")
    jobs = [item for item in inbox if item.get("job")]
    handed = [item for item in inbox if not item.get("job")]
    if jobs:
        parts.append(
            "The user asked you, in a chat, to do this. Do it first, then "
            "your goal if there is time, and report on it whatever else "
            "you find:\n"
            + "\n".join(f"- {item.get('text', '')}" for item in jobs)
        )
    if handed:
        parts.append("Handed to you by other sparks:\n" + "\n".join(
            f"- From {item.get('from', 'a spark')}: {item.get('text', '')}"
            for item in handed
        ))
    return "\n\n".join(parts)


def shift(spark_id: str, client=None) -> Optional[Report]:
    """Run one shift of SPARK_ID now, on this thread, and file its report.

    A paused spark still runs when asked to. One that waits on a step
    the user has answered carries on from that step. None if the spark
    is gone, already working, or waiting on an answer.
    """

    from . import tools as flash_tools  # deferred: avoids a module cycle

    with _held():
        spark = find(spark_id)
        if spark is None or spark.status == WORKING or spark.waiting:
            return None
        resuming = dict(spark.pending) if spark.pending else None
        if resuming:
            # What was handed to it while it waited is for the next
            # shift: this one carries on from where it stopped.
            why, inbox = "", []
        else:
            why, inbox = spark.why, list(spark.inbox)
            spark.why, spark.inbox = "", []
        spark.status = WORKING
        spark.activity = "Starting"
        spark.stop_asked = False
        spark.pending = {}
        _save(spark)
    _changed()

    # What System One holds this shift's tool calls up against, when it
    # reviews them: the spark's standing goal, its lines, and its jobs.
    from . import systemone  # deferred: avoids a module cycle

    systemone.set_request("\n".join(part for part in (
        f"{spark.name}'s standing goal: {spark.goal}",
        f"Lines it must not cross: {spark.boundaries}"
        if spark.boundaries else "",
        *(f"Job: {i['job']}" for i in inbox if i.get("job")),
    ) if part))

    steps: list[str] = list(resuming["steps"]) if resuming else []
    # The web chats that gave it a job this shift, for its report.
    chats = list(resuming.get("chats", [])) if resuming else list(
        dict.fromkeys(i["chat"] for i in inbox if i.get("chat"))
    )
    has_job = bool(chats) or any(i.get("job") for i in inbox) or bool(
        resuming and resuming.get("job")
    )
    notes = [resuming["notes"] if resuming else spark.notes]
    messages: list[dict] = list(resuming["messages"]) if resuming else []
    own = {
        "keep_notes": _keeping_notes(notes),
        "hand_off": _handing_off(spark),
        "ask_spark": _asking(spark),
    }
    # Every tool a sub-agent has, the ones that ask first too: in
    # autonomous mode they run, and otherwise the shift waits for the
    # user's answer.
    names = flash_tools.SPARK_TOOL_NAMES
    auto = autonomous()

    def gate(name: str) -> bool:
        # Sending email asks every time; the rest only out of
        # autonomous mode.
        return name in flash_tools.ALWAYS_ASK_TOOL_NAMES or (
            not auto and name in flash_tools.CONFIRMED_TOOL_NAMES
        )

    def record(kind: str, text: str, style: str) -> None:
        if kind == "line":
            steps.append(text)

    pending: dict = {}
    try:
        if not model_of(spark):
            raise RuntimeError("no model is set, so it could not run")
        if resuming:
            tool, args = resuming["tool"], resuming["args"]
            if resuming.get("answer") == "yes":
                # Said yes to already: asked again, it is a yes.
                with capture_tool_output(record), \
                        answer_from(lambda question: "y"):
                    result = flash_tools.run_tool((tool, args))
            else:
                result = (
                    "The user said no to this step"
                    + (f": {resuming['why']}" if resuming.get("why") else "")
                    + ". Do not try it again this shift. Carry on without "
                    "it, or say in your report what you would need."
                )
                steps.append(f"{resuming['label']} (you said no)")
            messages.append({
                "role": "tool", "tool_name": tool,
                "content": flash_tools.trim_tool_output(str(result), tool),
            })
            messages.extend(
                {"role": "tool", "tool_name": later, "content": NOT_RUN}
                for later, _ in resuming.get("later", [])
            )
        else:
            host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
            prompt = _prompt(
                spark, host, model_of(spark),
                flash_tools.CURRENT_DATE_PROMPT,
            )
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": _opening(why, inbox)},
            ]
        # In autonomous mode a tool that asks is told yes, the answer
        # the user gave by switching it on, however stale this process's
        # own setting is: nobody is here to answer, and asking would wait
        # forever.
        with answer_from(lambda question: "y") if auto \
                else contextlib.nullcontext():
            text = _work(
                spark, messages, own,
                [KEEP_NOTES_TOOL, HAND_OFF_TOOL, ASK_SPARK_TOOL],
                steps, lambda doing: _set_activity(spark.id, doing),
                shift_rounds(), ROUND_LIMIT_MESSAGE, client,
                names=names, gate=gate,
                stopping=lambda: bool(
                    getattr(find(spark.id), "stop_asked", 0)
                ),
            )
        quiet = not text or text.strip(" .").upper() == NOTHING_NEW
        if quiet and has_job:
            # A job asked for is answered, even with nothing to show.
            text, quiet = "Done, with nothing to report on it.", False
        report = Report(
            at=time.time(),
            text="Nothing new this shift." if quiet else text,
            quiet=quiet, read=quiet, steps=steps,
        )
    except NeedsApproval as need:
        label, detail = describe(need.name, need.arguments)
        pending = {
            "tool": need.name, "args": need.arguments, "label": label,
            "detail": detail, "later": [list(c) for c in need.later],
            "messages": messages, "steps": steps, "notes": notes[0],
            "at": time.time(), "answer": "", "why": "",
            "chats": chats, "job": has_job,
        }
        report = Report(
            at=time.time(), text=f"Waiting for your approval: {label}.",
            steps=steps, approval=True,
        )
    except Stopped:
        report = Report(
            at=time.time(), text="Stopped, as you asked, before it finished.",
            steps=steps, read=True,
        )
    except Exception as e:  # noqa: BLE001
        report = Report(
            at=time.time(),
            text=f"This shift failed: {e.__class__.__name__}: {e}",
            failed=True, steps=steps,
        )

    # The chats that asked get the report: the asking too, so they know
    # it waits on the user.
    report.chats = chats
    with _held():
        spark = find(spark_id)
        if spark is None:
            return report  # removed mid-shift: nowhere to file it
        spark.reports.append(report)
        del spark.reports[:-MAX_REPORTS]
        spark.notes = notes[0][:NOTES_CHARS]
        spark.activity = ""
        # Anything handed over while it worked is still to do: another
        # shift soon, not at its next time.
        spark.asked = bool(spark.inbox or spark.why)
        spark.stop_asked = False
        if pending:
            # The rest of the shift waits on the user, and so does the
            # schedule: nothing new starts over a question.
            spark.status = WAITING
            spark.pending = pending
        else:
            spark.status = FAILED if report.failed else IDLE
            spark.runs += 1
            spark.last_run = report.at
            spark.next_run = next_shift(spark, report.at)
        _save(spark)
    _changed()
    return report


def answer_step(key: str, yes: bool, why: str = "") -> Spark:
    """The user's answer to the step SPARK waits on: its shift carries
    on with it, soon."""

    def change(spark: Spark) -> None:
        if not spark.waiting:
            raise SparkError(f"{spark.name} is not waiting on anything.")
        spark.pending["answer"] = "yes" if yes else "no"
        spark.pending["why"] = " ".join(str(why or "").split())[:LESSON_CHARS]
        spark.asked = True
        for report in spark.reports:
            if report.approval:
                report.read = True

    spark = _edit(key, change)
    wake()
    return spark


def stop(key: str) -> Spark:
    """Stop SPARK's shift: the one running, at its next step, or the one
    waiting on an answer, now."""

    def change(spark: Spark) -> None:
        if spark.status == WAITING:
            spark.pending = {}
            spark.status = IDLE
            spark.reports.append(Report(
                at=time.time(), read=True,
                text="Called off while it waited for your approval.",
            ))
            for report in spark.reports:
                if report.approval:
                    report.read = True
            spark.last_run = time.time()
            spark.next_run = next_shift(spark, spark.last_run)
        elif spark.status == WORKING:
            spark.stop_asked = True
        else:
            raise SparkError(f"{spark.name} is not working on anything.")

    return _edit(key, change)


def _call(
    name: str, args: dict, allowed: tuple[str, ...],
    steps: list[str], own: dict[str, Callable[[dict], str]],
) -> str:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    if name in own:
        return own[name](args)
    if name == "reason":
        return "(noted)"
    if name not in allowed:
        steps.append(f"{name}() (not allowed)")
        return f"Unknown tool: {name}. Available: {', '.join(allowed)}."
    return flash_tools.run_tool((name, args))


# --- Chat ----------------------------------------------------------------
#
# A spark can be talked to between shifts: in the web UI as a chat of its
# own on the main screen, in the terminal with /sparks chat. Either way it
# answers as itself, with what it knows, and ChatKit is what lets it
# learn from what it is told and change its own goal or schedule.

# The tools a spark has in a chat, beside its own: a sub-agent's, all of
# them, since the user is there to answer the ones that ask first.
def chat_tool_names() -> tuple[str, ...]:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    return flash_tools.SPARK_TOOL_NAMES


def _until(when: float) -> str:
    minutes = int((when - time.time()) // 60)
    if minutes <= 0:
        return "any moment now"
    if minutes < 90:
        return f"in {minutes} minutes"
    if minutes < 2880:
        return f"in about {round(minutes / 60)} hours"
    return f"in about {round(minutes / 1440)} days"


def status_block(spark: Spark) -> str:
    """SPARK as it is right now, for it to know when it is talked to:
    what it is doing, when it last worked and next will, and what it is
    set up with."""

    if spark.status == WORKING:
        doing = "In the middle of a shift"
        if spark.activity:
            doing += f" ({spark.activity})"
        if spark.stop_asked:
            doing += "; the user asked it to stop at its next step"
    elif spark.waiting:
        detail = " ".join(str(spark.pending.get("detail", "")).split())
        doing = (
            "Stopped part way through a shift, waiting for the user to "
            f"approve a step: {spark.pending.get('label', 'a step')}"
            + (f" ({detail[:200]})" if detail else "")
            + ". You cannot approve it yourself: if they want it, they "
            f"press Approve in your window, or type /sparks approve "
            f"{spark.handle[1:].removesuffix('-spark')}."
        )
    elif spark.status == WAITING:
        doing = "About to carry on with a step the user just answered"
    elif spark.paused:
        doing = "Paused: no shifts run until the user resumes you"
    else:
        doing = "Idle, between shifts"

    lines = ["=== Your status right now ===", f"- {doing}."]
    if spark.last_run:
        last = next((r for r in reversed(spark.reports)), None)
        how = " and it failed" if last and last.failed else ""
        lines.append(f"- Last shift: {_since(spark.last_run)}{how}.")
    else:
        lines.append("- You have not run a shift yet.")
    if not spark.paused and not spark.waiting and spark.status != WORKING:
        when = "right away" if spark.asked else _until(spark.next_run)
        lines.append(f"- Next shift: {when}.")
    lines.append(
        f"- Shifts so far: {spark.runs}. Reports the user has not read: "
        f"{spark.unread}."
    )
    if spark.inbox:
        lines.append(
            f"- Handed to you by other sparks, for your next shift: "
            f"{len(spark.inbox)} note{'' if len(spark.inbox) == 1 else 's'}."
        )
    lines.append(f"- You run on the model {model_of(spark) or '(none set)'}.")
    found = project_of(spark)
    if found:
        lines.append(f"- You work on the project {found.name}.")
    if spark.watch:
        lines.append(
            f"- A change in {spark.watch} starts a shift too."
        )
    return "\n".join(lines)


def chat_prompt(
    spark: Spark, host: str, model: str, date: str, project: bool = True,
) -> str:
    """The system prompt for talking with SPARK. PROJECT False leaves its
    project out, for a web chat that adds the project itself."""

    lessons = "\n".join(f"- {lesson}" for lesson in spark.lessons)
    reports = "\n\n".join(
        f"[{time.strftime('%a %d %b %H:%M', time.localtime(r.at))}] "
        f"{r.text}"
        for r in [r for r in spark.reports if not r.quiet][-5:]
    )
    body = CHAT_PROMPT.format(
        name=spark.name,
        handle=spark.handle,
        titled=_titled(spark),
        every=schedule_words(spark),
        goal=spark.goal,
        boundaries=spark.boundaries or "(none beyond your usual care)",
        lessons=lessons or "(nothing yet)",
        notes=spark.notes or "(none yet)",
        reports=reports or "(none yet: you have not reported anything)",
    )
    parts = [
        get_model_system_prompt(host, model), body, status_block(spark),
        project_block(spark) if project else "", team_block(spark), date,
    ]
    return "\n\n".join(part for part in parts if part)


class ChatKit:
    """A spark's own tools in a chat, and what they changed.

    Changes are kept here while the answer is made, and written to the
    spark in one go by apply(), so a shift finishing meanwhile does not
    lose them or have its own undone.
    """

    schemas = [
        KEEP_NOTES_TOOL, HAND_OFF_TOOL, ASK_SPARK_TOOL, TAKE_ON_TOOL,
        *CHAT_TOOLS,
    ]

    def __init__(self, spark: Spark, chat: str = "") -> None:
        self.spark_id = spark.id
        # The web chat it is talking in, for a job's report to come back
        # to; "" in the terminal, where it goes to its reports.
        self.chat = chat
        self.notes = [spark.notes]
        self.first_notes = spark.notes
        self.lessons: list[str] = []
        self.changes: dict = {}
        self.tools: dict[str, Callable[[dict], str]] = {
            "keep_notes": _keeping_notes(self.notes),
            "learn": self._learn,
            "set_goal": self._set_goal,
            "set_schedule": self._set_schedule,
            "hand_off": _handing_off(spark),
            "ask_spark": _asking(spark),
            "take_on": self._take_on,
        }

    def _take_on(self, args: dict) -> str:
        try:
            take_on(self.spark_id, str(args.get("job", "")), self.chat)
        except SparkError as exc:
            return f"Error: {exc}"
        job = " ".join(str(args.get("job", "")).split())
        tool_line(f"TakeOn({job[:120]})")
        where = "in this chat" if self.chat else "in your reports"
        tool_result(f"A shift starts now; the report comes back {where}")
        return (
            f"(taken on: a shift starts now, and your report on it comes "
            f"back {where})"
        )

    def offer(self, tools: list[dict]) -> list[dict]:
        """Of TOOLS, the ones a spark may use here, and its own."""

        names = chat_tool_names()
        return [
            t for t in tools if t["function"]["name"] in names
        ] + self.schemas

    def _learn(self, args: dict) -> str:
        lesson = " ".join(str(args.get("lesson", "")).split())
        if not lesson:
            return "Error: the lesson was empty."
        self.lessons.append(lesson[:LESSON_CHARS])
        tool_line(f"Learn({lesson})")
        tool_result("Kept: every later shift will follow it")
        return "(kept: every later shift will follow it)"

    def _set_goal(self, args: dict) -> str:
        goal = str(args.get("goal", "")).strip()[:GOAL_CHARS]
        if not goal:
            return "Error: the goal was empty."
        self.changes["goal"] = goal
        tool_line(f"SetGoal({goal})")
        return "(goal changed)"

    def _set_schedule(self, args: dict) -> str:
        try:
            every, at = parse_schedule(args.get("every", ""))
        except SparkError as exc:
            return f"Error: {exc}"
        self.changes["every"] = (every, at)
        words = schedule_words({"every": every, "at": at})
        tool_line(f"SetSchedule({words})")
        return f"(now {words})"

    def apply(self, add: Optional[Message] = None) -> Optional[Spark]:
        """Write what changed to the spark, with ADD put in its chat."""

        with _held():
            spark = find(self.spark_id)
            if spark is None:
                return None
            if add is not None:
                spark.chat.append(add)
                del spark.chat[:-MAX_CHAT]
                spark.replying = 0.0
                spark.reply_activity = ""
            if self.notes[0] != self.first_notes:
                spark.notes = self.notes[0][:NOTES_CHARS]
            for lesson in self.lessons:
                _add_lesson(spark, lesson)
            if "goal" in self.changes:
                spark.goal = self.changes["goal"]
            if "every" in self.changes:
                _schedule(spark, *self.changes["every"])
            _save(spark)
        _changed()
        return spark


# @scout, or @scout-spark, standing on its own: not the middle of an
# address (me@scout.com), nor the start of a path (@scout/notes.md).
_MENTION_RE = re.compile(r"(?<![\w@/.])@([a-z0-9][a-z0-9-]*)(?![\w./-])", re.I)

MENTIONED_PROMPT = """
=== You were mentioned ===
The user @mentioned you in a conversation they are having with {host}.
You are shown its latest part, then what they said to you. Answer that,
as yourself: the conversation is theirs and {host}'s, so do not answer
what was said to {host}, and do not speak for it.
""".strip()

# How much of a conversation a spark @mentioned into it is shown: the
# latest messages, each cut to a length, so a long chat costs a small
# model little.
HISTORY_MESSAGES = 12
HISTORY_CHARS = 1500

# What starts the note a conversation keeps when a spark answers in it.
CALLED = "[Spark called]"


def mentioned(text: str) -> list[Spark]:
    """The sparks TEXT @mentions, in the order it first names them."""

    found: list[Spark] = []
    for match in _MENTION_RE.finditer(text or ""):
        spark = find(match.group(1))
        if spark is not None and all(s.id != spark.id for s in found):
            found.append(spark)
    return found


def called_note(
    spark: Spark, text: str, used: list[str], shift: bool = False,
) -> str:
    """What a conversation keeps of a spark's answer in it: who was
    called, what it did, and what it said. Kept as a note, not as a
    reply, so whoever the chat is with sees it was not theirs. SHIFT:
    the report of a job the user gave it in this chat, done since."""

    if shift:
        return (
            f"{CALLED} {spark.name} ({spark.handle}), one of the user's "
            "sparks, finished the job the user gave it in this chat, in a "
            f"shift of its own. Its report:\n{text}"
        )
    tools = f", using {', '.join(dict.fromkeys(used))}" if used else ""
    return (
        f"{CALLED} The user @mentioned {spark.name} ({spark.handle}), one "
        f"of their sparks, in this chat. It answered{tools}:\n{text}"
    )


def _cut(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > HISTORY_CHARS:
        text = text[:HISTORY_CHARS].rstrip() + " [...]"
    return text


def guest_view(messages: list[dict], host: str) -> str:
    """The conversation a spark was @mentioned into, as it is shown to
    it: the latest part, who said what, then what was said to it, and
    what any spark called before it answered.

    MESSAGES are the chat's, the user's message to it among them.
    """

    asked_at = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"),
        default=len(messages),
    )
    asked = messages[asked_at]["content"] if asked_at < len(messages) else ""

    def line(message: dict) -> str:
        role, text = message.get("role"), message.get("content") or ""
        if not str(text).strip():
            return ""
        if role == "user":
            return f"User: {_cut(text)}"
        if role == "assistant":
            return f"{host}: {_cut(text)}"
        if role == "system" and str(text).startswith(CALLED):
            return f"({_cut(text[len(CALLED):])})"
        return ""  # tool results and the like: the work, not the talk

    before = [ln for ln in map(line, messages[:asked_at]) if ln]
    before = before[-HISTORY_MESSAGES:]
    after = [ln for ln in map(line, messages[asked_at + 1:]) if ln]
    parts = []
    if before:
        parts.append(
            f"=== The conversation so far: its latest {len(before)} "
            "messages ===\n" + "\n\n".join(before)
            + "\n=== End of the conversation so far ==="
        )
    parts.append(f"The user now says, to you:\n{asked}")
    if after:
        parts.append(
            "Already answered by others it named:\n" + "\n\n".join(after)
        )
    return "\n\n".join(parts)


def ask(key: str, text: str) -> Spark:
    """Put what the user said in SPARK's chat, and mark it answering.

    The answer is answer()'s to make. Refused while the last answer is
    still on its way, so the two never cross.
    """

    text = str(text or "").strip()[:MESSAGE_CHARS]
    if not text:
        raise SparkError("Say something first.")

    def change(spark: Spark) -> None:
        if spark.answering:
            raise SparkError(f"{spark.name} is still answering.")
        spark.chat.append(Message(at=time.time(), who="you", text=text))
        del spark.chat[:-MAX_CHAT]
        spark.replying = time.time()
        spark.reply_activity = "Thinking"

    return _edit(key, change)


def answer(spark_id: str, client=None) -> Optional[Message]:
    """SPARK_ID's answer to the chat so far, made now on this thread and
    put in its chat: the terminal's chat. None if the spark is gone."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    spark = find(spark_id)
    if spark is None:
        return None

    steps: list[str] = []
    kit = ChatKit(spark)
    host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
    try:
        if not model_of(spark):
            raise RuntimeError("no model is set, so it could not run")
        messages: list[dict] = [{
            "role": "system",
            "content": chat_prompt(
                spark, host, model_of(spark),
                flash_tools.CURRENT_DATE_PROMPT,
            ),
        }] + [
            {
                "role": "user" if m.who == "you" else "assistant",
                "content": m.text,
            }
            for m in spark.chat[-CHAT_CONTEXT:]
            if not m.failed
        ]
        text = _work(
            spark, messages, kit.tools, kit.schemas, steps,
            lambda doing: _set_activity(spark.id, doing, "reply_activity"),
            MAX_CHAT_ROUNDS, CHAT_LAST_WORD, client,
        )
        reply = Message(
            at=time.time(), who="spark",
            text=text or "(I have nothing to say to that.)", steps=steps,
        )
    except Exception as e:  # noqa: BLE001
        reply = Message(
            at=time.time(), who="spark", steps=steps, failed=True,
            text=f"I could not answer: {e.__class__.__name__}: {e}",
        )

    kit.apply(add=reply)
    return reply


def say(key: str, text: str, client=None) -> Optional[Message]:
    """Say TEXT to a spark and wait for its answer: the terminal's way."""

    return answer(ask(key, text).id, client)


# --- The keeper ----------------------------------------------------------
#
# Every Flash that is open runs a keeper, and so does `flash --sparks`,
# the one the operating system starts at login when sparks are always
# on (see flash/keepalive.py). Only one of them may run shifts, or two
# would run the same spark at once: whichever holds the lock on
# .keeper.lock does, and the rest keep trying for it, so when the one
# holding it quits, another carries on. The holder also writes
# .keeper.json every tick, which is how anyone can tell a keeper is
# running and whether it is the always-on one.
#
# Every keeper, holding the lock or not, watches the folder: a spark
# another process changed is news for this one's page too.

_keeper: Optional[threading.Thread] = None
_nudge = threading.Event()
_floor = None  # the open lock file, while this process holds it

HEARTBEAT = ".keeper.json"
LOCK = ".keeper.lock"

# A heartbeat older than this belongs to a keeper that is gone.
STALE_SECONDS = TICK_SECONDS * 4

# How many ticks apart an always-on keeper checks it is still wanted.
CHECK_EVERY_TICKS = 12


def due(now: Optional[float] = None) -> list[Spark]:
    now = time.time() if now is None else now
    return [
        s for s in all_sparks()
        if s.status != WORKING and not s.waiting
        and (s.asked or (
            s.status != WAITING and not s.paused and s.next_run <= now
        ))
    ]


def run_now(key: str) -> Spark:
    """Put a spark's next shift ahead of its schedule.

    Asked for in its file, so the keeper doing the work hears of it
    whichever process that is.
    """

    spark = _edit(key, lambda s: setattr(s, "asked", True))
    wake()
    return spark


def wake() -> None:
    _nudge.set()


def _take_floor() -> bool:
    """Whether this process holds the keeper's lock, taking it if free."""

    global _floor

    if _floor is not None:
        return True
    folder = sparks_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handle = open(folder / LOCK, "a+b")  # held open while it holds
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _floor = handle
    return True


def _give_floor() -> None:
    global _floor

    if _floor is None:
        return
    handle, _floor = _floor, None  # the beat stops with it
    with contextlib.suppress(OSError):
        (sparks_dir() / HEARTBEAT).unlink(missing_ok=True)
        handle.close()  # closing it lets the lock go


def _beat(always: bool) -> None:
    """Say a keeper is here for as long as this process holds the lock,
    on a thread of its own, since one shift can outlast many ticks."""

    beat = {
        "pid": os.getpid(), "always": always, "since": time.time(),
        **code_identity(),
    }
    held = _floor

    def keep_beating() -> None:
        while _floor is held and held is not None:
            beat["beat"] = time.time()
            with contextlib.suppress(OSError):
                temp = sparks_dir() / ".keeper.tmp"
                temp.write_text(json.dumps(beat), encoding="utf-8")
                os.replace(temp, sparks_dir() / HEARTBEAT)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=keep_beating, daemon=True).start()


def keeper() -> Optional[dict]:
    """The keeper running shifts now, if any: its pid, whether it is the
    always-on one, and since when."""

    try:
        beat = json.loads(
            (sparks_dir() / HEARTBEAT).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(beat, dict):
        return None
    if time.time() - float(beat.get("beat") or 0) > STALE_SECONDS:
        return None
    # Gone since its last beat: no keeper, though the beat looks fresh.
    if not alive(beat.get("pid")):
        return None
    return beat


def alive(pid) -> bool:
    """Whether process PID is running. Unknowable on Windows from here
    without risk, so there it is taken to be."""

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)  # signal 0: asks, and sends nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _signature() -> tuple:
    """What the folder holds, cheaply: enough to see that it changed."""

    try:
        return tuple(sorted(
            (p.name, p.stat().st_mtime_ns, p.stat().st_size)
            for p in _spark_files()
        ))
    except OSError:
        return ()


_watched: dict[str, tuple[str, dict]] = {}


def _snapshot(folder: str) -> dict:
    """Each file under FOLDER and when it last changed, up to a limit."""

    seen: dict = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [
            d for d in dirs if d not in WATCH_SKIP and not d.startswith(".")
        ]
        for name in files:
            path = os.path.join(root, name)
            with contextlib.suppress(OSError):
                seen[path] = os.stat(path).st_mtime_ns
            if len(seen) >= WATCH_FILES:
                return seen
    return seen


def _changes(before: dict, now: dict, folder: str) -> list[str]:
    changed = [
        p for p in now.keys() | before.keys() if before.get(p) != now.get(p)
    ]
    return sorted(os.path.relpath(p, folder) for p in changed)


def watch_tick(now: Optional[float] = None) -> list[Spark]:
    """Start a shift of each spark whose watched folder changed. The
    first look at a folder only learns it. The sparks it started."""

    now = time.time() if now is None else now
    started = []
    for spark in all_sparks():
        if not spark.watch or not os.path.isdir(spark.watch):
            _watched.pop(spark.id, None)
            continue
        folder = spark.watch
        known = _watched.get(spark.id)
        if known is None or known[0] != folder:
            _watched[spark.id] = (folder, _snapshot(folder))
            continue
        if spark.paused or spark.status in (WORKING, WAITING) or spark.asked:
            continue
        if now - spark.last_run < WATCH_SETTLE_SECONDS:
            continue  # the changes wait, to be seen after it settles
        latest = _snapshot(folder)
        changed = _changes(known[1], latest, folder)
        if not changed:
            continue
        _watched[spark.id] = (folder, latest)
        shown = ", ".join(changed[:8])
        more = f", and {len(changed) - 8} more" if len(changed) > 8 else ""

        def ask(s: Spark, shown=shown, more=more) -> None:
            s.why = f"files changed in {folder}: {shown}{more}"
            s.asked = True

        started.append(_edit(spark.id, ask))
    return started


def _code_stamp() -> tuple:
    """Flash's own code as it is on disk now: every module's name, size
    and time, to tell an update by."""

    here = Path(__file__).resolve().parent
    stamp = []
    for path in sorted(here.rglob("*.py")):
        with contextlib.suppress(OSError):
            found = path.stat()
            stamp.append((str(path), found.st_size, found.st_mtime_ns))
    return tuple(stamp)


# The code this process runs: what it loaded at start.
_RUNNING_CODE = _code_stamp()


def _fingerprint(stamp: tuple) -> str:
    return hashlib.sha256(repr(stamp).encode("utf-8")).hexdigest()[:16]


def code_identity() -> dict:
    """Which Flash this process is: where its code is, and a short
    fingerprint of that code as it loaded it."""

    return {
        "code": str(Path(__file__).resolve().parent),
        "stamp": _fingerprint(_RUNNING_CODE),
    }


def disk_stamp() -> str:
    """The fingerprint of this Flash's code as it is on disk now, which
    a process started now would run."""

    return _fingerprint(_code_stamp())


def _keep(
    always: bool = False,
    prepare: Optional[Callable[[], None]] = None,
    announce: Optional[Callable[[Spark, Report], None]] = None,
    stop: Optional[threading.Event] = None,
    wanted: Optional[Callable[[], bool]] = None,
    renew: bool = False,
) -> bool:
    """The keeper's loop, until STOP is set or WANTED says no.

    ALWAYS marks the keeper started at login. PREPARE runs before each
    shift, to pick up settings changed since; ANNOUNCE hears each report
    worth telling someone about. WANTED is asked now and then, so a
    keeper whose login entry was taken away does not run on until
    logout. RENEW ends it, between shifts, once Flash's code on disk is
    not what it is running: True then, for it to start again on the new
    code. A keeper that runs from login would otherwise run every shift
    on the Flash it started with, whatever has been updated since.
    """

    seen = _signature()
    holding = False
    ticks = 0
    # The code seen at the last look: an update is acted on once it has
    # held still for a look, not while its files are still being written.
    looked = _RUNNING_CODE
    while stop is None or not stop.is_set():
        ticks += 1
        if wanted is not None and ticks % CHECK_EVERY_TICKS == 0:
            if not wanted():
                break
        if renew and ticks % CHECK_EVERY_TICKS == 0:
            now_code = _code_stamp()
            if now_code != _RUNNING_CODE and now_code == looked:
                if holding:
                    _give_floor()
                return True
            looked = now_code
        if not holding and _take_floor():
            holding = True
            _beat(always)
            # A spark left working belongs to a keeper that quit mid-shift:
            # holding the lock, this one knows no other is running it.
            for spark in all_sparks():
                if spark.status == WORKING:
                    _edit(spark.id, lambda s: setattr(s, "status", IDLE))
        if holding:
            if ticks % WATCH_EVERY_TICKS == 0:
                with contextlib.suppress(OSError):
                    watch_tick()
            for spark in due():
                _safe_shift(spark.id, prepare, announce)

        now = _signature()
        if now != seen:
            seen = now
            _changed()

        _nudge.wait(TICK_SECONDS)
        _nudge.clear()
    if holding:
        _give_floor()
    return False


def _safe_shift(
    spark_id: str,
    prepare: Optional[Callable[[], None]] = None,
    announce: Optional[Callable[[Spark, Report], None]] = None,
) -> None:
    # A disk that fails mid-shift costs that shift, not the keeper: the
    # rest go on, and this one is tried again when it next comes due.
    with contextlib.suppress(OSError):
        if prepare is not None:
            prepare()
        report = shift(spark_id)
        spark = find(spark_id)
        if announce and report and spark and not report.quiet:
            announce(spark, report)


def start() -> None:
    """Start the keeper, once per process, beside the terminal or web UI."""

    global _keeper

    with _lock:
        if _keeper is not None and _keeper.is_alive():
            return
        _keeper = threading.Thread(target=_keep, daemon=True, name="sparks")
        _keeper.start()


def serve(
    prepare: Optional[Callable[[], None]] = None,
    announce: Optional[Callable[[Spark, Report], None]] = None,
    wanted: Optional[Callable[[], bool]] = None,
) -> bool:
    """Keep sparks working with nothing else open: `flash --sparks`.

    Runs until interrupted, or until WANTED says it is not any more.
    Started at login when sparks are always on, it waits its turn while
    an open Flash holds the lock. True when it ended because Flash was
    updated, to be started again on the new code.
    """

    try:
        return _keep(
            always=True, prepare=prepare, announce=announce, wanted=wanted,
            renew=True,
        )
    finally:
        _give_floor()
