"""Tests for System One models served by extensions, and their typed API."""

# pylint: disable=C0115,C0116

import io
import json

import httpx
import pytest

from flash import ai, extensions, systemone, web
from flash import systemone_api as api
from flash.extensions import ExtensionError
from tests.test_extensions import install, make

YES_NO = {"type": "noul", "instructions": "Safe?",
          "criteria": {"true": "Safe", "false": "Unsafe"}}
CHOICE = {"type": "choice", "instructions": "Which?",
          "criteria": {"red": "Red", "blue": "Blue"}}
SCORE = {"type": "score", "instructions": "How much?",
         "criteria": ["none", "some", "lots"]}

# A model written with the typed API, as an extension would.
GUARD = '''\
from flash.systemone_api import Model, choice, score, serve, yes_no


class Guard(Model):
    name = "guard"

    def yes_no(self, state, question):
        return yes_no(0.05 if "rm -rf" in str(state) else 0.9)

    def choice(self, state, question):
        return choice(list(question["criteria"])[-1], confidence=0.7)

    def score(self, state, question):
        return score(len(question["criteria"]) - 1)


serve(Guard())
'''

# One that writes whatever WRONG.json holds, for answers that are bad.
WRONG = '''\
import json, os, sys
sys.stdin.read()
here = os.environ["FLASH_EXTENSION_DIR"]
print(open(os.path.join(here, "wrong.json"), encoding="utf-8").read())
'''


def served(tmp_path, wrong=None):
    files = {"guard.py": GUARD, "wrong.py": WRONG,
             "wrong.json": json.dumps(wrong or {})}
    return install(tmp_path, make(tmp_path / "s1", {
        "name": "judges",
        "system_one": [
            {"name": "guard", "description": "Rules first",
             "run": ["python", "./guard.py"], "timeout": 30},
            {"name": "wrong", "run": ["python", "./wrong.py"]},
        ],
    }, files))


class TestTypes:
    def test_questions_are_checked_by_type(self):
        assert api.check_questions(
            {"a": YES_NO, "b": CHOICE, "c": SCORE}) == {
            "a": YES_NO, "b": CHOICE, "c": SCORE}

    @pytest.mark.parametrize("question, why", [
        ({"type": "maybe", "instructions": "?"}, "type has to be"),
        ({"type": "noul"}, "instructions"),
        ({"type": "noul", "instructions": "?", "criteria": {"yes": "y"}},
         '"true" and "false"'),
        ({"type": "choice", "instructions": "?", "criteria": {"a": "A"}},
         "two options"),
        ({"type": "score", "instructions": "?", "criteria": ["only"]},
         "two or more levels"),
        ({"type": "score", "instructions": "?", "criteria": ["a", "a"]},
         "listed twice"),
        ({**YES_NO, "colour": "red"}, "should not: colour"),
    ])
    def test_a_bad_question_says_what_is_wrong(self, question, why):
        with pytest.raises(api.ProtocolError, match=why):
            api.check_question(question)

    @pytest.mark.parametrize("question, answer, why", [
        (YES_NO, {"noul": 1.4}, "from 0 to 1"),
        (YES_NO, {"noul": "high"}, "has to be a number"),
        (YES_NO, {"noul": True}, "has to be a number"),
        (YES_NO, {}, "no noul"),
        (CHOICE, {"choice": "green"}, "not one of the options"),
        (CHOICE, {"choice": "red", "probabilities": {"pink": 0.2}},
         "not offered: pink"),
        (CHOICE, {"choice": "red", "confidence": -1}, "from 0 to 1"),
        (SCORE, {"score": 3}, "from 0 to 2"),
        (SCORE, {"score": float("nan")}, "has to be a number"),
        (SCORE, {"score": 1, "legend": {"0": 1}}, "names to text"),
        (SCORE, {"score": 1, "why": "because"}, "should not: why"),
    ])
    def test_a_bad_answer_says_what_is_wrong(self, question, answer, why):
        with pytest.raises(api.ProtocolError, match=why):
            api.check_answer(question, answer)

    def test_every_question_is_answered_and_no_other(self):
        asked = {"a": YES_NO, "b": SCORE}

        with pytest.raises(api.ProtocolError, match="no answer to 'b'"):
            api.check_answers(asked, {"a": {"noul": 0.5}})
        with pytest.raises(api.ProtocolError, match="not asked: 'c'"):
            api.check_answers(asked, {"a": {"noul": 0.5}, "b": {"score": 1},
                                      "c": {"noul": 1}})
        assert api.check_answers(asked, {
            "a": {"noul": 1}, "b": {"score": 2, "confidence": 0.5}}) == {
            "a": {"noul": 1.0}, "b": {"score": 2.0, "confidence": 0.5}}

    def test_answers_are_built_checked(self):
        assert api.yes_no(0.25) == {"noul": 0.25}
        assert api.choice("red", {"red": 0.8, "blue": 0.2}, 0.9) == {
            "choice": "red", "probabilities": {"red": 0.8, "blue": 0.2},
            "confidence": 0.9}
        with pytest.raises(api.ProtocolError):
            api.yes_no(2)
        with pytest.raises(api.ProtocolError):
            api.score(1, confidence=7)


