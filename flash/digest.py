"""Digests: anything too big to read at once, read part by part and mapped.

A 600-page PDF, a whole repository, a two-hour talk: no model holds all of
it in mind at once, and reading it a screen at a time loses the thread. A
digest reads it the way a person with a highlighter would: in parts, each
summed up with where it is (pages, files, lines, times), then the parts
put together into one map. The map is what Flash reads first; it says
where to look, and read, the YouTube tool or the transcript then gives the
exact words.

Each part is summed up by the model the user picked, a few at a time. A
digest is kept on disk by what was read and what was asked, so asking
again about the same file costs nothing.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import re
import shutil
import subprocess  # nosec B404 -- ffmpeg, with arguments we build
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Optional

PART_CHARS = 12_000          # one part, about three thousand tokens
MERGE_CHARS = 14_000         # notes longer than this are put together in rounds
MOST_PARTS = 160             # past this a source is sampled evenly, and the map says so
WORKERS = 3
CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp",
                 ".hpp", ".cs", ".rb", ".php", ".scala", ".sh", ".sql", ".md", ".rst", ".txt", ".toml", ".yaml",
                 ".yml", ".json", ".html", ".css", ".vue", ".svelte", ".lua", ".r", ".m", ".dart", ".ex", ".exs",
                 ".cfg", ".ini", ".gradle", ".mk", ".cmake", ".proto", ".graphql", ".tf", ".zig", ".nim", ".jl"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".tox",
             ".mypy_cache", ".pytest_cache", "vendor", "Pods", ".idea", ".gradle", "coverage", ".cache"}
MEDIA = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MINUTES_A_PART = 6           # audio heard a piece at a time

PART_PROMPT = (
    "You are reading one part of something too long to read at once: {what}. This part is {where}.\n"
    "Sum it up for someone who will later need to find things in it: 4 to 10 short bullets, the actual content "
    "(claims, numbers, names, decisions, definitions, functions and what they do), never a description like "
    "'this section discusses'. Keep exact figures and names. {ask}"
    "No preamble.\n\n=== {where} ===\n{text}"
)
MERGE_PROMPT = (
    "Below are notes on the parts of {what}, in order, each headed by where it is. Write the map of the whole: "
    "first an overview in 3 to 6 sentences (what it is, its shape, the main points), then '## Where things are', "
    "one line per part or run of parts giving where it is and what is there, keeping the locations exactly as "
    "written. {ask}No preamble.\n\n{notes}"
)
ASK_PART = "The user wants to know: {q}. Mark anything about that with >> at the start of the bullet. "
ASK_MERGE = "The user wants to know: {q}. After the map, '## On your question': what the parts say about it, with " \
            "where each piece is, or that they do not say. "


class DigestError(Exception):
    pass


Summer = Callable[[str], str]


def _model_summer() -> Summer:
    """Sum up with the model the user picked, one prompt in, text out."""

    from . import ai, btw

    if not ai.Config.model:
        raise DigestError("no model is set")

    def ask(prompt: str) -> str:
        return btw.ask(ai._client(), ai.Config.model, [{"role": "user", "content": prompt}], ai._chat_options())

    return ask


# ----- cutting a source into parts ------------------------------------------

def _cut_text(text: str, unit: str = "lines") -> list[tuple[str, str]]:
    """TEXT in parts of about PART_CHARS, each with where it is: lines a-b, or pages a-b for a PDF's text."""

    if unit == "pages":
        pages = re.split(r"(?m)^--- page (\d+)(?: \(no extractable text\))? ---$", text)
        # ["", "1", body1, "2", body2, ...]
        parts, buf, first, last = [], [], None, None
        for i in range(1, len(pages) - 1, 2):
            n, body = int(pages[i]), pages[i + 1].strip()
            if buf and sum(len(b) for b in buf) + len(body) > PART_CHARS:
                parts.append((f"pages {first}-{last}" if first != last else f"page {first}", "\n".join(buf)))
                buf, first = [], None
            first = first or n
            last = n
            buf.append(f"[page {n}] {body}")
        if buf:
            parts.append((f"pages {first}-{last}" if first != last else f"page {first}", "\n".join(buf)))
        return parts
    lines = text.splitlines()
    parts, start, size = [], 0, 0
    for i, line in enumerate(lines):
        size += len(line) + 1
        if size >= PART_CHARS:
            parts.append((f"lines {start + 1}-{i + 1}", "\n".join(lines[start:i + 1])))
            start, size = i + 1, 0
    if start < len(lines):
        parts.append((f"lines {start + 1}-{len(lines)}", "\n".join(lines[start:])))
    return parts


