"""Keep the Mac awake while Flash's server runs.

Asleep, Flash stops: the watch cannot reach it and no spark can call.
While the setting is on (it is unless turned off), the server holds
``caffeinate -i -w <pid>``, which keeps the system from idle sleep for
as long as this process lives. The display still sleeps, and a closed
lid on battery still puts the Mac to sleep: macOS allows no app to
stop that.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import sys
import threading
from typing import Optional

SETTING = "FLASH_KEEP_AWAKE"

_lock = threading.Lock()
_held: Optional[subprocess.Popen] = None


def enabled() -> bool:
    """On unless turned off."""

    from .notify import _saved

    return _saved(SETTING).strip().lower() not in ("0", "off", "false", "no")


def supported() -> bool:
    return sys.platform == "darwin" and shutil.which("caffeinate") is not None


def holding() -> bool:
    with _lock:
        return _held is not None and _held.poll() is None


def start() -> None:
    """Hold the Mac awake, when on and possible; idempotent."""

    global _held
    if not enabled() or not supported():
        return
    with _lock:
        if _held is not None and _held.poll() is None:
            return
        try:
            _held = subprocess.Popen(  # nosec B603 B607
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            _held = None


def stop() -> None:
    global _held
    with _lock:
        if _held is not None and _held.poll() is None:
            _held.terminate()
        _held = None


def set_setting(on: bool) -> None:
    from .envfile import set_env_var
    from .paths import ENV_PATH

    os.environ[SETTING] = "1" if on else "0"
    set_env_var(ENV_PATH, SETTING, os.environ[SETTING])
    if on:
        start()
    else:
        stop()


def state() -> dict:
    """For Settings."""

    return {"keep_awake": enabled(), "keep_awake_supported": supported(),
            "keep_awake_holding": holding()}