class TestServing:
    class Half(api.Model):
        name = "half"

        def yes_no(self, state, question):
            return api.yes_no(0.5)

    def reply(self, request):
        out = io.StringIO()
        failed = api.serve(self.Half(), stdin=io.StringIO(
            json.dumps(request)), stdout=out)
        return failed, json.loads(out.getvalue())

    def test_a_request_is_answered(self):
        assert self.reply({"model": "half", "state": "x",
                           "questions": {"q": YES_NO}}) == (
            0, {"answers": {"q": {"noul": 0.5}}})

    def test_what_it_cannot_answer_is_an_error(self):
        failed, said = self.reply({"model": "half", "state": "x",
                                   "questions": {"q": CHOICE}})
        assert failed == 1
        assert said == {"error": "half does not answer choices"}

        failed, said = self.reply({"model": "other", "state": "x",
                                   "questions": {"q": YES_NO}})
        assert failed == 1 and "no model called 'other'" in said["error"]


class TestManifest:
    def test_models_are_listed_by_the_name_they_are_picked_by(
            self, tmp_path):
        found = served(tmp_path)

        assert "System One models: @judges/guard, @judges/wrong" in \
            found.contents()
        assert systemone.extension_models() == [
            "@judges/guard", "@judges/wrong"]
        assert found.system_one[0].timeout == 30

    def test_a_bad_entry_is_refused(self, tmp_path):
        make(tmp_path, {"name": "x", "system_one": [
            {"name": "Bad Name", "run": ["x"]}]})
        with pytest.raises(ExtensionError, match="not a model name"):
            extensions.load(tmp_path)
        make(tmp_path, {"name": "x", "system_one": [
            {"name": "a", "run": ["x"]}, {"name": "a", "run": ["y"]}]})
        with pytest.raises(ExtensionError, match="declared twice"):
            extensions.load(tmp_path)

    def test_a_manifest_with_only_models_declares_something(self, tmp_path):
        make(tmp_path, {"name": "x", "system_one": [
            {"name": "a", "run": ["x"]}]})
        assert extensions.load(tmp_path).system_one


