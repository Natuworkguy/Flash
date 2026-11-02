"""Whether the user is at the computer, for sparks to know.

"online" when they have moved the mouse or typed in the last
IDLE_AFTER seconds, "idle" when they have not, "offline" when the
screen is locked, and "unknown" when this system will not say. Each
platform is read with what it already has: ioreg on macOS, user32 on
Windows, loginctl, xprintidle and D-Bus on Linux. Nothing here needs a
new package, and nothing here ever raises.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Optional

IDLE_AFTER = 120  # seconds without input before the user is idle
_TIMEOUT = 3


def _run(*args: str) -> str:
    """ARGS' output, or "" when it is missing, fails or hangs."""

    if not shutil.which(args[0]):
        return ""
    try:
        done = subprocess.run(
            list(args), capture_output=True, text=True, timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


# --- macOS ---------------------------------------------------------------


def _mac_idle() -> Optional[float]:
    out = _run("ioreg", "-c", "IOHIDSystem", "-d", "4")
    found = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(found.group(1)) / 1e9 if found else None


def _mac_locked() -> Optional[bool]:
    try:  # Quartz, when PyObjC happens to be there
        import Quartz  # type: ignore[import-not-found]

        session = Quartz.CGSessionCopyCurrentDictionary() or {}
        return bool(session.get("CGSSessionScreenIsLocked", False))
    except Exception:  # noqa: BLE001, S110
        pass
    # The same dictionary, as the console users ioreg lists.
    out = _run("ioreg", "-n", "Root", "-d1")
    if "IOConsoleUsers" not in out:
        return None
    return bool(re.search(r'"CGSSessionScreenIsLocked"\s*=\s*Yes', out))


# --- Windows -------------------------------------------------------------


def _win_idle() -> Optional[float]:
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000
    except Exception:  # noqa: BLE001
        return None


def _win_locked() -> Optional[bool]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # The input desktop cannot be opened, or switched to, while the
        # lock screen (the secure desktop) has it.
        desk = user32.OpenInputDesktop(0, False, 0x0100)  # SWITCHDESKTOP
        if not desk:
            return True
        try:
            return not user32.SwitchDesktop(desk)
        finally:
            user32.CloseDesktop(desk)
    except Exception:  # noqa: BLE001
        return None


# --- Linux and the other Unixes ------------------------------------------


def _linux_idle() -> Optional[float]:
    out = _run("xprintidle").strip()
    if out.isdigit():
        return int(out) / 1000
    out = _run(  # GNOME, Wayland too
        "gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
        "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
        "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime",
    )
    found = re.search(r"(\d+)\s*,?\s*\)", out)
    return int(found.group(1)) / 1000 if found else None


def _linux_locked() -> Optional[bool]:
    session = os.environ.get("XDG_SESSION_ID") or "self"
    out = _run(
        "loginctl", "show-session", session, "-p", "LockedHint", "--value",
    ).strip().lower()
    if out in ("yes", "no"):
        return out == "yes"
    out = _run(
        "gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
        "--object-path", "/org/freedesktop/ScreenSaver",
        "--method", "org.freedesktop.ScreenSaver.GetActive",
    ).lower()
    if "true" in out or "false" in out:
        return "true" in out
    out = _run("xssstate", "-s").strip().lower()
    if out:
        return out == "on"  # the screensaver is up; "off" or "disabled"
    return None


# --- Together ------------------------------------------------------------


def _readers():
    if sys.platform == "darwin":
        return _mac_idle, _mac_locked
    if sys.platform.startswith("win"):
        return _win_idle, _win_locked
    return _linux_idle, _linux_locked


def status_from(idle: Optional[float], locked: Optional[bool]) -> str:
    """The word for IDLE seconds without input and the LOCKED screen."""

    if locked:
        return "offline"
    if idle is None:
        return "unknown"
    return "idle" if idle >= IDLE_AFTER else "online"


def user_status() -> dict:
    """{"status", "idle_seconds"}: the user's presence now."""

    read_idle, read_locked = _readers()
    try:
        locked = read_locked()
    except Exception:  # noqa: BLE001
        locked = None
    try:
        idle = None if locked else read_idle()
    except Exception:  # noqa: BLE001
        idle = None
    return {
        "status": status_from(idle, locked),
        "idle_seconds": None if idle is None else round(idle),
    }


def presence_line() -> str:
    """The user's presence as one line for a spark's prompt."""

    now = user_status()
    status, idle = now["status"], now["idle_seconds"]
    if status == "online":
        return "- The user is online: at the computer, active just now."
    if status == "idle":
        return (
            f"- The user is idle: no mouse or keyboard input for "
            f"{max(1, idle // 60)} min."
        )
    if status == "offline":
        return "- The user is offline: the computer is locked."
    return "- The user's presence is unknown: this system does not say."
