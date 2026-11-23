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

import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Optional

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
        "$s.Description = 'Open Flash'; $s.Save()")
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
    with the link's prompt. Flash starts in the background, with the
    places a Mac keeps its tools (Homebrew's, pipx's) on its PATH, and
    writes what it says to ~/.flash/launcher.log. The applet waits up to
    ten seconds: if Flash stops by then with an error, or the Python it
    runs on is gone (moved, or replaced by an update), it says so in an
    alert rather than doing nothing; a Flash that is still running by
    then is serving, and the applet quits."""

    def shell(parts: list[str]) -> str:
        return " ".join("'" + p.replace("'", "'\\''") + "'" for p in parts)

    def literal(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    log, status = FLASH_DIR / "launcher.log", FLASH_DIR / "launcher.status"
    opened = literal(shell([*base, *OPEN]))
    linked = literal(shell([*base, "--web", "--uri"]) + " ")
    return APPLET.format(
        opened=opened, linked=linked, log=literal(str(log)),
        status=literal(str(status)), py=literal(base[0]))


# The applet's script: see _applescript. Kept to short lines, the parts of
# its long commands put together a piece at a time.
APPLET = """on run
  launch({opened})
end run
on open location theURL
  launch({linked} & quoted form of theURL)
end open location
on launch(cmd)
  set logPath to {log}
  set statusPath to {status}
  set py to {py}
  try
    do shell script "test -x " & quoted form of py
  on error
    activate
    set why to "The Python it runs on is gone (" & py & "): Flash was "
    set why to why & "moved or updated. Open Terminal and run "
    set why to why & "flash --shortcut to fix this app."
    display alert "Flash can't start" message why as critical
    return
  end try
  set here to do shell script "dirname " & quoted form of logPath
  set job to cmd & " >" & quoted form of logPath & " 2>&1; "
  set job to job & "echo $? >" & quoted form of statusPath
  set todo to "mkdir -p " & quoted form of here & "; "
  set todo to todo & "rm -f " & quoted form of statusPath & "; "
  set todo to todo & "export PATH=/opt/homebrew/bin:/usr/local/bin:"
  set todo to todo & "$HOME/.local/bin:$PATH; "
  set todo to todo & "/bin/sh -c " & quoted form of job
  do shell script todo & " >/dev/null 2>&1 &"
  repeat 40 times
    delay 0.25
    try
      set code to do shell script "cat " & quoted form of statusPath
    on error
      set code to ""
    end try
    if code is not "" then
      if code is not "0" then
        set said to do shell script "tail -n 12 " & quoted form of logPath
        set said to said & linefeed & linefeed & "(All of it is in "
        set said to said & logPath & ".)"
        activate
        set title to "Flash stopped as it started"
        display alert title message said as critical
      end if
      return
    end if
  end repeat