class TestAsking:
    @pytest.fixture(autouse=True)
    def no_ollama(self, monkeypatch):
        """No Ollama to reach, and every try at it kept: asking an
        extension's model should not make one."""

        tried = []

        def refuse(url, *_a, **_k):
            tried.append(url)
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(systemone, "_get", refuse)
        monkeypatch.setattr(systemone, "_post", refuse)
        return tried

    def test_the_model_answers_every_kind_with_no_ollama(
            self, tmp_path, no_ollama):
        served(tmp_path)

        answers = systemone.ask(
            "localhost", {"command": "rm -rf /"},
            {"safe": YES_NO, "pick": CHOICE, "how": SCORE},
            name="@judges/guard")

        assert answers == {"safe": {"noul": 0.05},
                           "pick": {"choice": "blue", "confidence": 0.7},
                           "how": {"score": 2.0}}
        assert no_ollama == []

    def test_a_review_goes_to_the_picked_model(self, tmp_path, monkeypatch):
        served(tmp_path)
        assert systemone.choose("localhost", "@Judges/guard") == \
            "@judges/guard"
        assert systemone.enabled()
        systemone.set_request("tidy up")

        verdict = systemone.review("localhost", "shell",
                                   {"command": "rm -rf ~"})

        assert not verdict.allowed and verdict.model == "@judges/guard"
        assert "may be unsafe" in verdict.reason
        assert systemone.review(
            "localhost", "shell", {"command": "ls"}).allowed

    @pytest.mark.parametrize("wrong, why", [
        ({"answers": {"safe": {"noul": 3}}}, "from 0 to 1"),
        ({"answers": {}}, "no answer to 'safe'"),
        ({"answers": {"safe": {"choice": "yes"}}}, "should not: choice"),
        ({"nothing": True}, "without answers"),
        ({"error": "out of tokens"}, "could not answer: out of tokens"),
    ])
    def test_a_wrong_answer_is_refused_and_says_why(self, tmp_path, wrong,
                                                    why):
        served(tmp_path, wrong)

        with pytest.raises(systemone.SystemOneError, match=why):
            systemone.ask("localhost", "x", {"safe": YES_NO},
                          name="@judges/wrong")

    def test_a_question_flash_would_send_wrongly_is_not_sent(self,
                                                             tmp_path):
        served(tmp_path)

        with pytest.raises(systemone.SystemOneError,
                           match="Not a question System One takes"):
            systemone.ask("localhost", "x", {"q": {"type": "noul"}},
                          name="@judges/guard")

    def test_a_model_no_longer_installed_is_said(self, tmp_path):
        with pytest.raises(systemone.SystemOneError,
                           match="not a System One model of any"):
            systemone.choose("localhost", "@gone/guard")
        assert not systemone.enabled()

    def test_turning_on_with_no_ollama_takes_an_extension_s(self,
                                                            tmp_path):
        served(tmp_path)

        assert systemone.turn_on("localhost") == "@judges/guard"
        assert systemone.capable("localhost", "@judges/guard") is True
        assert systemone.capable("localhost", "@judges/gone") is None

    def test_the_web_ui_lists_them_with_no_ollama(self, tmp_path):
        served(tmp_path)

        state = web.system_one_state(ai, models=True)

        assert state["models"] == ["@judges/guard", "@judges/wrong"]
        assert state["judge_only"] == state["models"]
        assert state["problem"] == ""


def test_ollama_and_extension_models_are_listed_together(tmp_path,
                                                         monkeypatch):
    from tests.test_systemone import FakeServer

    fake = FakeServer()
    monkeypatch.setattr(systemone, "_get", fake.get)
    monkeypatch.setattr(systemone, "_post", fake.post)
    served(tmp_path)

    assert systemone.available("localhost") == [
        "@judges/guard", "@judges/wrong", "Natuworkguy/tev1:0.8b",
        "nimble:latest"]


BADLY_TYPED = """\
from flash.systemone_api import ChoiceAnswer, ChoiceQuestion, Model


class Bad(Model):
    name = "bad"

    def choice(self, state: object,
               question: ChoiceQuestion) -> ChoiceAnswer:
        return {"pick": question["criteria"]}
"""


def test_a_type_checker_holds_a_model_to_the_types(tmp_path, monkeypatch):
    from pathlib import Path

    mypy = pytest.importorskip("mypy.api")
    module = Path(api.__file__)
    # Where flash is found, whatever another test left as the folder.
    monkeypatch.chdir(module.parent.parent)
    bad = tmp_path / "bad.py"
    bad.write_text(BADLY_TYPED, encoding="utf-8")

    said, _, failed = mypy.run([
        "--strict", "--no-incremental", "--ignore-missing-imports",
        "--follow-imports=skip", str(module)])
    assert failed == 0, said

    said, _, failed = mypy.run([
        "--no-incremental", "--follow-imports=silent", str(bad)])
    assert failed and 'Extra key "pick"' in said
