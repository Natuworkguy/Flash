"""What the terminal shows while Flash starts: the robot's face, full
screen, until the welcome screen is ready.

The face is the one Flash Bot wears on its screen (two upright pills in
cream on near-black, no pupils), drawn here in half blocks, two pixels to
a character cell. It looks around, blinks now and then, and glances down
at the line under it as that line says what Flash is doing.

It never makes starting slower: nothing shows for the first moment, so a
quick start goes straight to the welcome screen, and the face goes the
moment Flash is ready. It draws on the terminal's second screen, so
whatever was on the first is left as it was.

Standard library only: it starts before the rest of Flash is imported.
Off with FLASH_NO_LOADER=1, and never when output isn't a terminal.
"""

from __future__ import annotations

import atexit
import math
import os
import random
import shutil
import sys
import threading
import time
from typing import Optional

BG = (20, 20, 19)          # the robot's screen, #141413
EYE = (245, 244, 239)      # its eyes, #f5f4ef
WORDS = (138, 136, 128)    # the line under the face
DOTS = (217, 119, 87)      # Flash's clay accent, #d97757
FPS = 30
QUIET_FOR = 0.15           # a start quicker than this shows nothing


def _truecolor() -> bool:
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _rgb(colour: tuple[int, int, int], ground: bool) -> str:
    r, g, b = colour
    if _truecolor():
        return f"\033[{48 if ground else 38};2;{r};{g};{b}m"
    # The 256-colour cube's nearest, for terminals without truecolour.
    def level(v: int) -> int:
        return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
    if abs(r - g) < 10 and abs(g - b) < 10:
        index = 232 + min(23, max(0, (r - 8) // 10))
    else:
        index = 16 + 36 * level(r) + 6 * level(g) + level(b)
    return f"\033[{48 if ground else 38};5;{index}m"


class Face:
    """Where the eyes are looking, and how open they are, at a moment."""

    def __init__(self) -> None:
        self.look = (0.0, 0.0)
        self.aim = (0.0, 0.0)
        self.next_glance = time.monotonic() + 0.6
        self.next_blink = time.monotonic() + random.uniform(1.2, 2.6)
        self.blink_at = -1.0
        self.down_until = 0.0

    def glance_down(self) -> None:
        """At the line under it, which just changed."""

        self.down_until = time.monotonic() + 0.7

    def step(self, now: float) -> tuple[float, float, float]:
        """Look x, look y (each -1 to 1) and openness (0 to 1)."""

        if now >= self.next_glance:
            self.aim = (random.uniform(-1, 1), random.uniform(-0.6, 0.5))
            if random.random() < 0.3:
                self.aim = (0.0, 0.0)
            self.next_glance = now + random.uniform(0.8, 1.9)
        aim = (self.aim[0] * 0.3, 0.75) if now < self.down_until else self.aim
        # Eased towards where it is looking, quick then settling.
        k = 0.22
        self.look = (self.look[0] + (aim[0] - self.look[0]) * k,
                     self.look[1] + (aim[1] - self.look[1]) * k)
        if now >= self.next_blink:
            self.blink_at = now
            self.next_blink = now + random.uniform(2.2, 4.5)
            if random.random() < 0.25:
                # Now and then a double blink.
                self.next_blink = now + 0.32
        open_ = 1.0
        since = now - self.blink_at
        if 0 <= since < 0.16:
            open_ = max(0.08, abs(math.cos(since / 0.16 * math.pi)))
        return self.look[0], self.look[1], open_


def frame(width: int, height: int, look_x: float, look_y: float,
          open_: float, words: str, beat: int) -> str:
    """One frame of the whole screen, as text with colours."""

    px_h = height * 2
    eye_w = max(4, round(width * 0.075))
    eye_h = max(6, round(px_h * 0.34))
    gap = max(eye_w, round(width * 0.11))
    cx = width / 2 + look_x * width * 0.04
    cy = px_h * 0.42 + look_y * px_h * 0.05
    shown_h = max(2.0, eye_h * open_)
    lefts = (cx - gap / 2 - eye_w, cx + gap / 2)
    radius = eye_w / 2

    def lit(x: float, y: float) -> bool:
        """Whether pixel (x, y) is inside either eye: an upright pill."""

        top, bottom = cy - shown_h / 2, cy + shown_h / 2
        if not top <= y <= bottom:
            return False
        for left in lefts:
            if not left <= x <= left + eye_w:
                continue
            mid = left + radius
            r = min(radius, shown_h / 2)
            # The round ends: inside the circle at the top or bottom end.
            if y < top + r:
                return (x - mid) ** 2 + (y - (top + r)) ** 2 <= r * r
            if y > bottom - r:
                return (x - mid) ** 2 + (y - (bottom - r)) ** 2 <= r * r
            return True
        return False

    bg = _rgb(BG, True)
    eye_fg = _rgb(EYE, False)
    blank = bg + " " * width
    band_top = int((cy - eye_h / 2) // 2) - 1
    band_bottom = int((cy + eye_h / 2) // 2) + 1
    text_row = min(height - 2, band_bottom + max(2, height // 8))
    rows = []
    for row in range(height):
        if band_top <= row <= band_bottom:
            out = [bg]
            for col in range(width):
                x = col + 0.5
                top = lit(x, row * 2 + 0.5)
                bottom = lit(x, row * 2 + 1.5)
                if top and bottom:
                    out.append(eye_fg + "█")
                elif top:
                    out.append(eye_fg + "▀")
                elif bottom:
                    out.append(eye_fg + "▄")
                else:
                    out.append(" ")
            rows.append("".join(out))
        elif row == text_row and words:
            # What Flash is doing, with three dots that fill in turn.
            dots = "".join("●" if i == beat % 4 else "·" for i in range(3))
            said = f"{words}  "
            pad = max(0, (width - len(said) - 3) // 2)
            line = (bg + " " * pad + _rgb(WORDS, False) + said
                    + _rgb(DOTS, False) + dots)
            rows.append(line + bg + " " * max(0, width - pad - len(said) - 3))
        else:
            rows.append(blank)
    return "\033[H" + "\n".join(rows) + "\033[0m"


class Loader:
    def __init__(self) -> None:
        self._done = threading.Event()
        self._words = "Waking up"
        self._face = Face()
        self._shown = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="startup face")

    def say(self, words: str) -> None:
        with self._lock:
            if words != self._words:
                self._words = words
                self._face.glance_down()

    def _run(self) -> None:
        if self._done.wait(QUIET_FOR):
            return
        out = sys.stdout
        with self._lock:
            self._shown = True
        # The second screen, no cursor.
        out.write("\033[?1049h\033[?25l")
        out.flush()
        beat = 0
        started = time.monotonic()
        while not self._done.is_set():
            now = time.monotonic()
            size = shutil.get_terminal_size((80, 24))
            x, y, open_ = self._face.step(now)
            with self._lock:
                words = self._words
            beat = int((now - started) * 3)
            try:
                out.write(frame(size.columns, size.lines, x, y, open_,
                                words, beat))
                out.flush()
            except (OSError, ValueError):
                return
            self._done.wait(1 / FPS)

    def start(self) -> "Loader":
        self._thread.start()
        atexit.register(self.stop)
        return self

    def stop(self) -> bool:
        """Gone, and the terminal as it was. Whether it was ever shown."""

        self._done.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        with self._lock:
            shown, self._shown = self._shown, False
        if shown:
            try:
                sys.stdout.write("\033[0m\033[?25h\033[?1049l")
                sys.stdout.flush()
            except (OSError, ValueError):
                pass
        return shown


_loader: Optional[Loader] = None


def wanted(argv: list[str]) -> bool:
    """Only for the terminal chat itself, started with no arguments, on a
    terminal that can show it."""

    return (not argv and sys.stdout is not None and sys.stdout.isatty()
            and sys.stdin is not None and sys.stdin.isatty()
            and os.environ.get("TERM", "") != "dumb"
            and os.environ.get("FLASH_NO_LOADER", "") not in ("1", "true"))


def start(argv: list[str]) -> None:
    global _loader
    if _loader is None and wanted(argv):
        _loader = Loader().start()


def say(words: str) -> None:
    """What Flash is doing now, under the face."""

    if _loader is not None:
        _loader.say(words)


def active() -> bool:
    return _loader is not None


def stop() -> bool:
    """The face gone. Whether it had been on screen."""

    global _loader
    loader, _loader = _loader, None
    return loader.stop() if loader is not None else False
