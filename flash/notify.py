"""Desktop notifications for Flash.

Best-effort only: notifications must never crash or block the main loop, so
every failure is swallowed and the app keeps running without them.

Uses winotify on Windows (reliable, no background message pump) instead of
win10toast, which raises "WNDPROC return value cannot be converted to LRESULT"
on current Python/Windows builds from inside its own toast thread.
"""

import json
import math
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

_APP_NAME = "Flash CLI"

# Resolve the notifier once. winotify is Windows-only and optional, so a missing  # noqa: E501
# package or import error simply disables notifications.
_Notification = None
if os.name == "nt":
    try:
        from winotify import Notification  # type: ignore[import-untyped]

        _Notification = Notification
        del Notification
    except Exception:  # noqa: BLE001
        _Notification = None


def notify(title: str, message: str) -> None:
    """Show a desktop notification. Never raises and never blocks."""

    if _Notification is None:
        return

    try:
        toast = _Notification(
            app_id=_APP_NAME,
            title=title,
            msg=message,
        )
        # winotify launches a detached helper, so show() returns immediately.
        toast.show()
    except Exception:  # noqa: BLE001, S110  # nosec B110
        # Any backend failure just means no notification; keep the CLI running.
        pass


def notify_reply_ready() -> None:
    """Notify the user that Flash has finished answering."""

    notify(_APP_NAME, "Response ready.")


def notify_needs_input() -> None:
    """Notify the user that Flash is waiting for command approval."""

    notify(_APP_NAME, "Waiting for your approval to run a command.")


def notify_spark(
    name: str, text: str, failed: bool = False, asking: bool = False,
) -> None:
    """Tell the user a spark has news, from the keeper that runs while
    Flash is closed. With nothing of Flash open this is the only way
    they hear of it, so unlike the others it tries every desktop: the
    built-in notifier on macOS and notify-send on Linux too."""

    title = (
        f"{name} needs your approval" if asking
        else f"{name} could not finish a shift" if failed
        else f"{name} has news"
    )
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    message = line[:160] or "Open Flash to read it."

    desktop(title, message)


def desktop(title: str, message: str) -> None:
    """Show a notification on whichever desktop this is: winotify on
    Windows, the built-in notifier on macOS, notify-send on Linux.
    Never raises; with none of them, nothing is shown."""

    if _Notification is not None:
        notify(title, message)
        return

    if sys.platform == "darwin":
        def quoted(value: str) -> str:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        args = [
            "osascript", "-e",
            f"display notification {quoted(message)} "
            f"with title {quoted(title)}",
        ]
    elif shutil.which("notify-send"):
        args = ["notify-send", "--app-name=Flash", title, message]
    else:
        return

    try:
        subprocess.run(  # nosec B603 -- fixed program, text as arguments
            args, check=False, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001, S110  # nosec B110
        pass


# How often the agent may notify the user, whichever chat or spark it
# is: a few at a time, then a wait, so a loop that calls the tool over
# and over cannot bury the user's desktop in them.
NOTIFY_LIMIT = 4
NOTIFY_WINDOW = 15 * 60
NOTIFY_GAP = 30

_sent_lock = threading.Lock()


def _sent_path() -> Path:
    # FLASH_DIR is looked up here, not bound at import, so a test can
    # point it at a temporary home.
    return FLASH_DIR / "notified.json"


def _sent_times() -> list[float]:
    try:
        found = json.loads(_sent_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(found, list):
        return []
    return [float(t) for t in found if isinstance(t, (int, float))]


def wait_to_notify(now: Optional[float] = None) -> int:
    """Seconds until the agent may notify the user again: 0 when it may
    now. Kept on disk, so the keeper's sparks and an open Flash share
    one limit."""

    now = time.time() if now is None else now
    recent = sorted(t for t in _sent_times() if now - t < NOTIFY_WINDOW)
    waits = [0.0]
    if recent:
        waits.append(recent[-1] + NOTIFY_GAP - now)
    if len(recent) >= NOTIFY_LIMIT:
        waits.append(recent[-NOTIFY_LIMIT] + NOTIFY_WINDOW - now)
    return max(0, math.ceil(max(waits)))


def take_notify_turn(now: Optional[float] = None) -> int:
    """Claim a notification, if the limit allows one now: 0 when it was
    claimed, or the seconds to wait before one can be."""

    now = time.time() if now is None else now
    with _sent_lock:
        wait = wait_to_notify(now)
        if wait:
            return wait
        kept = [t for t in _sent_times() if now - t < NOTIFY_WINDOW]
        try:
            path = _sent_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(kept + [now]), encoding="utf-8")
        except OSError:
            # Unable to keep count, it still sends: a full disk is no
            # reason to lose what the agent had to say.
            pass
        return 0
