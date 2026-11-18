"""Background tasks: commands that run on while the conversation goes on.

A build, a test run, a dev server, a long download: the model starts it
with the shell tool's "background", gets an id at once, and carries on.
What it prints goes to a log beside it. The model reads what is new in
that log whenever it likes (task_output), ends it when it is done with it
(task_stop), and when it ends on its own, its chat hears about it: on
its next turn, or, if the chat is idle, woken to say what happened. A
task the model stopped itself is not announced; it knows.

Tasks belong to whoever started them (a chat, a spark, a sub-agent, or
the terminal), so each hears only of its own, and live as long as Flash
does. Stop on a turn does not end them: they were set aside to outlive
it. task_stop, the Background panel, or quitting Flash does.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess  # nosec B404 -- the agent's own commands, approved as any
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .paths import FLASH_DIR

RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
MAX_RUNNING = 8
TAIL_CHARS = 4000
NOTICE_TAIL = 1500
STOP_GRACE = 3.0


def log_dir() -> Path:
    return FLASH_DIR / "tasks"


@dataclass
class Task:
    id: str
    command: str
    owner: str
    description: str = ""
    cwd: str = ""
    started: float = field(default_factory=time.time)
    ended: Optional[float] = None
    status: str = RUNNING
    code: Optional[int] = None
    log: str = ""
    pid: int = 0
    read_to: int = 0          # how much of the log the model has read
    delivered: bool = False   # its ending told to its owner (or it was stopped)

    def seconds(self) -> float:
        return (self.ended or time.time()) - self.started

    def label(self) -> str:
        return self.description or (self.command if len(self.command) <= 80 else self.command[:77] + "...")

    def row(self) -> dict:
        return {"id": self.id, "command": self.command, "description": self.description, "owner": self.owner,
                "status": self.status, "code": self.code, "seconds": round(self.seconds()), "pid": self.pid,
                "background": True}


_tasks: dict[str, Task] = {}
_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
_exit_hook: list = []


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def start(args: Union[str, list[str]], *, shell: bool, command: str, owner: str = "",
          description: str = "", env: Optional[dict] = None, cwd: Optional[str] = None) -> Task:
    """Start ARGS on its own, its output (both streams) to its log, and
    return at once."""

    with _lock:
        running = [t for t in _tasks.values() if t.owner == owner and t.status == RUNNING]
    if len(running) >= MAX_RUNNING:
        raise RuntimeError(f"{len(running)} background tasks are running already; stop one first "
                           f"(task_stop) or wait for one to end.")
    task = Task(id="b" + uuid.uuid4().hex[:7], command=command, owner=owner,
                description=" ".join(str(description or "").split())[:120], cwd=cwd or os.getcwd())
    folder = log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    task.log = str(folder / f"{task.id}.log")
    out = open(task.log, "wb")  # noqa: SIM115 -- handed to the process, closed when it ends
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    try:
        proc = subprocess.Popen(args, shell=shell, stdin=subprocess.DEVNULL, stdout=out,  # nosec B602 B603
                                stderr=subprocess.STDOUT, env=env, cwd=task.cwd, **group)
    except OSError:
        out.close()
        raise
    task.pid = proc.pid
    if not _exit_hook:
        # nothing outlives Flash: a dev server left running would hold its port
        atexit.register(stop_all)
        _exit_hook.append(True)
    with _lock:
        _tasks[task.id] = task
        _procs[task.id] = proc
    threading.Thread(target=_watch, args=(task.id, proc, out), daemon=True).start()
    return task


def _watch(task_id: str, proc: subprocess.Popen, out) -> None:
    code = proc.wait()
    try:
        out.close()
    except OSError:
        pass
    with _lock:
        task = _tasks.get(task_id)
        _procs.pop(task_id, None)
        if task is None:
            return
        task.ended = time.time()
        task.code = code
        if task.status == RUNNING:
            task.status = DONE if code == 0 else FAILED


def get(task_id: str) -> Optional[Task]:
    with _lock:
        return _tasks.get(str(task_id).strip())


def for_owner(owner: Optional[str] = None) -> list[Task]:
    """Tasks of OWNER (all with None), oldest first."""

    with _lock:
        return [t for t in _tasks.values() if owner is None or t.owner == owner]


def running() -> list[Task]:
    with _lock:
        return [t for t in _tasks.values() if t.status == RUNNING]


def _read(task: Task, start_at: int, most: int) -> tuple[str, int]:
    try:
        with open(task.log, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            begin = max(start_at, size - most)
            f.seek(begin)
            data = f.read(size - begin)
    except OSError:
        return "", start_at
    text = data.decode("utf-8", "replace")
    if begin > start_at:
        text = f"[{begin - start_at} earlier bytes not shown]\n" + text
    return text, size


def output(task_id: str, everything: bool = False, most: int = TAIL_CHARS) -> str:
    """What TASK printed since the model last read it (or all of it, up
    to its last MOST characters), with where it stands."""

    task = get(task_id)
    if task is None:
        return f"No background task {task_id!r}."
    text, size = _read(task, 0 if everything else task.read_to, most)
    with _lock:
        task.read_to = size
    state = _state(task)
    return f"{state}\n" + (text.rstrip() or "(nothing new)")


def _state(task: Task) -> str:
    if task.status == RUNNING:
        return f"Task {task.id} ({task.label()}) is running, {_clock(task.seconds())} so far."
    how = {DONE: "finished", FAILED: f"failed (exit {task.code})", STOPPED: "was stopped"}[task.status]
    return f"Task {task.id} ({task.label()}) {how} after {_clock(task.seconds())}."


def stop(task_id: str, told: bool = True) -> str:
    """End TASK and all it started. TOLD: the one asking knows it ended,
    so its ending is not announced again."""

    with _lock:
        task = _tasks.get(str(task_id).strip())
        proc = _procs.get(task.id) if task else None
    if task is None:
        return f"No background task {task_id!r}."
    if task.status != RUNNING or proc is None:
        return _state(task)
    with _lock:
        task.status = STOPPED
        task.delivered = task.delivered or told
    _end_tree(proc)
    try:
        proc.wait(timeout=STOP_GRACE + 2)
    except subprocess.TimeoutExpired:
        pass
    return f"Stopped task {task.id} ({task.label()}) after {_clock(task.seconds())}."


def stop_pid(pid: int) -> bool:
    """For the Background panel: end the task running as PID."""

    for task in running():
        if task.pid == pid:
            stop(task.id, told=False)
            return True
    return False


def stop_all() -> int:
    """Every task ended (Flash is quitting). How many."""

    tasks = running()
    for task in tasks:
        stop(task.id)
    return len(tasks)


def _end_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # nosec B603 B607
                           capture_output=True, check=False)
            return
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def unseen(owner: Optional[str] = None) -> list[Task]:
    """Tasks of OWNER that ended on their own, not yet told to it."""

    with _lock:
        return [t for t in _tasks.values() if t.status != RUNNING and not t.delivered
                and (owner is None or t.owner == owner)]


def notices(owner: Optional[str] = None) -> tuple[str, list[str]]:
    """What OWNER's tasks did since it last heard: the text to put in
    front of the model, and the ids to mark_delivered once it has."""

    ended = unseen(owner)
    if not ended:
        return "", []
    parts = []
    for task in ended:
        tail, _ = _read(task, task.read_to, NOTICE_TAIL)
        parts.append(_state(task) + (f" Its last output:\n{tail.rstrip()}" if tail.strip() else " It printed nothing more."))
    head = "(Background tasks you started ended:)" if len(ended) > 1 else "(A background task you started ended:)"
    return head + "\n" + "\n\n".join(parts), [t.id for t in ended]


def mark_delivered(task_ids: list[str]) -> None:
    with _lock:
        for task_id in task_ids:
            if task_id in _tasks:
                _tasks[task_id].delivered = True
                _tasks[task_id].read_to = max(_tasks[task_id].read_to, _size(_tasks[task_id]))


def _size(task: Task) -> int:
    try:
        return os.path.getsize(task.log)
    except OSError:
        return 0


def listing(owner: Optional[str] = None) -> str:
    tasks = for_owner(owner)
    if not tasks:
        return "No background tasks."
    return "\n".join(f"{t.id}  {t.status:8}  {_clock(t.seconds()):>8}  {t.label()}" for t in tasks)


WAKE_NOTE = ("(The user has not written anything new. You were woken because a background task you "
             "started ended; say what came of it, and carry on with what it was for.)")
