"""The loading animations: what turns beside "Thinking…" while the model
works, in the terminal and in the browser alike.

Each is a run of frames for the terminal, with plain ASCII frames for a
terminal that can't show the fancy ones. The web UI draws most of them
in CSS, and any it has no drawing of from these same frames, so one an
extension adds shows in both.

LOADER in ~/.flash.env picks one. LOADER_MORPH=1 has it turn into
another, at random, every MORPH_SECONDS for as long as the wait goes
on. An extension adds its own with "loaders" in its manifest.
"""

import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

SETTING = "LOADER"
DEFAULT = "dots"

# Morph: each loader shows for MORPH_SECONDS, and the last BLEND_SECONDS
# of that are spent turning into the next.
MORPH_SETTING = "LOADER_MORPH"
MORPH_SECONDS = 10.0
BLEND_SECONDS = 0.8

# Bounds on what an extension may ask for: frames are drawn every
# GLIMMER_FRAME_SECONDS or so, and sit beside a line of text.
MIN_SECONDS = 0.04
MAX_SECONDS = 1.0
MAX_FRAMES = 60
MAX_WIDTH = 12


@dataclass(frozen=True)
class Loader:
    id: str
    name: str
    frames: tuple[str, ...]
    ascii: tuple[str, ...]
    seconds: float = 0.1
    # What it is like, for the picker.
    about: str = ""
    # The extension it comes from, if any.
    source: str = ""

    def frame(self, elapsed: float, fancy: bool = True) -> str:
        """The frame ELAPSED seconds in, every one as wide as the widest
        so the words beside it hold still."""

        frames = self.frames if fancy else self.ascii
        text = frames[int(max(0.0, elapsed) / self.seconds) % len(frames)]
        width = max(len(f) for f in frames)
        return text.ljust(width)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "about": self.about,
            "frames": list(self.frames), "ascii": list(self.ascii),
            "seconds": self.seconds, "source": self.source,
        }


BUILTIN: tuple[Loader, ...] = (
    Loader(
        "dots", "Dots",
        ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"),
        ("|", "/", "-", "\\"), 0.09,
        "A gap running round a little grid of dots",
    ),
    Loader(
        "bloom", "Bloom",
        ("✶", "✷", "✸", "✹", "✺", "✹", "✸", "✷"),
        ("+", "x", "*", "#", "*", "x"), 0.13,
        "Petals that grow in turn and spin",
    ),
    Loader(
        "orbit", "Orbit",
        ("⠁", "⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂"),
        ("'", "`", ".", ","), 0.1,
        "A dot going round",
    ),
    Loader(
        "pulse", "Pulse",
        ("·", "∙", "•", "●", "•", "∙"),
        (".", "o", "O", "o"), 0.15,
        "A dot that breathes",
    ),
    Loader(
        "wave", "Wave",
        ("▁▃▅▇", "▃▅▇▅", "▅▇▅▃", "▇▅▃▁", "▅▃▁▃", "▃▁▃▅"),
        ("._-~", "_-~-", "-~-_", "~-_.", "-_._", "_._-"), 0.11,
        "Bars that rise and fall in turn",
    ),
    Loader(
        "bounce", "Bounce",
        ("•∙∙", "∙•∙", "∙∙•", "∙•∙"),
        ("o..", ".o.", "..o", ".o."), 0.16,
        "Three dots, one hopping along",
    ),
    Loader(
        "grid", "Grid",
        ("▖", "▘", "▝", "▗"),
        (".", "'", "'", "."), 0.14,
        "Squares that light up in turn",
    ),
    Loader(
        "snake", "Snake",
        ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
        ("/", "-", "\\", "|"), 0.08,
        "A line that chases its tail",
    ),
    Loader(
        "arc", "Arc",
        ("◜", "◠", "◝", "◞", "◡", "◟"),
        ("(", "^", ")", ")", "v", "("), 0.1,
        "A curve sweeping round",
    ),
    Loader(
        "cursor", "Cursor",
        ("▍", " "),
        ("_", " "), 0.5,
        "A blinking cursor, as if it were typing",
    ),
    Loader(
        "comet", "Comet",
        ("✦·  ", "·✦· ", " ·✦·", "  ·✦", " ·✦·", "·✦· "),
        ("*.  ", ".*. ", " .*.", "  .*", " .*.", ".*. "), 0.12,
        "A star with a tail, back and forth",
    ),
)


def _extension_loaders() -> list[Loader]:
    from . import extensions  # deferred: extensions are read on demand

    found = []
    taken = {loader.id for loader in BUILTIN}
    for extension in extensions.installed():
        for entry in getattr(extension, "loaders", []):
            if entry.id in taken:
                continue
            taken.add(entry.id)
            found.append(entry)
    return found


