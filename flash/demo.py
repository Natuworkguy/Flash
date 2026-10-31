"""Learning a task from a recording of the user doing it.

A screen recording, from the web page or from anywhere else (the
computer's own screen recorder, a phone, a video sent over), becomes
stills caught as the screen changed, each with what the user said over
it. The model sees them in order, works out the procedure, and saves it
as a skill; next time it does the task itself, with computer use,
following the skill and checking the screen against what each step
should show.
"""

import re
import shutil
import subprocess  # nosec B404 -- ffmpeg, with arguments
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Optional

MAX_STILLS = 16
SCENE_CHANGE = 0.06
STILL_WIDTH = 1280
SAMPLE_RATE = 16000
MAX_SECONDS = 20 * 60
VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


class DemoError(Exception):
    """The recording could not be read."""


def thin(items: list, most: int = MAX_STILLS) -> list:
    """At most MOST of ITEMS: the first and the last, and the rest
    evenly between."""

    if len(items) <= most:
        return items
    keep = {0, len(items) - 1}
    n = 1
    while len(keep) < most:
        keep.add(round(n * (len(items) - 1) / (most - 1)))
        n += 1
    return [item for i, item in enumerate(items) if i in keep]


def said_over(stills: list[dict], pcm: bytes,
              hear: Callable[[bytes], tuple[str, str]]) -> str:
    """Fill each still's "said" with what was said from its moment to
    the next one's, heard by HEAR. Returns why nothing could be heard,
    or ""."""

    if not stills or len(pcm) < SAMPLE_RATE:  # under half a second
        return ""
    per_second = SAMPLE_RATE * 2
    for number, still in enumerate(stills):
        start = int(still["at"] * per_second) & ~1
        end = (
            int(stills[number + 1]["at"] * per_second) & ~1
            if number + 1 < len(stills) else len(pcm)
        )
        if end - start < per_second // 2:
            continue
        said, why = hear(pcm[start:end])
        if why:
            return why
        still["said"] = said
    return ""


def note(where: str, stills: list[dict]) -> str:
    """What the model is told of a recording it is shown the stills
    of."""

    lines = [
        f"Attached screen recording: {where}. The images with this "
        f"message are {len(stills)} stills from it, in order, each caught "
        "as the screen changed:"
    ]
    for number, still in enumerate(stills, start=1):
        at = int(still["at"])
        line = f"{number}. at {at // 60}:{at % 60:02d}"
        if still.get("said"):
            line += f", the user saying: \"{still['said']}\""
        lines.append(line)
    lines.append(
        "If it shows how the user does something, work out the steps "
        "from it and save them as a skill with skill_manage, so you can "
        "do it for them: for each step, the app or page, what to click "
        "or type, and what the screen shows once it is done. Ask about "
        "anything the stills do not show."
    )
    return "\n".join(lines)


def _ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise DemoError("reading a recording needs ffmpeg: install it "
                        "(brew install ffmpeg, or apt install ffmpeg)")
    return found


def _duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if not probe:
        return 0.0
    done = subprocess.run(  # nosec B603
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def stills_of(path: Path, most: int = MAX_STILLS) -> list[dict]:
    """The recording's stills: its first frame, one each time the screen
    changed, and its last, as JPEG bytes with the second each comes
    at."""

    ffmpeg = _ffmpeg()
    with tempfile.TemporaryDirectory(prefix="flash-demo-") as folder:
        out = Path(folder)
        done = subprocess.run(  # nosec B603
            [ffmpeg, "-hide_banner", "-nostdin", "-t", str(MAX_SECONDS),
             "-i", str(path), "-vf",
             f"select='eq(n\\,0)+gt(scene\\,{SCENE_CHANGE})',showinfo,"
             f"scale='min({STILL_WIDTH}\\,iw)':-2",
             "-vsync", "vfr", "-q:v", "4", str(out / "%04d.jpg")],
            capture_output=True, text=True, timeout=600, check=False,
        )
        times = [float(t) for t in re.findall(
            r"pts_time:\s*([\d.]+)", done.stderr)]
        frames = sorted(out.glob("*.jpg"))
        if not frames:
            raise DemoError(
                "no pictures could be read from it: "
                + (done.stderr.strip().splitlines() or ["not a video"])[-1])
        stills = [{"jpeg": f.read_bytes(),
                   "at": times[i] if i < len(times) else 0.0, "said": ""}
                  for i, f in enumerate(frames)]
        length = _duration(path)
        if length - stills[-1]["at"] > 1.0:
            last = out / "last.jpg"
            subprocess.run(  # nosec B603
                [ffmpeg, "-hide_banner", "-nostdin", "-sseof", "-0.5",
                 "-i", str(path), "-frames:v", "1", "-vf",
                 f"scale='min({STILL_WIDTH}\\,iw)':-2", "-q:v", "4",
                 str(last)],
                capture_output=True, timeout=120, check=False,
            )
            if last.is_file():
                stills.append({"jpeg": last.read_bytes(),
                               "at": max(0.0, length - 0.5), "said": ""})
    return thin(stills, most)


def sound_of(path: Path) -> bytes:
    """The recording's sound, as 16 kHz mono 16-bit audio: b"" when it
    has none."""

    done = subprocess.run(  # nosec B603
        [_ffmpeg(), "-hide_banner", "-nostdin", "-t", str(MAX_SECONDS),
         "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "pipe:1"],
        capture_output=True, timeout=600, check=False,
    )
    return done.stdout if done.returncode == 0 else b""


def read(path: Path, hear: Optional[Callable[[bytes], tuple[str, str]]]
         = None) -> tuple[list[dict], str]:
    """PATH's stills, each with what was said over it when HEAR can hear
    it; and why the words could not be heard, or ""."""

    if not path.is_file():
        raise DemoError(f"no file at {path}")
    if path.suffix.lower() not in VIDEO_TYPES:
        raise DemoError(f"{path.name} is not a video: use one of "
                        + ", ".join(VIDEO_TYPES))
    stills = stills_of(path)
    if hear is None:
        return stills, ""
    return stills, said_over(stills, sound_of(path), hear)
