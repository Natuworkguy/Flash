"""Waiting for something: a task the agent sets aside until a time
comes, or until something it is watching for is true, then takes up again
in the same chat, knowing all it did before.

"When CI goes green, deploy." "If they have not replied by Friday,
follow up." "In two hours, check the download finished." The model calls
wait_for with what it is waiting for and what to do then, and its turn
ends. Each wait is one of:

- a time: it is over when the time comes;
- a command, run every so often in the chat's folder: it is over when the
  command succeeds (exit 0). The user says yes to the command once, when
  the wait is set, since it runs again and again unwatched;
- a check in words ("their reply is in my inbox"): every so often a
  sub-agent looks with the tools a sub-agent has, changing nothing, and
  says whether it is so.

A time and a check together wait for the time, then start checking. A
check gives up after a while (three days unless told), and the chat is
told it did.

Waits are kept in ~/.flash/waits.json, so they outlast a restart, a
sleeping laptop, and an update. A time that passed while Flash was not
running is over the moment it is back.
"""

import json
import os
import subprocess  # nosec B404
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Optional

from .paths import FLASH_DIR

WAITING = "waiting"
DONE = "done"
GAVE_UP = "gave_up"
CANCELLED = "cancelled"

# How often a command or a check is tried, in minutes: a check is a whole
# model run, so it is tried less often.
DEFAULT_EVERY = 15
MIN_COMMAND_EVERY = 1
MIN_CHECK_EVERY = 5
MAX_EVERY = 24 * 60
DEFAULT_GIVE_UP_HOURS = 72
MAX_DAYS = 30
MAX_OPEN_PER_CHAT = 5
COMMAND_SECONDS = 60
SAID_CHARS = 2000
WHAT_CHARS = 200
THEN_CHARS = 2000

CHECK_PROMPT = """Something you are waiting for, to carry on a task: \
{check}

Find out whether it is so now, with your tools. Look only: change \
nothing, send nothing. Then answer with READY or NOT YET on the first \
line, and on the next one or two lines what you found that says so."""


class WaitError(ValueError):
    """A wait that could not be set, and why, for the model."""


@dataclass
class Wait:
    id: str
    chat: str
    what: str
    then: str
    at: Optional[float] = None
    check: str = ""
    command: str = ""
    every: int = DEFAULT_EVERY
    give_up: Optional[float] = None
    cwd: str = ""
    created: float = 0.0
    # When it is next looked at: the time, or the next try.
    next: float = 0.0
    checks: int = 0
    # A look found it so: over as soon as its chat is free to hear it.
    found: bool = False
    status: str = WAITING
    # What the last try found, or why it ended.
    said: str = ""
    ended: Optional[float] = None

    def card(self) -> dict:
        """What the page draws for it."""

        return {
            "id": self.id, "what": self.what, "then": self.then,
            "at": self.at, "check": self.check, "command": self.command,
            "every": self.every, "give_up": self.give_up,
            "next": self.next, "checks": self.checks, "found": self.found,
            "status": self.status, "said": self.said, "ended": self.ended,
        }


_lock = threading.Lock()
_cache: Optional[list[Wait]] = None


def _path():
    return FLASH_DIR / "waits.json"


def _load() -> list[Wait]:
    global _cache
    if _cache is None:
        try:
            raw = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        names = {f.name for f in fields(Wait)}
        _cache = [
            Wait(**{k: v for k, v in item.items() if k in names})
            for item in raw if isinstance(item, dict) and item.get("id")
        ]
    return _cache


def _save() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.part")
    # The finished ones are kept a while, for their cards; not for ever.
    kept = [w for w in _load() if w.status == WAITING
            or (w.ended or 0) > time.time() - MAX_DAYS * 86400]
    partial.write_text(json.dumps([asdict(w) for w in kept], indent=1),
                       encoding="utf-8")
    partial.replace(path)


def forget_cache() -> None:
    """Read the file again next time: for tests, and a changed home."""

    global _cache
    with _lock:
        _cache = None


def parse_time(text: str, now: Optional[float] = None) -> float:
    """An ISO 8601 date and time, local unless it says otherwise."""

    try:
        found = datetime.fromisoformat(str(text).strip().replace("Z",
                                                                 "+00:00"))
    except ValueError:
        raise WaitError(
            f"{text!r} is not a time. Give it as 2026-10-23T09:00, local "
            "time, or use in_minutes."
        ) from None
    if found.tzinfo is None:
        found = found.astimezone()
    return found.timestamp()


def _number(args: dict, key: str) -> Optional[float]:
    value = args.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise WaitError(f"{key} has to be a number.") from None


