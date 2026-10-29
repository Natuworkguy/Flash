"""Teaching: what the user is learning, kept as a graph of small facts.

When someone asks Flash to teach them something, the subject is cut
into propositions: single claims that can each be checked with one
question ("a derivative is the slope of a function at a point"). Each
names the ones it rests on, so the graph says what to teach first.

Every answer the user gives to a practice question moves an estimate of
whether they know that proposition, by knowledge tracing: a right answer
raises it, less so when guessing would have got it right, and a wrong
one lowers it, less so when it could have been a slip. Something known
comes back for review after a day, then two, four, and so on, and a
wrong answer brings it back the next day.

The plan is what to do next: reviews that are due, then the weakest
propositions whose groundwork is known.

On the web page a subject is a course, drawn in the chat: its path of
lessons, each a short explanation, then a question to tap an answer to
or code to write and run, marked at once, with points for each right
answer and a streak of days practised. Answers given there never go
through the model; "Ask Flash" does.

Subjects learned by doing, like C or Linux, give a proposition an
exercise: a task, the language it is answered in (shell, for the
command line), any files it starts from, and the output a right answer
prints. check() runs the learner's own answer in a folder of its own,
compiled first where the language is, and marks it right or wrong by
what it printed, so it is measured by what their code does, not by
what anyone thinks of it.
"""

from __future__ import annotations

import datetime as dt
import os
import random
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import workspace

FILE = "tutor.json"
DAY = 86400
# Knowledge tracing: what it is thought known to start, the chance of
# learning it from one practice, of guessing it right, and of slipping.
START, LEARN, GUESS, SLIP = 0.2, 0.15, 0.2, 0.1
KNOWN = 0.9
MOST_INTERVAL_DAYS = 120
_lock = threading.Lock()
XP_RIGHT = 10
MOST_LESSON = 6000
RUN_SECONDS = 10
MOST_OUTPUT = 8000
MATCHES = ("exact", "contains", "regex")
# Each language: the file the answer goes in, how it is built (or
# None), how it is run, and the programs it needs.
LANGUAGES: dict[str, dict[str, Any]] = {
    "c": {"file": "main.c", "build": ["cc", "-std=c17", "-Wall", "-o",
                                      "main", "main.c", "-lm"],
          "run": ["./main"], "needs": ["cc"]},
    "cpp": {"file": "main.cpp", "build": ["c++", "-std=c++20", "-Wall",
                                          "-o", "main", "main.cpp"],
            "run": ["./main"], "needs": ["c++"]},
    "python": {"file": "main.py", "build": None,
               "run": [sys.executable, "main.py"], "needs": []},
    "javascript": {"file": "main.js", "build": None,
                   "run": ["node", "main.js"], "needs": ["node"]},
    "rust": {"file": "main.rs", "build": ["rustc", "-o", "main", "main.rs"],
             "run": ["./main"], "needs": ["rustc"]},
    "go": {"file": "main.go", "build": ["go", "build", "-o", "main",
                                        "main.go"],
           "run": ["./main"], "needs": ["go"]},
    "java": {"file": "Main.java", "build": ["javac", "Main.java"],
             "run": ["java", "Main"], "needs": ["javac", "java"]},
    "shell": {"file": "answer.sh", "build": None,
              "run": ["bash", "answer.sh"], "needs": ["bash"]},
}
_ALIASES = {"c++": "cpp", "js": "javascript", "node": "javascript",
            "py": "python", "bash": "shell", "sh": "shell",
            "linux": "shell", "terminal": "shell", "rs": "rust",
            "golang": "go"}


class TutorError(ValueError):
    """A request the graph cannot carry out, in words."""


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60]


def _load() -> dict:
    found = workspace._read(FILE, {})
    return found if isinstance(found, dict) else {}


def subjects() -> list[str]:
    return sorted(_load())


def _subject(data: dict, subject: str) -> dict:
    key = _key(subject)
    if not key or key not in data:
        raise TutorError(f"There is no subject {subject!r}. Add "
                         "propositions to it first.")
    return data[key]


