"""Progress emails: the milestones of what Flash is working on, sent to
your email, so you can follow along from a phone, a watch, anywhere.

- "▶ Flash started" when it starts on something,
- "☐ Plan, 4 steps" and "☑ Step 2/4" as it plans and ticks steps off,
- "❓ Flash needs you" when it is waiting on your yes or no,
- "✓ Flash finished" (the answer's first line, and how long it took),
  or "✗ Flash stopped" (and why).

Subjects are short, since a small screen shows little more, and the
emails of one request share a thread. They go through the account
/email connect set up, to your own address unless you name another.

Off until turned on, with /email progress on or in the web UI's email
settings. Kept in ~/.flash.env as PROGRESS_EMAIL (on or off),
PROGRESS_EMAIL_TO, PROGRESS_EMAIL_FROM and PROGRESS_EMAIL_KINDS.
"""

import json
import os
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

SETTING = "PROGRESS_EMAIL"
TO_SETTING = "PROGRESS_EMAIL_TO"
FROM_SETTING = "PROGRESS_EMAIL_FROM"
KINDS_SETTING = "PROGRESS_EMAIL_KINDS"

# What can be emailed about, and the progress event each comes from.
KINDS = {
    "start": "turn-start",
    "plan": "plan",
    "ask": "ask",
    "end": "turn-end",
}
ABOUT = {
    "start": "when Flash starts on something",
    "plan": "when it sets out a plan, and as it ticks off each step",
    "ask": "when it is waiting on your yes or no",
    "end": "when it finishes, or fails",
}

SUBJECT_TEXT = 60

# For trying it out: the emails written to this file, a JSON object a
# line, instead of sent.
DRY_RUN = "FLASH_PROGRESS_EMAIL_DRY_RUN"


def settings() -> dict:
    """How progress emails are set up, from the environment, which
    ~/.flash.env fills."""

    kinds = [
        k.strip() for k in (
            os.environ.get(KINDS_SETTING) or ",".join(KINDS)
        ).split(",")
        if k.strip() in KINDS
    ]
    return {
        "on": (os.environ.get(SETTING) or "").strip().lower()
        in ("1", "on", "true", "yes"),
        "to": (os.environ.get(TO_SETTING) or "").strip(),
        "account": (os.environ.get(FROM_SETTING) or "").strip(),
        "kinds": kinds,
    }


def recipient(now: Optional[dict] = None) -> str:
    """Where they go: the address set, or your own default one."""

    from . import mail

    now = now or settings()
    return now["to"] or mail.default_address()


def kind_of(event: dict) -> str:
    for kind, name in KINDS.items():
        if event.get("event") == name:
            return kind
    return ""


def wants(event: str) -> bool:
    """Whether an event of this name would be emailed now: cheap, so the
    progress module can skip the work when nothing would be."""

    now = settings()
    if not now["on"]:
        return False
    kind = next((k for k, name in KINDS.items() if name == event), "")
    return kind in now["kinds"]


