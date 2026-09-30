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
from .dashes import undash
from .paths import FLASH_DIR
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
GOAL_CHARS = 2000
BOUNDARY_CHARS = 1000
LESSON_CHARS = 400
NOTES_CHARS = 4000

MAX_LESSONS = 30
MAX_REPORTS = 40
MAX_STEPS = 40
MAX_SHIFT_ROUNDS = 12

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
You are {name} ({handle}), a spark: an agent that works on one standing
goal for this user, on a schedule, while they get on with other things.
This is one of your shifts. Nobody is watching it and nobody can answer
a question, so work on your own, then write your report.

Your goal:
{goal}

Stay inside these boundaries, whatever the goal seems to need:
{boundaries}

What the user has told you about how to do this (always follow it):
{lessons}

Your notes from last time, written by you for you:
{notes}

Your last report:
{last}

This shift runs every {every}; the last one was {since}.

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
You are {name} ({handle}), a spark: an agent that works on one standing
goal for this user, on a schedule, every {every}. Right now the user is
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
        "every", "How often: 30m, 2h, daily, weekly. At least 15m.",
    ),
]


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
    # It stopped to ask: the user's yes or no carries it on.
    approval: bool = False


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
    boundaries: str = ""
    every: int = DEFAULT_EVERY_MINUTES
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
        return data


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


def parse_every(text) -> int:
    """Minutes between shifts, from "30m", "2 hours", "daily" or 45."""

    if isinstance(text, (int, float)) and not isinstance(text, bool):
        minutes = int(text)
    else:
        words = str(text or "").strip().lower()
        if not words:
            return DEFAULT_EVERY_MINUTES
        if words.isdigit():
            minutes = int(words)
        elif words in _WORDS or words.removeprefix("every ") in _WORDS:
            minutes = _WORDS[words.removeprefix("every ")]
        else:
            match = _EVERY_RE.match(words)
            if not match or match.group(2) not in _UNITS:
                raise SparkError(
                    f"Could not read {text!r} as a schedule. Try 30m, 2h, "
                    "daily, or weekly."
                )
            count = float(match.group(1) or 1)
            minutes = int(count * _UNITS[match.group(2)])

    if minutes < MIN_EVERY_MINUTES:
        raise SparkError(
            f"A spark runs at most every {MIN_EVERY_MINUTES} minutes."
        )
    return min(minutes, MAX_EVERY_MINUTES)


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
        "blurb": "Tells you when a web page changes",
        "goal": (
            "Fetch the page at the address in your lessons and tell me when "
            "what it says has changed since last time, and how. Keep what it "
            "said in your notes. If there is no address, ask me for one."
        ),
        "every": 360,
        "boundaries": "Only read. Never fill in or submit anything.",
    },
]

SHARE_PREFIX = "flash-spark:"


def share_code(key: str) -> str:
    """SPARK as a code to paste somewhere: its template, no more."""

    spark = _must_find(key)
    data = {
        "v": 1, "name": spark.name, "goal": spark.goal,
        "boundaries": spark.boundaries, "every": spark.every,
        "lessons": spark.lessons,
    }
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
    return {
        "name": " ".join(str(data.get("name") or "Spark").split())[
            :NAME_CHARS],
        "goal": str(data["goal"]).strip()[:GOAL_CHARS],
        "boundaries": str(data.get("boundaries") or "").strip()[
            :BOUNDARY_CHARS],
        "every": parse_every(data.get("every") or DEFAULT_EVERY_MINUTES),
        "lessons": [
            " ".join(str(lesson).split())[:LESSON_CHARS]
            for lesson in lessons[:MAX_LESSONS] if str(lesson).strip()
        ] if isinstance(lessons, list) else [],
    }


def add_from(source: str, project: str = "", model: str = "") -> Spark:
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
        name, data["goal"], data.get("boundaries", ""), data["every"],
        project, model=model,
    )
    lessons = data.get("lessons") or []
    if lessons:
        spark = _edit(spark.id, lambda s: s.lessons.extend(lessons))
    return spark


