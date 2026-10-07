"""Tab completion for /sparks in the terminal.

What each of its actions takes, word by word: a spark, a team, a
template, a schedule. Typed after /sparks, the menu offers the actions;
after one, what goes next: the sparks by the name /sparks knows them by,
the teams by name (spaces and all), and the words an action understands.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from prompt_toolkit.completion import Completion

# What each action is for, and what its words after it are, in order.
# A word kind is one of the names in _choices; a team, a template or a
# schedule can run to several words, so it is matched whole.
ACTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("new", "make a spark, a question at a time", ()),
    ("chat", "talk with a spark between its shifts", ("spark",)),
    ("run", "start a shift now", ("spark",)),
    ("pause", "pause a spark, or all of them", ("spark+all",)),
    ("resume", "put a spark back on its schedule, or all", ("spark+all",)),
    ("stop", "stop the shift running now, or all", ("spark+all",)),
    ("teach", "tell a spark how to do its job", ("spark",)),
    ("unteach", "let go of a lesson, by its number", ("spark",)),
    ("like", "more like its latest report", ("spark",)),
    ("dislike", "less like its latest report, and why", ("spark",)),
    ("approve", "let the step it waits on run", ("spark",)),
    ("deny", "turn down the step it waits on", ("spark",)),
    ("goal", "give a spark a new goal", ("spark",)),
    ("every", "how often it works", ("spark", "every")),
    ("at", "set times it works", ("spark", "at")),
    ("title", "its job title", ("spark", "none")),
    ("colour", "its colour: a name, or any as #rrggbb", ("spark", "colour")),
    ("watch", "a folder whose changes start a shift", ("spark", "none")),
    ("model", "the model it runs on", ("spark",)),
    ("project", "the project it works on", ("spark", "none")),
    ("team", "put a spark on a team", ("spark", "team+none")),
    ("lead", "who a spark reports to", ("spark", "spark+none")),
    ("budget", "a token budget, or off", ("spark", "budget")),
    ("audit", "its audit log", ("spark",)),
    ("share", "a code anyone can add a copy from", ("spark",)),
    ("add", "a spark from a code or a template", ("template",)),
    ("templates", "ready-made sparks", ()),
    ("remove", "remove a spark and its reports", ("spark",)),
    ("teams", "teams: new, rules, pause, share, add", ("teams",)),
    ("teamchat", "a team's group chat", ("team",)),
    ("hires", "proposals waiting on your yes", ()),
    ("hire", "say yes or no to a proposal", ("hire", "yesno")),
    ("rounds", "how many tool rounds a shift gets", ("rounds",)),
    ("default", "the model new sparks are made on", ("default",)),
    ("always", "keep sparks working with Flash closed", ("onoff",)),
    ("takeover", "keep them from this Flash, not another", ()),
]

TEAMS_ACTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("new", "make a team", ()),
    ("rules", "rules every spark on a team keeps to", ("team",)),
    ("pause", "pause a whole team", ("team",)),
    ("resume", "put a team back on its schedules", ("team",)),
    ("share", "a code for the whole team", ("team",)),
    ("remove", "take a team away; its sparks stay", ("team",)),
    ("add", "a team from a code or a template", ("teamtemplate",)),
    ("templates", "ready-made teams", ()),
]

# The kinds whose words can have spaces in them.
_WHOLE = {"team", "team+none", "template", "teamtemplate", "every", "at"}

_WORDS = {
    "none": [("none", "take it away")],
    "all": [("all", "every spark")],
    "every": [
        ("30m", "every half hour"), ("1h", "every hour"),
        ("2h", "every two hours"), ("daily", "once a day"),
        ("weekly", "once a week"), ("on call", "only when called on"),
    ],
    "at": [
        ("9am weekdays", "at nine on weekdays"),
        ("0 9 * * 1-5", "a cron line, local time"),
    ],
    "budget": [
        ("50k", "tokens a month"), ("50k day", "tokens a day"),
        ("hour", "count by the hour"), ("day", "count by the day"),
        ("week", "count by the week"), ("month", "count by the month"),
        ("off", "no budget"),
    ],
    "yesno": [("yes", "approve it"), ("no", "decline it, with why")],
    "rounds": [("unlimited", "work until the report")],
    "default": [("flash", "Flash's own model")],
    "onoff": [("on", "keep them working"), ("off", "only while open")],
}

# Sparks and teams are read from disk; the menu asks on every key, so
# what it read is kept for a moment.
_CACHE_SECONDS = 2.0
_cache: dict[str, tuple[float, list]] = {}


def _cached(name: str, read) -> list:
    now = time.monotonic()
    found = _cache.get(name)
    if found is None or now - found[0] > _CACHE_SECONDS:
        found = (now, read())
        _cache[name] = found
    return found[1]


def _spark_key(spark) -> str:
    """What to type after /sparks to mean SPARK: its handle, bare."""

    return spark.handle[1:].removesuffix("-spark")


def _sparks() -> list[tuple[str, ...]]:
    """Each spark: its key, what the menu says of it, and its name, which
    finds it too."""

    from . import sparks  # deferred: only once someone types /sparks

    return _cached("sparks", lambda: [
        (_spark_key(s), s.name + (f", {s.title}" if s.title else ""), s.name)
        for s in sparks.all_sparks()
    ])


def _teams() -> list[tuple[str, str]]:
    from . import sparks

    def read() -> list[tuple[str, str]]:
        out = []
        for team in sparks.teams():
            count = len(sparks.members(team.id))
            out.append((team.name, f"team of {count}"
                        + (", paused" if team.paused else "")))
        return out

    return _cached("teams", read)


def _choices(kind: str) -> list[tuple[str, ...]]:
    from . import sparks

    out: list[tuple[str, ...]] = []
    for part in kind.split("+"):
        if part == "spark":
            out += _sparks()
        elif part == "team":
            out += _teams()
        elif part == "template":
            out += [(t["name"], t.get("blurb", "template"))
                    for t in sparks.TEMPLATES]
        elif part == "teamtemplate":
            out += [(t["name"], t["blurb"]) for t in sparks.TEAM_TEMPLATES]
        elif part == "colour":
            out += [(name, hex_) for name, hex_ in sparks.COLOUR_NAMES.items()]
        elif part == "hire":
            out += _cached("hires", lambda: [
                (h["id"], h.get("by_name", "") + (
                    f" hires {h['spark']['name']}" if h.get("kind") == "hire"
                    else f" lets {h.get('target_name', 'one')} go"
                ))
                for h in sparks.hires()
            ])
        else:
            out += _WORDS.get(part, [])
    return out


def _offer(
    choices: list[tuple[str, ...]], typed: str,
) -> Iterator[Completion]:
    """Those of CHOICES, (text, meaning[, name]), whose text or name
    starts with TYPED. A spark's @ is let be."""

    want = typed.lower().lstrip("@")
    for text, meta, *name in choices:
        if text.lower().startswith(want) or any(
            n.lower().startswith(want) for n in name
        ):
            yield Completion(
                text, start_position=-len(typed), display=text,
                display_meta=meta,
            )


