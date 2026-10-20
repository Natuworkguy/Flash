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
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable
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
                    running = self._run(  # nosec B603 -- fixed commands
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
