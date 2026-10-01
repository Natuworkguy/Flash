"""Sparks always on: a keeper the operating system starts at login.

Left to themselves, sparks work while a Flash is open. Always on, a
Flash of their own, `flash --sparks`, runs from login onward with no
window, and keeps them working with the terminal and the browser both
closed. It is registered the way each system expects a program the user
wants running in the background:

- Linux: a systemd user service, or, without systemd, an autostart
  entry for the desktop session, started once by hand right away.
- macOS: a launchd agent in ~/Library/LaunchAgents.
- Windows: a scheduled task that starts at logon, with pythonw so no
  console window opens.

Nothing here needs administrator rights, and turning it off removes
what turning it on wrote. The keeper and an open Flash share the work
through the lock in flash/sparks.py, so running both is fine.
"""

import os
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Optional

from . import sparks

NAME = "flash-sparks"
LABEL = "io.github.natuworkguy.flash.sparks"
TASK = "Flash Sparks"


class KeepAliveError(RuntimeError):
    """Sparks could not be set to run always on this machine."""


def _host() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _checkout_script() -> Optional[Path]:
    """run.py, when this Flash runs from a clone of its repository."""

    script = Path(__file__).resolve().parent.parent / "run.py"
    return script if script.is_file() else None


def command() -> list[str]:
    """What the system runs: this Python, this Flash, keeper only.

    This interpreter rather than whatever `flash` is on PATH at login: a
    pipx install's is the one with Flash's packages in it. And this
    Flash: run from a clone, its run.py, since `-m flash` from the home
    folder would load whatever other Flash that Python has installed,
    and every shift would run on that one's code instead.
    """

    python = Path(sys.executable)
    if _host() == "windows":
        quiet = python.with_name("pythonw.exe")
        if quiet.is_file():
            python = quiet
    script = _checkout_script()
    if script is not None:
        return [str(python), str(script), "--sparks"]
    return [str(python), "-m", "flash", "--sparks"]


def _log() -> Path:
    return sparks.sparks_dir() / "keeper.log"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 -- fixed argument lists
        args, check=False, capture_output=True, text=True, timeout=30,
    )


def _ok(args: list[str]) -> bool:
    if not shutil.which(args[0]):
        return False
    try:
        return _run(args).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --- Linux ---------------------------------------------------------------


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{NAME}.service"


def _autostart_path() -> Path:
    return Path.home() / ".config" / "autostart" / f"{NAME}.desktop"


def _unit() -> str:
    # systemd reads % as the start of a specifier, so each is doubled.
    run = " ".join(f'"{part}"' for part in command()).replace("%", "%%")
    # The login session's PATH, not systemd's short default, so a spark
    # finds the same programs a shell would.
    path = os.environ.get("PATH", "").replace("%", "%%")
    return (
        "[Unit]\n"
        "Description=Flash sparks, working while Flash is closed\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={run}\n"
        "WorkingDirectory=%h\n"
        f'Environment="PATH={path}"\n'
        "Restart=on-failure\n"
        "RestartSec=30\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd() -> bool:
    return _ok(["systemctl", "--user", "show-environment"])


def _arg(part) -> str:
    # A path in a command line is written with forward slashes.
    return part.as_posix() if isinstance(part, Path) else str(part)


def _exec_line() -> str:
    # The desktop entry spec quotes an argument with a space in it.
    return " ".join(
        f'"{arg}"' if " " in arg else arg
        for arg in map(_arg, command())
    )


def _on_linux() -> str:
    if _systemd():
        path = _unit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_unit(), encoding="utf-8")
        _ok(["systemctl", "--user", "daemon-reload"])
        if not _ok(["systemctl", "--user", "enable", "--now", NAME]):
            raise KeepAliveError(
                f"systemd would not start {NAME}. "
                f"`systemctl --user status {NAME}` says why."
            )
        return (
            "a systemd user service. It runs while you are logged in; "
            "`loginctl enable-linger` keeps it running after you log out"
        )

    # No systemd: the desktop starts it at the next login, and it is
    # started by hand for this one.
    path = _autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Flash Sparks\n"
        "Comment=Flash sparks, working while Flash is closed\n"
        f"Exec={_exec_line()}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    _spawn()
    return "an autostart entry, started at each login to your desktop"


