"""Hearing a call without touching the Mac's speakers.

On macOS 14.2 and later a program can listen to what the Mac is playing
(a process tap). So instead of switching the Mac's speakers to a
loopback device, and playing the call back to the user on the real ones,
Flash runs a small helper (calltap.swift) that hears the call as the
user does, leaving Flash's own voice out. The speakers stay as they are,
and only the call's microphone is changed, then put back.

The helper is compiled once with swiftc (it comes with Xcode's command
line tools) into ~/.flash/bin. Anywhere it can't be, calls fall back to
the loopback devices.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess  # nosec B404 -- our own helper, built from our source
import threading
import time
from pathlib import Path
from typing import Optional

SOURCE = Path(__file__).with_name("calltap.swift")
SETTING = "CALL_HEAR"   # "loopback" turns the tap off
BUILD_SECONDS = 240
_built: Optional[Path] = None
_lock = threading.Lock()


class TapError(Exception):
    pass


def _mac_version() -> tuple[int, ...]:
    release = platform.mac_ver()[0]
    try:
        return tuple(int(part) for part in release.split("."))
    except ValueError:
        return ()


def usable() -> bool:
    """Whether this Mac can hear a call through a tap: macOS 14.2 or later,
    the tap not turned off, and the helper built or buildable."""

    from . import voice

    if platform.system() != "Darwin" or _mac_version() < (14, 2):
        return False
    if voice._setting(SETTING, "tap").lower() == "loopback":  # pylint: disable=W0212
        return False
    return _target().exists() or bool(shutil.which("swiftc"))


def _target() -> Path:
    from . import paths

    tag = hashlib.sha1(SOURCE.read_bytes()).hexdigest()[:10]
    return paths.FLASH_DIR / "bin" / f"flash-call-tap-{tag}"


def build() -> Path:
    """The helper, compiled the first time (it takes a moment)."""

    global _built

    with _lock:
        if _built and _built.exists():
            return _built
        target = _target()
        if not target.exists():
            swiftc = shutil.which("swiftc")
            if not swiftc:
                raise TapError("hearing calls this way needs Xcode's command line tools: xcode-select --install")
            target.parent.mkdir(parents=True, exist_ok=True)
            done = subprocess.run(  # nosec B603 -- swiftc on our own source
                [swiftc, "-O", str(SOURCE), "-o", str(target)],
                capture_output=True, text=True, timeout=BUILD_SECONDS, check=False)
            if done.returncode != 0 or not target.exists():
                raise TapError("couldn't build the call listener: "
                               + (done.stderr.strip().splitlines() or ["swiftc failed"])[-1])
        _built = target
        return target


class Tap:
    """What the Mac plays, as 16-bit mono PCM at RATE, read like a
    sounddevice input stream: read(frames) and read_available."""

    def __init__(self, rate: int) -> None:
        self.device_rate = rate
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._done = False
        self._proc = subprocess.Popen(  # nosec B603 -- our own helper
            [str(build()), str(rate), str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._pump, daemon=True).start()
        # It says straight away when macOS won't let it listen.
        time.sleep(0.4)
        if self._proc.poll() is not None:
            raise TapError(self._why())

    def _why(self) -> str:
        try:
            said = (self._proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            said = ""
        return said or "the call listener stopped"

    def _pump(self) -> None:
        out = self._proc.stdout
        while True:
            chunk = out.read1(8192) if hasattr(out, "read1") else out.read(8192)
            if not chunk:
                break
            with self._cond:
                self._buf += chunk
                self._cond.notify_all()
        with self._cond:
            self._done = True
            self._cond.notify_all()

    @property
    def read_available(self) -> int:
        with self._cond:
            return len(self._buf) // 2

    def read(self, frames: int) -> tuple[bytes, bool]:
        want = frames * 2
        with self._cond:
            while len(self._buf) < want and not self._done:
                self._cond.wait(1.0)
            if len(self._buf) < want:
                from .calls import CallError

                raise CallError(self._why())
            block = bytes(self._buf[:want])
            del self._buf[:want]
        return block, False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.close()

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
