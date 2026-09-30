"""Desktop notifications for Flash.

Best-effort only: notifications must never crash or block the main loop, so
every failure is swallowed and the app keeps running without them.

Uses winotify on Windows (reliable, no background message pump) instead of
win10toast, which raises "WNDPROC return value cannot be converted to LRESULT"
on current Python/Windows builds from inside its own toast thread.
"""

import os
import shutil
import subprocess  # nosec B404
import sys

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


def notify_spark(name: str, text: str, failed: bool = False) -> None:
    """Tell the user a spark has news, from the keeper that runs while
    Flash is closed. With nothing of Flash open this is the only way
    they hear of it, so unlike the others it tries every desktop: the
    built-in notifier on macOS and notify-send on Linux too."""

    title = (
        f"{name} could not finish a shift" if failed else f"{name} has news"
    )
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    message = line[:160] or "Open Flash to read it."

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
