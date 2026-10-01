"""Keeping the computer awake while Flash works, as Cowork does.

A turn, a task or a spark's shift is not cut off half way by the computer
going to sleep. Only sleep is held off: the screen still dims and locks
as usual. Whatever holds it lets go when Flash is done, and if Flash
dies, the operating system lets go too: macOS's caffeinate and the Linux
watcher are tied to Flash's process, and Windows ties it to a thread.

KEEP_AWAKE=0 turns it off.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Iterator
from typing import Any

_lock = threading.Lock()
# What is working now, by a key of its own: "terminal", "chat:ab12cd34",
# "spark:9f8e7d6c". Sleep is held off while there is anything here.
_working: set[str] = set()
# What holds it off: a process, or the Windows thread's stop event.
_holding: Any = None

# Windows' SetThreadExecutionState flags.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def enabled() -> bool:
    value = os.getenv("KEEP_AWAKE", "1").strip().lower()
    return value not in ("0", "false", "off", "no")


def hold(key: str) -> None:
    """KEY is working: keep the computer awake until it lets go."""

    global _holding
    if not enabled():
        return
    with _lock:
        _working.add(key)
        if _holding is None:
            _holding = _start()


def let_go(key: str) -> None:
    """KEY is done. Letting go of what is not held does nothing."""

    global _holding
    with _lock:
        _working.discard(key)
        if not _working and _holding is not None:
            _stop(_holding)
            _holding = None


@contextlib.contextmanager
def working(key: str) -> Iterator[None]:
    hold(key)
    try:
        yield
    finally:
        let_go(key)


def holding() -> bool:
    return _holding is not None


def _start() -> Any:
    """Whatever holds sleep off here, or None where nothing can."""

    try:
        if sys.platform == "darwin":
            caffeinate = shutil.which("caffeinate")
            if caffeinate:
                # -w: it ends with Flash, whatever happens to Flash.
                return subprocess.Popen(  # nosec B603
                    [caffeinate, "-i", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            return None
        if sys.platform == "win32":
            return _hold_windows()
        inhibit = shutil.which("systemd-inhibit")
        if inhibit:
            # Held for as long as the shell runs, which is as long as
            # Flash does: it checks every half minute.
            watch = (
                f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 30; done"
            )
            return subprocess.Popen(  # nosec B603
                [inhibit, "--what=sleep:idle", "--who=Flash",
                 "--why=Flash is working", "--mode=block",
                 "sh", "-c", watch],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass
    return None


def _hold_windows() -> threading.Event:
    import ctypes  # deferred: only Windows gets here

    done = threading.Event()

    def hold() -> None:
        # The state belongs to this thread, and goes with it.
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        done.wait()
        kernel.SetThreadExecutionState(_ES_CONTINUOUS)

    threading.Thread(target=hold, name="keep-awake", daemon=True).start()
    return done


def _stop(holding: Any) -> None:
    if isinstance(holding, threading.Event):
        holding.set()
        return
    with contextlib.suppress(OSError):
        holding.terminate()
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        holding.wait(timeout=2)