def all_loaders() -> list[Loader]:
    """Every loader there is: the built-in ones, then extensions'."""

    return list(BUILTIN) + _extension_loaders()


def find(loader_id: str) -> Optional[Loader]:
    wanted = (loader_id or "").strip().lower()
    return next(
        (
            loader for loader in all_loaders()
            if loader.id == wanted or loader.name.lower() == wanted
        ),
        None,
    )


def chosen() -> str:
    """The loader the setting names, or the default when it names none
    there is."""

    value = (os.environ.get(SETTING) or "").strip().lower()
    return value if find(value) is not None else DEFAULT


def morphing() -> bool:
    """Whether the loader turns into another every MORPH_SECONDS."""

    return (os.environ.get(MORPH_SETTING) or "").strip().lower() in (
        "1", "on", "true", "yes",
    )


class Run:
    """The loaders of one wait: the chosen one, and with morph on, a new
    one at random every MORPH_SECONDS, each turning into the next."""

    def __init__(
        self,
        first: Optional[Loader] = None,
        morph: Optional[bool] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.morph = morphing() if morph is None else morph
        self._rng = rng or random.Random()  # nosec B311 -- for looks
        self._every = all_loaders()
        self._chain = [first or find(chosen()) or BUILTIN[0]]

    def loader(self, stretch: int) -> Loader:
        """The loader shown in the STRETCH-th MORPH_SECONDS of the wait,
        picked the first time it is asked for and kept after."""

        while len(self._chain) <= stretch:
            last = self._chain[-1]
            others = [lo for lo in self._every if lo.id != last.id] or [last]
            self._chain.append(self._rng.choice(others))
        return self._chain[stretch]

    def frame(
        self, elapsed: float, can_show: Callable[[str], bool],
    ) -> str:
        """The text ELAPSED seconds in. Near the end of a stretch the
        loader dissolves into dots from the left, then the next one
        comes out of them, the same way."""

        elapsed = max(0.0, elapsed)
        if not self.morph:
            return _drawn(self._chain[0], elapsed, can_show)

        stretch = int(elapsed // MORPH_SECONDS)
        into = (stretch + 1) * MORPH_SECONDS - elapsed
        now = _drawn(self.loader(stretch), elapsed, can_show)
        if into > BLEND_SECONDS:
            return now

        after = _drawn(self.loader(stretch + 1), elapsed, can_show)
        width = max(len(now), len(after))
        now, after = now.ljust(width), after.ljust(width)
        dot = "·" if can_show("·") else "."
        done = 1 - into / BLEND_SECONDS
        if done < 0.5:
            gone = round(done * 2 * width)
            return dot * gone + now[gone:]
        come = round((done - 0.5) * 2 * width)
        return after[:come] + dot * (width - come)


def _drawn(
    loader: Loader, elapsed: float, can_show: Callable[[str], bool],
) -> str:
    return loader.frame(elapsed, can_show("".join(loader.frames)))


def fancy(loader: Loader) -> bool:
    """Whether the terminal can show LOADER's own frames, or needs its
    ASCII ones."""

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "".join(loader.frames).encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def from_manifest(entry: object, where: str, source: str) -> Loader:
    """A loader an extension's manifest describes, checked."""

    if not isinstance(entry, dict):
        raise ValueError(f"{where} has to be an object")
    loader_id = str(entry.get("name") or "").strip().lower()
    if not loader_id or not loader_id.replace("-", "").isalnum() \
            or len(loader_id) > 32:
        raise ValueError(
            f"{where} needs a name of letters, digits and dashes"
        )
    frames = entry.get("frames")
    if not isinstance(frames, list) or not frames \
            or len(frames) > MAX_FRAMES \
            or not all(isinstance(f, str) and 0 < len(f) <= MAX_WIDTH
                       for f in frames):
        raise ValueError(
            f"{where} needs \"frames\": 1 to {MAX_FRAMES} strings of up "
            f"to {MAX_WIDTH} characters"
        )
    plain = entry.get("ascii") or [
        "".join(c if ord(c) < 128 else "*" for c in f) for f in frames
    ]
    if not isinstance(plain, list) or not all(
        isinstance(f, str) and 0 < len(f) <= MAX_WIDTH
        and all(ord(c) < 128 for c in f) for f in plain
    ) or len(plain) > MAX_FRAMES:
        raise ValueError(f"{where} has \"ascii\" frames that aren't ASCII")
    try:
        seconds = float(entry.get("interval", 0.1))
    except (TypeError, ValueError):
        raise ValueError(f"{where} has an interval that isn't a number")
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(
            f"{where} needs an interval from {MIN_SECONDS} to "
            f"{MAX_SECONDS} seconds"
        )
    return Loader(
        loader_id, str(entry.get("label") or loader_id.title())[:40],
        tuple(frames), tuple(plain), seconds,
        str(entry.get("description") or "")[:120], source,
    )
