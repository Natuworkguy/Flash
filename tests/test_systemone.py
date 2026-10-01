# pylint: disable=C0114,C0115,C0116

import json
import subprocess  # nosec B404

import httpx
import pytest

from flash import ai, systemone, tools, web


class FakeServer:
    """An Ollama that answers /api/version and /v1/systemone."""

    def __init__(self, version="0.35.0"):
        self.version = version
        self.posts = []
        self.answers = {}
        self.status = 200
        self.error = ""
        self.down = False

    def get(self, url, timeout):
        if self.down:
            raise httpx.ConnectError("refused")
        assert url.endswith("/api/version")  # nosec B101
        return httpx.Response(
            200, json={"version": self.version},
            request=httpx.Request("GET", url),
        )

    def post(self, url, body, timeout):
        self.posts.append((url, body))
        if self.status != 200:
            return httpx.Response(
                self.status, json={"error": self.error},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200, json={"model": body["model"], "answers": self.answers},
            request=httpx.Request("POST", url),
        )


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(systemone, "_get", fake.get)
    monkeypatch.setattr(systemone, "_post", fake.post)
    return fake


@pytest.fixture
def at_work(monkeypatch, server):
    """System One switched on, in autonomous mode."""

    monkeypatch.setenv("SYSTEM_ONE", "1")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")
    return server


# --- Versions ----------------------------------------------------------------


@pytest.mark.parametrize("version, ok", [
    ("0.35.0", True),
    ("0.35.0-rc1", True),
    ("0.36.2", True),
    ("1.0.0", True),
    ("0.34.9", False),
    ("0.12.6", False),
    ("0.0.0", True),
])
def test_supports(version, ok):
    assert systemone.supports(version) is ok  # nosec B101


def test_parse_version():
    assert systemone.parse_version("v0.35.1") == (0, 35, 1)  # nosec B101
    assert systemone.parse_version("0.35") == (0, 35)  # nosec B101
    assert systemone.parse_version("nightly") is None  # nosec B101


def test_check_old_server_says_to_update(server):
    server.version = "0.34.2"

    with pytest.raises(systemone.TooOld) as raised:
        systemone.check("http://localhost:11434")

    message = str(raised.value)
    assert "v0.35" in message and "0.35+" in message  # nosec B101
    assert "v0.34.2" in message  # nosec B101
    assert raised.value.version == "0.34.2"  # nosec B101


def test_check_new_server_is_remembered(server):
    assert systemone.check("localhost:11434") == "0.35.0"  # nosec B101

    server.down = True
    # Asked once: a review does not ask the version every call.
    assert systemone.check("localhost:11434") == "0.35.0"  # nosec B101


def test_check_old_server_is_asked_again(server):
    server.version = "0.30.0"
    with pytest.raises(systemone.TooOld):
        systemone.check("localhost:11434")

    server.version = "0.35.1"
    assert systemone.check("localhost:11434") == "0.35.1"  # nosec B101


def test_check_unreachable(server):
    server.down = True

    with pytest.raises(systemone.SystemOneError) as raised:
        systemone.check("http://localhost:11434")

    assert "Could not reach" in str(raised.value)  # nosec B101
    assert not isinstance(raised.value, systemone.TooOld)  # nosec B101


# --- Asking ------------------------------------------------------------------


def test_ask_posts_to_the_endpoint(server, monkeypatch):
    monkeypatch.setenv("SYSTEM_ONE_MODEL", "tev1")
    server.answers = {"q": {"type": "noul", "noul": 0.9}}

    answers = systemone.ask(
        "http://localhost:11434", {"a": 1},
        {"q": {"type": "noul", "instructions": "?"}},
    )

    url, body = server.posts[0]
    assert url == "http://localhost:11434/v1/systemone"  # nosec B101
    assert body["model"] == "tev1"  # nosec B101
    assert body["state"] == {"a": 1}  # nosec B101
    assert answers["q"]["noul"] == 0.9  # nosec B101


def test_ask_missing_model(server):
    server.status = 404
    server.error = "model 'nimble' not found"

    with pytest.raises(systemone.SystemOneError) as raised:
        systemone.ask("localhost", {}, {})

    assert "ollama pull nimble" in str(raised.value)  # nosec B101


def test_ask_missing_endpoint(server):
    server.status = 404
    server.error = "404 page not found"

    with pytest.raises(systemone.SystemOneError) as raised:
        systemone.ask("localhost", {}, {})

    assert "v0.35" in str(raised.value)  # nosec B101