def make(chat_id: str, args: dict, cwd: str = "",
         now: Optional[float] = None) -> Wait:
    """A wait from the model's wait_for call. Raises WaitError saying
    what is wrong with it."""

    now = time.time() if now is None else now
    what = " ".join(str(args.get("what") or "").split())[:WHAT_CHARS]
    then = str(args.get("then") or "").strip()[:THEN_CHARS]
    check = " ".join(str(args.get("check") or "").split())
    command = str(args.get("command") or "").strip()
    if not what:
        raise WaitError("Say what you are waiting for, in `what`.")
    if not then:
        raise WaitError("Say what to do once it happens, in `then`.")

    at = None
    if str(args.get("at") or "").strip():
        at = parse_time(str(args["at"]), now)
    minutes = _number(args, "in_minutes")
    if minutes is not None:
        if minutes <= 0:
            raise WaitError("in_minutes has to be more than 0.")
        at = now + minutes * 60
    if at is None and not check and not command:
        raise WaitError(
            "Give a time to wait for (at, or in_minutes), a check, or a "
            "command."
        )
    if at is not None and at > now + MAX_DAYS * 86400:
        raise WaitError(f"A wait can be at most {MAX_DAYS} days long.")
    if at is not None and at <= now and not check and not command:
        raise WaitError("That time has already passed.")

    every = _number(args, "every_minutes")
    least = MIN_COMMAND_EVERY if command else MIN_CHECK_EVERY
    every = int(max(least, min(MAX_EVERY, every or DEFAULT_EVERY)))

    give_up = None
    if check or command:
        hours = _number(args, "give_up_after_hours")
        hours = DEFAULT_GIVE_UP_HOURS if hours is None else hours
        start = at if at is not None else now
        give_up = start + max(0.1, min(hours, MAX_DAYS * 24)) * 3600
    with _lock:
        open_here = [w for w in _load()
                     if w.chat == chat_id and w.status == WAITING]
    if len(open_here) >= MAX_OPEN_PER_CHAT:
        raise WaitError(
            f"This chat already waits for {MAX_OPEN_PER_CHAT} things. "
            "Cancel one first, with cancel_wait."
        )
    return Wait(
        id=uuid.uuid4().hex[:8], chat=chat_id, what=what, then=then, at=at,
        check=check, command=command, every=every, give_up=give_up,
        cwd=cwd, created=now,
        next=at if at is not None else now,
    )


def add(wait: Wait) -> Wait:
    with _lock:
        _load().append(wait)
        _save()
    return wait


def find(wait_id: str) -> Optional[Wait]:
    with _lock:
        return next((w for w in _load() if w.id == wait_id), None)


def for_chat(chat_id: str) -> list[Wait]:
    with _lock:
        return [w for w in _load() if w.chat == chat_id]


def waiting() -> list[Wait]:
    with _lock:
        return [w for w in _load() if w.status == WAITING]


def update(wait_id: str, **changes) -> Optional[Wait]:
    with _lock:
        found = next((w for w in _load() if w.id == wait_id), None)
        if found is None:
            return None
        for key, value in changes.items():
            setattr(found, key, value)
        _save()
        return found


def end(wait_id: str, status: str, said: str = "") -> Optional[Wait]:
    """Over, one way or another: DONE, GAVE_UP, or CANCELLED. None when
    it already was, so it is only ever over once."""

    with _lock:
        found = next((w for w in _load() if w.id == wait_id), None)
        if found is None or found.status != WAITING:
            return None
        found.status = status
        found.ended = time.time()
        if said:
            found.said = said[:SAID_CHARS]
        _save()
        return found


def cancel(wait_id: str, chat_id: str = "") -> Wait:
    found = find(str(wait_id or "").strip())
    if found is None or (chat_id and found.chat != chat_id):
        raise WaitError(f"No wait {wait_id!r} in this chat.")
    if found.status != WAITING:
        raise WaitError(f"That wait is already {found.status}.")
    return end(found.id, CANCELLED, "Cancelled.")


def due(now: Optional[float] = None) -> list[Wait]:
    """The waits whose time, or next try, has come."""

    now = time.time() if now is None else now
    return [w for w in waiting() if w.next <= now]


def check(wait: Wait) -> tuple[bool, str]:
    """Whether what WAIT waits for is so now, and what was found."""

    if wait.command:
        return _run_command(wait)
    if wait.check:
        return _ask(wait.check)
    return True, ""


def _run_command(wait: Wait) -> tuple[bool, str]:
    try:
        done = subprocess.run(  # nosec B602
            # The user's yes, given when the wait was set, is for this
            # very command, in the shell the shell tool uses.
            wait.command, shell=True, cwd=wait.cwd or None,
            capture_output=True, text=True, timeout=COMMAND_SECONDS,
            stdin=subprocess.DEVNULL, env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return False, f"It ran past {COMMAND_SECONDS} seconds."
    except OSError as exc:
        return False, f"It could not run: {exc}"
    said = (done.stdout + done.stderr).strip()[-SAID_CHARS:]
    return done.returncode == 0, said or f"exit {done.returncode}"


def _ask(question: str) -> tuple[bool, str]:
    from . import agent  # deferred: agent imports the tools

    ok, said = agent.run_now(CHECK_PROMPT.format(check=question))
    if not ok:
        return False, said
    first, _, rest = said.strip().partition("\n")
    ready = first.strip().strip("*.:").upper().startswith("READY")
    return ready, (rest.strip() or first.strip())[:SAID_CHARS]


def when(wait: Wait) -> str:
    """When it is waiting for, in words, for the model."""

    if wait.at is None:
        return "now"
    return datetime.fromtimestamp(wait.at).astimezone().strftime(
        "%a %b %d, %H:%M %Z"
    )