def complete(text: str) -> Iterator[Completion]:
    """Completions for TEXT, all typed after "/sparks "."""

    tokens = text.split(" ")
    done, typed = [w for w in tokens[:-1] if w], tokens[-1]
    if not done:
        yield from _offer([(a, d) for a, d, _ in ACTIONS], typed)
        if typed:
            # A spark's name alone reads its reports.
            yield from _offer([
                (key, f"{meta}: read its reports", *name)
                for key, meta, *name in _sparks()
                if all(key != a for a, _, _ in ACTIONS)
            ], typed)
        return
    action = done[0].lower()
    spec = next((s for a, _, s in ACTIONS if a == action), None)
    base = 1
    if spec == ("teams",):
        if len(done) == 1:
            yield from _offer([(a, d) for a, d, _ in TEAMS_ACTIONS], typed)
            return
        sub = done[1].lower()
        spec = next((s for a, _, s in TEAMS_ACTIONS if a == sub), None)
        base = 2
    if not spec:
        return
    at = len(done) - base
    for index, kind in enumerate(spec):
        if index == at:
            yield from _offer(_choices(kind), typed)
            return
        if index < at and kind in _WHOLE:
            # Several words in: the whole of them, as one name.
            whole = " ".join([*done[base + index:], typed])
            yield from _offer(_choices(kind), whole)
            return
