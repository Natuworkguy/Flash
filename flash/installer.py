"""Installing a package Flash needs, from a button in the web UI.

When something needs a package that is not installed (voice mode's, a
Kokoro voice's, the browser tools' Playwright...), the page offers to
install it. It goes into this Flash's own environment: with `pipx
inject` for a Flash installed with pipx, which keeps an environment of
its own that pip cannot reach, and with this Python's pip otherwise.
Some need a step after: Playwright its browser.

Only the packages Flash itself asks for can be installed this way, never
whatever a request names. One install runs at a time, on a thread of its
own; every line it prints goes to whoever is listening, and the latest
are kept, for a page that opens its logs later.
"""

import importlib
import re
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

# What can be installed, each with what has to run after it.
PACKAGES: dict[str, list[list[str]]] = {
    "vosk": [],
    "piper-tts": [],
    "sounddevice": [],
    "kokoro-onnx": [],
    "moonshine-voice": [],
    "playwright": [[sys.executable, "-m", "playwright", "install",
                    "chromium"]],
    "python-pptx": [],
    "pypdf": [],
    "python-docx": [],
}
KEPT_LINES = 400
LINE_CHARS = 2000


# Flash's own requirements, beside a clone of it.
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
# Where a package's import name is not its name.
IMPORT_NAMES = {"dotenv": "python-dotenv", "docx": "python-docx",
                "yaml": "pyyaml", "PIL": "pillow"}


def required() -> list[str]:
    """What Flash needs, as pip takes it: from requirements.txt beside a
    clone, else from the installed package's own record."""

    try:
        lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        try:
            from importlib.metadata import requires

            lines = [r for r in requires("flash") or []
                     if "extra ==" not in r]
        except Exception:  # noqa: BLE001  (not installed as a package)
            lines = []
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _name(requirement: str) -> str:
    return re.split(r"[\s;<>=!~\[]", requirement, maxsplit=1)[0] \
        .lower().replace("_", "-")


def requirement_for(module: str) -> Optional[str]:
    """The requirement of Flash's that MODULE comes from, if one does."""

    top = (module or "").split(".")[0]
    wanted = IMPORT_NAMES.get(top, top).lower().replace("_", "-")
    return next((r for r in required() if _name(r) == wanted), None)


def repair(error: ModuleNotFoundError,
           say: Callable[[str], None] = print) -> bool:
    """ERROR is a package Flash needs, not installed: install everything
    Flash needs into this Python, as setting it up would have. Whether it
    did; False, doing nothing, for a module that is not Flash's."""

    if requirement_for(error.name or "") is None:
        return False
    packages = required()
    pipx = _pipx()
    command = ([pipx, "inject", "flash", *packages] if pipx
               else [sys.executable, "-m", "pip", "install", *packages])
    say(f"{error.name} isn't installed. Installing what Flash needs, then "
        "starting again.")
    say("$ " + " ".join(command))
    try:
        code = subprocess.call(command)  # nosec B603  (Flash's own list)
    except OSError as exc:
        say(f"Could not run it: {exc}")
        return False
    if code != 0:
        say(f"The install stopped with exit code {code}. Install them "
            "yourself with: pip install -r requirements.txt")
        return False
    importlib.invalidate_caches()
    return True


class InstallError(ValueError):
    """An install that cannot start, and why."""


def _pipx() -> Optional[str]:
    """pipx, when this Flash is one pipx installed."""

    if "pipx" not in sys.prefix.lower():
        return None
    return shutil.which("pipx")


def commands(packages: list[str]) -> list[list[str]]:
    """What installs PACKAGES into this Flash's environment, in order."""

    pipx = _pipx()
    first = (
        [pipx, "inject", "flash", *packages] if pipx
        else [sys.executable, "-m", "pip", "install", *packages]
    )
    after = [step for package in packages for step in PACKAGES[package]]
    return [first, *after]


class Installer:
    """The one install that runs at a time, and what it has said.

    TOLD hears each line as it comes, and the end: {"line"} or
    {"state", "ok"}. RUN stands in for subprocess.Popen, for tests.
    """

    def __init__(
        self, told: Callable[[dict], None],
        run: Optional[Callable] = None,
    ) -> None:
        self._told = told
        self._run = run or subprocess.Popen
        self._lock = threading.Lock()
        self.packages: list[str] = []
        self.lines: list[str] = []
        self.running = False
        self.ok: Optional[bool] = None
        self.started = 0.0

    def snapshot(self) -> dict:
        return {
            "packages": self.packages, "running": self.running,
            "ok": self.ok, "lines": list(self.lines),
            "started": self.started,
        }

    def start(self, packages: list[str]) -> dict:
        wanted = []
        for package in packages:
            name = str(package or "").strip().lower()
            if name not in PACKAGES:
                raise InstallError(f"{package!r} is not a package Flash "
                                   "installs.")
            if name not in wanted:
                wanted.append(name)
        if not wanted:
            raise InstallError("Say which package to install.")
        with self._lock:
            if self.running:
                raise InstallError(
                    f"{', '.join(self.packages)} is installing already."
                )
            self.running = True
            self.ok = None
            self.packages = wanted
            self.lines = []
            self.started = time.time()
            # As it starts: a quick one may be over before this returns.
            begun = self.snapshot()
        threading.Thread(target=self._go, args=(wanted,),
                         daemon=True).start()
        return begun

    def _say(self, line: str) -> None:
        line = line.rstrip()[:LINE_CHARS]
        self.lines.append(line)
        del self.lines[:-KEPT_LINES]
        self._told({"line": line})

    def _go(self, packages: list[str]) -> None:
        ok = True
        try:
            for command in commands(packages):
                self._say("$ " + " ".join(command))
                try:
                    # Fixed commands, from PACKAGES only.
                    running = self._run(  # nosec B603
                        command, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                        text=True, errors="replace", bufsize=1,
                    )
                except OSError as exc:
                    self._say(f"Could not run it: {exc}")
                    ok = False
                    break
                for line in running.stdout:
                    self._say(line)
                code = running.wait()
                if code != 0:
                    self._say(f"It stopped with exit code {code}.")
                    ok = False
                    break
        except Exception as exc:  # noqa: BLE001
            self._say(f"The install failed: {exc}")
            ok = False
        # What was just installed is importable from now on.
        importlib.invalidate_caches()
        self._say("Installed." if ok else "Not installed.")
        with self._lock:
            self.running = False
            self.ok = ok
        self._told({"state": "done", "ok": ok, "packages": packages})