def add(subject: str, propositions: list[dict]) -> list[str]:
    """Add PROPOSITIONS to SUBJECT: each {id, text, needs: [ids]}. Ids
    already there have their text and needs replaced, and what is known
    of them kept."""

    key = _key(subject)
    if not key:
        raise TutorError("Name the subject.")
    with _lock:
        data = _load()
        held = data.setdefault(key, {"name": subject, "props": {}})
        props = held["props"]
        added = []
        for given in propositions or []:
            pid = _key(given.get("id") or given.get("text"))
            text = str(given.get("text") or "").strip()
            if not pid or not text:
                continue
            old = props.get(pid, {})
            exercise = _exercise(given.get("exercise"), pid)
            quiz = _quiz(given.get("quiz"), pid)
            blank = _blank(given.get("blank"), pid)
            order = _order(given.get("order"), pid)
            needs = [_key(n) for n in given.get("needs") or []
                     if _key(n) and _key(n) != pid]
            if needs and not (exercise or quiz or blank or order):
                raise TutorError(
                    f"{pid} has nothing to answer. Every step after a "
                    "unit's introduction needs one of: quiz {question, "
                    "choices, answer}, blank {prompt, accept}, order "
                    "{prompt, items}, or exercise {task, language, expect}."
                    " Add the steps again, each with one.")
            props[pid] = {"text": text, "exercise": exercise, "quiz": quiz,
                          "blank": blank, "order": order,
                          "title": str(given.get("title") or "").strip()[:60],
                          "lesson": str(given.get("lesson") or "").strip()[
                              :MOST_LESSON],
                          "needs": needs,
                          "known": old.get("known", START),
                          "seen": old.get("seen", 0),
                          "right": old.get("right", 0),
                          "streak": old.get("streak", 0),
                          "due": old.get("due", 0.0)}
            added.append(pid)
        missing = sorted({n for p in props.values() for n in p["needs"]}
                         - set(props))
        if missing:
            raise TutorError("These are needed but not given: "
                             + ", ".join(missing) + ".")
        if _cycle(props):
            raise TutorError("The propositions need each other in a loop.")
        workspace._write(FILE, data)
    return added


def language(name: str) -> str:
    """NAME as one of LANGUAGES, or "" when it is not one."""

    name = str(name or "").strip().lower()
    name = _ALIASES.get(name, name)
    return name if name in LANGUAGES else ""


def _exercise(given: Any, pid: str) -> Optional[dict]:
    """An exercise as it is kept, or None for a proposition without
    one."""

    if not given:
        return None
    if not isinstance(given, dict):
        raise TutorError(f"{pid}: an exercise is {{task, language, "
                         "expect}}.")
    task = str(given.get("task") or "").strip()
    lang = language(given.get("language"))
    if not task or not lang:
        raise TutorError(f"{pid}: an exercise needs a task and a language, "
                         "one of " + ", ".join(LANGUAGES) + ".")
    match = str(given.get("match") or "exact").lower()
    if match not in MATCHES:
        raise TutorError(f"{pid}: match is one of " + ", ".join(MATCHES)
                         + ".")
    expect = str(given.get("expect") or "")
    if match == "regex":
        try:
            re.compile(expect)
        except re.error as exc:
            raise TutorError(f"{pid}: its expect is not a regex: {exc}"
                             ) from None
    files = given.get("files") or {}
    if not isinstance(files, dict) or any(
            not _safe_name(n) for n in files):
        raise TutorError(f"{pid}: files are {{name: content}}, named "
                         "inside the exercise's folder.")
    return {"task": task, "language": lang, "expect": expect,
            "match": match, "stdin": str(given.get("stdin") or ""),
            "starter": str(given.get("starter") or ""),
            "verify": str(given.get("verify") or ""),
            "files": {str(n): str(c) for n, c in files.items()}}