end launch
"""


WINDOW_SOURCE = Path(__file__).with_name("window.swift")


def _swiftc() -> Optional[str]:
    """Apple's Swift compiler, from the Command Line Tools or Xcode, or None."""

    found = shutil.which("swiftc")
    if found:
        return found
    if _host() != "mac":
        return None
    try:
        done = subprocess.run(["xcrun", "--find", "swiftc"],  # nosec B603 B607
                              capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def _window_stamp(base: list[str]) -> str:
    """What the window app is made from: its source and the command it runs."""

    return hashlib.sha256(WINDOW_SOURCE.read_bytes() + json.dumps(base).encode()).hexdigest()


def _window_app(target: Path, base: list[str], swiftc: str) -> None:
    """Flash.app as a window of its own (window.swift): Flash's web UI in a native window, not a browser tab."""

    from .web import RUNNING_FILE
    from . import workspace

    work = FLASH_DIR / "window-build"
    shutil.rmtree(work, ignore_errors=True)
    contents = work / f"{NAME}.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    done = subprocess.run(  # nosec B603 -- a fixed tool, on Flash's own source
        [swiftc, "-O", "-target", f"{arch}-apple-macos12.0", "-framework", "Cocoa", "-framework", "WebKit",
         str(WINDOW_SOURCE), "-o", str(contents / "MacOS" / NAME)],
        capture_output=True, text=True, timeout=600, check=False)
    if done.returncode:
        raise ShortcutError("Swift would not build Flash's window: " + (done.stderr.strip()[-600:] or "no reason given"))
    (contents / "Resources" / "launch.json").write_text(json.dumps({
        "command": [*base, *OPEN], "log": str(FLASH_DIR / "launcher.log"),
        "status": str(FLASH_DIR / "launcher.status"), "running": str(workspace.store() / RUNNING_FILE),
    }), encoding="utf-8")
    shutil.copyfile(WEB / "flash.icns", contents / "Resources" / "Flash.icns")
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleExecutable": NAME,
            "CFBundleIdentifier": "app.flash.open", "CFBundleIconFile": "Flash", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Flash listens when you talk to it in voice mode and on calls.",
            "NSCameraUsageDescription": "Flash sees what you show it when you turn the camera on.",
            "CFBundleURLTypes": [{"CFBundleURLName": "Flash link", "CFBundleURLSchemes": ["flash"]}],
        }, f)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(work / f"{NAME}.app")],  # nosec B603 B607
                   capture_output=True, timeout=120, check=False)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(work / f"{NAME}.app"), str(target))
    shutil.rmtree(work, ignore_errors=True)
    (FLASH_DIR / "window.stamp").write_text(_window_stamp(base), encoding="utf-8")
    if Path(LSREGISTER).exists():
        subprocess.run([LSREGISTER, "-f", str(target)],  # nosec B603
                       capture_output=True, timeout=60, check=False)


def _mac(target: Path, argv: list[str]) -> None:
    base = argv[:-len(OPEN)] if argv[-len(OPEN):] == list(OPEN) else argv
    # A window of its own where Swift is there to build it (Apple's Command Line Tools), else the applet that
    # opens Flash in the browser. FLASH_WINDOW=0 asks for the browser.
    swiftc = _swiftc() if os.environ.get("FLASH_WINDOW", "1") != "0" else None
    if swiftc:
        try:
            _window_app(target, base, swiftc)
            return
        except (ShortcutError, OSError, subprocess.SubprocessError):
            pass
    (FLASH_DIR / "window.stamp").unlink(missing_ok=True)
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


def refresh() -> bool:
    """On a Mac, make the Flash app again when what it would run has
    changed (a newer applet, or Flash moved), so a fix to the app reaches
    it without anyone running --shortcut. Quiet, and never in the way:
    True when it was made again."""

    if _host() != "mac" or not place().exists():
        return False
    source = FLASH_DIR / "launcher.applescript"
    base = base_command()
    try:
        stamp = (FLASH_DIR / "window.stamp").read_text(encoding="utf-8")
    except OSError:
        stamp = ""
    wants_window = os.environ.get("FLASH_WINDOW", "1") != "0"
    try:
        if stamp and stamp == _window_stamp(base) and wants_window:
            return False
        if not stamp and source.read_text(encoding="utf-8") == _applescript(base) and \
                (not wants_window or not _swiftc()):
            return False
    except OSError:
        pass
    try:
        _mac(place(), [*base, *OPEN])
    # a shortcut is never worth failing over
    except Exception:  # noqa: BLE001
        return False
    return True


def _linux(target: Path, argv: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    quoted = " ".join(f'"{a}"' if " " in a else a for a in argv)
    target.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={NAME}\nComment=Open Flash\n"
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
    from .web import opens_in_window
    if _host() == "mac":
        how = "in a window of its own" if (FLASH_DIR / "window.stamp").exists() else "in your browser"
    else:
        how = "in a window of its own" if opens_in_window() else "in your browser"
    return f"Added {NAME} {where}: it opens Flash {how}.{links}"


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