def test_question_for_kinds():
    assert systemone.question_for("Ok?", "yes_no") == {  # nosec B101
        "type": "noul", "instructions": "Ok?",
    }
    choice = systemone.question_for("Which?", "choice", ["a", "b", "a"])
    assert choice["criteria"] == {"a": "a", "b": "b"}  # nosec B101
    score = systemone.question_for("How bad?", "score", "low, high")
    assert score["criteria"] == ["low", "high"]  # nosec B101

    with pytest.raises(systemone.SystemOneError):
        systemone.question_for("Which?", "choice", ["only"])
    with pytest.raises(systemone.SystemOneError):
        systemone.question_for("", "yes_no")
    with pytest.raises(systemone.SystemOneError):
        systemone.question_for("Hm?", "essay")


def test_describe_answers():
    yes = systemone.describe({"type": "noul"}, {"noul": 0.97})
    assert yes.startswith("Yes")  # nosec B101

    choice = systemone.describe(
        {"type": "choice"},
        {"choice": "billing", "confidence": 0.9,
         "probabilities": {"billing": 0.98, "technical": 0.02}},
    )
    assert choice.startswith("billing (0.98)")  # nosec B101
    assert "technical 0.02" in choice  # nosec B101

    score = systemone.describe(
        {"type": "score", "criteria": ["Routine", "Soon", "Urgent"]},
        {"score": 0.8, "legend": {"0": "Routine", "1": "Soon", "2": "Urgent"},
         "probabilities": {"0": 0.1, "1": 0.6, "2": "0.3"}},
    )
    assert "Score 0.80" in score  # nosec B101
    assert "from Routine (0) to Urgent (1)" in score  # nosec B101
    assert "most likely Soon" in score  # nosec B101


# --- Settings ----------------------------------------------------------------


