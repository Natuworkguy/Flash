"""Extensions' services: programs that run for as long as Flash's web page
does (a robot's face finder, say). Each is started with the page, started
again if it stops (waiting longer each time it keeps stopping), started
again when its extension is installed, updated or removed, and stopped
when Flash stops or restarts.

A service gets what an extension's tool gets: FLASH_WEB_URL and
FLASH_WEB_TOKEN for Flash's API, its settings, and Flash's own Python as
`python`. What it prints goes to a log of its own, in Flash's folder.
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # nosec B404 -- an installed extension's own program
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import extensions
from .paths import FLASH_DIR

CHECK_SECONDS = 3
# One that ran this long without stopping was fine: its next stop is waited
# on as little as a first.
SETTLED_SECONDS = 60
LONGEST_WAIT = 60
STOP_SECONDS = 3


# Folders an extension's program never runs from: not looked through for
# changes.
SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pio",
        "build", "dist"}


def stamp(folder: Path) -> tuple[int, int]:
    """How the files in FOLDER stand now (how many, and the newest
    change): different once an update has changed any of them, though the
    command that starts the service has not."""

    count, newest = 0, 0
    todo = [folder]
    while todo:
        try:
            with os.scandir(todo.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP:
                                todo.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            count += 1
                            changed = entry.stat(follow_symlinks=False)
                            newest = max(newest, changed.st_mtime_ns)
                    except OSError:
                        continue
        except OSError:
            continue
    return count, newest


def log_path(extension: str, service: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in service)
    return FLASH_DIR / "logs" / f"{extension}-{safe}.log"


class Running:
    """One service's program, and how its stops have gone."""

    def __init__(
        self, argv: list[str], files: tuple[int, int] = (0, 0)
    ) -> None:
        self.argv = argv
        self.files = files
        self.process: Optional[subprocess.Popen] = None
        self.started = 0.0
        self.stops = 0
        self.not_before = 0.0

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=STOP_SECONDS)


class Supervisor:
    """Keeps every installed extension's services running."""

    def __init__(self, check_seconds: float = CHECK_SECONDS) -> None:
        self.check_seconds = check_seconds
        self._running: dict[tuple[str, str], Running] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch, name="extension-services", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self.check_seconds + 1)
        with self._lock:
            for running in self._running.values():
                running.stop()
            self._running.clear()

    def names(self) -> list[str]:
        """ "bot: eyes", for each one running now."""

        with self._lock:
            return [
                f"{e}: {s}"
                for (e, s), r in self._running.items()
                if r.process is not None and r.process.poll() is None
            ]

    def _wanted(self) -> dict[tuple[str, str], tuple[Any, Any]]:
        wanted = {}
        try:
            installed = extensions.installed()
        except Exception:  # noqa: BLE001 -- a broken install never stops the rest
            return {}
        for extension in installed:
            for service in extension.services:
                wanted[(extension.name, service.name)] = (extension, service)
        return wanted

    def check(self) -> None:
        """Once: start what should run, stop what shouldn't."""

        wanted = self._wanted()
        now = time.monotonic()
        # An update that changed its files (not its command) starts it
        # again too, with the new program.
        stamps = {e.name: stamp(e.path) for e, _ in wanted.values()}
        with self._lock:
            for key in list(self._running):
                running = self._running[key]
                found = wanted.get(key)
                gone = (
                    found is None
                    or extensions.argv(found[0], found[1].run) != running.argv
                    or stamps.get(key[0]) != running.files
                )
                # Removed, or changed by an update: stopped (and, if
                # changed, started anew below).
                if gone:
                    running.stop()
                    del self._running[key]
            for key, (extension, service) in wanted.items():
                argv = extensions.argv(extension, service.run)
                running = self._running.setdefault(
                    key, Running(argv, stamps.get(key[0], (0, 0))))
                process = running.process
                if process is not None and process.poll() is None:
                    continue
                if process is not None:
                    # It stopped: started again, after a wait that grows
                    # while it keeps stopping.
                    running.process = None
                    if now - running.started >= SETTLED_SECONDS:
                        running.stops = 0
                    running.stops += 1
                    running.not_before = now + min(
                        LONGEST_WAIT, 2**running.stops
                    )
                if now < running.not_before:
                    continue
                running.process = self._launch(extension, service, argv)
                running.started = now
                if (
                    running.process is None
                ):  # couldn't start at all: tried again later
                    running.stops += 1
                    running.not_before = now + min(
                        LONGEST_WAIT, 2**running.stops
                    )

    def _launch(
        self, extension: Any, service: Any, argv: list[str]
    ) -> Optional[subprocess.Popen]:
        path = log_path(extension.name, service.name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            log = open(path, "w", encoding="utf-8")
        except OSError:
            log = subprocess.DEVNULL
        try:
            return subprocess.Popen(  # nosec B603 -- an installed extension's program
                argv,
                cwd=str(extension.path),
                env=extensions.environment(extension),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            if log is not subprocess.DEVNULL:
                log.write(f"Couldn't start {argv[0]}: {exc}\n")
            return None
        finally:
            if log is not subprocess.DEVNULL:
                log.close()

    def _watch(self) -> None:
        while not self._stop.is_set():
            self.check()
            self._stop.wait(self.check_seconds)


_supervisor: Optional[Supervisor] = None


def start() -> Supervisor:
    """The services, running (once, however often this is called)."""

    global _supervisor
    if _supervisor is None:
        _supervisor = Supervisor()
        import atexit

        atexit.register(stop)
    _supervisor.start()
    return _supervisor


def stop() -> None:
    if _supervisor is not None:
        _supervisor.stop()


def report(extension: str) -> list[dict]:
    """How EXTENSION's services are doing, for its page to show when one
    doesn't answer: each one's name, whether it runs now, how many times
    it has stopped, and the end of its log."""

    found = extensions.find(extension)
    if found is None:
        return []
    out = []
    for service in found.services:
        running = None
        if _supervisor is not None:
            with _supervisor._lock:
                running = _supervisor._running.get((found.name, service.name))
        process = running.process if running is not None else None
        try:
            lines = log_path(found.name, service.name).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        out.append({
            "name": service.name,
            "running": process is not None and process.poll() is None,
            "started": _supervisor is not None,
            "stops": running.stops if running is not None else 0,
            "log": "\n".join(lines[-15:])[-3000:],
        })
    return out