def _short(text: str, limit: int = SUBJECT_TEXT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _took(seconds: float) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def words(event: dict) -> Optional[tuple[str, str]]:
    """(subject, body) for EVENT, or None for one with nothing to say."""

    what = _short(event.get("request") or event.get("title") or "a request")
    asked = event.get("request") or what
    kind = kind_of(event)
    if kind == "start":
        return f"▶ Flash started · {what}", f"Flash is working on: {asked}"
    if kind == "plan":
        steps = event.get("steps") or []
        listed = "\n".join(
            ("☑ " if s.get("status") == "done" else "☐ ") + s.get("text", "")
            for s in steps
        )
        if event.get("change") == "set":
            first = steps[0].get("text", "") if steps else ""
            return (
                f"☐ Plan, {len(steps)} steps · {_short(first, 40)}",
                f"Flash's plan for: {asked}\n\n{listed}",
            )
        index = int(event.get("index") or 0)
        step = steps[index - 1].get("text", "") \
            if 0 < index <= len(steps) else ""
        return (
            f"☑ Step {event.get('done', 0)}/{event.get('total', 0)} · "
            f"{_short(step, 45)}",
            f"Done: {step}\n\nThe plan for {asked}:\n\n{listed}",
        )
    if kind == "ask":
        question = str(event.get("question") or "")
        head = question.partition("\n")[0]
        return f"❓ Flash needs you · {_short(head, 50)}", (
            f"Flash is waiting on your yes or no, for: {asked}\n\n"
            f"{question}\n\nAnswer it in Flash."
        )
    if kind == "end":
        took = _took(event.get("seconds", 0))
        if event.get("ok"):
            return f"✓ Flash finished · {what} ({took})", (
                (event.get("summary") or "Done.")
                + f"\n\nAsked: {asked}\nTook {took}."
            )
        return f"✗ Flash stopped · {what}", (
            f"It didn't finish: {event.get('error') or 'Stopped.'}"
            f"\n\nAsked: {asked}\nAfter {took}."
        )
    return None


def thread_id(event: dict) -> str:
    return f"<flash-turn-{event.get('turn', 'x')}@flash.local>"


def message(event: dict, now: Optional[dict] = None) -> \
        Optional[EmailMessage]:
    """The email for EVENT, in the same thread as the rest of its
    request's."""

    from . import mail

    now = now or settings()
    said = words(event)
    if said is None:
        return None
    subject, body = said
    composed = mail.compose(
        to=recipient(now), subject=subject, body=body,
        account=now["account"],
    )
    # Each answers the request's first, so they show as one thread.
    first = thread_id(event)
    if kind_of(event) == "start":
        del composed["Message-ID"]
        composed["Message-ID"] = first
    else:
        composed["In-Reply-To"] = first
        composed["References"] = first
    return composed


def send(composed: EmailMessage) -> str:
    """Send it, or with FLASH_PROGRESS_EMAIL_DRY_RUN set, write it down."""

    outbox = os.environ.get(DRY_RUN)
    if outbox:
        with open(outbox, "a", encoding="utf-8") as out:
            out.write(json.dumps({
                "to": composed["To"], "from": composed["From"],
                "subject": composed["Subject"],
                "body": composed.get_content().strip(),
                "in_reply_to": composed["In-Reply-To"] or "",
            }) + "\n")
        return str(composed["To"])

    from . import mail

    return mail.send(composed)


def _log_path() -> Path:
    return FLASH_DIR / "progress-email.log"


def _note(text: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def last_problem() -> str:
    """The last time one could not be sent, and why, if it was the last
    thing that happened."""

    try:
        lines = _log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    last = lines[-1] if lines else ""
    return "" if " sent " in last else last


def handle(event: dict) -> None:
    """Email EVENT, if it is one to. Run on the progress module's own
    thread, so a slow mail server never holds a request up; a failure
    goes in a log, there being nobody to tell mid-request."""

    now = settings()
    if not now["on"] or kind_of(event) not in now["kinds"]:
        return
    try:
        if not recipient(now):
            raise RuntimeError(
                "no address to send to: connect an account with /email"
            )
        composed = message(event, now)
        if composed is not None:
            send(composed)
            _note(f"{event.get('event')} sent to {composed['To']}")
    except Exception as exc:  # noqa: BLE001 -- noted, never raised
        _note(f"{event.get('event')} not sent: {exc}")


def test() -> str:
    """Send one now, to see it arrive. Who it went to."""

    event = {
        "event": "turn-end", "turn": "test", "ok": True,
        "request": "a test of progress emails", "seconds": 1,
        "summary": "If this arrived, Flash's progress emails will too.",
    }
    now = settings()
    if not recipient(now):
        raise RuntimeError(
            "No address to send to: connect an account with /email connect."
        )
    return send(message(event, now))
