"""Snapshots of the files Flash changed, so a turn can be taken back.

Approving an edit is a judgement made in a second, from a diff a few
lines long, and the file it lands in may be the only copy. Git covers
the case where the work was committed; this covers the much more common
one where it was not.

Every file a tool is about to change is snapshotted first, grouped by
the turn that changed it, so `/undo` puts a whole turn back rather than
one edit out of the five that belonged together. Snapshots live in
memory for the length of the session: they exist to undo a mistake the
user just watched happen, not to be an archive.

A file too large to hold is recorded as unrevertable rather than
skipped, so `/undo` can say which files it could not put back instead of
reporting a clean revert that left one of them changed.
"""

import os
import shlex
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Big enough for any source file, small enough that a session that
# rewrites a few dozen of them costs megabytes rather than gigabytes.
MAX_SNAPSHOT_BYTES = 4_000_000

# Turns kept before the oldest is dropped.
MAX_GROUPS = 25

# The snapshot of a file that did not exist yet. Undoing it deletes the
# file, rather than restoring it as empty.
ABSENT = None


@dataclass
class Group:
    """Every file one turn touched, as it looked before the turn."""

    label: str = ""
    files: dict[str, Optional[bytes]] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    # Which of FILES a shell command changed rather than an edit tool, for the time machine to say.
    by_shell: set = field(default_factory=set)
    # Files that changed but were too big to snapshot.
    skipped: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.files) + len(self.skipped)


_groups: list[Group] = []


def clear() -> None:
    """Drop every snapshot, for /clear and a fresh session."""

    _groups.clear()


def start_turn(label: str = "") -> None:
    """Open a group for the turn that is about to run.

    An empty group left by the previous turn is reused rather than
    stacked, so a turn that changed nothing does not push a no-op onto
    the undo history.
    """

    if _groups and not len(_groups[-1]):
        _groups[-1].label = label
        _groups[-1].at = time.time()
        return

    _groups.append(Group(label=label))

    while len(_groups) > MAX_GROUPS:
        _groups.pop(0)


def _current() -> Group:
    if not _groups:
        _groups.append(Group())

    return _groups[-1]


def record(path: Path) -> None:
    """Snapshot `path` as it is now, before something changes it.

    Only the first call for a given file in a turn takes effect: the
    point of the snapshot is how the file looked before the turn, not
    before the most recent of several edits to it.
    """

    group = _current()
    key = str(Path(path).expanduser().resolve())

    if key in group.files or key in group.skipped:
        return

    target = Path(key)

    if not target.exists():
        group.files[key] = ABSENT
        return

    try:
        if target.stat().st_size > MAX_SNAPSHOT_BYTES:
            group.skipped.append(key)
            return
        group.files[key] = target.read_bytes()
    except OSError:
        group.skipped.append(key)


def _put(key: str, before: Optional[bytes], shell: bool = False) -> None:
    """Keep BEFORE as how KEY looked before this turn, unless the turn already has it."""

    group = _current()
    if key in group.files or key in group.skipped:
        return
    group.files[key] = before
    if shell:
        group.by_shell.add(key)


# ----- shell commands ---------------------------------------------------
#
# An edit tool says which file it is about to change; a shell command does
# not (a build, a formatter, `sed -i`, `rm -rf dist`). So the folder it runs
# in is looked at before and after: every file whose size or time changed,
# that appeared or that went, is recorded as it was, from a copy taken
# before the command ran. Copies are kept between commands and only read
# again when a file changes, so a second command in the same folder costs a
# walk of the tree, not a read of it.

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".cache", ".gradle", ".idea", "target", "Pods",
             "DerivedData", ".terraform", "site-packages"}
WATCH_FILES = 5000              # more files than this in the folder: only the paths the command names are watched
WATCH_BYTES = 80_000_000        # and copies kept at most, over every folder watched this session
_copies: dict[str, tuple[int, int, bytes]] = {}     # path -> (mtime_ns, size, bytes)
_copied = [0]
_copy_lock = threading.Lock()


