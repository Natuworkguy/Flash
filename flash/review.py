"""Code review: what in a change looks wrong, checked twice before it is
said.

Ask a model whether a diff has bugs and it finds some, whether or not
there are any: a list is what was asked for. So a review runs in two
passes. The first reads the diff a few files at a time and lists what
might be wrong, each at its file and line. The second takes each of
those on its own and tries to show it is fine after all: a check
elsewhere, an input that cannot happen, a misreading. Only what it
cannot explain away is reported, the surest first.

The diff is read from git: the uncommitted changes by default, new
files included, or what a branch has that another does not, or a range
of commits. Every line it shows the model carries its number in the
new version of the file, so a finding's line is one the model read
rather than one it counted.
"""

import json
import re
import subprocess  # nosec B404 -- git, with arguments, never a shell
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# A finding the second pass is less sure of than this is dropped.
MIN_CONFIDENCE = 0.5

# How much diff one first-pass call reads: enough for a few files, and
# little enough for a small local model's window.
CHUNK_CHARS = 12000

# Past this the rest of the change is not reviewed, and the report says
# so rather than passing it as clean.
MAX_DIFF_CHARS = 120000

# The most candidates sent to the second pass, and the most reported.
MAX_CANDIDATES = 24

# New files bigger than this are listed but not read.
MAX_NEW_FILE_BYTES = 200_000

# Lines of the file shown on either side of a finding, for the second
# pass to check it against more than the diff.
AROUND_LINES = 40

GIT_SECONDS = 30

PROPOSE_PROMPT = """\
You are reviewing a code change for bugs. Read the diff and list each \
place where the changed code is likely wrong: it would crash, give a \
wrong result, lose or corrupt data, leak a secret, or break something \
that used to work. Only lines the change adds or edits, and only real \
defects: not style, naming, comments, missing tests, or what could be \
nicer.

Each line of the diff starts with its number in the new version of the \
file; removed lines have none. For each defect give the file, that line \
number, a title of a few words, and why it is wrong, naming the input \
or situation that shows it.

Answer with JSON only:
{"findings": [{"file": "path", "line": 12, "title": "...", "why": "..."}]}
With nothing wrong, answer {"findings": []}."""

REFUTE_PROMPT = """\
A reviewer says the code change below has a bug. Try to prove them \
wrong. Read the code and look for what makes it fine after all: a \
check somewhere else, an input that can never happen, a misreading of \
what the code does, a line that is not in the change at all. Call it \
real only if you cannot explain it away.

Answer with JSON only:
{"real": true or false, "confidence": 0 to 1 that it is a real bug, \
"reason": "one sentence"}"""

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


class ReviewError(ValueError):
    """The change could not be read: not a repository, an unknown
    branch, git missing."""


@dataclass
class Finding:
    file: str
    line: int
    title: str
    why: str
    confidence: float = 0.0
    reason: str = ""


@dataclass
class Review:
    what: str
    files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # How many the first pass put forward, before the second.
    candidates: int = 0
    # Files left unread, for being past MAX_DIFF_CHARS or too big.
    unread: list[str] = field(default_factory=list)