def create(
    name: str, goal: str, boundaries: str = "", every="", project: str = "",
    watch: str = "", model: str = "",
) -> Spark:
    """Make a spark. Its first shift runs as soon as the keeper looks."""

    name = " ".join(str(name or "").split())[:NAME_CHARS]
    goal = str(goal or "").strip()[:GOAL_CHARS]
    if not name:
        raise SparkError("A spark needs a name.")
    if not goal:
        raise SparkError("A spark needs a goal.")
    minutes = parse_every(every)
    project = resolve_project(project)
    watch = _watch_folder(watch)

    with _held():
        taken = {s.handle for s in all_sparks()}
        if handle_of(name) in taken:
            raise SparkError(f"There is already a spark called {name}.")
        spark = Spark(
            id=uuid.uuid4().hex[:8],
            name=name,
            goal=goal,
            boundaries=str(boundaries or "").strip()[:BOUNDARY_CHARS],
            every=minutes,
            colour=COLOURS[len(taken) % len(COLOURS)],
            project=project,
            watch=watch,
            model=_model_name(model),
        )
        _save(spark)
    _changed()
    wake()
    return spark


def update(key: str, **changes) -> Spark:
    """Change a spark's goal, boundaries, schedule, or name."""

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
            spark.every = parse_every(changes["every"])
            if spark.last_run:
                spark.next_run = spark.last_run + spark.every * 60
        if "project" in changes:
            spark.project = resolve_project(changes["project"])
        if "watch" in changes:
            spark.watch = _watch_folder(changes["watch"])
        if "model" in changes:
            spark.model = _model_name(changes["model"])
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
        goal=spark.goal,
        boundaries=spark.boundaries or "(none beyond your usual care)",
        lessons=lessons or "(nothing yet)",
        notes=spark.notes or "(none yet)",
        last=last or "(none yet)",
        every=every_words(spark.every),
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
    if name in ("edit", "multi_edit"):
        return f"Change {path}", json.dumps(
            {k: v for k, v in args.items() if k not in ("file_path", "path")},
            ensure_ascii=False, indent=1,
        )[:1200]
    return name, json.dumps(args, ensure_ascii=False)[:1200]