def _walk(root: Path, limit: int) -> Optional[dict[str, tuple[int, int]]]:
    """Every file under ROOT as {path: (mtime_ns, size)}, or None past LIMIT files."""

    seen: dict[str, tuple[int, int]] = {}
    try:
        if root.is_file():
            st = root.stat()
            return {str(root.resolve()): (st.st_mtime_ns, st.st_size)}
    except OSError:
        return {}
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            path = os.path.join(here, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            seen[os.path.realpath(path)] = (st.st_mtime_ns, st.st_size)
            if len(seen) > limit:
                return None
    return seen


def _copy(path: str, stamp: tuple[int, int]) -> Optional[bytes]:
    with _copy_lock:
        kept = _copies.get(path)
        if kept and kept[:2] == stamp:
            return kept[2]
    if stamp[1] > MAX_SNAPSHOT_BYTES:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    with _copy_lock:
        old = _copies.pop(path, None)
        if old:
            _copied[0] -= len(old[2])
        if _copied[0] + len(data) > WATCH_BYTES:
            return None
        _copies[path] = (*stamp, data)
        _copied[0] += len(data)
    return data


def _named(command: str, cwd: Path) -> list[Path]:
    """Files and folders COMMAND names that exist, or that a redirection writes to."""

    try:
        words = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        words = command.split()
    found = []
    for i, w in enumerate(words):
        if w.startswith("-") or len(w) > 400 or any(ch in w for ch in "*?$`|;&"):
            continue
        target = w.lstrip(">")
        if not target or target in (".", ".."):
            continue
        p = Path(os.path.expanduser(target))
        p = p if p.is_absolute() else cwd / p
        writes = w.startswith(">") or (i and words[i - 1] in (">", ">>", "tee", "-o", "--output"))
        if p.exists() or (writes and p.parent.is_dir()):
            found.append(p)
    return found


class ShellWatch:
    """What one shell command changes: open it before the command runs, finish() after."""

    def __init__(self, command: str, cwd: Optional[str] = None):
        self.roots: list[Path] = []
        self.before: dict[str, tuple[int, int]] = {}
        here = Path(cwd or os.getcwd()).resolve()
        home = Path.home().resolve()
        # the home folder or the root is too much to walk: only what the command names
        if here not in (home, Path(here.anchor)):
            tree = _walk(here, WATCH_FILES)
            if tree is not None:
                self.roots.append(here)
                self.before.update(tree)
        for p in _named(command, here):
            p = p.resolve() if p.exists() else p
            if any(str(p).startswith(str(r) + os.sep) or p == r for r in self.roots):
                continue
            if p.exists():
                tree = _walk(p, WATCH_FILES // 5)
                if tree is None:
                    continue
                self.before.update(tree)
            self.roots.append(p)
        for path, stamp in self.before.items():
            _copy(path, stamp)

    def finish(self) -> list[str]:
        """Record every file the command changed, made or deleted, as it was before. The paths, for the record."""

        after: dict[str, tuple[int, int]] = {}
        for root in self.roots:
            tree = _walk(root, WATCH_FILES * 2) if root.exists() else {}
            after.update(tree or {})
        changed = []
        for path, stamp in self.before.items():
            if after.get(path) != stamp:
                kept = _copies.get(path)
                if kept and kept[:2] == stamp:
                    _put(path, kept[2], shell=True)
                else:
                    group = _current()
                    if path not in group.files and path not in group.skipped:
                        group.skipped.append(path)
                changed.append(path)
        for path in after.keys() - self.before.keys():
            _put(path, ABSENT, shell=True)
            changed.append(path)
        for path in changed:  # keep the copies in step for the next command
            if path in after:
                _copy(path, after[path])
        return changed


# ----- the time machine -------------------------------------------------

def history() -> list[dict]:
    """Every turn that changed files, oldest first: its label, when, and each file and what undoing does to it."""

    out = []
    for i, group in enumerate(_groups):
        if not len(group):
            continue
        files = []
        for key, before in group.files.items():
            now = Path(key).exists()
            files.append({"path": key, "name": Path(key).name,
                          "change": "made" if before is ABSENT else ("deleted" if not now else "changed"),
                          "shell": key in group.by_shell})
        files += [{"path": k, "name": Path(k).name, "change": "too big to keep", "shell": k in group.by_shell} for k in group.skipped]
        out.append({"index": i, "label": group.label, "at": round(group.at), "files": files})
    return out


def undo_to(index: int) -> str:
    """Put every file back as it was before turn INDEX (from history), undoing the later turns first."""

    if not 0 <= index < len(_groups):
        return "That point is no longer in the history."
    summaries = []
    while len(_groups) > index:
        group = _groups[-1]
        if not len(group):
            _groups.pop()
            continue
        summaries.append(undo())
    said = [s for s in summaries if s != "Nothing to undo."]
    return " ".join(said) if said else "Nothing to undo."


def depth() -> int:
    """How many turns could still be undone."""

    return sum(1 for group in _groups if len(group))


def describe() -> str:
    """What the next undo would put back, as one line."""

    group = _last()

    if group is None:
        return "Nothing to undo."

    count = len(group.files) + len(group.skipped)
    noun = "file" if count == 1 else "files"
    label = f" from {group.label}" if group.label else ""

    return f"Undo would restore {count} {noun}{label}."


def _last() -> Optional[Group]:
    for group in reversed(_groups):
        if len(group):
            return group

    return None


def _restore(key: str, before: Optional[bytes]) -> tuple[str, str]:
    """Put one file back. Returns (outcome, detail) for the summary."""

    target = Path(key)

    if before is ABSENT:
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            return "failed", f"{key}: {exc}"
        return "deleted", key

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)
    except OSError as exc:
        return "failed", f"{key}: {exc}"

    return "restored", key


def undo() -> str:
    """Put the most recent turn's files back. Returns a summary."""

    group = _last()

    if group is None:
        return "Nothing to undo."

    _groups.remove(group)

    restored: list[str] = []
    deleted: list[str] = []
    failed: list[str] = []

    for key, before in group.files.items():
        outcome, detail = _restore(key, before)
        if outcome == "restored":
            restored.append(detail)
        elif outcome == "deleted":
            deleted.append(detail)
        else:
            failed.append(detail)

    return _summary(restored, deleted, failed, group.skipped)


def _names(paths: list[str]) -> str:
    return ", ".join(Path(path).name for path in paths)


def _summary(
    restored: list[str],
    deleted: list[str],
    failed: list[str],
    skipped: list[str],
) -> str:
    parts = []

    if restored:
        count = len(restored)
        noun = "file" if count == 1 else "files"
        parts.append(f"Restored {count} {noun}: {_names(restored)}")
    if deleted:
        count = len(deleted)
        noun = "file" if count == 1 else "files"
        parts.append(f"Deleted {count} new {noun}: {_names(deleted)}")
    if skipped:
        parts.append(
            f"Left unchanged (too large to snapshot): {_names(skipped)}"
        )
    if failed:
        parts.append("Could not restore: " + "; ".join(failed))

    if not parts:
        return "Nothing to undo."

    return ". ".join(parts) + "."
