"""Git worktrees: a chat working in a checkout of its own.

A worktree is a second checkout of the same repository, on its own
branch, in its own folder. A chat in one changes files there and
nowhere else, so two chats can work on the same project at once, and
the main checkout stays as it was until the user brings the work over.

The worktrees Flash makes live under ~/.flash/worktrees, outside the
repository, on plain local branches named after what they are for. They
are private: Flash never pushes their branches or opens pull requests
from them (the shell tool refuses), and a branch made here has no
upstream, so a bare `git push` from it goes nowhere. Bringing the work
over is a local merge into the main checkout, and the user's own push
is theirs to make.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess  # nosec B404 -- git, with fixed arguments
from pathlib import Path
from typing import Optional

TIMEOUT = 60
# The remote a private branch "pushes" to by default: one that does not
# exist, so `git push` with no arguments fails instead of publishing it.
NOWHERE = "nowhere"
# A command a chat in a private worktree may not run: publishing its
# work is the user's to do.
PUBLISHING = re.compile(
    r"\bgit\s+(?:(?:-C|-c|--git-dir|--work-tree)\s+\S+\s+|--?\S+\s+)*push\b|\bgh\s+pr\s+create\b|\bgh\s+pr\s+merge\b|"
    r"\bglab\s+mr\s+create\b|\bhub\s+pull-request\b", re.I)


class WorktreeError(ValueError):
    """A worktree that could not be made, used or removed, and why."""


def _git(folder: str, *args: str, check: bool = True) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607 -- git with fixed arguments
            ["git", "-C", folder, *args], capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )
    except FileNotFoundError:
        raise WorktreeError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise WorktreeError(f"git {args[0]} took too long") from None
    if check and done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise WorktreeError(said[-1] if said else f"git {args[0]} failed")
    return done.stdout


def home() -> Path:
    from . import paths

    return paths.FLASH_DIR / "worktrees"


def repo_of(folder: str) -> Optional[str]:
    """The main checkout of the repository FOLDER is in, or None."""

    if not folder or not os.path.isdir(folder):
        return None
    try:
        common = _git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    except WorktreeError:
        return None
    common_path = Path(common)
    return str(common_path.parent if common_path.name == ".git" else common_path)


def listing(folder: str) -> list[dict]:
    """The checkouts of FOLDER's repository: the main one first, then
    each worktree, with its branch, whether Flash made it, and how many
    files are changed in it."""

    main = repo_of(folder)
    if main is None:
        raise WorktreeError("that folder is not in a git repository")
    found: list[dict] = []
    item: dict = {}
    for line in _git(main, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if item.get("path"):
                found.append(item)
            item = {}
        elif line.startswith("worktree "):
            item = {"path": line[9:], "branch": "", "detached": False}
        elif line.startswith("branch "):
            item["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "detached":
            item["detached"] = True
        elif line.startswith("prunable"):
            item["gone"] = True
    flash_home = str(home())
    for n, wt in enumerate(found):
        wt["main"] = n == 0
        wt["private"] = wt["path"].startswith(flash_home)
        wt["changed"] = 0 if wt.get("gone") else changed(wt["path"])
    return found


def changed(path: str) -> int:
    """How many files are changed, added or removed in the checkout at PATH."""

    try:
        return len([ln for ln in _git(path, "status", "--porcelain").splitlines() if ln.strip()])
    except WorktreeError:
        return 0


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "work"


def create(folder: str, name: str, base: str = "") -> dict:
    """A new private worktree of FOLDER's repository, on a new local
    branch named after NAME, starting from BASE (the main checkout's
    current commit if not given)."""

    main = repo_of(folder)
    if main is None:
        raise WorktreeError("that folder is not in a git repository")
    if not _git(main, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
        raise WorktreeError("the repository has no commits yet: commit once, then make a worktree")
    slug = _slug(name)
    taken = set(_git(main, "branch", "--format=%(refname:short)").split())
    branch, n = slug, 2
    while branch in taken:
        branch, n = f"{slug}-{n}", n + 1
    tag = hashlib.sha1(main.encode()).hexdigest()[:6]
    place = home() / f"{Path(main).name}-{tag}" / branch
    place.parent.mkdir(parents=True, exist_ok=True)
    start = base.strip() or "HEAD"
    _git(main, "worktree", "add", "-b", branch, str(place), start)
    # No upstream, and a push remote that is not there: a bare `git push`
    # from this branch publishes nothing.
    _git(main, "config", f"branch.{branch}.pushRemote", NOWHERE)
    return {"path": str(place), "branch": branch, "main": False, "private": True, "changed": 0}


def find(folder: str, path: str) -> dict:
    for wt in listing(folder):
        if os.path.realpath(wt["path"]) == os.path.realpath(path):
            return wt
    raise WorktreeError("that worktree is not part of this repository any more")


def is_private(folder: str) -> bool:
    """Whether FOLDER is in a worktree Flash made."""

    try:
        return os.path.realpath(folder).startswith(os.path.realpath(home()) + os.sep)
    except (OSError, ValueError):
        return False


def publishes(command: str) -> bool:
    return bool(PUBLISHING.search(command or ""))


def merge(folder: str, path: str) -> str:
    """Bring the work in the worktree at PATH into the main checkout,
    on whatever branch it has out, as a local merge. Its uncommitted
    changes are committed first. Nothing is pushed."""

    wt = find(folder, path)
    if wt["main"]:
        raise WorktreeError("that is the main checkout")
    main = repo_of(folder) or folder
    if changed(main):
        raise WorktreeError("the main checkout has changes of its own: commit or stash them first")
    if changed(wt["path"]):
        _git(wt["path"], "add", "-A")
        _git(wt["path"], "commit", "-q", "-m", f"Work on {wt['branch']}")
    target = _git(main, "rev-parse", "--abbrev-ref", "HEAD").strip()
    ahead = _git(main, "rev-list", "--count", f"{target}..{wt['branch']}").strip()
    if ahead == "0":
        return f"{target} already has everything from {wt['branch']}."
    done = subprocess.run(  # nosec B603 B607 -- git with fixed arguments
        ["git", "-C", main, "merge", "--no-ff", "--no-edit", wt["branch"]],
        capture_output=True, text=True, timeout=TIMEOUT, check=False)
    if done.returncode != 0:
        _git(main, "merge", "--abort", check=False)
        raise WorktreeError(f"{wt['branch']} does not merge cleanly into {target}: "
                            f"resolve it in the worktree (merge {target} there first), then try again")
    return f"Merged {ahead} commit{'s' if ahead != '1' else ''} from {wt['branch']} into {target}. Nothing was pushed."


def remove(folder: str, path: str, force: bool = False) -> str:
    """Delete the worktree at PATH, and its branch once its work is in
    the main checkout's branch (or FORCE, which drops it either way)."""

    wt = find(folder, path)
    if wt["main"]:
        raise WorktreeError("the main checkout is not removed from here")
    if not force and changed(wt["path"]):
        raise WorktreeError(f"{wt['branch'] or 'it'} has {wt['changed']} changed files: merge it first, or remove it anyway")
    main = repo_of(folder) or folder
    _git(main, "worktree", "remove", *(["--force"] if force else []), wt["path"])
    kept = ""
    if wt["branch"] and wt.get("private"):
        if _git(main, "branch", "-d" if not force else "-D", wt["branch"], check=False) == "":
            kept = f" The branch {wt['branch']} is kept, since its work is not merged."
    return f"Removed the worktree{' for ' + wt['branch'] if wt['branch'] else ''}.{kept}"


def describe(path: str) -> str:
    """For the model: where it is working, and the rules of a private
    worktree."""

    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "a detached commit"
    main = repo_of(path) or "?"
    lines = [
        "=== Worktree ===",
        f"This chat works in a git worktree at {path}, on the branch {branch}: a checkout of its own of "
        f"the repository whose main checkout is {main}. Relative paths start here. Change and run things "
        "here only; the main checkout is not this chat's to touch.",
    ]
    if is_private(path):
        lines.append(
            "The branch is private to this computer. Commit here when a piece of work is done, with plain "
            "messages that say what changed, but never push it, never set an upstream, and never open a pull "
            "request: the user brings the work over with Merge in the worktree menu, and publishes it "
            "themselves if they want to.")
    return "\n".join(lines)
