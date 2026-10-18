"""Ctrl+C away from the prompt: the first press says how to leave, and a
second one soon after leaves.

At the prompt the input box does the same itself (repl_input). This is
for the moments around it: Flash starting up, before there is a box to
type in, and the web UI's server, which runs until it is stopped. While
a turn runs a single press still stops the turn, so the guard is let go
of before the conversation starts.
"""

import signal
import sys
import threading
import time
from pathlib import Path

# As long as the prompt waits for the second press.
EXIT_PRESS_SECONDS = 1.5
HINT = "Press Ctrl+C again to exit"

_armed_until = 0.0
_hint = HINT


def _pressed(signum, frame) -> None:
    global _armed_until

    now = time.monotonic()
    if now < _armed_until:
        _armed_until = 0.0
        raise KeyboardInterrupt
    _armed_until = now + EXIT_PRESS_SECONDS
    try:
        sys.stderr.write(f"\r\n\x1b[2m{_hint}\x1b[0m\r\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def guard(hint: str = HINT) -> None:
    """From now on Ctrl+C takes two presses, the first showing HINT. Only
    the main thread can listen for it, so elsewhere this does nothing."""

    global _hint, _armed_until

    if threading.current_thread() is not threading.main_thread():
        return
    _hint = hint
    _armed_until = 0.0
    signal.signal(signal.SIGINT, _pressed)


def release() -> float:
    """Ctrl+C back to one press, as a running turn needs it. Returns
    until when a first press made just now still waits for its second,
    or 0, so the prompt can keep waiting."""

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, signal.default_int_handler)
    return _armed_until if time.monotonic() < _armed_until else 0.0


def started_as_cli(argv: list) -> bool:
    """Whether this process is Flash itself, run from a terminal: the
    flash command, run.py, or python -m flash, and not a test runner or
    another program importing it."""

    if not argv or not sys.stdin or not sys.stdin.isatty():
        return False
    path = Path(argv[0])
    return path.stem in ("flash", "run") or (
        path.name == "__main__.py" and path.parent.name == "flash"
    )