def _quiz(given: Any, pid: str) -> Optional[dict]:
    """A question with choices, as it is kept: the right one by its
    place, or None."""

    if not given:
        return None
    if not isinstance(given, dict):
        raise TutorError(f"{pid}: a quiz is {{question, choices, answer}}.")
    question = str(given.get("question") or "").strip()
    choices = [str(c).strip() for c in given.get("choices") or []
               if str(c).strip()]
    if not question or not 2 <= len(choices) <= 6:
        raise TutorError(f"{pid}: a quiz needs a question and 2 to 6 "
                         "choices.")
    answer = given.get("answer")
    if isinstance(answer, str) and not answer.strip().isdigit():
        found = [i for i, c in enumerate(choices)
                 if c.lower() == answer.strip().lower()]
        answer = found[0] if found else None
    try:
        answer = int(answer)
    except (TypeError, ValueError):
        answer = -1
    if not 0 <= answer < len(choices):
        raise TutorError(f"{pid}: the quiz's answer is one of its choices, "
                         "or its place among them, from 0.")
    return {"question": question, "choices": choices, "answer": answer,
            "explain": str(given.get("explain") or "").strip()}


def _blank(given: Any, pid: str) -> Optional[dict]:
    """A sentence to finish by typing: the answers that count, or None."""

    if not given:
        return None
    if not isinstance(given, dict):
        raise TutorError(f"{pid}: a blank is {{prompt, accept}}.")
    prompt = str(given.get("prompt") or "").strip()
    accept = given.get("accept")
    accept = [accept] if isinstance(accept, str) else accept or []
    accept = [str(a).strip() for a in accept if str(a).strip()]
    if not prompt or not accept:
        raise TutorError(f"{pid}: a blank needs a prompt (with ___ where "
                         "the answer goes) and accept, the answers that "
                         "count.")
    return {"prompt": prompt, "accept": accept,
            "explain": str(given.get("explain") or "").strip()}


def _order(given: Any, pid: str) -> Optional[dict]:
    """Things to put in order, given in the right order, or None."""

    if not given:
        return None
    if not isinstance(given, dict):
        raise TutorError(f"{pid}: an order is {{prompt, items}}.")
    prompt = str(given.get("prompt") or "").strip()
    items = [str(i).strip() for i in given.get("items") or []
             if str(i).strip()]
    if not prompt or not 3 <= len(items) <= 8 or \
            len(set(items)) != len(items):
        raise TutorError(f"{pid}: an order needs a prompt and 3 to 8 "
                         "different items, in the right order.")
    return {"prompt": prompt, "items": items,
            "explain": str(given.get("explain") or "").strip()}


def _same(a: str, b: str) -> bool:
    """Typed answers match whatever their case, spacing or full stop."""

    def tidy(text: str) -> str:
        return " ".join(str(text).lower().split()).strip(" .!")

    return tidy(a) == tidy(b)


def _safe_name(name: Any) -> bool:
    path = Path(str(name or ""))
    return bool(str(name or "").strip()) and not path.is_absolute() \
        and ".." not in path.parts


def _cycle(props: dict) -> bool:
    state: dict[str, int] = {}

    def visit(pid: str) -> bool:
        if state.get(pid) == 1:
            return True
        if state.get(pid) == 2:
            return False
        state[pid] = 1
        if any(visit(n) for n in props[pid]["needs"]):
            return True
        state[pid] = 2
        return False

    return any(visit(pid) for pid in props)


def trace(known: float, right: bool) -> float:
    """What is thought known after one answer."""

    if right:
        seen = known * (1 - SLIP) / (known * (1 - SLIP) + (1 - known) * GUESS)
    else:
        seen = known * SLIP / (known * SLIP + (1 - known) * (1 - GUESS))
    return seen + (1 - seen) * LEARN


def measure(subject: str, pid: str, right: bool,
            now: Optional[float] = None) -> dict:
    """Record one practice answer to proposition PID."""

    now = time.time() if now is None else now
    with _lock:
        data = _load()
        props = _subject(data, subject)["props"]
        prop = props.get(_key(pid))
        if prop is None:
            raise TutorError(f"There is no proposition {pid!r} in "
                             f"{subject!r}.")
        prop["known"] = round(trace(prop["known"], right), 4)
        prop["seen"] += 1
        prop["right"] += int(right)
        prop["streak"] = prop["streak"] + 1 if right else 0
        days = min(MOST_INTERVAL_DAYS, 2 ** max(0, prop["streak"] - 1)) \
            if right else 1
        prop["due"] = now + days * DAY
        held = _subject(data, subject)
        held["xp"] = held.get("xp", 0) + (XP_RIGHT if right else 0)
        today = local_day(now).isoformat()
        held["days"] = sorted(set(held.get("days", [])) | {today})[-400:]
        workspace._write(FILE, data)
    return {"id": _key(pid), "known": prop["known"],
            "is_known": prop["known"] >= KNOWN, "review_in_days": days}


