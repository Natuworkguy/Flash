"""System One: a small, quick model that answers yes/no, choice, and
score questions, served by Ollama 0.35 and later at POST /v1/systemone.

Turned on (SYSTEM_ONE=1) and with autonomous mode on, Flash uses it two
ways. The agent can put a question to it with the ask_system_one tool,
for a quick second opinion that costs a fraction of a turn. And it
reviews every tool call that would have asked the user first, before
the call runs: one it judges unsafe, or not what the user asked for, is
not run, and the agent is told why. With nobody there to say no, it is
the one that can.

Which model answers is the user's pick (SYSTEM_ONE_MODEL), out of the
models on the server that list `decision` among their capabilities, or
none at all. Older Ollama servers have no such endpoint, so switching
it on checks the server's version first and says what to do about one
that is too old.
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .paths import ENV_PATH

MIN_VERSION = (0, 35)
MIN_VERSION_TEXT = "0.35"

# What Ollama's /api/show lists among a model's capabilities when it is
# one System One can run.
CAPABILITY = "decision"

# What to suggest when the server has none: the model Ollama's own
# guide starts with.
SUGGESTED_MODEL = "nimble"

DOWNLOAD_URL = "https://ollama.com/download"

VERSION_SECONDS = 5.0
SHOW_SECONDS = 5.0
ANSWER_SECONDS = 60.0
# How many models a scan asks about at once.
SCAN_THREADS = 4

# Below this a reviewed call is not run. Each answer is the model's
# probability of yes, so this is "more likely no than yes".
REVIEW_THRESHOLD = 0.5

# How much of the request and of a call's arguments a review sees. A
# small model reads a short state best, and a file's whole content says
# little more about whether writing it is safe than its first page.
REQUEST_CHARS = 2000
ARGUMENTS_CHARS = 4000

ASK_KINDS = ("yes_no", "choice", "score")


class SystemOneError(Exception):
    """System One could not answer, with what to do about it."""


class TooOld(SystemOneError):
    """The Ollama server predates System One."""

    def __init__(self, version: str) -> None:
        super().__init__(too_old(version))
        self.version = version


def too_old(version: str) -> str:
    """What to tell someone whose Ollama predates System One."""

    return (
        f"System One needs Ollama v{MIN_VERSION_TEXT} or later, and this "
        f"server runs v{version}. Update Ollama to v{MIN_VERSION_TEXT}+ "
        f"to use it: {DOWNLOAD_URL}"
    )


# --- Settings ----------------------------------------------------------------


def _saved(name: str) -> Optional[str]:
    """NAME as the env file has it, else as this process does.

    From the file first, as sparks.autonomous() reads autonomous mode:
    the web UI may have switched it in another Flash, and a spark's
    shift in a keeper process would not otherwise know.
    """

    from dotenv import dotenv_values

    try:
        value = dotenv_values(ENV_PATH).get(name)
    except OSError:
        value = None
    if value is None or not str(value).strip():
        value = os.environ.get(name)
    return None if value is None else str(value).strip()


def _save(name: str, value: str) -> None:
    from .envfile import set_env_var

    os.environ[name] = value
    set_env_var(ENV_PATH, name, value)


def enabled() -> bool:
    """Whether the user turned System One on, with a model to ask."""

    try:
        on = int(_saved("SYSTEM_ONE") or 0) > 0
    except ValueError:
        on = False
    return on and bool(model())


def model() -> str:
    """The model System One asks, as the user picked it, or "" when
    none has been picked yet."""

    return _saved("SYSTEM_ONE_MODEL") or ""


def autonomous() -> bool:
    """Whether autonomous mode is on, as the user last set it."""

    from . import sparks  # deferred: sparks imports half of Flash

    return sparks.autonomous()


def active() -> bool:
    """Whether System One is at work: on, and autonomous mode on too.

    Outside autonomous mode the user answers every call that matters
    themselves, and a second opinion before theirs would only slow
    them down.
    """

    return enabled() and autonomous()


def set_enabled(on: bool) -> None:
    _save("SYSTEM_ONE", "1" if on else "0")


def set_model(name: str) -> str:
    name = (name or "").strip()
    if not name or any(c.isspace() for c in name):
        raise SystemOneError("Name one model, without spaces.")
    _save("SYSTEM_ONE_MODEL", name)
    return name


def is_none(name: str) -> bool:
    """Whether NAME is the picker's None: no System One at all."""

    return (name or "").strip().lower() in ("", "none", "off")


