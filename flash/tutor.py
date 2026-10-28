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
propositions whose groundwork is known. Only the agent uses this, as a
tool: there is nothing to open.

Subjects learned by doing, like C or Linux, give a proposition an
exercise: a task, the language it is answered in (shell, for the
command line), any files it starts from, and the output a right answer
prints. check() runs the learner's own answer in a folder of its own,
compiled first where the language is, and marks it right or wrong by
what it printed, so it is measured by what their code does, not by
what anyone thinks of it.
"""

from __future__ import annotations

import os
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
            props[pid] = {"text": text, "exercise": exercise,
                          "needs": [_key(n) for n in given.get("needs") or []
                                    if _key(n) and _key(n) != pid],
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
        workspace._write(FILE, data)
    return {"id": _key(pid), "known": prop["known"],
            "is_known": prop["known"] >= KNOWN, "review_in_days": days}


def plan(subject: str, limit: int = 5, now: Optional[float] = None
         ) -> list[dict]:
    """What to do next: reviews that are due, then the least known
    propositions whose groundwork is known."""

    now = time.time() if now is None else now
    props = _subject(_load(), subject)["props"]
    due, ready = [], []
    for pid, prop in props.items():
        if prop["known"] >= KNOWN:
            if prop["due"] and prop["due"] <= now:
                due.append(("review", pid, prop))
        elif all(props[n]["known"] >= KNOWN for n in prop["needs"]):
            ready.append(("teach" if not prop["seen"] else "practice",
                          pid, prop))
    due.sort(key=lambda d: d[2]["due"])
    ready.sort(key=lambda r: (r[2]["known"], len(r[2]["needs"])))
    return [{"do": do, "id": pid, "text": prop["text"],
             "known": prop["known"], "exercise": prop.get("exercise")}
            for do, pid, prop in (due + ready)[:limit]]


def describe(subject: str) -> dict[str, Any]:
    """How far the user is: how many propositions are known, and the
    graph, groundwork first."""

    held = _subject(_load(), subject)
    props = held["props"]
    order: list[str] = []

    def place(pid: str) -> None:
        if pid in order:
            return
        for need in props[pid]["needs"]:
            place(need)
        order.append(pid)

    for pid in sorted(props):
        place(pid)
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
