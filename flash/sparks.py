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
change. One keeper thread runs the shifts one at a time: the model is
usually local, and two shifts at once would only make both slow.
"""

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
from .theme import ERROR, capture_tool_output

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

# How often a spark may run. Every shift is a full agent run against the
# model, so the floor keeps a spark from hogging it.
MIN_EVERY_MINUTES = 15
MAX_EVERY_MINUTES = 7 * 24 * 60
DEFAULT_EVERY_MINUTES = 60

# How often the keeper looks for a spark that is due.
TICK_SECONDS = 20.0

IDLE = "idle"
WORKING = "working"
FAILED = "failed"

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

    @property
    def handle(self) -> str:
        return handle_of(self.name)

    @property
    def unread(self) -> int:
        return sum(1 for r in self.reports if not r.read and not r.quiet)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["handle"] = self.handle
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
        found = [_load(p) for p in folder.glob("*.json")]
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
    with _lock:
        spark = _must_find(key)
        change(spark)
        _save(spark)
    _changed()
    return spark


def create(
    name: str, goal: str, boundaries: str = "", every="",
) -> Spark:
    """Make a spark. Its first shift runs as soon as the keeper looks."""

    name = " ".join(str(name or "").split())[:NAME_CHARS]
    goal = str(goal or "").strip()[:GOAL_CHARS]
    if not name:
        raise SparkError("A spark needs a name.")
    if not goal:
        raise SparkError("A spark needs a goal.")
    minutes = parse_every(every)

    with _lock:
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
    with _lock:
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


def teach(key: str, lesson: str, report_at: Optional[float] = None) -> Spark:
    """Keep LESSON for every later shift, and hang it on a report."""

    lesson = " ".join(str(lesson or "").split())[:LESSON_CHARS]
    if not lesson:
        raise SparkError("Say what it should do differently.")

    def change(spark: Spark) -> None:
        spark.lessons.append(lesson)
        del spark.lessons[:-MAX_LESSONS]
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
    return sum(s.unread for s in all_sparks())


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
    parts = [get_model_system_prompt(host, model), body, date_prompt]
    return "\n\n".join(part for part in parts if part)


def _set_activity(spark_id: str, activity: str) -> None:
    with _lock:
        spark = find(spark_id)
        if spark is None:
            return
        spark.activity = activity
        _save(spark)
    _changed()


def shift(spark_id: str, client=None) -> Optional[Report]:
    """Run one shift of SPARK_ID now, on this thread, and file its report.

    A paused spark still runs when asked to. None if the spark is gone
    or already working.
    """

    from . import tools as flash_tools  # deferred: avoids a module cycle

    with _lock:
        spark = find(spark_id)
        if spark is None or spark.status == WORKING:
            return None
        spark.status = WORKING
        spark.activity = "Starting"
        _save(spark)
    _changed()

    steps: list[str] = []
    notes = [spark.notes]

    def record(kind: str, text: str, style: str) -> None:
        if kind == "line":
            steps.append(text)
            del steps[:-MAX_STEPS]
            _set_activity(spark.id, f"Running {text}")
        elif kind == "result" and style == ERROR and steps:
            steps[-1] += " (failed)"

    try:
        model = flash_tools.MODEL_NAME
        if not model:
            raise RuntimeError("no model is set, so it could not run")
        host = flash_tools.OLLAMA_HOST or subagents.OLLAMA_HOST_DEFAULT
        client = client or ollama.Client(host=host)
        allowed = subagents.allowed_tool_names()
        schemas = [
            t for t in flash_tools.tools
            if t["function"]["name"] in allowed
        ] + [KEEP_NOTES_TOOL]
        messages: list[dict] = [
            {
                "role": "system",
                "content": _prompt(
                    spark, host, model, flash_tools.CURRENT_DATE_PROMPT
                ),
            },
            {"role": "user", "content": "Start your shift."},
        ]

        final = ""
        tool_calls: list = []
        with capture_tool_output(record):
            for _ in range(MAX_SHIFT_ROUNDS):
                _set_activity(spark.id, "Thinking")
                response = client.chat(
                    model=model, messages=messages, tools=schemas,
                    options=subagents.chat_options(),
                )
                message = getattr(response, "message", None)
                final = getattr(message, "content", "") or ""
                tool_calls = list(getattr(message, "tool_calls", None) or [])
                if not tool_calls:
                    break

                calls = [
                    subagents._tool_call_name_args(c)
                    for c in tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": final,
                    "tool_calls": [
                        {"function": {"name": n, "arguments": a}}
                        for n, a in calls
                    ],
                })
                for name, args in calls:
                    result = _call(name, args, allowed, steps, notes)
                    messages.append({
                        "role": "tool",
                        "content": flash_tools.trim_tool_output(result, name),
                        "tool_name": name,
                    })

        if tool_calls:
            _set_activity(spark.id, "Writing its report")
            messages.append({"role": "system", "content": ROUND_LIMIT_MESSAGE})
            response = client.chat(
                model=model, messages=messages,
                options=subagents.chat_options(),
            )
            final = getattr(
                getattr(response, "message", None), "content", ""
            ) or ""

        text = undash(final).strip()
        quiet = not text or text.strip(" .").upper() == NOTHING_NEW
        report = Report(
            at=time.time(),
            text="Nothing new this shift." if quiet else text,
            quiet=quiet, read=quiet, steps=steps,
        )
    except Exception as e:  # noqa: BLE001
        report = Report(
            at=time.time(),
            text=f"This shift failed: {e.__class__.__name__}: {e}",
            failed=True, steps=steps,
        )

    with _lock:
        spark = find(spark_id)
        if spark is None:
            return report  # removed mid-shift: nowhere to file it
        spark.reports.append(report)
        del spark.reports[:-MAX_REPORTS]
        spark.notes = notes[0][:NOTES_CHARS]
        spark.status = FAILED if report.failed else IDLE
        spark.activity = ""
        spark.runs += 1
        spark.last_run = report.at
        spark.next_run = report.at + spark.every * 60
        _save(spark)
    _changed()
    return report


def _call(
    name: str, args: dict, allowed: tuple[str, ...],
    steps: list[str], notes: list[str],
) -> str:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    if name == "keep_notes":
        notes[0] = str(args.get("notes", "")).strip()
        steps.append("Kept notes")
        return "(notes kept)"
    if name == "reason":
        return "(noted)"
    if name not in allowed:
        steps.append(f"{name}() (not allowed)")
        return f"Unknown tool: {name}. Available: {', '.join(allowed)}."
    return flash_tools.run_tool((name, args))


# --- The keeper ----------------------------------------------------------

_keeper: Optional[threading.Thread] = None
_nudge = threading.Event()
_asked: list[str] = []


def due(now: Optional[float] = None) -> list[Spark]:
    now = time.time() if now is None else now
    return [
        s for s in all_sparks()
        if not s.paused and s.status != WORKING and s.next_run <= now
    ]


def run_now(key: str) -> Spark:
    """Put a spark's next shift ahead of its schedule."""

    spark = _must_find(key)
    with _lock:
        if spark.id not in _asked:
            _asked.append(spark.id)
    wake()
    return spark


def wake() -> None:
    _nudge.set()


def _keep() -> None:
    # A spark left "working" belongs to a Flash that quit mid-shift.
    for spark in all_sparks():
        if spark.status == WORKING:
            _edit(spark.id, lambda s: setattr(s, "status", IDLE))

    while True:
        _nudge.wait(TICK_SECONDS)
        _nudge.clear()
        with _lock:
            asked = list(_asked)
            _asked.clear()
        for spark_id in asked:
            _safe_shift(spark_id)
        for spark in due():
            _safe_shift(spark.id)


def _safe_shift(spark_id: str) -> None:
    # A disk that fails mid-shift costs that shift, not the keeper: the
    # rest go on, and this one is tried again when it next comes due.
    with contextlib.suppress(OSError):
        shift(spark_id)


def start() -> None:
    """Start the keeper, once per process, beside the terminal or web UI."""

    global _keeper

    with _lock:
        if _keeper is not None and _keeper.is_alive():
            return
        _keeper = threading.Thread(target=_keep, daemon=True, name="sparks")
        _keeper.start()