def plan(subject: str, limit: int = 5, now: Optional[float] = None
         ) -> list[dict]:
    """What to do next: reviews that are due, then, in the path's order,
    the propositions not yet passed whose groundwork is."""

    now = time.time() if now is None else now
    props = _subject(_load(), subject)["props"]
    order = {pid: n for n, pid in enumerate(_ordered(props))}
    due, ready = [], []
    for pid, prop in props.items():
        if passed(prop):
            if prop["due"] and prop["due"] <= now:
                due.append(("review" if prop["known"] >= KNOWN
                            else "practice", pid, prop))
        elif all(passed(props[n]) for n in prop["needs"]):
            ready.append(("teach" if not prop["seen"] else "practice",
                          pid, prop))
    due.sort(key=lambda d: d[2]["due"])
    ready.sort(key=lambda r: order[r[1]])
    return [{"do": do, "id": pid, "text": prop["text"],
             "known": prop["known"], "exercise": prop.get("exercise")}
            for do, pid, prop in (due + ready)[:limit]]


def passed(prop: dict) -> bool:
    """Answered right at least once, or known: what comes after it opens.
    How well it is known still decides when it comes back."""

    return prop.get("right", 0) > 0 or prop["known"] >= KNOWN


def _ordered(props: dict) -> list[str]:
    """The propositions, each after what it needs."""

    order: list[str] = []

    def place(pid: str) -> None:
        if pid in order:
            return
        for need in props[pid]["needs"]:
            place(need)
        order.append(pid)

    for pid in props:
        place(pid)
    return order


def describe(subject: str) -> dict[str, Any]:
    """How far the user is: how many propositions are known, and the
    graph, groundwork first."""

    held = _subject(_load(), subject)
    props = held["props"]
    order = _ordered(props)
    known = sum(1 for p in props.values() if p["known"] >= KNOWN)
    return {"subject": held["name"], "known": known, "of": len(props),
            "propositions": [{"id": pid, "text": props[pid]["text"],
                              "needs": props[pid]["needs"],
                              "known": props[pid]["known"],
                              "exercise": props[pid].get("exercise")}
                             for pid in order]}


def exercise(subject: str, pid: str) -> dict:
    """Proposition PID's exercise."""

    prop = _subject(_load(), subject)["props"].get(_key(pid))
    if prop is None:
        raise TutorError(f"There is no proposition {pid!r} in {subject!r}.")
    if not prop.get("exercise"):
        raise TutorError(f"{pid} has no exercise: ask about it, and "
                         "measure the answer.")
    return prop["exercise"]


def _clip(text: str) -> str:
    return text if len(text) <= MOST_OUTPUT else \
        text[:MOST_OUTPUT] + "\n... (cut)"