def _asks_first(name: str) -> bool:
    """Whether a step waits for the user's yes: the ones Flash asks
    about in a chat, unless autonomous mode is on."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    return (
        not flash_tools.NO_COMMAND_CONFIRMATION
        and name in flash_tools.CONFIRMED_TOOL_NAMES
    )


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


def team_block(spark: Spark) -> str:
    """The other sparks, for SPARK to hand work to; "" if there are none."""

    others = [s for s in all_sparks() if s.id != spark.id]
    if not others:
        return ""
    lines = ["=== The other sparks: hand_off sends one work ==="]
    for other in others:
        first = other.goal.splitlines()[0][:120]
        lines.append(f"- {other.name} ({other.handle}): {first}")
    return "\n".join(lines)


def _work(
    spark: Spark,
    messages: list[dict],
    own: dict[str, Callable[[dict], str]],
    own_tools: list[dict],
    steps: list[str],
    doing: Callable[[str], None],
    rounds: int,
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
    schemas = [
        t for t in flash_tools.tools if t["function"]["name"] in allowed
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
        for _ in range(rounds):
            if stopping is not None and stopping():
                raise Stopped()
            doing("Thinking")
            response = client.chat(
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
        response = client.chat(
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


def _opening(why: str, inbox: list) -> str:
    """What a shift is told first: to start, and why it started now."""

    parts = ["Start your shift."]
    if why:
        parts.append(f"It started early because: {why}")
    if inbox:
        parts.append("Handed to you by other sparks:\n" + "\n".join(
            f"- From {item.get('from', 'a spark')}: {item.get('text', '')}"
            for item in inbox
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
        why, inbox = spark.why, list(spark.inbox)
        spark.status = WORKING
        spark.activity = "Starting"
        spark.stop_asked = False
        spark.why, spark.inbox, spark.pending = "", [], {}
        _save(spark)
    _changed()

    steps: list[str] = list(resuming["steps"]) if resuming else []
    notes = [resuming["notes"] if resuming else spark.notes]
    messages: list[dict] = list(resuming["messages"]) if resuming else []
    own = {
        "keep_notes": _keeping_notes(notes),
        "hand_off": _handing_off(spark),
    }
    names = subagents.allowed_tool_names()
    if not flash_tools.NO_COMMAND_CONFIRMATION:
        # The ones that ask first too: a shift waits for the answer.
        names = flash_tools.SUBAGENT_TOOL_NAMES

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
        text = _work(
            spark, messages, own, [KEEP_NOTES_TOOL, HAND_OFF_TOOL], steps,
            lambda doing: _set_activity(spark.id, doing),
            MAX_SHIFT_ROUNDS, ROUND_LIMIT_MESSAGE, client,
            names=names, gate=_asks_first,
            stopping=lambda: bool(getattr(find(spark.id), "stop_asked", 0)),
        )
        quiet = not text or text.strip(" .").upper() == NOTHING_NEW
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

    with _held():
        spark = find(spark_id)
        if spark is None:
            return report  # removed mid-shift: nowhere to file it
        spark.reports.append(report)
        del spark.reports[:-MAX_REPORTS]
        spark.notes = notes[0][:NOTES_CHARS]
        spark.activity = ""
        spark.asked = False
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
            spark.next_run = report.at + spark.every * 60
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
            spark.next_run = spark.last_run + spark.every * 60
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

    return flash_tools.SUBAGENT_TOOL_NAMES


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
        every=every_words(spark.every),
        goal=spark.goal,
        boundaries=spark.boundaries or "(none beyond your usual care)",
        lessons=lessons or "(nothing yet)",
        notes=spark.notes or "(none yet)",
        reports=reports or "(none yet: you have not reported anything)",
    )
    parts = [
        get_model_system_prompt(host, model), body,
        project_block(spark) if project else "", team_block(spark), date,
    ]
    return "\n\n".join(part for part in parts if part)


class ChatKit:
    """A spark's own tools in a chat, and what they changed.

    Changes are kept here while the answer is made, and written to the
    spark in one go by apply(), so a shift finishing meanwhile does not
    lose them or have its own undone.
    """

    schemas = [KEEP_NOTES_TOOL, HAND_OFF_TOOL, *CHAT_TOOLS]

    def __init__(self, spark: Spark) -> None:
        self.spark_id = spark.id
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
        }

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
            every = parse_every(args.get("every", ""))
        except SparkError as exc:
            return f"Error: {exc}"
        self.changes["every"] = every
        tool_line(f"SetSchedule(every {every_words(every)})")
        return f"(now every {every_words(every)})"

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
                spark.every = self.changes["every"]
                if spark.last_run:
                    spark.next_run = spark.last_run + spark.every * 60
            _save(spark)
        _changed()
        return spark


# @scout, or @scout-spark, standing on its own: not the middle of an
# address (me@scout.com), nor the start of a path (@scout/notes.md).
_MENTION_RE = re.compile(r"(?<![\w@/.])@([a-z0-9][a-z0-9-]*)(?![\w./-])", re.I)

MENTIONED_PROMPT = """
=== You were mentioned ===
The user @mentioned you in a conversation they are having with {host}.
Earlier replies that start with a name in brackets came from that spark;
the others came from {host}. Answer what the user asked of you, as
yourself, in this conversation.
""".strip()


def mentioned(text: str) -> list[Spark]:
    """The sparks TEXT @mentions, in the order it first names them."""

    found: list[Spark] = []
    for match in _MENTION_RE.finditer(text or ""):
        spark = find(match.group(1))
        if spark is not None and all(s.id != spark.id for s in found):
            found.append(spark)
    return found


def said_by(spark: Spark, text: str) -> str:
    """A spark's reply as the rest of a conversation keeps it: marked,
    so no one takes it for their own."""

    return f"[{spark.name} ({spark.handle}), one of the user's sparks]\n{text}"


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

    beat = {"pid": os.getpid(), "always": always, "since": time.time()}
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
    return beat


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


def _keep(
    always: bool = False,
    prepare: Optional[Callable[[], None]] = None,
    announce: Optional[Callable[[Spark, Report], None]] = None,
    stop: Optional[threading.Event] = None,
    wanted: Optional[Callable[[], bool]] = None,
) -> None:
    """The keeper's loop, until STOP is set or WANTED says no.

    ALWAYS marks the keeper started at login. PREPARE runs before each
    shift, to pick up settings changed since; ANNOUNCE hears each report
    worth telling someone about. WANTED is asked now and then, so a
    keeper whose login entry was taken away does not run on until
    logout.
    """

    seen = _signature()
    holding = False
    ticks = 0
    while stop is None or not stop.is_set():
        ticks += 1
        if wanted is not None and ticks % CHECK_EVERY_TICKS == 0:
            if not wanted():
                break
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
) -> None:
    """Keep sparks working with nothing else open: `flash --sparks`.

    Runs until interrupted, or until WANTED says it is not any more.
    Started at login when sparks are always on, it waits its turn while
    an open Flash holds the lock.
    """

    try:
        _keep(
            always=True, prepare=prepare, announce=announce, wanted=wanted,
        )
    finally:
        _give_floor()
