"""A shortcut to Flash in the browser: the web UI already running, or a
new one started out of sight (flash://open, which web.open_ui answers).

`flash --shortcut` makes it, called "Flash", where each system keeps
such things: the Start menu on Windows (a .lnk that runs pythonw, so no
console window opens), ~/Applications on a Mac, and the applications
menu on Linux (a .desktop entry). `flash --shortcut remove` takes it
away again. The installers do both.

On a Mac it is a real app, an AppleScript applet, so it can also be what
opens flash:// links: macOS hands an app a link as an Apple event, which
only an app can answer, not a script. Opened, it starts Flash's web UI
(or brings up the one running); a flash://?prompt= link opens the web UI
with the prompt waiting in the message box. Either way Flash runs out of
sight, with no Terminal.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from .paths import FLASH_DIR

NAME = "Flash"
OPEN = ("--uri", "flash://open")
WEB = Path(__file__).resolve().parent / "web"


class ShortcutError(Exception):
    """The shortcut could not be made, and why."""


def _host() -> str:
    if sys.platform == "darwin":
        return "mac"
    return "windows" if os.name == "nt" else "linux"


def command() -> list[str]:
    """What the shortcut runs: this Python (pythonw on Windows, which
    opens no console), this Flash, flash://open."""

    return [*base_command(), *OPEN]


def base_command() -> list[str]:
    """This Python (pythonw on Windows) and this Flash, to give a flag."""

    from . import keepalive  # it knows this Python and a clone's run.py

    python = Path(sys.executable)
    if _host() == "windows":
        quiet = python.with_name("pythonw.exe")
        if quiet.is_file():
            python = quiet
    script = keepalive._checkout_script()  # pylint: disable=W0212
    flash = [str(script)] if script is not None else ["-m", "flash"]
    return [str(python), *flash]


def place() -> Path:
    """Where the shortcut goes on this system."""

    host = _host()
    if host == "windows":
        programs = Path(os.environ.get("APPDATA") or Path.home())
        return programs / "Microsoft" / "Windows" / "Start Menu" \
            / "Programs" / f"{NAME}.lnk"
    if host == "mac":
        return Path.home() / "Applications" / f"{NAME}.app"
    data = Path(os.environ.get("XDG_DATA_HOME")
                or Path.home() / ".local" / "share")
    return data / "applications" / "flash.desktop"


def _icon(name: str) -> Path:
    """The icon, copied beside Flash's settings: it outlives an update
    that moves the installed package."""

    kept = FLASH_DIR / name
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WEB / name, kept)
    return kept


def _windows(target: Path, argv: list[str]) -> None:
    # The values go in through the environment, so no quoting can break
    # the script.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        "$env:FLASH_LNK); $s.TargetPath = $env:FLASH_TARGET; "
        "$s.Arguments = $env:FLASH_ARGS; $s.IconLocation = $env:FLASH_ICON; "
        "$s.WorkingDirectory = $env:USERPROFILE; "
        "$s.Description = 'Open Flash in your browser'; $s.Save()")
    args = subprocess.list2cmdline(argv[1:])
    env = {**os.environ, "FLASH_LNK": str(target),
           "FLASH_TARGET": argv[0], "FLASH_ARGS": args,
           "FLASH_ICON": str(_icon("flash.ico"))}
    target.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(  # nosec B603 B607 -- a fixed script
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script], env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False)
    if done.returncode or not target.exists():
        raise ShortcutError("Windows would not make the shortcut: "
                            + (done.stderr.strip() or "no reason given"))


LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")


def _applescript(base: list[str]) -> str:
    """The applet: opened, the web UI; given a flash:// link, the web UI
    with the link's prompt. Flash starts in the background and the applet
    quits, so nothing waits on it."""

    def shell(parts: list[str]) -> str:
        return " ".join("'" + p.replace("'", "'\\''") + "'" for p in parts)

    def literal(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    quiet = " >/dev/null 2>&1 &"
    return (
        "on run\n"
        f"  do shell script {literal(shell([*base, *OPEN]) + quiet)}\n"
        "end run\n"
        "on open location theURL\n"
        f"  do shell script {literal(shell([*base, '--web', '--uri']) + ' ')}"
        f" & quoted form of theURL & {literal(quiet)}\n"
        "end open location\n")


def _mac(target: Path, argv: list[str]) -> None:
    base = argv[:-len(OPEN)] if argv[-len(OPEN):] == list(OPEN) else argv
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = FLASH_DIR / "launcher.applescript"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_applescript(base), encoding="utf-8")
    done = subprocess.run(  # nosec B603 B607 -- a fixed tool
        ["osacompile", "-o", str(target), str(source)],
        capture_output=True, text=True, timeout=60, check=False)
    if done.returncode or not target.exists():
        raise ShortcutError("macOS would not make the Flash app: "
                            + (done.stderr.strip() or "no reason given"))
    contents = target / "Contents"
    # its own name, Flash's icon, no Dock icon while it runs, and flash://
    shutil.copyfile(WEB / "flash.icns", contents / "Resources" / "applet.icns")
    with open(contents / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleName": NAME, "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": "app.flash.open", "CFBundleIconFile": "applet",
        "LSUIElement": True,
        "CFBundleURLTypes": [{"CFBundleURLName": "Flash link",
                              "CFBundleURLSchemes": ["flash"]}],
    })
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    # The plist changed after osacompile signed it: sign it again (ad hoc,
    # as osacompile does), or Apple silicon will not open it. Then tell
    # Launch Services, so links find it now rather than at the next login.
    subprocess.run(["codesign", "--force", "--sign", "-", str(target)],  # nosec B603 B607
                   capture_output=True, timeout=60, check=False)
    if Path(LSREGISTER).exists():
        subprocess.run([LSREGISTER, "-f", str(target)],  # nosec B603
                       capture_output=True, timeout=60, check=False)


def _linux(target: Path, argv: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    quoted = " ".join(f'"{a}"' if " " in a else a for a in argv)
    target.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={NAME}\nComment=Open Flash in your browser\n"
        f"Exec={quoted}\nIcon={_icon('flash-256.png')}\n"
        "Terminal=false\nCategories=Utility;Development;\n",
        encoding="utf-8")
    target.chmod(0o755)


def install() -> str:
    """Make the shortcut, or make it again. What was done, said."""

    target, argv = place(), command()
    {"windows": _windows, "mac": _mac, "linux": _linux}[_host()](
        target, argv)
    where = {"windows": "in the Start menu", "mac": "in ~/Applications",
             "linux": "in your applications menu"}[_host()]
    links = " It also opens flash:// links." if _host() == "mac" else ""
    return f"Added {NAME} {where}: it opens Flash in your browser.{links}"


def remove() -> str:
    target = place()
    if not target.exists():
        return f"There was no {NAME} shortcut to remove."
    if target.is_dir():
        if _host() == "mac" and Path(LSREGISTER).exists():
            subprocess.run([LSREGISTER, "-u", str(target)],  # nosec B603
                           capture_output=True, timeout=60, check=False)
        shutil.rmtree(target)
    else:
        target.unlink()
    return f"Removed the {NAME} shortcut."