def _git(args: list[str], folder: Path, ok: tuple[int, ...] = (0,)) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607 -- git, no shell
            ["git", *args], cwd=folder, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=GIT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ReviewError("git is not installed, and a review reads the "
                          "change from git.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"git {args[0]} took too long.") from exc
    if done.returncode not in ok:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise ReviewError(said[0] if said else f"git {args[0]} failed.")
    return done.stdout


def _new_file_diff(folder: Path, name: str) -> Optional[str]:
    """A file git does not track yet, as a diff that adds all of it, or
    None when it is too big or not text."""

    path = folder / name
    try:
        if path.stat().st_size > MAX_NEW_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    body = "".join(f"+{line}\n" for line in lines)
    return (f"diff --git a/{name} b/{name}\nnew file\n--- /dev/null\n"
            f"+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n{body}")


def diff_of(target: str = "", folder: Optional[Path] = None
            ) -> tuple[str, str, list[str]]:
    """The change to review, what it is in words, and the new files too
    big to read. TARGET "" is the uncommitted changes, new files
    included; "A..B" that range of commits; anything else a branch or
    commit, for what HEAD has that it does not."""

    here = Path(folder or Path.cwd())
    try:
        top = Path(_git(["rev-parse", "--show-toplevel"], here).strip())
    except ReviewError as exc:
        raise ReviewError(
            f"{here} is not in a git repository, and a review reads the "
            "change from git.") from exc
    target = " ".join(str(target or "").split())
    if target.startswith("-"):
        raise ReviewError("Name a branch, a commit, or a range like a..b.")
    skipped: list[str] = []

    if not target:
        has_head = True
        try:
            _git(["rev-parse", "--verify", "--quiet", "HEAD"], top)
        except ReviewError:
            has_head = False
        diff = _git(["diff", "HEAD"] if has_head else ["diff", "--cached"],
                    top)
        new = _git(["ls-files", "--others", "--exclude-standard", "-z"],
                   top).split("\0")
        for name in filter(None, new):
            added = _new_file_diff(top, name)
            if added is None:
                skipped.append(name)
            else:
                diff += ("" if not diff or diff.endswith("\n") else "\n") \
                    + added
        return diff, "your uncommitted changes", skipped

    if ".." in target:
        return _git(["diff", target, "--"], top), target, skipped
    return (
        _git(["diff", f"{target}...HEAD", "--"], top),
        f"what this branch has that {target} does not",
        skipped,
    )


def split_files(diff: str) -> list[tuple[str, str]]:
    """DIFF as (file, its part of the diff), in order."""

    files: list[tuple[str, list[str]]] = []
    for line in diff.splitlines():
        found = _FILE_RE.match(line)
        if found:
            files.append((found.group(2), [line]))
        elif files:
            files[-1][1].append(line)
    return [(name, "\n".join(lines)) for name, lines in files]


def numbered(file_diff: str) -> str:
    """FILE_DIFF with each kept or added line led by its number in the
    new version of the file, and the header lines git starts it with
    left out."""

    out: list[str] = []
    number: Optional[int] = None
    for line in file_diff.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk:
            number = int(hunk.group(1))
            out.append(line)
            continue
        if number is None:
            continue
        if line.startswith("-"):
            out.append(f"{'':>6} {line}")
        elif line.startswith(("+", " ")) or line == "":
            out.append(f"{number:>6} {line}")
            number += 1
        elif line.startswith("\\"):
            continue
    return "\n".join(out)


def _chunks(files: list[tuple[str, str]]) -> list[str]:
    """The numbered files, a few to each first-pass call."""

    chunks: list[str] = []
    current = ""
    for name, part in files:
        shown = f"=== {name} ===\n{numbered(part)}"
        if len(shown) > CHUNK_CHARS:
            shown = shown[:CHUNK_CHARS] + "\n[the rest of this file's " \
                "change is cut]"
        if current and len(current) + len(shown) > CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current += ("\n\n" if current else "") + shown
    if current:
        chunks.append(current)
    return chunks


def _content(response: Any) -> str:
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    content = (message.get("content") if isinstance(message, dict)
               else getattr(message, "content", ""))
    return str(content or "")


def _json(text: str) -> dict:
    """The JSON object in what the model said, or {}."""

    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    for attempt in (text, text[text.find("{"):text.rfind("}") + 1]):
        try:
            found = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    return {}


def _ask(client: Any, model: str, system: str, user: str,
         options: Optional[dict]) -> dict:
    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        format="json", options=options or {}, stream=False,
    )
    return _json(_content(response))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def propose(client: Any, model: str, chunk: str,
            options: Optional[dict] = None) -> list[Finding]:
    """The first pass over CHUNK: everything that might be wrong."""

    said = _ask(client, model, PROPOSE_PROMPT,
                f"The change:\n\n{chunk}", options)
    found = []
    for item in said.get("findings") or []:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())[:120]
        why = " ".join(str(item.get("why") or "").split())[:800]
        name = str(item.get("file") or "").strip()
        if not (title or why) or not name:
            continue
        found.append(Finding(
            file=name, line=int(_number(item.get("line"))),
            title=title or why[:80], why=why,
        ))
    return found


def _around(folder: Path, finding: Finding) -> str:
    """The lines of the file around FINDING, numbered, as it is now."""

    try:
        lines = (folder / finding.file).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if finding.line < 1:
        return ""
    start = max(1, finding.line - AROUND_LINES)
    end = min(len(lines), finding.line + AROUND_LINES)
    return "\n".join(f"{n:>6}  {lines[n - 1]}" for n in range(start, end + 1))