def run(lang: str, answer: str, files: Optional[dict] = None,
        stdin: str = "", verify: str = "") -> dict:
    """ANSWER in LANG, built and run in a folder of its own with FILES
    beside it: {built, stdout, stderr, exit, timed_out}. With VERIFY, a
    shell script run after it in the same folder, stdout is what VERIFY
    prints, for an answer that changes files rather than printing."""

    spec = LANGUAGES[lang]
    missing = [n for n in spec["needs"] if not shutil.which(n)]
    if missing:
        raise TutorError(f"{', '.join(missing)} is not installed here, so "
                         f"{lang} answers cannot be run. Install it first.")
    with tempfile.TemporaryDirectory(prefix="flash-tutor-") as folder:
        root = Path(folder)
        for name, content in (files or {}).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (root / spec["file"]).write_text(answer, encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": folder,
               "TMPDIR": folder, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "TERM": "dumb"}
        # Toolchains that find themselves through the home folder still
        # find themselves: the answer's home is its own folder.
        real_home = Path.home()
        for name, default in (("RUSTUP_HOME", real_home / ".rustup"),
                              ("CARGO_HOME", real_home / ".cargo"),
                              ("GOROOT", None), ("GOPATH", None),
                              ("JAVA_HOME", None), ("SYSTEMROOT", None)):
            value = os.environ.get(name) or (
                str(default) if default and default.exists() else "")
            if value:
                env[name] = value
        if verify:
            (root / ".verify.sh").write_text(verify, encoding="utf-8")
        steps = ([spec["build"]] if spec["build"] else []) + [spec["run"]] \
            + ([["bash", ".verify.sh"]] if verify else [])
        answered = None
        for n, argv in enumerate(steps):
            building = spec["build"] is not None and n == 0
            try:
                done = subprocess.run(  # nosec B603 -- the learner's own
                    argv, cwd=folder, env=env, capture_output=True,
                    text=True, timeout=RUN_SECONDS * (3 if building else 1),
                    input=None if building else stdin, check=False)
            except subprocess.TimeoutExpired as exc:
                return {"built": not building, "exit": None,
                        "timed_out": True,
                        "stdout": _clip(str(exc.stdout or "")),
                        "stderr": _clip(str(exc.stderr or ""))}
            if building and done.returncode:
                return {"built": False, "exit": done.returncode,
                        "timed_out": False, "stdout": _clip(done.stdout),
                        "stderr": _clip(done.stderr)}
            if argv == spec["run"]:
                answered = done
        # The answer's own exit and errors; the check's output.
        assert answered is not None  # nosec B101 -- the run step ran
        return {"built": True, "exit": answered.returncode,
                "timed_out": False, "stdout": _clip(done.stdout),
                "answer_output": _clip(answered.stdout),
                "stderr": _clip(answered.stderr)}


def matches(expect: str, output: str, match: str = "exact") -> bool:
    """Whether OUTPUT is what a right answer prints. Trailing space and
    the line endings never count."""

    def tidy(text: str) -> str:
        lines = text.replace("\r\n", "\n").split("\n")
        return "\n".join(line.rstrip() for line in lines).strip("\n")

    if match == "regex":
        return re.search(expect, output, re.M) is not None
    if match == "contains":
        return tidy(expect) in tidy(output)
    return tidy(expect) == tidy(output)


def check(subject: str, pid: str, answer: str,
          now: Optional[float] = None) -> dict:
    """Run the learner's ANSWER to PID's exercise, mark it, and measure
    it. A toolchain that is missing measures nothing."""

    task = exercise(subject, pid)
    if not str(answer or "").strip():
        raise TutorError("There is no answer to run: the learner writes it.")
    ran = run(task["language"], answer, task["files"], task["stdin"],
              task.get("verify", ""))
    right = ran["built"] and not ran["timed_out"] and ran["exit"] == 0 \
        and matches(task["expect"], ran["stdout"], task["match"])
    return {**ran, "right": right, "expected": task["expect"],
            "match": task["match"],
            "measured": measure(subject, pid, right, now)}


def forget(subject: str) -> None:
    with _lock:
        data = _load()
        _subject(data, subject)
        data.pop(_key(subject))
        workspace._write(FILE, data)


# The course, as the page draws it ------------------------------------------


def local_day(stamp: float) -> dt.date:
    """The day STAMP falls on, here."""

    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone() \
        .date()


def title_of(pid: str, prop: dict) -> str:
    if prop.get("title"):
        return prop["title"]
    words = pid.replace("-", " ").strip()
    return words[:1].upper() + words[1:]


def streak(days: list[str], today: Optional[dt.date] = None) -> int:
    """How many days in a row, up to today or yesterday, were practised."""

    have = set(days)
    day = today or local_day(time.time())
    if day.isoformat() not in have:
        day -= dt.timedelta(days=1)
    count = 0
    while day.isoformat() in have:
        count += 1
        day -= dt.timedelta(days=1)
    return count


def kind_of(prop: dict) -> str:
    for kind, key in (("code", "exercise"), ("quiz", "quiz"),
                      ("blank", "blank"), ("order", "order")):
        if prop.get(key):
            return kind
    return "read"


