"""What this computer can run: its memory and graphics card, and which
models fit in them.

A model has to sit whole in the memory it runs from to be quick: a
graphics card's own, or, on a Mac with Apple's chips, the memory the
whole computer shares. One that does not fit spills onto the processor,
and slows to a crawl. So each model gets a word: fits, tight (it runs,
with less room for a long conversation), or too big.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from typing import Optional

GB = 1024 ** 3
# What a model needs beyond its file: the conversation's cache and the
# runtime's own, as a share of its size.
OVERHEAD = 1.2
FITS, TIGHT, TOO_BIG = "fits", "tight", "too big"


@dataclass
class Machine:
    memory: int
    gpu: str = ""
    vram: int = 0
    unified: bool = False

    @property
    def budget(self) -> int:
        """How much a model can take and stay quick."""

        if self.vram:
            return self.vram
        # Shared memory: the rest of the computer keeps some.
        return int(self.memory * (0.7 if self.unified else 0.5))

    def describe(self) -> str:
        parts = [f"{self.memory / GB:.0f} GB of memory"]
        if self.gpu:
            parts.append(f"{self.gpu} with {self.vram / GB:.0f} GB")
        elif self.unified:
            parts.append("shared with the graphics, as Apple's chips do")
        return ", ".join(parts)

    def biggest(self) -> str:
        """The largest model that fits, as a count of parameters at the
        usual 4-bit size (about 0.6 bytes each, with its overhead)."""

        billions = self.budget / GB / 0.6 / OVERHEAD
        return f"about {max(1, int(billions))}B parameters at 4-bit"


def _memory() -> int:
    if sys.platform == "darwin":
        found = _run(["sysctl", "-n", "hw.memsize"])
        return int(found) if found.isdigit() else 0
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong),
                        ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong),
                        ("rest", ctypes.c_ulonglong * 6)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        windll = getattr(ctypes, "windll", None)
        if windll and windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)):
            return int(status.total)
        return 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as info:
            for line in info:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True,  # nosec B603
                              text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _nvidia() -> tuple[str, int]:
    if not shutil.which("nvidia-smi"):
        return "", 0
    found = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                  "--format=csv,noheader,nounits"])
    best = ("", 0)
    for line in found.splitlines():
        name, _, mib = line.rpartition(",")
        if mib.strip().isdigit() and int(mib) * 1024 ** 2 > best[1]:
            best = (name.strip(), int(mib) * 1024 ** 2)
    return best


_machine: Optional[Machine] = None


def machine() -> Machine:
    """This computer, looked at once."""

    global _machine
    if _machine is None:
        gpu, vram = _nvidia()
        unified = sys.platform == "darwin" and platform.machine() == "arm64"
        _machine = Machine(memory=_memory(), gpu=gpu, vram=vram,
                           unified=unified)
    return _machine


def fit(size: int, here: Optional[Machine] = None) -> str:
    """Whether a model whose file is SIZE bytes fits: FITS, TIGHT or
    TOO_BIG. "" when its size is not known (a provider's)."""

    if not size:
        return ""
    here = here or machine()
    if not here.budget:
        return ""
    need = size * OVERHEAD
    if need <= here.budget * 0.85:
        return FITS
    if need <= here.budget * 1.1:
        return TIGHT
    return TOO_BIG