def refute(client: Any, model: str, finding: Finding, file_diff: str,
           folder: Path, options: Optional[dict] = None) -> Finding:
    """The second pass on FINDING: how sure it is, once someone has
    tried to explain it away."""

    shown = [
        f"The claim, about {finding.file} line {finding.line}: "
        f"{finding.title}. {finding.why}",
        f"The change to {finding.file}:\n{numbered(file_diff)[:CHUNK_CHARS]}",
    ]
    around = _around(folder, finding)
    if around:
        shown.append(f"{finding.file} around line {finding.line}, as it "
                     f"is now:\n{around}")
    said = _ask(client, model, REFUTE_PROMPT, "\n\n".join(shown), options)
    real = said.get("real")
    confidence = max(0.0, min(1.0, _number(said.get("confidence"))))
    if real is False or str(real).lower() == "false":
        confidence = min(confidence, 1 - confidence)
    finding.confidence = confidence
    finding.reason = " ".join(str(said.get("reason") or "").split())[:400]
    return finding


def _same(a: Finding, b: Finding) -> bool:
    """Whether A and B are one finding said twice, as two chunks or a
    model repeating itself say it: the same line, or the same title a
    line or two apart."""

    if a.file != b.file:
        return False
    return a.line == b.line or (
        abs(a.line - b.line) <= 2 and a.title.lower() == b.title.lower())


def review(
    client: Any, model: str, target: str = "",
    folder: Optional[Path] = None, options: Optional[dict] = None,
    doing: Optional[Callable[[str], None]] = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> Review:
    """Review the change TARGET names (see diff_of) in FOLDER with
    MODEL. DOING hears each step, for whoever is watching."""

    def say(text: str) -> None:
        if doing is not None:
            doing(text)

    here = Path(folder or Path.cwd())
    diff, what, skipped = diff_of(target, here)
    top = Path(_git(["rev-parse", "--show-toplevel"], here).strip())
    result = Review(what=what, unread=list(skipped))
    files = split_files(diff)
    kept: list[tuple[str, str]] = []
    total = 0
    for name, part in files:
        if total + len(part) > MAX_DIFF_CHARS:
            result.unread.append(name)
            continue
        total += len(part)
        kept.append((name, part))
    result.files = [name for name, _ in kept]
    if not kept:
        return result

    by_file = dict(kept)
    candidates: list[Finding] = []
    chunks = _chunks(kept)
    for n, chunk in enumerate(chunks, start=1):
        say(f"Reading the change ({n} of {len(chunks)})"
            if len(chunks) > 1 else "Reading the change")
        for found in propose(client, model, chunk, options):
            # A path the model shortened or decorated, matched back.
            if found.file not in by_file:
                match = [f for f in by_file
                         if f.endswith(found.file) or found.file.endswith(f)]
                if len(match) != 1:
                    continue
                found.file = match[0]
            if not any(_same(found, had) for had in candidates):
                candidates.append(found)
    candidates = candidates[:MAX_CANDIDATES]
    result.candidates = len(candidates)

    for n, found in enumerate(candidates, start=1):
        say(f"Checking {n} of {len(candidates)}: {found.title}")
        refute(client, model, found, by_file[found.file], top, options)
    result.findings = sorted(
        (f for f in candidates if f.confidence >= min_confidence),
        key=lambda f: -f.confidence,
    )
    return result


def report(result: Review) -> str:
    """RESULT in words: each finding at its file and line."""

    if not result.files and not result.unread:
        return f"There is nothing to review in {result.what}."
    count = len(result.files)
    lines = [f"Reviewed {result.what}: {count} file"
             f"{'' if count == 1 else 's'}."]
    if result.findings:
        dropped = result.candidates - len(result.findings)
        lines.append(
            f"{len(result.findings)} likely bug"
            f"{'' if len(result.findings) == 1 else 's'}"
            + (f" ({dropped} more were looked into and explained away)"
               if dropped else "") + ":")
        for found in result.findings:
            at = f"{found.file}:{found.line}" if found.line else found.file
            lines.append(f"- {at} {found.title} "
                         f"({round(found.confidence * 100)}% sure)")
            if found.why:
                lines.append(f"  {found.why}")
    elif result.candidates:
        lines.append(f"No bugs found: {result.candidates} possible ones "
                     "were looked into and each was explained away.")
    elif result.files:
        lines.append("No bugs found.")
    if result.unread:
        shown = ", ".join(result.unread[:8])
        more = len(result.unread) - 8
        lines.append(
            "Not read, for being too big: " + shown
            + (f" and {more} more" if more > 0 else "") + ".")
    return "\n".join(lines)
