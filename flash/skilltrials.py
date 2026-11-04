"""Skills that get better: each use of a skill is scored, a skill that
keeps going badly is rewritten from its runs, and the new version is
kept only if it does better.

A run is one turn that read the skill with skill_view: what was asked,
how many tools ran after, which of them failed, and the user's rating
of the reply, if they gave one. Its score is 1 for a good rating, 0 for
a bad one, and otherwise 1 less a quarter for each failed tool.

When the current version's last few runs score poorly, the model is
shown the skill and those runs and asked for a better version. That
version is tried on the next few runs, the old one kept aside. If the
new runs score higher than the old ones did, it stays; if not, the old
one comes back. Either way the history says what was tried and why.

Only skills Flash wrote itself (managed) are rewritten on their own,
unless SKILL_TRIALS is "all"; "off" stops it. A skill the user
wrote is improved only when they ask (/skills improve, or the Skills
page). Everything lives in ~/.flash/skill-trials/<name>/, apart from
the skill itself.
"""

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from . import providers, skills
from .paths import FLASH_DIR

# How many runs a version needs before it is judged.
RUNS_TO_JUDGE = 3
# A version whose last runs average under this is rewritten.
POOR = 0.75
# How many of the old version's runs the new one is measured against.
BASELINE_RUNS = 5
# Runs kept per skill.
MAX_RUNS = 60
TASK_CHARS = 300
ERROR_CHARS = 240
MAX_ERRORS_KEPT = 3

REWRITE_PROMPT = """\
You improve a skill: a procedure an assistant reads before a kind of \
task and follows. Below are the skill and its recent runs: what was \
asked, which tool calls failed and how, and what the user thought.

Write a better version. Fix the steps that led to the failures, add the \
pitfalls the runs show, and cut what did not help. Keep what worked. \
Write steps and commands, not narrative. Stay under {limit} characters.

Reply with only the new skill, in one ```markdown block, without the \
--- header. If nothing in the runs is the skill's fault, reply NO CHANGE.\
"""

_lock = threading.Lock()
_busy: set[str] = set()
_news: list[str] = []


class TrialError(ValueError):
    """A trial that could not be started or undone, and why."""


def mode() -> str:
    """Which skills are rewritten on their own: managed, all, or off."""

    said = os.getenv("SKILL_TRIALS", "managed").strip().lower()
    return said if said in ("managed", "all", "off") else "managed"


def _folder(name: str) -> Path:
    return FLASH_DIR / "skill-trials" / name


def version_of(body: str) -> str:
    return hashlib.sha1(body.strip().encode("utf-8"),
                        usedforsecurity=False).hexdigest()[:8]


def _read_lines(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _write_lines(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items),
                    encoding="utf-8")


def runs(name: str) -> list[dict]:
    return _read_lines(_folder(name) / "runs.jsonl")


def history(name: str) -> list[dict]:
    return _read_lines(_folder(name) / "history.jsonl")


