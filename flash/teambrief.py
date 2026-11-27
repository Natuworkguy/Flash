"""A team's morning brief: once a day, as the user's quiet hours end (or at
8:00 with none set), each team's lead posts one message in the team chat
with what needs the user, the nearest first, and a line for each report
and failed shift since they last looked.

What goes in is gathered here, from the team as it is, so the lead need
not go looking: its sparks' steps waiting on the user's yes, the hires
and changes they proposed, the questions they @user'd the user with, and
the reports and failed shifts in the chat since the user last read it. A
team with nothing to tell gets no brief. Said once a day, never in quiet
hours, and not when Flash was off until well past the time.

On unless the user turns it off in Settings, kept in ~/.flash.env as
TEAM_BRIEF like Flash's other settings.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Optional

SETTING = "TEAM_BRIEF"
_OFF = ("0", "off", "false", "no")
# When it goes out with no quiet hours set.
DEFAULT_AT = "08:00"
# How far back a first brief looks, with the chat never read.
LOOK_BACK = 24 * 3600
# How many lines of each kind a brief is handed.
MOST_NEEDS = 6
MOST_NEWS = 8
LINE_CHARS = 140

NOTE = (
    "(Not from the user. {lead}, the user's day is starting: post "
    "{team}'s morning brief here, as yourself, in one message. First "
    "line: how many things need the user, and the nearest one, or that "
    "nothing does. Then a short line for each piece of news below, most "
    "pressing first. A greeting of a few words at most, no headings, "
    "nothing that is not below.\n{lines})"
)


def enabled() -> bool:
    """Whether team briefs go out. Read from the env file, so a keeper
    running elsewhere goes by what was set in another Flash."""

    from .notify import _saved

    return _saved(SETTING).strip().lower() not in _OFF


def _path() -> Path:
    from . import sparks

    return sparks.sparks_dir() / ".briefs.json"


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


def brief_at() -> str:
    """When the brief goes out, as 'HH:MM': the end of quiet hours, or
    DEFAULT_AT with none."""

    from . import checkins

    span = checkins.quiet_hours()
    if not span:
        return DEFAULT_AT
    end = span[1] % 1440
    return f"{end // 60:02d}:{end % 60:02d}"


def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= LINE_CHARS \
        else text[:LINE_CHARS].rstrip() + " [...]"


def gather(team, now: Optional[float] = None) -> tuple[list[str], list[str]]:
    """What TEAM's brief tells: what needs the user, and the news since
    they last read its chat (a day at most)."""

    from . import sparks, teamchat

    now = time.time() if now is None else now
    members = sparks.members(team.id)
    ids = {s.id for s in members}
    needs: list[str] = []
    for spark in members:
        if spark.waiting:
            needs.append(f"{spark.name} waits for your yes: "
                         f"{_short(spark.pending.get('label', ''))}")
    for hire in sparks.hires():
        if hire.get("by") not in ids:
            continue
        what = {
            "hire": f"hiring {(hire.get('spark') or {}).get('name', '')}",
            "remove": f"letting {hire.get('target_name', '')} go",
            "reorg": "a change to who reports to whom",
        }.get(hire.get("kind"), "a change to the team")
        needs.append(f"{hire.get('by_name', 'A spark')} proposes {what}")
    # pylint: disable=protected-access
    data = teamchat._read(team.id)
    since = max(float(data.get("read") or 0), now - LOOK_BACK)
    news: list[str] = []
    for entry in teamchat._live(data["entries"]):
        if entry.get("at", 0) <= since or entry.get("hidden"):
            continue
        name = entry.get("name") or "A spark"
        text = _short(entry.get("text", ""))
        if entry.get("kind") == teamchat.SPARK and "@user" in str(
                entry.get("text", "")).lower():
            needs.append(f"{name} asked you: {text}")
        elif entry.get("kind") == teamchat.EVENT and entry.get("spark"):
            if entry.get("what") == "report":
                news.append(f"{name} filed a report: {text}")
            elif entry.get("what") == "failed":
                news.append(f"{name}'s shift failed: {text}")
    return needs[:MOST_NEEDS], news[-MOST_NEWS:]


def note(team, lead, needs: list[str], news: list[str]) -> str:
    """The hidden note that has LEAD post TEAM's brief."""

    lines = ["Needs the user:", *(f"- {n}" for n in needs or ["nothing"]),
             "Since they last looked:", *(f"- {n}" for n in news or
                                          ["nothing new"])]
    return NOTE.format(lead=f"@{lead.handle.lstrip('@') or lead.id}",
                       team=team.name, lines="\n".join(lines))


def tick(now: Optional[float] = None, client=None, start=threading.Thread,
         force: bool = False) -> list[str]:
    """Have each team's lead post its brief, once a day at brief_at()
    (FORCE: now, whatever the time). The teams briefed."""

    from . import checkins, mornings, sparks, teamchat

    at = time.time() if now is None else now
    if not enabled() or (not force and checkins.is_quiet(at)):
        return []
    sent = _read()
    day = mornings.today(at)
    when = brief_at()
    told: list[str] = []
    for team in sparks.teams():
        if team.paused or sent.get(team.id) == day:
            continue
        if not force and not mornings.due(when, sent.get(team.id, ""), at):
            continue
        leads = teamchat._leads(team.id)  # pylint: disable=protected-access
        lead = next((s for s in leads if not s.paused), None)
        if lead is None:
            continue
        # Looked at once a day, said or not: nothing to tell is no brief.
        sent[team.id] = day
        needs, news = gather(team, at)
        if not needs and not news:
            continue
        told.append(team.id)
        sparks.log(lead.id, "team_brief", needs=len(needs), news=len(news))
        said = note(team, lead, needs, news)

        def run(team_id=team.id, said=said) -> None:
            with contextlib.suppress(Exception):
                asked = teamchat.say(team_id, said, hidden=True)
                teamchat.answer(team_id, asked, client)

        start(target=run, daemon=True).start()
    _write(sent)
    return told