def course(subject: str, now: Optional[float] = None) -> dict:
    """SUBJECT as a course: its path, groundwork first, each step known,
    up next, to review, or waiting on what it needs; and which is next."""

    now = time.time() if now is None else now
    held = describe(subject)
    data = _subject(_load(), subject)
    props = data["props"]
    nexts = plan(subject, 1, now)
    current = nexts[0]["id"] if nexts else None
    steps = []
    for p in held["propositions"]:
        prop = props[p["id"]]
        if passed(prop):
            state = "review" if prop["due"] and prop["due"] <= now \
                else "known" if prop["known"] >= KNOWN else "passed"
        elif all(passed(props[n]) for n in prop["needs"]):
            state = "ready"
        else:
            state = "locked"
        steps.append({"id": p["id"], "title": title_of(p["id"], prop),
                      "kind": kind_of(prop), "state": state,
                      "known": prop["known"]})
    return {"subject": held["subject"], "key": _key(subject),
            "known": held["known"], "of": held["of"],
            "passed": sum(1 for p in props.values() if passed(p)),
            "xp": data.get("xp", 0),
            "streak": streak(data.get("days", []),
                             local_day(now)),
            "steps": steps, "current": current}


def step(subject: str, pid: str) -> dict:
    """One step, to show: its lesson, and its question or exercise,
    without the quiz's answer."""

    prop = _subject(_load(), subject)["props"].get(_key(pid))
    if prop is None:
        raise TutorError(f"There is no lesson {pid!r} in {subject!r}.")
    quiz = prop.get("quiz")
    task = prop.get("exercise")
    blank = prop.get("blank")
    order = prop.get("order")
    shuffled = None
    if order:
        # Shuffled the same way each time it is shown, never in order.
        shuffled = list(order["items"])
        random.Random(_key(pid)).shuffle(shuffled)  # nosec B311
        if shuffled == order["items"]:
            shuffled = shuffled[1:] + shuffled[:1]
    return {"id": _key(pid), "title": title_of(_key(pid), prop),
            "blank": {"prompt": blank["prompt"]} if blank else None,
            "order": {"prompt": order["prompt"], "items": shuffled}
            if order else None,
            "kind": kind_of(prop), "text": prop["text"],
            "lesson": prop.get("lesson") or "",
            "known": prop["known"], "seen": prop["seen"],
            "quiz": {"question": quiz["question"],
                     "choices": quiz["choices"]} if quiz else None,
            "exercise": {k: task[k] for k in
                         ("task", "language", "expect", "match",
                          "starter")} if task else None}


def answer(subject: str, pid: str, given: Any,
           now: Optional[float] = None) -> dict:
    """An answer given on the page, marked and measured: a choice's place
    for a quiz, the words typed for a blank, the items in the order put
    for an order."""

    prop = _subject(_load(), subject)["props"].get(_key(pid))
    kind = kind_of(prop) if prop else ""
    if kind == "quiz":
        quiz = prop["quiz"]
        try:
            right = int(given) == quiz["answer"]
        except (TypeError, ValueError):
            right = False
        said = {"answer": quiz["answer"], "explain": quiz["explain"]}
    elif kind == "blank":
        blank = prop["blank"]
        right = any(_same(given or "", a) for a in blank["accept"])
        said = {"answer": blank["accept"][0], "explain": blank["explain"]}
    elif kind == "order":
        order = prop["order"]
        right = [str(i) for i in given or []] == order["items"]
        said = {"answer": order["items"], "explain": order["explain"]}
    else:
        raise TutorError(f"{pid} has no question to answer.")
    return {"right": right, **said,
            "measured": measure(subject, pid, right, now)}


def understood(subject: str, pid: str, now: Optional[float] = None) -> dict:
    """A step with nothing to answer, read: it counts as known."""

    now = time.time() if now is None else now
    with _lock:
        data = _load()
        props = _subject(data, subject)["props"]
        prop = props.get(_key(pid))
        if prop is None:
            raise TutorError(f"There is no lesson {pid!r} in {subject!r}.")
        if kind_of(prop) != "read":
            raise TutorError(f"{pid} is answered, not just read.")
        prop.update(known=max(prop["known"], KNOWN), seen=prop["seen"] + 1,
                    streak=max(prop["streak"], 1), due=now + 7 * DAY)
        held = _subject(data, subject)
        held["days"] = sorted(set(held.get("days", []))
                              | {local_day(now).isoformat()})
        workspace._write(FILE, data)
    return {"id": _key(pid), "known": prop["known"]}