def trial(name: str) -> Optional[dict]:
    try:
        found = json.loads((_folder(name) / "trial.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _set_trial(name: str, value: Optional[dict]) -> None:
    path = _folder(name) / "trial.json"
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _note(name: str, event: str, **more: Any) -> None:
    items = history(name)
    items.append({"at": round(time.time()), "event": event, **more})
    _write_lines(_folder(name) / "history.jsonl", items[-100:])


def _keep_version(name: str, body: str) -> str:
    vid = version_of(body)
    path = _folder(name) / "versions" / f"{vid}.md"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.strip() + "\n", encoding="utf-8")
    return vid


def score(run: dict) -> float:
    if run.get("rating") == "bad":
        return 0.0
    if run.get("rating") == "good":
        return 1.0
    return max(0.0, 1.0 - 0.25 * int(run.get("errors") or 0))


def average(items: list[dict]) -> Optional[float]:
    return sum(score(r) for r in items) / len(items) if items else None


# --- Runs, from a finished turn ------------------------------------------


def _arguments(call: Any) -> tuple[str, dict]:
    fn = call.get("function") if isinstance(call, dict) else \
        getattr(call, "function", None)
    if fn is None:
        return "", {}
    name = fn.get("name") if isinstance(fn, dict) else getattr(
        fn, "name", "")
    args = fn.get("arguments") if isinstance(fn, dict) else getattr(
        fn, "arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return str(name or ""), args if isinstance(args, dict) else {}


def _this_turn(messages: list[dict]) -> list[dict]:
    """The last turn: from the user's message that started it (a steer,
    said while tools ran, does not start one) to the end."""

    for at in range(len(messages) - 1, -1, -1):
        m = messages[at]
        if m.get("role") != "user":
            continue
        before = messages[at - 1] if at else {}
        if before.get("role") == "tool" or before.get("tool_calls"):
            continue
        return messages[at:]
    return list(messages)


def _text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content
                        if isinstance(p, dict))
    return str(content or "")


def reply_key(reply: str) -> str:
    return hashlib.sha1(" ".join(str(reply or "").split()).encode("utf-8"),
                        usedforsecurity=False).hexdigest()[:12]


def runs_in(messages: list[dict]) -> dict[str, dict]:
    """The runs in the last turn of MESSAGES: for each of the user's own
    skills it read, what came after."""

    turn = _this_turn(messages)
    if not turn:
        return {}
    task = " ".join(_text(turn[0].get("content")).split())[:TASK_CHARS]
    reply = next((_text(m.get("content")) for m in reversed(turn)
                  if m.get("role") == "assistant"
                  and not m.get("tool_calls")), "")
    found: dict[str, dict] = {}
    for at, m in enumerate(turn):
        for call in m.get("tool_calls") or []:
            name, args = _arguments(call)
            skill_name = str(args.get("name") or "").strip()
            if name != "skill_view" or args.get("path") or \
                    skill_name in found:
                continue
            skill = skills.find(skill_name, guides=False)
            if skill is None:
                continue
            found[skill_name] = {"start": at, "version": version_of(
                skill.body)}
    out = {}
    for skill_name, seen in found.items():
        after = turn[seen["start"] + 1:]
        results = [m for m in after if m.get("role") == "tool"]
        failed = [_text(m.get("content")) for m in results
                  if _text(m.get("content")).lstrip().startswith("Error")]
        out[skill_name] = {
            "at": round(time.time()), "version": seen["version"],
            "task": task, "tools": max(0, len(results) - 1),
            "errors": len(failed),
            "failures": [" ".join(f.split())[:ERROR_CHARS]
                         for f in failed[:MAX_ERRORS_KEPT]],
            "reply": reply_key(reply),
        }
    return out


def record(messages: list[dict]) -> list[str]:
    """Keep the runs in the turn just finished; the skills they were."""

    found = runs_in(messages)
    for name, run in found.items():
        items = runs(name) + [run]
        _write_lines(_folder(name) / "runs.jsonl", items[-MAX_RUNS:])
    return list(found)


def rated(reply: str, good: bool) -> int:
    """The user rated REPLY: the runs that led to it get the rating.
    How many there were."""

    key = reply_key(reply)
    marked = 0
    root = FLASH_DIR / "skill-trials"
    try:
        names = [p.name for p in root.iterdir() if p.is_dir()]
    except OSError:
        return 0
    for name in names:
        items = runs(name)
        hit = False
        for run in items:
            if run.get("reply") == key:
                run["rating"] = "good" if good else "bad"
                hit = True
                marked += 1
        if hit:
            _write_lines(_folder(name) / "runs.jsonl", items)
    return marked


# --- Judging and rewriting -----------------------------------------------


def _runs_of(name: str, version: str) -> list[dict]:
    return [r for r in runs(name) if r.get("version") == version]


def judge(name: str) -> str:
    """A trial with runs enough: kept, or the old version back. What was
    decided, or "" with nothing to decide yet."""

    now = trial(name)
    skill = skills.find(name, guides=False)
    if not now:
        return ""
    if skill is None:
        _set_trial(name, None)
        return ""
    if version_of(skill.body) != now["to"]:
        # Changed by hand, or by the review, while on trial: theirs wins.
        _set_trial(name, None)
        _note(name, "ended", why="the skill was changed during the trial",
              to=now["to"])
        return ""
    tried = _runs_of(name, now["to"])[-RUNS_TO_JUDGE:]
    if len(tried) < RUNS_TO_JUDGE:
        return ""
    new, old = average(tried), float(now.get("baseline") or 0)
    _set_trial(name, None)
    if new is not None and new > old:
        _note(name, "kept", **{"from": now["from"], "to": now["to"],
                               "old": round(old, 2), "new": round(new, 2)})
        said = (f"Kept the new version of skill {name}: it scored "
                f"{new:.0%} against {old:.0%} before.")
    else:
        restore(name, now["from"], why="did no better", note=False)
        _note(name, "rolled back", **{"from": now["to"], "to": now["from"],
                                      "old": round(old, 2),
                                      "new": round(new or 0, 2)})
        said = (f"Went back to the old version of skill {name}: the new "
                f"one scored {(new or 0):.0%}, no better than {old:.0%}.")
    with _lock:
        _news.append(said)
    return said


def due(name: str) -> bool:
    """Whether NAME's current version has gone badly often enough to be
    rewritten: not on trial, its last runs poor, and none of them seen
    by an earlier attempt that came to nothing."""

    if trial(name):
        return False
    skill = skills.find(name, guides=False)
    if skill is None:
        return False
    version = version_of(skill.body)
    tried_at = max((h.get("at", 0) for h in history(name)
                    if h.get("from") == version
                    or h.get("version") == version), default=0)
    mine = [r for r in _runs_of(name, version) if r.get("at", 0) > tried_at]
    last = mine[-RUNS_TO_JUDGE:]
    if len(last) < RUNS_TO_JUDGE:
        return False
    return (average(last) or 0) < POOR


def _prompt(skill: "skills.Skill", recent: list[dict]) -> list[dict]:
    lines = []
    for n, run in enumerate(recent, 1):
        said = {"good": "rated good", "bad": "rated bad"}.get(
            run.get("rating", ""), "not rated")
        lines.append(f"Run {n}: {run.get('task') or '(no message)'}\n"
                     f"  {run.get('tools', 0)} tool calls after reading "
                     f"it, {run.get('errors', 0)} failed; {said}.")
        for failure in run.get("failures") or []:
            lines.append(f"  failed: {failure}")
    return [
        {"role": "system", "content": REWRITE_PROMPT.format(
            limit=skills.MAX_SKILL_CHARS)},
        {"role": "user", "content": (
            f"Skill: {skill.name}\n{skill.description}\n\n"
            f"```markdown\n{skill.body.strip()}\n```\n\nRecent runs:\n"
            + "\n".join(lines))},
    ]


BLOCK_RE = re.compile(r"```(?:markdown|md)?\s*\n(.*?)```", re.S)


def _new_body(reply: str) -> str:
    reply = str(reply or "").strip()
    if not reply or reply.upper().startswith("NO CHANGE"):
        return ""
    found = BLOCK_RE.search(reply)
    body = (found.group(1) if found else reply).strip()
    if body.startswith("---"):
        body = body.split("---", 2)[-1].strip()
    return body


Ask = Callable[[list[dict]], str]


def _ask_model(host: str, model: str) -> Ask:
    def ask(messages: list[dict]) -> str:
        from . import learning

        client = providers.client(host)
        reply, _calls = learning._stream(client, model, messages, [])
        return reply
    return ask


def improve(name: str, ask: Ask, *, asked: bool = False) -> str:
    """Rewrite NAME from its runs and put the new version on trial. What
    happened. ASKED is the user asking, which is allowed for any skill
    of theirs and with fewer runs."""

    skill = skills.find(name, guides=False)
    if skill is None:
        raise TrialError(f"No skill of yours called {name!r}.")
    if trial(name):
        raise TrialError(f"{name} is already trying a new version.")
    version = version_of(skill.body)
    recent = runs(name)[-8:]
    if not recent:
        raise TrialError(f"{name} has not been used yet, so there is "
                         "nothing to learn from.")
    with _lock:
        if name in _busy:
            raise TrialError(f"{name} is being rewritten already.")
        _busy.add(name)
    try:
        body = _new_body(ask(_prompt(skill, recent)))
        if not body or version_of(body) == version:
            _note(name, "unchanged", version=version, asked=asked)
            return f"Nothing in {name}'s runs called for a change."
        if len(body) > skills.MAX_SKILL_CHARS:
            _note(name, "unchanged", version=version, asked=asked,
                  why="too long")
            return f"The rewrite of {name} came out too long; kept as is."
        _keep_version(name, skill.body)
        baseline = average(_runs_of(name, version)[-BASELINE_RUNS:])
        skills.manage("rewrite", name, content=body)
        new = _keep_version(name, body)
        _set_trial(name, {"from": version, "to": new,
                          "baseline": round(baseline or 0, 3),
                          "started": round(time.time()), "asked": asked})
        _note(name, "trying", **{"from": version, "to": new,
                                 "baseline": round(baseline or 0, 3),
                                 "asked": asked})
        said = (f"Trying a new version of skill {name} on its next "
                f"{RUNS_TO_JUDGE} uses; the old one comes back if it does "
                "no better.")
        with _lock:
            _news.append(said)
        return said
    finally:
        with _lock:
            _busy.discard(name)


def restore(name: str, version: str, *, why: str = "restored",
            note: bool = True) -> str:
    """Put version VERSION of NAME back, keeping the current one."""

    skill = skills.find(name, guides=False)
    if skill is None:
        raise TrialError(f"No skill of yours called {name!r}.")
    if not re.fullmatch(r"[0-9a-f]{8}", version or ""):
        raise TrialError("That is not a version of the skill.")
    path = _folder(name) / "versions" / f"{version}.md"
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        raise TrialError(f"{name} has no version {version}.") from None
    current = _keep_version(name, skill.body)
    if current == version:
        return f"{name} is already that version."
    skills.manage("rewrite", name, content=body)
    if note:
        _set_trial(name, None)
        _note(name, "restored", **{"from": current, "to": version,
                                   "why": why})
    return f"Skill {name} is back to version {version}."


def report(name: str) -> dict:
    """What the Skills page shows of a skill's runs and trials."""

    skill = skills.find(name, guides=False)
    version = version_of(skill.body) if skill else ""
    mine = _runs_of(name, version)
    now = trial(name)
    folder = _folder(name) / "versions"
    try:
        kept = sorted(p.stem for p in folder.glob("*.md"))
    except OSError:
        kept = []
    return {
        "version": version,
        "runs": len(runs(name)),
        "version_runs": len(mine),
        "score": average(mine[-BASELINE_RUNS:]),
        "trial": {**now, "done": len(_runs_of(name, now["to"])),
                  "of": RUNS_TO_JUDGE} if now else None,
        "history": history(name)[-20:],
        "versions": [v for v in kept if v != version],
        "recent": [{k: r.get(k) for k in ("at", "task", "errors", "tools",
                                          "rating", "failures")}
                   | {"score": score(r)} for r in runs(name)[-5:]],
    }


def after_turn(messages: list[dict], *, host: str, model: str,
               start: Callable[..., Any] = threading.Thread) -> None:
    """A turn finished: its runs kept, trials judged, and a rewrite
    started for a skill whose runs keep going badly."""

    used = record(messages)
    for name in used:
        judge(name)
    if mode() == "off" or not model:
        return
    for name in used:
        skill = skills.find(name, guides=False)
        if skill is None or (mode() == "managed" and not skill.managed):
            continue
        if due(name):
            start(target=_quietly, args=(name, _ask_model(host, model)),
                  daemon=True).start()
            return


def _quietly(name: str, ask: Ask) -> None:
    try:
        improve(name, ask)
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _news.append(f"Could not rewrite skill {name}: {exc}")


def news() -> list[str]:
    """What trials did since last asked, once each."""

    with _lock:
        said, _news[:] = list(_news), []
    return said
