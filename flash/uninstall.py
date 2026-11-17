"""flash uninstall: take Flash off this machine.

Everything Flash set up outside its own package goes first: the sparks
and Hey Flash services that start at login, the flash:// handler and the
shortcut. Then the package itself, the way it was installed. What you
made with Flash (chats, memories, sparks, settings in ~/.flash) stays
unless you ask for it to go, with --purge or a yes when asked.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Callable, Optional

DATA = Path.home() / ".flash"


def _step(say: Callable[[str], None], what: str, run: Callable[[], object]
          ) -> None:
    """Run one step; a failure is said and the rest still runs."""

    try:
        done = run()
    except Exception as exc:  # noqa: BLE001 -- said, never raised
        say(f"{what}: could not ({exc}).")
        return
    if isinstance(done, str) and done:
        say(done)


def _services(say: Callable[[str], None]) -> None:
    from . import keepalive

    for service, name in ((keepalive.SPARKS, "sparks"),
                          (keepalive.LISTENER, "Hey Flash")):
        def off(service=service, name=name) -> str:
            return (f"Turned off the {name} service." if
                    keepalive.turn_off(service) else "")
        _step(say, f"Turning off the {name} service", off)


def _scheme() -> str:
    from .urlscheme import unregister

    return "Removed the flash:// handler." if unregister() else ""


def _shortcut() -> str:
    from . import launcher

    said = launcher.remove()
    return "" if said.startswith("There was no") else said


def how_installed() -> str:
    """'pipx', 'checkout' (pip -e from a folder), or 'pip'."""

    if "pipx" in sys.prefix.lower():
        return "pipx"
    here = Path(__file__).resolve().parent.parent
    if (here / ".git").exists() or (here / "pyproject.toml").exists():
        return "checkout"
    return "pip"


def package_command(how: Optional[str] = None) -> Optional[list[str]]:
    """What removes the package, or None for a checkout (its folder is
    yours to delete)."""

    how = how or how_installed()
    if how == "pipx":
        return [shutil.which("pipx") or "pipx", "uninstall", "flash"]
    if how == "pip":
        return [sys.executable, "-m", "pip", "uninstall", "-y", "flash"]
    return None


def _remove_package(say: Callable[[str], None]) -> None:
    how = how_installed()
    command = package_command(how)
    if command is None:
        here = Path(__file__).resolve().parent.parent
        say(f"Flash runs from {here}. Delete that folder to finish.")
        return
    if os.name == "nt":
        # Windows will not delete flash.exe while it runs: removed by a
        # hidden shell a moment after this one exits.
        line = subprocess.list2cmdline(command)
        subprocess.Popen(  # nosec B603 B607 -- pipx/pip, fixed args
            ["cmd", "/c", f"timeout /t 2 /nobreak >nul & {line}"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True)
        say("Flash will be removed in a moment, once this window is "
            "done with it.")
        return
    done = subprocess.run(command, capture_output=True, text=True,  # nosec B603
                          check=False)
    if done.returncode == 0:
        say("Removed the flash command.")
        return
    why = (done.stderr or done.stdout).strip().splitlines()
    say("Could not remove the flash command: "
        + (why[-1] if why else " ".join(command)))


def _data(say: Callable[[str], None]) -> None:
    if not DATA.exists():
        return
    shutil.rmtree(DATA, ignore_errors=True)
    if DATA.exists():
        say(f"Could not delete everything in {DATA}.")
    else:
        say(f"Deleted {DATA}.")


def run(*, purge: Optional[bool], say: Callable[[str], None],
        ask: Callable[[str], bool], package: bool = True) -> None:
    """Uninstall. PURGE True deletes ~/.flash, False keeps it, None asks."""

    _services(say)
    _step(say, "Removing the flash:// handler", _scheme)
    _step(say, "Removing the shortcut", _shortcut)
    if purge is None and DATA.exists():
        purge = ask(f"Also delete your chats, memories and settings in "
                    f"{DATA}?")
    if purge:
        _data(say)
    elif DATA.exists():
        say(f"Kept your chats, memories and settings in {DATA}.")
    if package:
        _remove_package(say)
