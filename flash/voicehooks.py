"""Extensions in the middle of a voice conversation.

An extension asks for them with "voice" in its manifest:

    "voice": {"run": ["python", "./voice.py"],
              "on": ["wake", "start", "state", "words", "heard",
                     "reply", "end"],
              "timeout": 5}

and its program is run once for each moment of a conversation held in
voice mode, with the moment as JSON on stdin. "source" says where:
"web" for the page's voice mode, "server" for a conversation Flash held
on its own microphone after "Hey Flash" with no page open (heyflash):

    {"event": "heard", "conversation": "k3j9x0aa2q", "source": "web",
     "at": 1760100000.0, "text": "what's on my calendar"}

Some moments are the extension's to change. For these Flash waits for
the program, and reads what it prints as JSON:

- wake: "Hey Flash" was heard. {"ignore": true} keeps voice mode off.
- heard: what the user said ("text"). {"text": ...} changes what Flash
  is asked; {"reply": ...} answers it instead, read aloud, and Flash is
  never asked; {"end": true} ends voice mode.
- reply: what Flash is about to say ("text"), as it will be read out.
  {"text": ...} is read out instead.

Several extensions are asked in turn, in the order they are installed,
each seeing the text as the one before left it; once one answers a
"heard", the rest are not asked. A program that fails, prints nothing,
or takes longer than its timeout changes nothing.

The others it is only told of, one at a time, in order, from a thread of
its own, so a slow program never holds the conversation up:

- start: voice mode began ("how": "wake" or "button").
- state: what voice mode is doing ("state": listening, hearing,
  thinking, speaking, paused or asking).
- words: what the user is saying, as they say it ("text").
- end: voice mode ended.
"""

import json
import queue
import subprocess  # nosec B404
import threading
import time
from typing import Any, Optional

ASKED = ("wake", "heard", "reply")
TOLD = ("start", "state", "words", "end")
EVENTS = ASKED + TOLD
STATES = ("listening", "hearing", "thinking", "speaking", "paused",
          "asking", "setup")
HOWS = ("wake", "button")
SOURCES = ("web", "server")

DEFAULT_TIMEOUT = 5
QUEUE_SIZE = 64
TEXT_CHARS = 8000

_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_SIZE)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _listeners(event: str) -> list:
    from . import extensions  # deferred: read on demand

    return [
        extension for extension in extensions.installed()
        if extension.voice is not None and event in extension.voice.on
    ]


def _run(extension: Any, payload: dict) -> str:
    """What EXTENSION's program printed for PAYLOAD; "" if it failed."""

    from . import extensions

    try:
        result = subprocess.run(  # nosec B603 -- the user installed it
            extensions.argv(extension, extension.voice.run),
            input=json.dumps(payload), text=True, encoding="utf-8",
            errors="replace", capture_output=True,
            timeout=extension.voice.timeout,
            env=extensions.environment(extension),
            cwd=str(extension.path), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _answer(printed: str) -> dict:
    """The JSON object a program printed, or {} for anything else."""

    printed = printed.strip()
    if not printed:
        return {}
    try:
        found = json.loads(printed)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _payload(event: str, conversation: str, data: dict) -> dict:
    source = str(data.get("source") or "")
    payload: dict = {
        "event": event, "conversation": str(conversation or "")[:32],
        "source": source if source in SOURCES else "web", "at": time.time(),
    }
    if "text" in data:
        payload["text"] = str(data.get("text") or "")[:TEXT_CHARS]
    if event == "state":
        state = str(data.get("state") or "")
        payload["state"] = state if state in STATES else "listening"
    if event == "start":
        how = str(data.get("how") or "")
        payload["how"] = how if how in HOWS else "button"
    return payload


def wanted() -> bool:
    """Whether any extension follows voice mode at all, so the page need
    not tell Flash of every moment when none does."""

    return any(_listeners(event) for event in EVENTS)


def ask(event: str, conversation: str = "", **data: Any) -> dict:
    """EVENT, one of ASKED, put to every extension that wants it, in
    turn. Returns what they decided, between them:

    - wake: {"ignore": bool}
    - heard: {"text": str, "reply": str or None, "end": bool}
    - reply: {"text": str}
    """

    if event not in ASKED:
        raise ValueError(f"{event!r} is not asked of extensions")
    text = str(data.get("text") or "")[:TEXT_CHARS]
    decided: dict = {"ignore": False} if event == "wake" else {"text": text}
    if event == "heard":
        decided.update(reply=None, end=False)
    for extension in _listeners(event):
        answer = _answer(_run(
            extension, _payload(event, conversation, {**data, "text": text}),
        ))
        if event == "wake":
            if answer.get("ignore") is True:
                decided["ignore"] = True
                break
            continue
        if isinstance(answer.get("text"), str):
            text = answer["text"][:TEXT_CHARS]
            decided["text"] = text
        if event == "heard":
            if answer.get("end") is True:
                decided["end"] = True
                break
            if isinstance(answer.get("reply"), str) and answer["reply"]:
                decided["reply"] = answer["reply"][:TEXT_CHARS]
                break
    return decided


def _deliver() -> None:
    while True:
        payload = _queue.get()
        for extension in _listeners(payload["event"]):
            _run(extension, payload)


def tell(event: str, conversation: str = "", **data: Any) -> None:
    """EVENT, one of TOLD, sent to every extension that wants it, in the
    background. Dropped, not queued without end, when they cannot keep
    up."""

    global _worker

    if event not in TOLD:
        raise ValueError(f"{event!r} is not told to extensions")
    if not _listeners(event):
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_deliver, daemon=True)
            _worker.start()
    try:
        _queue.put_nowait(_payload(event, conversation, data))
    except queue.Full:
        pass
