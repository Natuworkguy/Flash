"""The typed API for System One models served by an extension.

System One puts small questions to a model: yes or no, which of these
options, or how much on a scale. An extension can answer them with a
model of its own, which the user then picks like one of Ollama's. Its
manifest names each model and the program that answers for it:

    "system_one": [
      {"name": "guard", "description": "Rules first, then a small model",
       "run": ["python", "./guard.py"], "timeout": 30}
    ]

and the model is picked as @<extension>/guard. The program is given
one request as JSON on stdin and writes one reply as JSON on stdout:

    {"model": "guard", "state": ..., "questions": {key: question}}
    {"answers": {key: answer}}

or {"error": "why"} when it cannot answer. Every question and answer
has a type here, and Flash checks both ways: each question against its
type before it is sent, and each answer against its question's type
when it comes back, so an answer that is malformed (a probability of
1.4, a choice that was not offered, a score off the scale, a question
left out) is refused with a clear reason instead of being misread as a
judgment about a command.

A program in Python can import this module (`python` in "run" is the
Python Flash runs on) and let serve() do the reading, checking and
writing:

    from flash.systemone_api import (
        ChoiceQuestion, Model, ScoreQuestion, YesNoQuestion, choice,
        score, serve, yes_no)

    class Guard(Model):
        name = "guard"

        def yes_no(self, state, question: YesNoQuestion):
            risky = "rm -rf" in str(state)
            return yes_no(0.05 if risky else 0.9)

        def choice(self, state, question: ChoiceQuestion):
            return choice(next(iter(question["criteria"])))

        def score(self, state, question: ScoreQuestion):
            return score(0)

    serve(Guard())

It uses nothing outside Python's standard library, so it can also be
copied next to a program that runs on another Python.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from typing import IO, Any, Literal, Optional, TypedDict, Union, cast

# --- Questions ---------------------------------------------------------------


class _YesNoQuestionBase(TypedDict):
    type: Literal["noul"]
    instructions: str


class YesNoQuestion(_YesNoQuestionBase, total=False):
    """Yes or no. CRITERIA, when given, says what each answer means:
    {"true": ..., "false": ...}."""

    criteria: dict[str, str]


class ChoiceQuestion(TypedDict):
    """One of the options, which are CRITERIA's keys; each value says
    what that option means."""

    type: Literal["choice"]
    instructions: str
    criteria: dict[str, str]


class ScoreQuestion(TypedDict):
    """How much, on the scale CRITERIA lists lowest first: a score of 0
    is its first level, len(criteria) - 1 its last."""

    type: Literal["score"]
    instructions: str
    criteria: list[str]


Question = Union[YesNoQuestion, ChoiceQuestion, ScoreQuestion]
QUESTION_TYPES = ("noul", "choice", "score")

# --- Answers -----------------------------------------------------------------


class YesNoAnswer(TypedDict):
    """The probability of yes, from 0 to 1."""

    noul: float


class _ChoiceAnswerBase(TypedDict):
    choice: str


class ChoiceAnswer(_ChoiceAnswerBase, total=False):
    """The option picked; maybe how likely each option was, and how
    sure the model is overall, each from 0 to 1."""

    probabilities: dict[str, float]
    confidence: float


class _ScoreAnswerBase(TypedDict):
    score: float


class ScoreAnswer(_ScoreAnswerBase, total=False):
    """Where on the scale, from 0 to len(criteria) - 1; maybe how likely
    each level was (keyed as LEGEND names them), and how sure."""

    probabilities: dict[str, float]
    legend: dict[str, str]
    confidence: float


Answer = Union[YesNoAnswer, ChoiceAnswer, ScoreAnswer]


class Request(TypedDict):
    """What a program is asked: MODEL, one of the names it serves."""

    model: str
    state: Any
    questions: dict[str, Question]


class Reply(TypedDict):
    answers: dict[str, Answer]


class ProtocolError(ValueError):
    """A question or an answer that is not what its type says."""


# Keys a question or answer may be sent with, by type: anything else
# is a typo the program would never hear about.
_QUESTION_KEYS = {"type", "instructions", "criteria"}
_ANSWER_KEYS = {
    "noul": {"noul"},
    "choice": {"choice", "probabilities", "confidence"},
    "score": {"score", "probabilities", "legend", "confidence"},
}

# --- Building answers --------------------------------------------------------


def yes_no(probability: float) -> YesNoAnswer:
    """A yes/no answer: PROBABILITY of yes, from 0 to 1."""

    return {"noul": _unit(probability, "the probability of yes")}


def choice(
    picked: str,
    probabilities: Optional[Mapping[str, float]] = None,
    confidence: Optional[float] = None,
) -> ChoiceAnswer:
    """A choice: PICKED, one of the question's options."""

    answer: ChoiceAnswer = {"choice": _text(picked, "the choice")}
    if probabilities is not None:
        answer["probabilities"] = _chances(probabilities, "probabilities")
    if confidence is not None:
        answer["confidence"] = _unit(confidence, "confidence")
    return answer


