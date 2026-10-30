"""Tests for code review: reading the change from git, the two passes,
and the report, in the terminal and the browser."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from flash import ai, review, tools


def git(folder, *args):
    subprocess.run(["git", *args], cwd=folder, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "calc.py").write_text(
        "def average(values):\n    return sum(values) / len(values)\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "start")
    return tmp_path


class Reviewer:
    """A model that answers the first pass with FINDINGS, and the second
    with VERDICTS, one per finding, in order."""

    def __init__(self, findings, verdicts):
        self.findings = findings
        self.verdicts = list(verdicts)
        self.asked = []

    def chat(self, **kwargs):
        self.asked.append(kwargs)
        system = kwargs["messages"][0]["content"]
        if system == review.PROPOSE_PROMPT:
            said = {"findings": self.findings}
        else:
            said = self.verdicts.pop(0)
        return SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(said)))


class TestTheChange:
    def test_uncommitted_with_new_files_and_line_numbers(self, repo):
        (repo / "calc.py").write_text(
            "def average(values):\n    total = sum(values)\n"
            "    return total / len(values)\n")
        (repo / "new.py").write_text("print('hi')\n")

        diff, what, skipped = review.diff_of("", repo)

        assert what == "your uncommitted changes"
        assert [name for name, _ in review.split_files(diff)] == [
            "calc.py", "new.py"]
        shown = review.numbered(dict(review.split_files(diff))["calc.py"])
        assert "     2 +    total = sum(values)" in shown
        assert "       -    return sum(values) / len(values)" in shown
        assert "     3 +    return total / len(values)" in shown
        assert skipped == []

    def test_a_branch_and_a_range(self, repo):
        git(repo, "checkout", "-q", "-b", "work")
        (repo / "calc.py").write_text("def average(values):\n    return 0\n")
        git(repo, "commit", "-qam", "zero")

        diff, what, _ = review.diff_of("main", repo)
        assert "return 0" in diff
        assert what == "what this branch has that main does not"
        assert "return 0" in review.diff_of("main..work", repo)[0]

    def test_not_a_repository_or_a_bad_name(self, tmp_path_factory, repo):
        plain = tmp_path_factory.mktemp("plain")
        with pytest.raises(review.ReviewError, match="not in a git"):
            review.diff_of("", plain)
        with pytest.raises(review.ReviewError):
            review.diff_of("no-such-branch", repo)
        with pytest.raises(review.ReviewError, match="Name a branch"):
            review.diff_of("--output=x", repo)


class TestTwoPasses:
    def change(self, repo):
        (repo / "calc.py").write_text(
            "def average(values):\n    return sum(values) / len(values)\n\n"
            "def ratio(a, b):\n    return a / b\n")

    def test_only_what_holds_up_is_reported(self, repo):
        self.change(repo)
        model = Reviewer(
            findings=[
                {"file": "calc.py", "line": 5, "title": "Divides by zero",
                 "why": "ratio(1, 0) raises ZeroDivisionError."},
                {"file": "calc.py", "line": 4, "title": "Wrong name",
                 "why": "ratio is misnamed."},
                {"file": "elsewhere.py", "line": 1, "title": "Not in it",
                 "why": "x"},
            ],
            verdicts=[
                {"real": True, "confidence": 0.9, "reason": "b can be 0."},
                {"real": False, "confidence": 0.8, "reason": "A name."},
            ],
        )

        found = review.review(model, "m", "", repo)

        assert found.files == ["calc.py"]
        assert found.candidates == 2
        assert [(f.line, f.title) for f in found.findings] == [
            (5, "Divides by zero")]
        assert found.findings[0].confidence == 0.9
        # The second pass saw the file around the line, numbered.
        checked = model.asked[1]["messages"][1]["content"]
        assert "     5      return a / b" in checked
        assert model.asked[0]["format"] == "json"

        said = review.report(found)
        assert "calc.py:5 Divides by zero (90% sure)" in said
        assert "1 more were looked into and explained away" in said

    def test_nothing_to_review_or_nothing_found(self, repo):
        assert review.report(review.review(Reviewer([], []), "m", "",
                                           repo)) == (
            "There is nothing to review in your uncommitted changes.")
        self.change(repo)
        said = review.report(review.review(Reviewer([], []), "m", "", repo))
        assert said.endswith("No bugs found.")

    def test_an_answer_that_is_not_json_finds_nothing(self, repo):
        self.change(repo)

        class Chatty:
            def chat(self, **kwargs):
                return {"message": {"content": "Looks fine to me!"}}

        assert review.review(Chatty(), "m", "", repo).findings == []

    def test_json_in_a_fence_or_with_words_around_it(self):
        assert review._json('```json\n{"a": 1}\n```') == {"a": 1}
        assert review._json('Sure: {"a": 2} hope that helps') == {"a": 2}
        assert review._json("nope") == {}


class TestWhereItRuns:
    def test_the_tool_reports_it(self, repo, monkeypatch):
        (repo / "calc.py").write_text("def average(values):\n    return 1\n")
        monkeypatch.chdir(repo)
        model = Reviewer(
            [{"file": "calc.py", "line": 2, "title": "Always 1",
              "why": "The average is not computed."}],
            [{"real": True, "confidence": 0.95, "reason": "It returns 1."}],
        )
        monkeypatch.setattr(ai, "_client", lambda: model)
        monkeypatch.setattr(tools, "MODEL_NAME", "m")

        said = tools.review_code()

        assert "calc.py:2 Always 1 (95% sure)" in said
        assert "review_code" in tools.FUNCTIONS

    def test_the_terminal_keeps_it_in_the_conversation(
        self, repo, monkeypatch
    ):
        (repo / "calc.py").write_text("def average(values):\n    return 1\n")
        monkeypatch.chdir(repo)
        monkeypatch.setattr(ai, "_client", lambda: Reviewer([], []))
        monkeypatch.setattr(tools, "MODEL_NAME", "m")
        monkeypatch.setattr(ai.Config, "model", "m")
        messages = []

        ai._review_command("", messages)

        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "No bugs found." in messages[1]["content"]

    def test_an_error_is_not_kept(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(tools, "MODEL_NAME", "m")
        monkeypatch.setattr(ai.Config, "model", "m")
        messages = []

        ai._review_command("", messages)

        assert messages == []

    def test_the_browser_runs_it_in_the_chats_project(
        self, repo, monkeypatch
    ):
        import time

        from flash import web, workspace

        (repo / "calc.py").write_text("def average(values):\n    return 1\n")
        model = Reviewer(
            [{"file": "calc.py", "line": 2, "title": "Always 1",
              "why": "The average is not computed."}],
            [{"real": True, "confidence": 0.95, "reason": "It returns 1."}],
        )
        monkeypatch.setattr(ai, "_client", lambda: model)
        monkeypatch.setattr(ai.Config, "model", "m")
        made = workspace.create_project("Calc", str(repo))
        session = web.Session()
        chat = session.new_chat(made.id)

        assert web.command(session, {"name": "review", "chat": chat.id}) \
            == {}
        deadline = time.monotonic() + 5
        time.sleep(0.05)
        while chat.busy:
            assert time.monotonic() < deadline, "it never finished"
            time.sleep(0.02)

        assert chat.messages[0]["content"] == (
            "Review my uncommitted changes for bugs.")
        assert "calc.py:2 Always 1" in chat.messages[1]["content"]
        shown = [e.get("label") for e in chat.log if e["type"] == "tool"]
        assert "Review(uncommitted changes)" in shown

        chat.busy = True
        with pytest.raises(ValueError, match="Wait for the reply"):
            session.review_chat(chat)
