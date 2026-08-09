"""Desktop notifications for Flash.

Best-effort only: notifications must never crash or block the main loop, so
every failure is swallowed and the app keeps running without them.

Uses winotify on Windows (reliable, no background message pump) instead of
win10toast, which raises "WNDPROC return value cannot be converted to LRESULT"
on current Python/Windows builds from inside its own toast thread.
"""

import os

_APP_NAME = "Flash CLI"

# Resolve the notifier once. winotify is Windows-only and optional, so a missing  # noqa: E501, RUF100
# package or import error simply disables notifications.
_Notification = None
if os.name == "nt":
    try:
        from winotify import Notification  # type: ignore[import-untyped]

        _Notification = Notification
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
