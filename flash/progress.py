"""A turn's milestones, for progress emails and for extensions that
want to follow along.

Flash's own progress emails (progress_mail.py) are sent from these, and
an extension can have them too.

An extension asks for them with "events" in its manifest:

    "events": {"run": ["python", "./events.py"],
               "on": ["turn-start", "plan", "ask", "turn-end"]}

and its program is run once for each, with the event as JSON on stdin:

    {"event": "turn-end", "turn": "3f2a9c1d", "source": "web",
     "chat": "8b1e", "title": "Fix the login tests",
     "request": "fix the login tests", "at": 1760100000.0,
     "ok": true, "summary": "All 14 pass now.", "error": "",
     "seconds": 252.4}

"plan" carries "steps" ([{"text", "status"}]), "done", "total" and
"change" ("set", or "done" with the step's "index"), and "ask" the
"question" a step is waiting on the user for.

Only the turn the user is waiting on is reported: a turn is followed on
the thread it runs on, so a sub-agent's or a spark's work, on threads of
their own, is not. Events go out one at a time, in order, from a thread
of their own, so a slow program never holds a turn up; one that is too
slow to keep up has events dropped, not queued without end.
"""

import json
import queue
import subprocess  # nosec B404
import threading
import time
import uuid
from typing import Any, Optional

EVENTS = ("turn-start", "plan", "ask", "turn-end")

QUEUE_SIZE = 64
SUMMARY_CHARS = 280

_turn = threading.local()
_queue: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=QUEUE_SIZE)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _subscribers(event: str) -> list:
    from . import extensions  # deferred: read on demand

    return [
        extension for extension in extensions.installed()
        if extension.events is not None and event in extension.events.on
    ]


def _deliver() -> None:
    from . import extensions, progress_mail

    while True:
        event, payload = _queue.get()
        if progress_mail.wants(event):
            progress_mail.handle(payload)
        for extension in _subscribers(event):
            try:
                subprocess.run(  # nosec B603 -- the user installed it
                    extensions.argv(extension, extension.events.run),
                    input=json.dumps(payload), text=True,
                    capture_output=True, timeout=extension.events.timeout,
                    env=extensions.environment(extension),
                    cwd=str(extension.path),
                )
            except (OSError, subprocess.SubprocessError):
                pass


def _emit(event: str, **data: Any) -> None:
    global _worker

    from . import progress_mail

    current = getattr(_turn, "now", None)
    if current is None or not (
        progress_mail.wants(event) or _subscribers(event)
    ):
        return
    payload = {"event": event, **current, "at": time.time(), **data}
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_deliver, daemon=True)
            _worker.start()
    try:
        _queue.put_nowait((event, payload))
    except queue.Full:
        pass


def begin(source: str, chat: str = "", title: str = "", request: str = "") \
        -> None:
    """A turn starts on this thread. One still open here is over: it
    ended without saying so."""

    end(False, error="Stopped.")
    _turn.now = {
        "turn": uuid.uuid4().hex[:8], "source": source, "chat": chat,
        "title": " ".join(str(title or "").split())[:120],
        "request": str(request or "").strip()[:SUMMARY_CHARS],
    }
    _turn.started = time.monotonic()
    _turn.error = ""
    _emit("turn-start")


def failed(error: str) -> None:
    """What went wrong, for when this thread's turn ends."""

    if getattr(_turn, "now", None) is not None and not _turn.error:
        _turn.error = str(error or "").strip()[:SUMMARY_CHARS]


def end(ok: bool, summary: str = "", error: str = "") -> None:
    """This thread's turn is over: done, or not, and why. Nothing when
    no turn is open, so it is safe to call twice."""

    if getattr(_turn, "now", None) is None:
        return
    error = getattr(_turn, "error", "") or error
    ok = ok and not error
    lines = [line for line in str(summary or "").splitlines() if line.strip()]
    _emit(
        "turn-end", ok=ok, summary=lines[0].strip()[:SUMMARY_CHARS]
        if lines else "", error="" if ok else (error or "Stopped."),
        seconds=round(time.monotonic() - _turn.started, 1),
    )
    _turn.now = None


def plan(steps: list[dict], change: str, index: int = 0) -> None:
    """The checklist was set out, or a step of it ticked."""

    done = sum(1 for step in steps if step.get("status") == "done")
    _emit(
        "plan", steps=[dict(step) for step in steps], done=done,
        total=len(steps), change=change, index=index,
    )


def ask(question: str) -> None:
    """The turn is waiting on the user's yes or no."""

    _emit("ask", question=str(question or "").strip()[:SUMMARY_CHARS])