def _cut_timed(text: str) -> list[tuple[str, str]]:
    """A transcript of "[m:ss] words" lines in parts, each with the times it covers."""

    parts, buf, size, first = [], [], 0, ""
    for line in text.splitlines():
        stamp = re.match(r"\[(\d+:\d\d(?::\d\d)?)\]", line)
        if stamp and not first:
            first = stamp.group(1)
        buf.append(line)
        size += len(line) + 1
        if size >= PART_CHARS:
            last = next((m.group(1) for m in (re.match(r"\[(\d+:\d\d(?::\d\d)?)\]", b) for b in reversed(buf)) if m), first)
            parts.append((f"{first} to {last}", "\n".join(buf)))
            buf, size, first = [], 0, ""
    if buf:
        last = next((m.group(1) for m in (re.match(r"\[(\d+:\d\d(?::\d\d)?)\]", b) for b in reversed(buf)) if m), first)
        parts.append((f"{first or 'start'} to {last or 'end'}", "\n".join(buf)))
    return parts


def _cut_folder(root: Path) -> tuple[list[tuple[str, str]], str]:
    """A repository or folder: its tree, then its readable files in parts, each part naming its files."""

    files = []
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") or d in (".github",))
        for name in sorted(names):
            p = Path(here) / name
            if p.suffix.lower() in CODE_SUFFIXES or name in ("Dockerfile", "Makefile", "README", "LICENSE"):
                try:
                    if p.stat().st_size <= 400_000:
                        files.append(p)
                except OSError:
                    pass
    tree = "\n".join(str(f.relative_to(root)) for f in files[:600]) + ("\n…" if len(files) > 600 else "")
    parts, buf, size = [], [], 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            names = [n for n, _ in buf]
            where = names[0] if len(names) == 1 else f"{names[0]} … {names[-1]} ({len(names)} files)"
            parts.append((where, "\n\n".join(f"--- {n} ---\n{t}" for n, t in buf)))
            buf, size = [], 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(root))
        if len(text) > PART_CHARS:
            flush()
            for where, chunk in _cut_text(text):
                parts.append((f"{rel} {where}", chunk))
            continue
        if size + len(text) > PART_CHARS:
            flush()
        buf.append((rel, text))
        size += len(text)
    flush()
    return parts, tree


def _hear(path: Path) -> str:
    """What is said in an audio or video file, as "[m:ss] words" lines, heard by Flash's listening model."""

    from . import voice

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise DigestError("ffmpeg is needed to listen to audio and video; install it, or give a transcript")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "audio.pcm"
        done = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", str(raw)],  # nosec B603
                              capture_output=True, timeout=3600, check=False)
        if done.returncode or not raw.exists():
            raise DigestError(f"could not read the sound in {path.name}: {done.stderr.decode(errors='replace')[-200:]}")
        pcm = raw.read_bytes()
    step = 16000 * 2 * 60 * MINUTES_A_PART
    lines = []
    for at in range(0, len(pcm), step):
        text, why = voice.transcribe(pcm[at:at + step])
        if why:
            raise DigestError(f"the listening model is not ready: {why}")
        minute = at // (16000 * 2 * 60)
        if text.strip():
            lines.append(f"[{minute // 60}:{minute % 60:02d}:00] {text.strip()}")
    if not lines:
        raise DigestError(f"nothing was heard in {path.name}")
    return "\n".join(lines)


def _source(target: str) -> tuple[str, list[tuple[str, str]], str]:
    """What TARGET is (said for the prompts), its parts, and a note for the map (a tree, a duration)."""

    target = (target or "").strip()
    if not target:
        raise DigestError("say what to digest: a file, a folder, or a video link")
    if re.match(r"https?://", target):
        from . import youtube

        vid = youtube.video_id(target)
        if not vid:
            raise DigestError("only YouTube links can be digested; for a web page, fetch it")
        video = youtube.read_video(vid, want_frames=False, transcript_chars=3_000_000)
        if not video.transcript:
            raise DigestError("that video has no captions to read")
        mins = video.seconds // 60
        return f'the YouTube video "{video.title}" by {video.channel} ({mins} min)', _cut_timed(video.transcript), \
            f"{mins} minutes, captions in {video.caption_language or 'its language'}"
    path = Path(os.path.expanduser(target))
    if not path.exists():
        raise DigestError(f"no such file or folder: {path}")
    if path.is_dir():
        parts, tree = _cut_folder(path)
        if not parts:
            raise DigestError(f"nothing readable in {path}")
        return f"the folder {path.name} (a codebase or a set of files)", parts, "Files:\n" + tree
    suffix = path.suffix.lower()
    if suffix in MEDIA:
        heard = _hear(path)
        return f"the recording {path.name}", _cut_timed(heard), "heard by Flash"
    if suffix in (".pdf", ".docx"):
        from . import documents

        text, why = documents.extract_document_text(path)
        if text is None:
            raise DigestError(why)
        unit = "pages" if suffix == ".pdf" else "lines"
        parts = _cut_text(text, unit)
        pages = len(re.findall(r"(?m)^--- page \d+", text))
        return f"the document {path.name}", parts, (f"{pages} pages" if pages else f"{len(text):,} characters")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DigestError(f"could not read {path}: {exc}") from None
    return f"the file {path.name}", _cut_text(text), f"{len(text.splitlines()):,} lines"


