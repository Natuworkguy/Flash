"""Reading aloud: an article, a document or any text, in Flash's own voice.

"Read me this article" is a request to hear it, not to have it
summarised. The words are taken from where they are (a web page's
article, a file, or text in the chat), made fit to hear (no markdown
marks, no addresses spelled out), and spoken by the voice set for
voice mode, a paragraph at a time.

In the terminal the reading plays straight away, and Ctrl+C stops it.
In the browser it is made into a sound file that plays in the chat,
with a player to pause it, skip about and speed it up.
"""

import io
import re
import shutil
import subprocess  # nosec B404 -- ffmpeg, with arguments, never a shell
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Optional

# About an hour of speech; past it the rest is left unread, and said so.
MAX_CHARS = 60_000

# Spoken a piece at a time: short enough that the first is heard (or
# made) quickly, long enough to keep a sentence's tune.
PIECE_CHARS = 600

# A page's article is what is read, not its menus: the largest of these.
_ARTICLE_RE = re.compile(r"<(article|main)\b[^>]*>(.*?)</\1>", re.S | re.I)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_MARKS_RE = re.compile(r"(^|\s)[#>*\-+]+\s+|[*_`~|]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


class ReadError(Exception):
    """Nothing to read, or no voice to read it with."""


def article(html: str) -> str:
    """The part of a page that is its article: the largest <article>,
    or <main>, or the whole page when it has neither."""

    found = [m.group(2) for m in _ARTICLE_RE.finditer(html)]
    return max(found, key=len) if found else html


def speakable(text: str) -> str:
    """TEXT as it should be heard: links read as their words, addresses
    as "a link", markdown's marks gone, paragraphs kept apart."""

    paragraphs = []
    for block in re.split(r"\n\s*\n", text or ""):
        block = _LINK_RE.sub(r"\1", block)
        block = _URL_RE.sub("a link", block)
        lines = [_MARKS_RE.sub(r"\1", line).strip()
                 for line in block.splitlines()]
        joined = " ".join(line for line in lines if line)
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs)


def pieces(text: str) -> list[str]:
    """TEXT in pieces to speak one after another: paragraphs, and the
    long ones by sentence."""

    out: list[str] = []
    for paragraph in text.split("\n\n"):
        if len(paragraph) <= PIECE_CHARS:
            out.append(paragraph)
            continue
        current = ""
        for sentence in _SENTENCE_RE.split(paragraph):
            if current and len(current) + len(sentence) > PIECE_CHARS:
                out.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            out.append(current)
    return [p for p in out if p.strip()]


def prepare(text: str) -> tuple[str, bool]:
    """TEXT made fit to hear, cut to MAX_CHARS at a sentence: the words,
    and whether any were left out."""

    said = speakable(text)
    if not said:
        raise ReadError("there is nothing in it to read")
    if len(said) <= MAX_CHARS:
        return said, False
    cut = said[:MAX_CHARS]
    end = max(cut.rfind(". "), cut.rfind("\n\n"))
    return (cut[:end + 1] if end > MAX_CHARS // 2 else cut), True


def words(text: str) -> int:
    return len(text.split())


def speak(text: str, doing: Optional[Callable[[str], None]] = None,
          ) -> tuple[int, bool]:
    """Read TEXT aloud here, a piece at a time: how many pieces were
    read, and whether it was stopped before the end."""

    from . import voice

    parts = pieces(text)
    for n, part in enumerate(parts, start=1):
        if doing and len(parts) > 1 and n % 10 == 1:
            doing(f"Reading {n} of {len(parts)}")
        reason, interrupted = voice.speak(part)
        if reason:
            raise ReadError(reason)
        if interrupted:
            return n, True
    return len(parts), False


def record(text: str, out: Path,
           doing: Optional[Callable[[str], None]] = None) -> dict:
    """TEXT spoken into a sound file at OUT: a .wav, or, when ffmpeg is
    here and OUT asks for it, an .m4a, a tenth of the size. What was
    written: its path and length in seconds."""

    from . import voice

    parts = pieces(text)
    frames = bytearray()
    rate = 0
    for n, part in enumerate(parts, start=1):
        if doing and len(parts) > 1 and n % 10 == 1:
            doing(f"Speaking {n} of {len(parts)}")
        made, reason = voice.synthesize(part)
        if reason:
            raise ReadError(reason)
        with wave.open(io.BytesIO(made)) as piece:
            rate = rate or piece.getframerate()
            frames += piece.readframes(piece.getnframes())
            # a breath between paragraphs
            frames += b"\0\0" * int(rate * 0.35)
    if not rate:
        raise ReadError("the voice made no sound")

    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    pcm = io.BytesIO()
    with wave.open(pcm, "wb") as whole:
        whole.setnchannels(1)
        whole.setsampwidth(2)
        whole.setframerate(rate)
        whole.writeframes(bytes(frames))
    seconds = len(frames) / 2 / rate
    ffmpeg = shutil.which("ffmpeg")
    if out.suffix.lower() in (".m4a", ".mp3") and ffmpeg:
        codec = ["-c:a", "aac" if out.suffix.lower() == ".m4a"
                 else "libmp3lame", "-b:a", "64k"]
        done = subprocess.run(  # nosec B603 -- ffmpeg, no shell
            [ffmpeg, "-y", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
             *codec, str(out)],
            input=pcm.getvalue(), capture_output=True, timeout=600,
            check=False,
        )
        if done.returncode == 0:
            return {"path": out, "seconds": seconds}
    if out.suffix.lower() != ".wav":
        out = out.with_suffix(".wav")
    out.write_bytes(pcm.getvalue())
    return {"path": out, "seconds": seconds}


def default_name(title: str) -> str:
    """A file name for a reading of TITLE."""

    slug = re.sub(r"[^a-z0-9]+", "-", (title or "reading").lower()).strip("-")
    suffix = ".m4a" if shutil.which("ffmpeg") else ".wav"
    return (slug[:50].rstrip("-") or "reading") + suffix
