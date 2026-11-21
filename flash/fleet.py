"""/fleet: several chats take on the same task at once, side by side.

Each works in a private worktree of its own (worktrees.py), so none of
them touches the main checkout or each other. Each runs as a Flash of
its own in the background (fleet_worker.py): one turn at a time is what
a folder can take in one process, and a fleet is several at once. The
terminal shows them all working; when they are done it lays out what
each changed, can run the same check in every one, shows any one's
diff, and merges the one picked into the main checkout. The rest are
deleted with their branches. Nothing is pushed.

    /fleet fix the flaky upload test             three chats, this model
    /fleet 4: make the sidebar collapsible       four
    /fleet model-a, model-b: speed up the build  one chat a model
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess  # nosec B404 -- this Python running Flash, and git
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import worktrees

USAGE = ("Usage: /fleet [how many, or model, model]: the task. "
         "/fleet fix the login bug runs three; /fleet 4: ... or /fleet a, b: ...")
DEFAULT = 3
MOST = 6
CHECK_TIMEOUT = 600

RUNNING, DONE, FAILED, STOPPED = "running", "done", "error", "stopped"

FLEET_PROMPT = """
=== Fleet ===
{others} other chat{s} are given this same task at once, each in a worktree
of its own. The user will compare what each of you did and keep one. Do the
whole task here, as well as you can, and check it works (run the tests or
the code). Commit the work in this worktree when it is done. End with a
short summary: what you changed, and how you know it works.
""".strip()


def home() -> Path:
    from . import paths

    return paths.FLASH_DIR / "fleet"


def parse(text: str, model: str) -> tuple[list[str], str]:
    """The models (one per chat) and the task in "/fleet ...". The part
    before a colon counts only when it is a number or a list of models
    with commas, so "fix this: it crashes" is all task."""

    text = re.sub(r"^/fleet\b", "", str(text or "").strip()).strip()
    models: list[str] = []
    head, colon, rest = text.partition(":")
    head = head.strip()
    if colon and rest.strip():
        if re.fullmatch(r"\d+", head):
            models = [model] * int(head)
            text = rest.strip()
        elif re.fullmatch(r"[^\s,]+(?:\s*,\s*[^\s,]+)+", head):
            models = [m.strip() for m in head.split(",")]
            text = rest.strip()
    if not text:
        raise ValueError(USAGE)
    models = models or [model] * DEFAULT
    if not all(models):
        raise ValueError("Pick a model first (/model), or name them: /fleet a, b: the task")
    if not 2 <= len(models) <= MOST:
        raise ValueError(f"A fleet is 2 to {MOST} chats.")
    return models, text


@dataclass
class Member:
    """One chat of a fleet."""

    n: int
    model: str
    path: str
    branch: str
    spec: str
    state: str = RUNNING
    activity: str = "Starting"
    steps: list[str] = field(default_factory=list)
    result: str = ""
    rounds: int = 0
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    proc: Optional[subprocess.Popen] = None
    # After it is done: what it changed, and how a check went.
    files: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    check: Optional[bool] = None
    check_said: str = ""

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def summary(self) -> str:
        for line in self.result.splitlines():
            line = line.strip().lstrip("#*- ").strip()
            if line:
                return line
        return ""


def _launch(member: Member, log: Path) -> subprocess.Popen:
    out = open(log, "w", encoding="utf-8")  # noqa: SIM115  (the child's)
    try:
        return subprocess.Popen(  # nosec B603 -- this Python running Flash
            [sys.executable, "-m", "flash.fleet_worker", member.spec],
            cwd=member.path, stdin=subprocess.DEVNULL, stdout=out,
            stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "FLASH_NO_LOADER": "1"})
    finally:
        out.close()


class Fleet:
    """The chats working on one task, and what to do with their work."""

    def __init__(self, folder: str, task: str, base: str, place: Path) -> None:
        self.id = place.name
        self.folder = folder
        self.task = task
        self.base = base
        self.place = place
        self.members: list[Member] = []

    @classmethod
    def start(cls, folder: str, task: str, models: list[str], sandbox: bool = False,
              launch: Callable[[Member, Path], subprocess.Popen] = _launch) -> "Fleet":
        main = worktrees.repo_of(folder)
        if main is None:
            raise worktrees.WorktreeError("a fleet works in a git repository: this folder is not in one")
        base = worktrees._git(main, "rev-parse", "HEAD").strip()
        place = home() / uuid.uuid4().hex[:8]
        place.mkdir(parents=True, exist_ok=True)
        fleet = cls(main, task, base, place)
        others = len(models) - 1
        prompt = FLEET_PROMPT.format(others=others, s="" if others == 1 else "s")
        try:
            for n, model in enumerate(models, 1):
                made = worktrees.create(main, f"fleet {fleet.id} {n}")
                spec = place / f"{n}.json"
                spec.write_text(json.dumps({
                    "task": task, "prompt": prompt, "model": model,
                    "path": made["path"], "status": str(place / f"{n}.status.json"),
                    "sandbox": sandbox,
                }), encoding="utf-8")
                member = Member(n=n, model=model, path=made["path"],
                                branch=made["branch"], spec=str(spec))
                fleet.members.append(member)
                member.proc = launch(member, place / f"{n}.log")
        except Exception:
            fleet.stop()
            fleet.discard()
            raise
        return fleet

    def refresh(self) -> None:
        """Each chat's state, from what its Flash last wrote."""

        for m in self.members:
            if m.state != RUNNING:
                continue
            try:
                said = json.loads(Path(self.place / f"{m.n}.status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                said = {}
            for key in ("state", "activity", "steps", "result", "rounds"):
                if key in said:
                    setattr(m, key, said[key])
            if m.state != RUNNING:
                m.finished = said.get("finished") or time.time()
            elif m.proc is not None and m.proc.poll() is not None:
                # Gone without saying it was done.
                m.state, m.finished = FAILED, time.time()
                m.result = "It stopped: " + self._last_words(m)

    def _last_words(self, m: Member) -> str:
        try:
            lines = (self.place / f"{m.n}.log").read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            lines = []
        return lines[-1][:300] if lines else "no word why"

    def running(self) -> bool:
        return any(m.state == RUNNING for m in self.members)

    def stop(self) -> None:
        """Every chat still working stops now, and what it runs with it."""

        for m in self.members:
            if m.proc is not None and m.proc.poll() is None:
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(m.proc.pid, signal.SIGTERM)
                    else:
                        m.proc.terminate()
                except (OSError, ProcessLookupError):
                    pass
            if m.state == RUNNING:
                m.state, m.finished = STOPPED, time.time()
                m.result = m.result or "Stopped."

    # What they did.

    def _stage(self, m: Member) -> None:
        # The worktree is the fleet's own: staging everything there is how
        # new files show in its diff against where it started.
        worktrees._git(m.path, "add", "-A", check=False)

    def tally(self) -> None:
        """What each changed, from where they all started."""

        for m in self.members:
            if not os.path.isdir(m.path):
                continue
            self._stage(m)
            m.files, m.added, m.removed = [], 0, 0
            for line in worktrees._git(m.path, "diff", "--cached", "--numstat", self.base,
                                       check=False).splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                m.files.append(parts[2])
                m.added += int(parts[0]) if parts[0].isdigit() else 0
                m.removed += int(parts[1]) if parts[1].isdigit() else 0

    def diff(self, n: int) -> str:
        m = self.member(n)
        self._stage(m)
        return worktrees._git(m.path, "diff", "--cached", "--stat", "--patch", self.base, check=False)

    def run_check(self, command: str) -> None:
        """COMMAND (the tests, a build) run in every worktree at once."""

        def one(m: Member) -> None:
            try:
                done = subprocess.run(  # nosec B602 -- the user's own command
                    command, shell=True, cwd=m.path, capture_output=True, text=True,
                    timeout=CHECK_TIMEOUT, check=False)
                m.check = done.returncode == 0
                said = (done.stdout + done.stderr).strip().splitlines()
                m.check_said = said[-1][:200] if said else ""
            except subprocess.TimeoutExpired:
                m.check, m.check_said = False, f"took over {CHECK_TIMEOUT // 60} minutes"
            except OSError as exc:
                m.check, m.check_said = False, str(exc)

        threads = [threading.Thread(target=one, args=(m,)) for m in self.members
                   if os.path.isdir(m.path)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def member(self, n: int) -> Member:
        for m in self.members:
            if m.n == n:
                return m
        raise ValueError(f"There is no chat {n}: pick 1 to {len(self.members)}.")

    # Keeping one.

    def merge(self, n: int) -> str:
        """Chat N's work into the main checkout; the others deleted."""

        m = self.member(n)
        if m.state == RUNNING:
            raise ValueError(f"Chat {n} is still working.")
        said = worktrees.merge(self.folder, m.path)
        worktrees.remove(self.folder, m.path)
        self.discard(keep=n)
        return said

    def discard(self, keep: int = 0) -> None:
        """Every chat's worktree and branch deleted (but KEEP's)."""

        for m in self.members:
            if m.n == keep or not os.path.isdir(m.path):
                continue
            try:
                worktrees.remove(self.folder, m.path, force=True)
            except worktrees.WorktreeError:
                pass
        import shutil

        shutil.rmtree(self.place, ignore_errors=True)