# ----- reading it -----------------------------------------------------------

def _key(target: str, question: str) -> str:
    stamp = target
    p = Path(os.path.expanduser(target))
    try:
        if p.is_file():
            st = p.stat()
            stamp += f":{st.st_size}:{st.st_mtime_ns}"
        elif p.is_dir():
            stamp += ":" + str(max((f.stat().st_mtime_ns for f in p.rglob("*") if f.is_file()), default=0))
    except OSError:
        pass
    return hashlib.sha1(f"{stamp}|{question.strip().lower()}".encode()).hexdigest()[:20]


def _cache_dir() -> Path:
    from . import paths

    d = paths.FLASH_DIR / "digests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _merge(what: str, notes: list[str], question: str, summer: Summer, progress: Callable[[str], None]) -> str:
    """Notes on every part, put together into one map, in rounds while they are too long for one."""

    ask = ASK_MERGE.format(q=question) if question else ""
    while sum(len(n) for n in notes) > MERGE_CHARS and len(notes) > 1:
        groups, buf, size = [], [], 0
        for n in notes:
            if buf and size + len(n) > MERGE_CHARS:
                groups.append(buf)
                buf, size = [], 0
            buf.append(n)
            size += len(n)
        groups.append(buf)
        progress(f"putting {len(notes)} parts together in {len(groups)} groups")
        notes = [summer(MERGE_PROMPT.format(what=what, ask="", notes="\n\n".join(g))) for g in groups]
    return summer(MERGE_PROMPT.format(what=what, ask=ask, notes="\n\n".join(notes)))


def digest(target: str, question: str = "", summer: Optional[Summer] = None,
           progress: Optional[Callable[[str], None]] = None, fresh: bool = False) -> str:
    """TARGET (a file, a folder, a YouTube link, a recording) read in full, part by part, and mapped: an overview,
    where each thing is, and with QUESTION, what it says about that."""

    say = progress or (lambda _: None)
    cached = _cache_dir() / f"{_key(target, question)}.md"
    if cached.is_file() and not fresh:
        return cached.read_text(encoding="utf-8")
    what, parts, note = _source(target)
    sampled = ""
    if len(parts) > MOST_PARTS:
        step = len(parts) / MOST_PARTS
        kept = [parts[int(i * step)] for i in range(MOST_PARTS)]
        sampled = f" It was too long to read every part: {MOST_PARTS} of {len(parts)} parts, evenly spread, were read."
        parts = kept
    summer = summer or _model_summer()
    ask = ASK_PART.format(q=question) if question else ""
    notes: list[str] = [""] * len(parts)

    def one(i: int) -> None:
        where, text = parts[i]
        said = summer(PART_PROMPT.format(what=what, where=where, ask=ask, text=text))
        notes[i] = f"### {where}\n{said.strip()}"

    say(f"reading {len(parts)} parts of {what}")
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for n, _ in enumerate(pool.map(one, range(len(parts))), 1):
            if n % 5 == 0 or n == len(parts):
                say(f"read {n} of {len(parts)} parts")
    whole = _merge(what, notes, question, summer, say) if len(parts) > 1 else notes[0]
    out = (f"Digest of {what} ({note.splitlines()[0] if note else ''}; read in {len(parts)} parts).{sampled}\n\n"
           f"{whole.strip()}\n\n"
           + ("" if len(parts) == 1 else "## Notes on each part\n" + "\n\n".join(notes) + "\n\n")
           + "Read the exact words before quoting: read with offset for lines, the pages named for a PDF (its text "
             "marks each page), or the times for a video.")
    if note.startswith("Files:"):
        out += "\n\n" + note[:6000]
    cached.write_text(out, encoding="utf-8")
    return out