def score(
    value: float,
    probabilities: Optional[Mapping[str, float]] = None,
    legend: Optional[Mapping[str, str]] = None,
    confidence: Optional[float] = None,
) -> ScoreAnswer:
    """A score: VALUE, from 0 to one less than the number of levels."""

    answer: ScoreAnswer = {"score": _number(value, "the score")}
    if probabilities is not None:
        answer["probabilities"] = _chances(probabilities, "probabilities")
    if legend is not None:
        answer["legend"] = _names(legend, "legend")
    if confidence is not None:
        answer["confidence"] = _unit(confidence, "confidence")
    return answer


# --- Checking ----------------------------------------------------------------


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ProtocolError(f"{what} has to be a number, not {value!r}")
    return float(value)


def _unit(value: Any, what: str) -> float:
    found = _number(value, what)
    if not 0.0 <= found <= 1.0:
        raise ProtocolError(f"{what} has to be from 0 to 1, not {found}")
    return found


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{what} has to be text")
    return value


def _names(value: Any, what: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in value.items()):
        raise ProtocolError(f"{what} has to map names to text")
    return dict(value)


def _chances(value: Any, what: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{what} has to map names to numbers")
    found = {}
    for key, chance in value.items():
        if not isinstance(key, str):
            raise ProtocolError(f"{what} has to be keyed by name")
        found[key] = _unit(chance, f"{what}[{key!r}]")
    return found


def _extra(found: Mapping[Any, Any], allowed: set[str], what: str) -> None:
    unknown = sorted(str(k) for k in found if k not in allowed)
    if unknown:
        raise ProtocolError(
            f"{what} has keys it should not: {', '.join(unknown)}")


def check_question(value: Any, key: str = "question") -> Question:
    """VALUE as the question it is, or ProtocolError saying why not."""

    what = f"question {key!r}"
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{what} has to be an object")
    _extra(value, _QUESTION_KEYS, what)
    kind = value.get("type")
    if kind not in QUESTION_TYPES:
        raise ProtocolError(
            f"{what}: type has to be one of {', '.join(QUESTION_TYPES)}, "
            f"not {kind!r}")
    instructions = _text(value.get("instructions"), f"{what}: instructions")
    criteria = value.get("criteria")

    if kind == "noul":
        question: YesNoQuestion = {"type": "noul",
                                   "instructions": instructions}
        if criteria is not None:
            names = _names(criteria, f"{what}: criteria")
            if not set(names) <= {"true", "false"}:
                raise ProtocolError(
                    f'{what}: criteria are "true" and "false"')
            question["criteria"] = names
        return question

    if kind == "choice":
        names = _names(criteria, f"{what}: criteria")
        if len(names) < 2:
            raise ProtocolError(f"{what}: a choice needs two options")
        return {"type": "choice", "instructions": instructions,
                "criteria": names}

    if not isinstance(criteria, list) or len(criteria) < 2 or not all(
            isinstance(level, str) and level.strip() for level in criteria):
        raise ProtocolError(
            f"{what}: criteria has to list two or more levels, lowest "
            "first")
    if len(set(criteria)) != len(criteria):
        raise ProtocolError(f"{what}: a level is listed twice")
    return {"type": "score", "instructions": instructions,
            "criteria": list(criteria)}


def check_questions(value: Any) -> dict[str, Question]:
    """Every question in VALUE, checked, by key."""

    if not isinstance(value, Mapping) or not value:
        raise ProtocolError("questions has to be an object of questions")
    checked = {}
    for key, question in value.items():
        if not isinstance(key, str) or not key:
            raise ProtocolError("each question has to have a name")
        checked[key] = check_question(question, key)
    return checked


def check_answer(question: Question, value: Any,
                 key: str = "question") -> Answer:
    """VALUE as the answer to QUESTION, or ProtocolError saying why it
    is not one."""

    what = f"the answer to {key!r}"
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{what} has to be an object")
    kind = question["type"]
    _extra(value, _ANSWER_KEYS[kind], what)

    if kind == "noul":
        if "noul" not in value:
            raise ProtocolError(f"{what} has no noul, the probability of yes")
        return yes_no(_unit(value["noul"], f"{what}: noul"))

    if kind == "choice":
        options = cast(ChoiceQuestion, question)["criteria"]
        picked = value.get("choice")
        if picked not in options:
            raise ProtocolError(
                f"{what}: {picked!r} was not one of the options "
                f"({', '.join(options)})")
        chances = value.get("probabilities")
        if chances is not None:
            chances = _chances(chances, f"{what}: probabilities")
            stray = sorted(set(chances) - set(options))
            if stray:
                raise ProtocolError(
                    f"{what}: probabilities for options not offered: "
                    + ", ".join(stray))
        confidence = value.get("confidence")
        return choice(
            picked, chances,
            None if confidence is None
            else _unit(confidence, f"{what}: confidence"))

    levels = cast(ScoreQuestion, question)["criteria"]
    if "score" not in value:
        raise ProtocolError(f"{what} has no score")
    found = _number(value["score"], f"{what}: score")
    if not 0 <= found <= len(levels) - 1:
        raise ProtocolError(
            f"{what}: the score has to be from 0 to {len(levels) - 1}, "
            f"not {found}")
    chances = value.get("probabilities")
    if chances is not None:
        chances = _chances(chances, f"{what}: probabilities")
    legend = value.get("legend")
    if legend is not None:
        legend = _names(legend, f"{what}: legend")
    confidence = value.get("confidence")
    return score(
        found, chances, legend,
        None if confidence is None
        else _unit(confidence, f"{what}: confidence"))


def check_answers(questions: Mapping[str, Question],
                  value: Any) -> dict[str, Answer]:
    """VALUE as the answers to QUESTIONS: one to each, and no others."""

    if not isinstance(value, Mapping):
        raise ProtocolError("answers has to be an object of answers")
    missing = sorted(set(questions) - set(value))
    if missing:
        raise ProtocolError("no answer to " + ", ".join(
            repr(k) for k in missing))
    unasked = sorted(str(k) for k in set(value) - set(questions))
    if unasked:
        raise ProtocolError("answers to questions not asked: " + ", ".join(
            repr(k) for k in unasked))
    return {key: check_answer(question, value[key], key)
            for key, question in questions.items()}


def check_request(value: Any) -> Request:
    """VALUE as a request a program is given."""

    if not isinstance(value, Mapping):
        raise ProtocolError("the request has to be an object")
    if "state" not in value:
        raise ProtocolError("the request has no state")
    return {"model": _text(value.get("model"), "model"),
            "state": value["state"],
            "questions": check_questions(value.get("questions"))}


# --- Serving -----------------------------------------------------------------


class Model:
    """A System One model: NAME, as the manifest has it, answering each
    kind of question. A model that takes all the questions together,
    to answer them in one go, overrides answer() instead."""

    name: str = ""

    def yes_no(self, state: Any, question: YesNoQuestion) -> YesNoAnswer:
        raise ProtocolError(f"{self.name} does not answer yes/no questions")

    def choice(self, state: Any, question: ChoiceQuestion) -> ChoiceAnswer:
        raise ProtocolError(f"{self.name} does not answer choices")

    def score(self, state: Any, question: ScoreQuestion) -> ScoreAnswer:
        raise ProtocolError(f"{self.name} does not answer scores")

    def answer(self, state: Any,
               questions: dict[str, Question]) -> dict[str, Answer]:
        answers: dict[str, Answer] = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = self.yes_no(state, question)
            elif question["type"] == "choice":
                answers[key] = self.choice(state, question)
            else:
                answers[key] = self.score(state, question)
        return answers


def handle(models: Mapping[str, Model],
           request: Any) -> Union[Reply, dict[str, str]]:
    """The reply to REQUEST from whichever of MODELS it names: checked
    answers, or {"error": why}."""

    try:
        asked = check_request(request)
        model = models.get(asked["model"])
        if model is None:
            raise ProtocolError(
                f"no model called {asked['model']!r} here; there is "
                + (", ".join(models) or "none"))
        answers = model.answer(asked["state"], asked["questions"])
        return {"answers": check_answers(asked["questions"], answers)}
    except ProtocolError as exc:
        return {"error": str(exc)}


def serve(*models: Model, stdin: Optional[IO[str]] = None,
          stdout: Optional[IO[str]] = None) -> int:
    """Answer the one request on STDIN with MODELS, on STDOUT. Exits
    with 1 when it could not, having said why."""

    by_name = {}
    for model in models:
        if not model.name:
            raise ValueError(f"{type(model).__name__} has no name")
        by_name[model.name] = model
    given = stdin or sys.stdin
    out = stdout or sys.stdout
    try:
        request = json.loads(given.read())
    except ValueError as exc:
        reply: dict[str, Any] = {"error": f"the request is not JSON: {exc}"}
    else:
        reply = dict(handle(by_name, request))
    out.write(json.dumps(reply))
    out.flush()
    failed = 1 if "error" in reply else 0
    if stdin is None and stdout is None:
        sys.exit(failed)
    return failed