def test_active_needs_autonomous_mode(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setenv("SYSTEM_ONE", "1")
    assert systemone.enabled() and not systemone.active()  # nosec B101

    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    assert systemone.active()  # nosec B101


def test_settings_saved_to_the_env_file(isolated_home):
    systemone.set_enabled(True)
    systemone.set_model("tev1:0.8b")

    saved = (isolated_home / ".flash.env").read_text(encoding="utf-8")
    assert "SYSTEM_ONE=1" in saved  # nosec B101
    assert "SYSTEM_ONE_MODEL=tev1:0.8b" in saved  # nosec B101
    assert systemone.model() == "tev1:0.8b"  # nosec B101

    with pytest.raises(systemone.SystemOneError):
        systemone.set_model("two words")


def test_default_model():
    assert systemone.model() == "nimble"  # nosec B101


# --- Reviews -----------------------------------------------------------------


def test_review_asks_both_questions(server):
    server.answers = {
        "safe": {"type": "noul", "noul": 0.95},
        "on_task": {"type": "noul", "noul": 0.9},
    }
    systemone.set_request("List the files here")

    verdict = systemone.review("localhost", "shell", {"command": "ls"})

    _, body = server.posts[0]
    assert set(body["questions"]) == {"safe", "on_task"}  # nosec B101
    assert body["state"]["request"] == "List the files here"  # nosec B101
    assert json.loads(body["state"]["arguments"]) == {  # nosec B101
        "command": "ls",
    }
    assert verdict.allowed  # nosec B101


def test_review_without_a_request_only_asks_safety(server):
    server.answers = {"safe": {"type": "noul", "noul": 0.2}}
    systemone.set_request("")

    verdict = systemone.review("localhost", "shell", {"command": "rm -rf ~"})

    _, body = server.posts[0]
    assert set(body["questions"]) == {"safe"}  # nosec B101
    assert not verdict.allowed  # nosec B101
    assert "unsafe" in verdict.reason  # nosec B101


def test_review_off_task(server):
    server.answers = {
        "safe": {"type": "noul", "noul": 0.9},
        "on_task": {"type": "noul", "noul": 0.1},
    }
    systemone.set_request("Fix the typo in README")

    verdict = systemone.review("localhost", "shell", {"command": "git push"})

    assert not verdict.allowed  # nosec B101
    assert "not be what was asked" in verdict.reason  # nosec B101


def _no_shell(monkeypatch):
    ran = []

    def fake_run(*args, **kwargs):
        ran.append(args)
        return subprocess.CompletedProcess(args, 0, "done", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return ran


def test_run_tool_stops_a_call_system_one_rejects(at_work, monkeypatch):
    at_work.answers = {"safe": {"type": "noul", "noul": 0.05}}
    systemone.set_request("")
    ran = _no_shell(monkeypatch)

    result = tools.run_tool(("shell", {"command": "rm -rf /"}))

    assert ran == []  # nosec B101
    assert result.startswith("Not run: System One")  # nosec B101
    assert "nimble" in result  # nosec B101


def test_run_tool_runs_a_call_system_one_allows(at_work, monkeypatch):
    at_work.answers = {"safe": {"type": "noul", "noul": 0.99}}
    systemone.set_request("")
    ran = _no_shell(monkeypatch)

    result = tools.run_tool(("shell", {"command": "ls"}))

    assert len(ran) == 1  # nosec B101
    assert "done" in result  # nosec B101


def test_run_tool_fails_closed(at_work, monkeypatch):
    at_work.version = "0.20.0"
    ran = _no_shell(monkeypatch)

    result = tools.run_tool(("shell", {"command": "ls"}))

    assert ran == []  # nosec B101
    assert "could not" in result and "0.35" in result  # nosec B101


def test_run_tool_skips_review_outside_autonomous_mode(at_work, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    asked = []
    monkeypatch.setattr(
        systemone, "review", lambda *a, **k: asked.append(a),
    )
    monkeypatch.setattr(tools, "remote_answer", lambda question: "n")

    tools.run_tool(("shell", {"command": "ls"}))

    assert asked == []  # nosec B101


def test_run_tool_skips_review_when_off(at_work, monkeypatch):
    monkeypatch.setenv("SYSTEM_ONE", "0")
    _no_shell(monkeypatch)

    tools.run_tool(("shell", {"command": "ls"}))

    assert at_work.posts == []  # nosec B101


def test_run_tool_leaves_reading_tools_alone(at_work, tmp_path):
    tools.run_tool(("glob", {"pattern": "*", "path": str(tmp_path)}))

    assert at_work.posts == []  # nosec B101


# --- The agent's tool --------------------------------------------------------


def test_ask_tool_offered_only_at_work(monkeypatch, server):
    def names():
        return {t["function"]["name"] for t in tools.turn_tools()}

    assert "ask_system_one" not in names()  # nosec B101

    monkeypatch.setenv("SYSTEM_ONE", "1")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    assert "ask_system_one" in names()  # nosec B101


def test_ask_tool_answers(at_work):
    at_work.answers = {"question": {"type": "noul", "noul": 0.12}}
    systemone.set_request("Run the tests")

    result = tools.run_tool(("ask_system_one", {
        "question": "Did every test pass?",
        "context": "3 passed, 1 failed",
    }))

    _, body = at_work.posts[0]
    assert body["state"]["context"] == "3 passed, 1 failed"  # nosec B101
    assert body["state"]["request"] == "Run the tests"  # nosec B101
    assert result.startswith("System One (nimble): No")  # nosec B101


def test_ask_tool_when_off():
    result = tools.ask_system_one("Anything?")

    assert "System One is off" in result  # nosec B101


# --- Turning it on -----------------------------------------------------------


def test_cli_refuses_an_old_server(server, monkeypatch):
    server.version = "0.34.0"
    warned = []
    monkeypatch.setattr(ai, "warn", warned.append)

    ai._system_one_command("on")

    assert not systemone.enabled()  # nosec B101
    assert any("0.35+" in w for w in warned)  # nosec B101


def test_cli_turns_on(server, monkeypatch):
    monkeypatch.setattr(ai, "fetch_if_missing", lambda client, name: True)

    ai._system_one_command("on")

    assert systemone.enabled()  # nosec B101

    ai._system_one_command("off")
    assert not systemone.enabled()  # nosec B101


def test_cli_picks_a_model(server):
    ai._system_one_command("model tev1")

    assert systemone.model() == "tev1"  # nosec B101


def test_web_refuses_an_old_server(server):
    server.version = "0.33.1"

    with pytest.raises(ValueError, match="0.35"):
        web.command(web.Session(), {"name": "system-one", "arg": "on"})

    state = web.command(web.Session(), {"name": "system-one"})
    assert state["on"] is False  # nosec B101
    assert state["too_old"] is True  # nosec B101
    assert state["version"] == "0.33.1"  # nosec B101


def test_web_turns_on_and_picks_a_model(server, monkeypatch):
    monkeypatch.setattr(web, "is_installed", lambda client, name: True)

    state = web.command(web.Session(), {"name": "system-one", "arg": "on"})
    assert state["on"] is True and state["version"] == "0.35.0"  # nosec B101
    assert web.status(ai)["system_one"] is True  # nosec B101

    state = web.command(web.Session(), {
        "name": "system-one", "arg": "model", "model": "tev1",
    })
    assert state["model"] == "tev1"  # nosec B101


def test_web_wants_the_model_downloaded(server, monkeypatch):
    monkeypatch.setattr(web, "is_installed", lambda client, name: False)

    with pytest.raises(ValueError, match="ollama pull nimble"):
        web.command(web.Session(), {"name": "system-one", "arg": "on"})