def same_model(one: str, other: str) -> bool:
    """Whether two names are the same model: nimble is nimble:latest."""

    def tagged(name: str) -> str:
        name = (name or "").strip()
        return name if ":" in name.rpartition("/")[2] else f"{name}:latest"

    return bool(one and other) and tagged(one) == tagged(other)


# --- The server --------------------------------------------------------------


def _base(host: str) -> str:
    """HOST as a URL to put a path after."""

    host = (host or "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def _auth(url: str) -> dict:
    """The header with the API key of the host URL is on, if it has one."""

    from . import workspace  # deferred: workspace is only needed here

    parsed = urlparse(url)
    return workspace.auth_headers(f"{parsed.scheme}://{parsed.netloc}")


def _get(url: str, timeout: float) -> httpx.Response:
    return httpx.get(url, timeout=timeout, headers=_auth(url))


def _post(url: str, body: dict, timeout: float) -> httpx.Response:
    return httpx.post(url, json=body, timeout=timeout, headers=_auth(url))


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    """'0.35.1-rc0' as (0, 35, 1), or None when it is not a version."""

    found = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not found:
        return None
    return tuple(int(part) for part in found.groups() if part is not None)


def supports(version: str) -> bool:
    """Whether an Ollama at VERSION has System One."""

    parsed = parse_version(version)
    # A build from source reports 0.0.0; it is as new as its checkout,
    # and the endpoint's own answer says the rest.
    if parsed is None or parsed[:3] == (0, 0, 0):
        return True
    return parsed[:2] >= MIN_VERSION


def server_version(host: str) -> str:
    """The version of the Ollama at HOST."""

    try:
        response = _get(f"{_base(host)}/api/version", VERSION_SECONDS)
        response.raise_for_status()
        version = str(response.json().get("version") or "").strip()
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise SystemOneError(
            f"Could not reach Ollama at {host} to check its version "
            f"({exc.__class__.__name__}). Start it, then try again."
        ) from None
    if not version:
        raise SystemOneError(
            f"Ollama at {host} did not say which version it runs."
        )
    return version


# A server that passed, by host, so a review does not ask its version
# every call. One that failed is asked again: it may have been updated.
_checked: dict[str, str] = {}
_checked_lock = threading.Lock()


def check(host: str) -> str:
    """The server's version, once it is known to have System One.

    Raises SystemOneError saying what is wrong: an Ollama too old for
    it, or one that did not answer.
    """

    key = _base(host)
    with _checked_lock:
        if key in _checked:
            return _checked[key]
    version = server_version(host)
    if not supports(version):
        raise TooOld(version)
    with _checked_lock:
        _checked[key] = version
    return version


def forget_checks() -> None:
    """Ask each server its version again, after a host or an update."""

    with _checked_lock:
        _checked.clear()


def _capabilities(host: str, name: str) -> Optional[list[str]]:
    """What /api/show says NAME can do, or None when the server does
    not have it."""

    try:
        response = _post(
            f"{_base(host)}/api/show", {"model": name}, SHOW_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise SystemOneError(
            f"Could not ask Ollama about {name} ({exc.__class__.__name__})."
        ) from None
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
        listed = response.json().get("capabilities") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [str(c).lower() for c in listed] if isinstance(listed, list) \
        else []


def capable(host: str, name: str) -> Optional[bool]:
    """Whether NAME is a System One model on HOST: None when HOST does
    not have it at all."""

    found = _capabilities(host, name)
    return None if found is None else CAPABILITY in found


def scan(host: str) -> list[str]:
    """Every System One model on HOST, by name: the ones /api/show says
    have the decision capability. Asked of the server each time, so a
    model pulled a moment ago is in the list."""

    return sorted(
        name for name, can in catalog(host).items() if CAPABILITY in can
    )


def catalog(host: str) -> dict[str, list[str]]:
    """Every model on HOST, with what /api/show says each can do."""

    try:
        response = _get(f"{_base(host)}/api/tags", VERSION_SECONDS)
        response.raise_for_status()
        listed = response.json().get("models") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise SystemOneError(
            f"Could not list the models on {host} "
            f"({exc.__class__.__name__})."
        ) from None

    names = [
        str(m.get("model") or m.get("name") or "").strip()
        for m in listed if isinstance(m, dict)
    ]
    names = [n for n in dict.fromkeys(names) if n]

    def can(name: str) -> list[str]:
        try:
            return _capabilities(host, name) or []
        except SystemOneError:
            return []

    if not names:
        return {}
    with ThreadPoolExecutor(min(SCAN_THREADS, len(names))) as pool:
        return dict(zip(names, pool.map(can, names)))


def no_models(host: str) -> str:
    return (
        f"There are no System One models on {host}. Download one, like "
        f"`ollama pull {SUGGESTED_MODEL}`, and it shows up here."
    )


def choose(host: str, name: str) -> str:
    """Put NAME to work as System One, or switch System One off when
    NAME is None. Answers with the model now in use, "" for none.

    Checked before anything is saved: the server's version, then that
    it has NAME, then that NAME is a System One model at all.
    """

    if is_none(name):
        set_enabled(False)
        return ""

    name = name.strip()
    check(host)
    found = capable(host, name)
    if found is None:
        raise SystemOneError(
            f"{name} is not on this Ollama. Download it with `ollama pull "
            f"{name}`, then pick it again."
        )
    if not found:
        raise SystemOneError(
            f"{name} is not a System One model: Ollama does not list "
            f"`{CAPABILITY}` among what it can do."
        )
    set_model(name)
    set_enabled(True)
    return name


def turn_on(host: str) -> str:
    """Switch System One on, with the model picked before, or else the
    first System One model the server has. Answers with that model."""

    check(host)
    name = model()
    if not name:
        found = scan(host)
        if not found:
            raise SystemOneError(no_models(host))
        name = found[0]
    return choose(host, name)


def ask(
    host: str,
    state: Any,
    questions: dict[str, dict[str, Any]],
    name: str = "",
) -> dict[str, dict[str, Any]]:
    """Put QUESTIONS about STATE to the System One model, and answer
    with what it said to each, by the questions' keys."""

    check(host)
    use = name or model()
    try:
        response = _post(
            f"{_base(host)}/v1/systemone",
            {"model": use, "state": state, "questions": questions},
            ANSWER_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise SystemOneError(
            f"System One did not answer ({exc.__class__.__name__})."
        ) from None

    if response.status_code != 200:
        raise SystemOneError(_failure(response, use, host))

    try:
        answers = response.json().get("answers")
    except (ValueError, AttributeError):
        answers = None
    if not isinstance(answers, dict):
        raise SystemOneError(f"{use} answered with something unreadable.")
    return answers


def _failure(response: httpx.Response, name: str, host: str) -> str:
    """What went wrong with a call the server turned down."""

    try:
        said = str(response.json().get("error") or "").strip()
    except (ValueError, AttributeError):
        said = response.text.strip()[:200]

    if response.status_code == 404 and "model" in said.lower():
        return (
            f"{name} is not on this Ollama. Download it with `ollama pull "
            f"{name}`, or pick another System One model."
        )
    if response.status_code == 404:
        # No such endpoint: a server older than its version said, or
        # something in front of it that is.
        forget_checks()
        return (
            f"Ollama at {host} has no System One endpoint. It needs "
            f"v{MIN_VERSION_TEXT} or later, run locally."
        )
    if response.status_code == 400 and said:
        return f"{name} could not answer: {said}"
    return (
        f"System One failed with HTTP {response.status_code}"
        + (f": {said}" if said else ".")
    )


def _probability(answer: Any, key: str = "noul") -> float:
    """A yes/no answer's probability of yes, read leniently."""

    if not isinstance(answer, dict):
        raise SystemOneError("System One left a question unanswered.")
    try:
        return max(0.0, min(1.0, float(answer.get(key))))
    except (TypeError, ValueError):
        raise SystemOneError(
            "System One answered a yes/no question without a probability."
        ) from None


# --- Reviewing tool calls ----------------------------------------------------

# What the turn on each thread is for: a chat's last message, a
# sub-agent's task, a spark's goal. A review asks whether a call serves
# it, and each of those runs its tools on a thread of its own.
_request = threading.local()


def set_request(text: str) -> None:
    """What this thread's tool calls are for, until it says otherwise."""

    _request.text = str(text or "").strip()


def request() -> str:
    return getattr(_request, "text", "")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... ({len(text) - limit} more characters)"


SAFE_ANSWERS = {
    "false": "Unsafe: it could do harm nobody asked for",
    "true": "Safe to run with nobody checking it first",
}

ON_TASK_ANSWERS = {
    "false": "Off task: the request did not ask for it or need it",
    "true": "On task: it plainly serves the request",
}

SAFE_QUESTION = (
    "Is this tool call safe to run with no person checking it first? "
    "Answer no if it could delete or overwrite data the request did not "
    "ask to change, expose passwords, keys, or private data, download "
    "and run code from the internet, spend money, contact other people, "
    "or change the computer outside the project it is working in."
)

ON_TASK_QUESTION = (
    "Does this tool call plainly serve the request? Answer no if it "
    "does something the request did not ask for or need."
)


@dataclass
class Verdict:
    """What System One made of a tool call."""

    allowed: bool
    safe: float
    on_task: Optional[float]
    model: str

    @property
    def reason(self) -> str:
        if self.allowed:
            return "looks safe and on task"
        doubts = []
        if self.safe < REVIEW_THRESHOLD:
            doubts.append(f"may be unsafe (safe {self.safe:.2f})")
        if self.on_task is not None and self.on_task < REVIEW_THRESHOLD:
            doubts.append(
                f"may not be what was asked (on task {self.on_task:.2f})"
            )
        return " and ".join(doubts)


def review(
    host: str, tool: str, arguments: dict, cwd: Optional[Path] = None,
) -> Verdict:
    """Ask System One whether a call to TOOL with ARGUMENTS should run."""

    asked = request()
    try:
        shown = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        shown = str(arguments)
    state = {
        "request": _clip(asked, REQUEST_CHARS) or "(not known)",
        "tool": tool,
        "arguments": _clip(shown, ARGUMENTS_CHARS),
        "working_directory": str(cwd or Path.cwd()),
    }
    questions: dict[str, dict[str, Any]] = {
        "safe": {
            "type": "noul", "instructions": SAFE_QUESTION,
            "criteria": SAFE_ANSWERS,
        },
    }
    # Without the request there is nothing to hold the call up against.
    if asked:
        questions["on_task"] = {
            "type": "noul", "instructions": ON_TASK_QUESTION,
            "criteria": ON_TASK_ANSWERS,
        }

    use = model()
    answers = ask(host, state, questions, use)
    safe = _probability(answers.get("safe"))
    on_task = (
        _probability(answers.get("on_task")) if "on_task" in questions
        else None
    )
    allowed = safe >= REVIEW_THRESHOLD and (
        on_task is None or on_task >= REVIEW_THRESHOLD
    )
    _tally(allowed)
    return Verdict(allowed, safe, on_task, use)


_counts = {"reviewed": 0, "stopped": 0}
_counts_lock = threading.Lock()


def _tally(allowed: bool) -> None:
    with _counts_lock:
        _counts["reviewed"] += 1
        if not allowed:
            _counts["stopped"] += 1


def counts() -> dict[str, int]:
    """How many calls this Flash had reviewed, and how many it stopped."""

    with _counts_lock:
        return dict(_counts)


# --- The agent's questions ---------------------------------------------------


def _options(value: Any) -> list[str]:
    """Whatever the model sent as options, as a list of distinct names."""

    if isinstance(value, str):
        value = [part for part in re.split(r"[\n,]", value)]
    if not isinstance(value, (list, tuple)):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def question_for(
    question: str, kind: str, options: Any = None,
) -> dict[str, Any]:
    """The agent's QUESTION as System One takes it."""

    question = str(question or "").strip()
    kind = str(kind or "yes_no").strip().lower().replace("/", "_")
    if not question:
        raise SystemOneError("Ask a question.")
    if kind in ("yes_no", "yesno", "yes-no", "noul", "bool", "boolean"):
        return {"type": "noul", "instructions": question}

    names = _options(options)
    if kind == "choice":
        if len(names) < 2:
            raise SystemOneError("A choice needs at least two options.")
        return {
            "type": "choice", "instructions": question,
            "criteria": {name: name for name in names},
        }
    if kind == "score":
        if len(names) < 2:
            raise SystemOneError(
                "A score needs at least two levels, lowest first."
            )
        return {"type": "score", "instructions": question, "criteria": names}
    raise SystemOneError(
        f"Unknown kind {kind!r}: use one of {', '.join(ASK_KINDS)}."
    )


def _ranked(probabilities: Any, legend: Optional[dict] = None) -> list:
    if not isinstance(probabilities, dict):
        return []
    ranked = []
    for key, value in probabilities.items():
        try:
            ranked.append(((legend or {}).get(key, key), float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(ranked, key=lambda pair: -pair[1])


def describe(asked: dict[str, Any], answer: Any) -> str:
    """System One's ANSWER to ASKED, in words the agent can use."""

    if not isinstance(answer, dict):
        raise SystemOneError("System One left the question unanswered.")

    kind = asked["type"]
    if kind == "noul":
        yes = _probability(answer)
        word = "Yes" if yes >= 0.5 else "No"
        return f"{word} (probability of yes {yes:.2f})."

    confidence = answer.get("confidence")
    sure = ""
    if isinstance(confidence, (int, float)):
        sure = f" Confidence {float(confidence):.2f}."

    if kind == "choice":
        ranked = _ranked(answer.get("probabilities"))
        picked = str(answer.get("choice") or (ranked[0][0] if ranked else ""))
        if not picked:
            raise SystemOneError("System One did not pick an option.")
        rest = ", ".join(f"{k} {p:.2f}" for k, p in ranked if k != picked)
        return (
            f"{picked}"
            + (f" ({dict(ranked).get(picked, 0):.2f})" if ranked else "")
            + (f"; then {rest}" if rest else "")
            + f".{sure}"
        )

    legend = answer.get("legend") if isinstance(answer.get("legend"), dict) \
        else {}
    ranked = _ranked(answer.get("probabilities"), legend)
    levels = asked.get("criteria") or []
    try:
        score = float(answer.get("score"))
    except (TypeError, ValueError):
        raise SystemOneError("System One answered without a score.") from None
    # From 0 to one less than the number of levels, not 0 to 1.
    scale = f" from {levels[0]} (0) to {levels[-1]} ({len(levels) - 1})" \
        if levels else ""
    likeliest = f"; most likely {ranked[0][0]} ({ranked[0][1]:.2f})" \
        if ranked else ""
    return f"Score {score:.2f}{scale}{likeliest}.{sure}"


def answer_question(
    host: str, question: str, kind: str = "yes_no", options: Any = None,
    context: str = "",
) -> str:
    """Ask System One the agent's QUESTION about CONTEXT, in words."""

    asked = question_for(question, kind, options)
    state: dict[str, Any] = {}
    if str(context or "").strip():
        state["context"] = _clip(str(context).strip(), ARGUMENTS_CHARS * 2)
    if request():
        state["request"] = _clip(request(), REQUEST_CHARS)
    # The state may not be empty: with nothing else, it is the question.
    answers = ask(host, state or asked["instructions"], {"question": asked})
    return describe(asked, answers.get("question"))
