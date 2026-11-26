"""Windows that ask not to be worked by an agent, and Flash keeping out.

A window opts out in any of these ways, so any program can, without asking
Flash for anything:

  * its title contains "[no agents]": a web page's <title>, a document's
    name, a terminal's tab, anything that can set a title can say it;
  * its app is named in ~/.config/agent-control/deny.json, a list every
    agent can read:
        {"apps": ["com.example.Bank", "KeePassXC"], "titles": ["Payroll"]}
    (an app by its name or, on a Mac, its bundle identifier; a title by
    any part of it);
  * the user named the app in Flash's own setting, COMPUTER_USE_NEVER
    (names, comma separated);
  * it is a password manager: those are never worked by an agent.

Before Computer Use clicks, types or presses a key, it asks which window
that lands in (the one under the pointer for a click where the system
says, the front one otherwise) and stops if that window has opted out,
telling the model to hand that part to the user.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- the system's own tools, fixed arguments
import sys
from pathlib import Path
from typing import Optional

MARKERS = ("[no agents]", "[no agent]")
SETTING = "COMPUTER_USE_NEVER"
NEVER = (
    "1password",
    "bitwarden",
    "keepassxc",
    "keeper",
    "lastpass",
    "dashlane",
    "enpass",
    "keychain access",
    "com.agilebits.onepassword7",
    "com.1password.1password",
    "com.bitwarden.desktop",
    "com.apple.keychainaccess",
    "org.keepassxc.keepassxc",
)


def deny_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agent-control" / "deny.json"


def _listed() -> tuple[list[str], list[str]]:
    apps, titles = [], []
    try:
        found = json.loads(deny_file().read_text(encoding="utf-8"))
        apps += [str(a).lower() for a in found.get("apps") or []]
        titles += [str(t).lower() for t in found.get("titles") or []]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        from .notify import _saved

        apps += [
            a.strip().lower() for a in _saved(SETTING).split(",") if a.strip()
        ]
    except Exception:  # noqa: BLE001
        return apps, titles
    return apps, titles


def refused(window: dict) -> str:
    """Why WINDOW ({"app", "bundle", "title"}) is not to be worked, or ""
    when it may be."""

    app = str(window.get("app") or "").lower()
    bundle = str(window.get("bundle") or "").lower()
    title = str(window.get("title") or "")
    if any(m in title.lower() for m in MARKERS):
        return f'the window "{title}" says [no agents] in its title'
    if any(n == app or n == bundle or (n and n in app) for n in NEVER):
        return f"{window.get('app') or bundle} is a password manager"
    apps, titles = _listed()
    if any(a and (a == app or a == bundle) for a in apps):
        who = window.get("app") or bundle
        return f"{who} is on the list of apps agents keep out of"
    if any(t and t in title.lower() for t in titles):
        return (f'the window "{title}" is on the list of windows agents '
                "keep out of")
    return ""


def _quiet(argv: list[str], seconds: float = 2.0) -> str:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=seconds,  # nosec B603
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


_MAC = [
    'tell application "System Events"',
    "set p to first application process whose frontmost is true",
    'set t to ""',
    "try",
    "set t to name of front window of p",
    "end try",
    "return (name of p) & linefeed & (bundle identifier of p) & linefeed & t",
    "end tell",
]


def window(kind: str, at: Optional[tuple[float, float]] = None) -> dict:
    """The window AT a point (where the system can say) or in front:
    {"app", "bundle", "title"}; {} when it cannot be found out."""

    try:
        if kind == "mac":
            said = _quiet(
                ["osascript"]
                + [part for line in _MAC for part in ("-e", line)]
            )
            parts = (said.split("\n") + ["", "", ""])[:3]
            return {"app": parts[0], "bundle": parts[1], "title": parts[2]}
        if kind == "windows" and sys.platform == "win32":
            return _win_window(at)
        if kind == "linux":
            wid = _quiet(["xdotool", "getactivewindow"])
            if not wid:
                return {}
            title = _quiet(["xdotool", "getwindowname", wid])
            pid = _quiet(["xdotool", "getwindowpid", wid])
            app = ""
            if pid.isdigit():
                try:
                    app = Path(f"/proc/{pid}/comm").read_text().strip()
                except OSError:
                    pass
            return {"app": app, "bundle": "", "title": title}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _win_window(at: Optional[tuple[float, float]]) -> dict:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if at is not None:
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(at[0]), int(at[1])))
        hwnd = user32.GetAncestor(hwnd, 2) or hwnd
    else:
        hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {}
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        name = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, name, ctypes.byref(size)
        ):
            app = Path(name.value).stem
        kernel32.CloseHandle(handle)
    return {"app": app, "bundle": "", "title": buf.value}


def check(kind: str, at: Optional[tuple[float, float]] = None) -> str:
    """The reason to stop before working the window AT a point (or in
    front), or "" to go on."""

    found = window(kind, at)
    return refused(found) if found else ""