def _off_linux() -> bool:
    removed = False
    if _unit_path().exists():
        _ok(["systemctl", "--user", "disable", "--now", NAME])
        _unit_path().unlink()
        _ok(["systemctl", "--user", "daemon-reload"])
        removed = True
    if _autostart_path().exists():
        _autostart_path().unlink()
        removed = True
    return removed


# --- macOS ---------------------------------------------------------------


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _plist() -> str:
    def esc(text: str) -> str:
        return (
            text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    args = "".join(
        f"\n        <string>{esc(_arg(part))}</string>" for part in command()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>WorkingDirectory</key>
    <string>{esc(str(Path.home()))}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{esc(os.environ.get("PATH", ""))}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{esc(str(_log()))}</string>
    <key>StandardErrorPath</key>
    <string>{esc(str(_log()))}</string>
</dict>
</plist>
"""


def _on_macos() -> str:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _log().parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    # Loaded already, from an older Flash: out first, so the new one
    # is what runs.
    _ok(["launchctl", "bootout", f"{domain}/{LABEL}"])
    path.write_text(_plist(), encoding="utf-8")
    if not (
        _ok(["launchctl", "bootstrap", domain, str(path)])
        or _ok(["launchctl", "load", "-w", str(path)])
    ):
        raise KeepAliveError("launchd would not load the sparks agent.")
    return "a launchd agent, started at each login"


def _off_macos() -> bool:
    path = _plist_path()
    if not path.exists():
        return False
    if not _ok(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]):
        _ok(["launchctl", "unload", "-w", str(path)])
    path.unlink()
    return True


# --- Windows -------------------------------------------------------------


def _task_line() -> str:
    return " ".join(f'"{_arg(part)}"' for part in command())


def _on_windows() -> str:
    made = _ok([
        "schtasks", "/Create", "/F", "/TN", TASK, "/SC", "ONLOGON",
        "/RL", "LIMITED", "/TR", _task_line(),
    ])
    if not made:
        raise KeepAliveError(
            "Task Scheduler would not take the sparks task."
        )
    # A logon trigger waits for the next logon; this is the one now.
    if not _ok(["schtasks", "/Run", "/TN", TASK]):
        _spawn()
    return "a scheduled task, started at each logon"


def _off_windows() -> bool:
    if not _ok(["schtasks", "/Query", "/TN", TASK]):
        return False
    _ok(["schtasks", "/End", "/TN", TASK])
    return _ok(["schtasks", "/Delete", "/F", "/TN", TASK])


# --- Either way ----------------------------------------------------------


def _spawn() -> None:
    """Start the keeper now, detached from this Flash."""

    log = _log()
    log.parent.mkdir(parents=True, exist_ok=True)
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = getattr(
            subprocess, "DETACHED_PROCESS", 0
        ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    with open(log, "ab") as out:
        subprocess.Popen(  # nosec B603 -- this Python, fixed arguments
            command(), cwd=str(Path.home()), stdin=subprocess.DEVNULL,
            stdout=out, stderr=out, **options,
        )


def installed() -> bool:
    """Whether sparks are set to run always, on this machine."""

    host = _host()
    if host == "macos":
        return _plist_path().exists()
    if host == "windows":
        return _ok(["schtasks", "/Query", "/TN", TASK])
    return _unit_path().exists() or _autostart_path().exists()


def turn_on() -> str:
    """Register the keeper to start at login, and start it. Says how."""

    try:
        how = {
            "linux": _on_linux, "macos": _on_macos, "windows": _on_windows,
        }[_host()]()
    except OSError as exc:
        raise KeepAliveError(str(exc)) from None
    return how


def turn_off() -> bool:
    """Undo turn_on. False if it was not on."""

    try:
        return {
            "linux": _off_linux, "macos": _off_macos,
            "windows": _off_windows,
        }[_host()]()
    except OSError as exc:
        raise KeepAliveError(str(exc)) from None


def status() -> dict:
    """For /sparks and the page: set up, and whether it is running now."""

    beat = sparks.keeper()
    running: Optional[dict] = beat if beat and beat.get("always") else None
    return {
        "installed": installed(),
        "running": running is not None,
        "since": running.get("since") if running else None,
        # An open Flash doing the work meanwhile: the always-on keeper
        # waits for its turn while one is.
        "here": bool(beat) and not (beat or {}).get("always"),
        # The keeper doing the work is another Flash than this one, in
        # words ("another Flash, at ..."), or "" when it is this one or
        # none is running.
        "other": _other(beat),
    }


def _other(beat: Optional[dict]) -> str:
    if not beat or beat.get("pid") == os.getpid():
        return ""
    if not beat.get("code"):
        # From before keepers said which Flash they are: older, then.
        return "an older Flash"
    if beat["code"] != sparks.code_identity()["code"]:
        return f"another Flash, at {beat['code']}"
    # This Flash's code, but is it as it is now? Measured against the
    # disk, not this process, which may be the one behind.
    if beat.get("stamp") != sparks.disk_stamp():
        return "this Flash as it was before its last update"
    return ""


# How long a takeover waits for the keeper it replaced to go, and for
# this Flash to take its place.
TAKEOVER_SECONDS = 15.0
TAKEOVER_STEP = 0.25


def _command_line(pid: int) -> str:
    """How process PID was started, or "" if that cannot be read."""

    if os.name == "nt":
        found = _run([
            "powershell", "-NoProfile", "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}')"
            ".CommandLine",
        ])
    else:
        found = _run(["ps", "-o", "command=", "-p", str(pid)])
    return found.stdout.strip() if found.returncode == 0 else ""


def _headless_keeper(pid: int) -> bool:
    """Whether PID is a `flash --sparks` keeper: nothing open on screen,
    so stopping it loses nobody's work but a shift's."""

    line = _command_line(pid)
    return "--sparks" in line and ("flash" in line or "run.py" in line)


def _stop_process(pid: int) -> None:
    if os.name == "nt":
        _run(["taskkill", "/PID", str(pid), "/F"])
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _wait_for(done, seconds: float) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if done():
            return True
        time.sleep(TAKEOVER_STEP)
    return done()


def take_over() -> dict:
    """Have this Flash run the sparks, in place of another that does.

    Always on is set up again from this Flash, which replaces the keeper
    the system started. A keeper the system did not start, a stray
    `flash --sparks`, is stopped. A Flash open on someone's screen is
    not: that is theirs to close, and a KeepAliveError says so. Waits
    for the change, and returns status() once it shows.
    """

    beat = sparks.keeper()
    if not _other(beat):
        return status()
    pid = int(beat.get("pid") or 0)
    headless = beat.get("always") or _headless_keeper(pid)
    if not headless:
        raise KeepAliveError(
            f"Your sparks are being run by a Flash that is open, in "
            f"process {pid}: {_other(beat)}. Close it, and this one takes "
            "over."
        )
    if installed():
        turn_off()
    turn_on()
    replaced = _wait_for(lambda: sparks.keeper() is None or int(
        (sparks.keeper() or {}).get("pid") or 0
    ) != pid, TAKEOVER_SECONDS / 2)
    if not replaced and sparks.alive(pid) and _headless_keeper(pid):
        # Not the system's to stop: started by hand, or left over from
        # an older Flash's always on.
        _stop_process(pid)

    def ours() -> bool:
        holder = sparks.keeper()
        return holder is not None and not _other(holder)

    if not _wait_for(ours, TAKEOVER_SECONDS) and _other(sparks.keeper()):
        still = _other(sparks.keeper())
        raise KeepAliveError(
            f"{still[:1].upper()}{still[1:]} is still running your sparks. "
            "Quitting it, or restarting this computer, lets this one take "
            "over."
        )
    return status()
