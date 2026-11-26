"""Milestones: when the project a team works on passes one, a spark on it
says so in the team chat, in its own words: "Hey team, the repo just
passed 10,000 commits!"

What counts, looked at in the project's git repository every few
minutes: its commits passing a round number (100, 250, 500, 1,000 and on
to a million), a new release tag, and each year since its first commit.
The first look at a project only notes where it is, so a team never
hears about milestones it passed before it was watching. Said by the
team's lead, as a team's news is; never in the user's quiet hours (it
waits for them to end).
"""

from __future__ import annotations

import contextlib
import json
import subprocess  # nosec B404
import threading
import time
from pathlib import Path
from typing import Optional

# Commit counts worth a word.
COMMITS = (100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000,
           100_000, 250_000, 500_000, 1_000_000)
YEAR = 365.25 * 86400
# Between looks at the repositories.
EVERY = 300

NOTE = (
    "(Not from the user. {lead}, something just happened in {project}, "
    "the project this team works on: {what}. Tell the team in the chat, "
    "as yourself and in your own words: one or two lines, glad about it, "
    "with the number. No headings.)"
)

_last = [0.0]


def _path() -> Path:
    from . import sparks

    return sparks.sparks_dir() / ".milestones.json"


def _read() -> dict:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    temp.replace(path)


def _git(folder: str, *args: str) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607
            ["git", "-C", folder, *args], capture_output=True, text=True,
            timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def measure(folder: str) -> Optional[dict]:
    """Where the repository at FOLDER is: its commits, its newest tag
    ("" for none) and when its first commit was. None: not a repository,
    or one with no commits yet."""

    count = _git(folder, "rev-list", "--count", "HEAD")
    if not count.isdigit():
        return None
    tags = _git(folder, "tag", "--sort=-creatordate")
    first = _git(folder, "log", "--reverse", "--format=%ct", "--max-parents=0", "HEAD")
    born = first.split()[0] if first else ""
    return {"commits": int(count), "tag": tags.split("\n")[0].strip() if tags else "",
            "born": int(born) if born.isdigit() else 0}


def _number(n: int) -> str:
    return f"{n:,}"


def news(seen: Optional[dict], now: dict, at: float) -> tuple[list[str], dict]:
    """What is new between SEEN (the last look, None for a first one) and
    NOW (measure()'s), at time AT: lines to tell the team, and what to
    keep for the next look. A first look tells nothing."""

    years = int((at - now["born"]) // YEAR) if now.get("born") else 0
    keep = {"commits": now["commits"], "tag": now["tag"], "years": years}
    if seen is None:
        return [], keep
    said = []
    passed = [c for c in COMMITS if int(seen.get("commits", 0)) < c <= now["commits"]]
    if passed:
        said.append(f"the repository just passed {_number(passed[-1])} commits "
                    f"({_number(now['commits'])} now)")
    if now["tag"] and now["tag"] != seen.get("tag", ""):
        said.append(f"a new release was tagged: {now['tag']}")
    if years > int(seen.get("years", years)):
        said.append(f"it is {years} year{'s' if years > 1 else ''} since its "
                    f"first commit")
    return said, keep


def _project_folder(team) -> tuple[str, str]:
    from . import sparks

    project = sparks.team_project(team)
    if project is None or not getattr(project, "path", ""):
        return "", ""
    return project.path, project.name


def tick(now: Optional[float] = None, client=None, start=threading.Thread,
         force: bool = False) -> list[tuple[str, str]]:
    """Look at each team's project, every EVERY seconds (FORCE: now), and
    have its lead tell the team of anything passed since the last look.
    The (team, line) pairs told."""

    from . import checkins, sparks, teamchat

    at = time.time() if now is None else now
    if not force and at - _last[0] < EVERY:
        return []
    if checkins.is_quiet(at):
        # Kept for when the quiet hours end: nothing is looked at, so
        # nothing is passed by unsaid.
        return []
    _last[0] = at
    kept = _read()
    told: list[tuple[str, str]] = []
    for team in sparks.teams():
        if team.paused:
            continue
        folder, name = _project_folder(team)
        if not folder:
            continue
        found = measure(folder)
        if found is None:
            continue
        key = f"{team.id}:{team.project}"
        lines, kept[key] = news(kept.get(key), found, at)
        if not lines:
            continue
        lead = next((s for s in teamchat._leads(team.id) if not s.paused), None)  # pylint: disable=protected-access
        if lead is None:
            continue
        what = "; and ".join(lines)
        told += [(team.id, line) for line in lines]
        sparks.log(lead.id, "milestone", text=what)

        def run(team_id=team.id, lead=lead, what=what, name=name) -> None:
            with contextlib.suppress(Exception):
                note = teamchat.say(team_id, NOTE.format(
                    lead=f"@{lead.handle.lstrip('@') or lead.id}", project=name or "the project",
                    what=what), hidden=True)
                teamchat.answer(team_id, note, client)

        start(target=run, daemon=True).start()
    _write(kept)
    return told
