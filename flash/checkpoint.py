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

from dataclasses import dataclass, field
from pathlib import Path

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
    files: dict[str, bytes | None] = field(default_factory=dict)
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


def _last() -> Group | None:
    for group in reversed(_groups):
        if len(group):
            return group

    return None


def _restore(key: str, before: bytes | None) -> tuple[str, str]:
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
