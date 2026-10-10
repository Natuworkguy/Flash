"""Tests for turn milestones sent to extensions ("events")."""

import json
import threading
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, extensions, plan, progress, theme, web
from flash.extensions import ExtensionError

RECORDER = """\
import json, os, sys
with open(os.environ["FLASH_TEST_EVENTS"], "a", encoding="utf-8") as out:
    out.write(json.dumps(json.load(sys.stdin)) + "\\n")
"""


def part(content="", calls=None, done=False):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, thinking="",
                                tool_calls=calls),
        done=done, eval_count=0, eval_duration=0,
    )


def call(name, **arguments):
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments)
    )


class FakeClient:
    scripts: list = []

    def __init__(self, host=None, **_):
        pass

    def chat(self, **kwargs):
        script = FakeClient.scripts.pop(0)
        return iter(script() if callable(script) else script)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeClient.scripts = []
    monkeypatch.setattr(ollama, "Client", FakeClient)
    monkeypatch.setattr(ai.Config, "model", "flash-test")
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
    monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)
    plan.clear()
    extensions.reload()
    yield
    progress.end(False)
    extensions.reload()


def install(tmp_path, events, files=None):
    folder = tmp_path / "src"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / extensions.MANIFEST).write_text(
        json.dumps({"name": "follow", "events": events}), encoding="utf-8",
    )
    for name, text in (files or {}).items():
        (folder / name).write_text(text, encoding="utf-8")
    checkout = extensions.fetch(f"path@{folder}")
    try:
        installed = extensions.install(checkout, f"path@{folder}")
    finally:
        extensions.discard(checkout)
    extensions.reload()
    return installed


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """An extension that writes down every event it is sent."""

    out = tmp_path / "events.jsonl"
    monkeypatch.setenv("FLASH_TEST_EVENTS", str(out))
    install(tmp_path, {"run": ["python", "./record.py"]},
            {"record.py": RECORDER})

    def seen(count, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            lines = out.read_text().splitlines() if out.exists() else []
            if len(lines) >= count or time.monotonic() > deadline:
                return [json.loads(line) for line in lines]
            time.sleep(0.05)

    return seen


class TestManifest:
    def test_events_are_listed(self, tmp_path):
        added = install(
            tmp_path, {"run": ["python", "./e.py"], "on": ["turn-end"]},
            {"e.py": "pass\n"},
        )
        assert added.events.on == ("turn-end",)
        assert "follows turns: turn-end" in added.contents()

    def test_every_event_by_default(self, tmp_path):
        added = install(tmp_path, {"run": ["python", "./e.py"]},
                        {"e.py": "pass\n"})
        assert added.events.on == progress.EVENTS

    @pytest.mark.parametrize("bad", [
        {"run": ["python", "./e.py"], "on": ["lunch"]},
        {"run": ["python", "./e.py"], "on": []},
        {"on": ["turn-end"]},
        "events.py",
    ])
    def test_bad_ones_are_refused(self, tmp_path, bad):
        with pytest.raises(ExtensionError):
            install(tmp_path, bad, {"e.py": "pass\n"})


class TestMilestones:
    def test_a_web_turn_is_followed(self, recorded):
        FakeClient.scripts = [
            [part(calls=[call("plan", steps=["Look", "Fix"])]),
             part(done=True)],
            [part(calls=[call("check_step", index=1)]), part(done=True)],
            [part("Fixed it.\nMore detail here."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()
        session.send(chat, "fix the login tests")

        seen = recorded(4)

        assert [e["event"] for e in seen] == [
            "turn-start", "plan", "plan", "turn-end",
        ]
        assert seen[0]["source"] == "web"
        assert seen[0]["request"] == "fix the login tests"
        assert len({e["turn"] for e in seen}) == 1
        assert (seen[1]["change"], seen[1]["total"]) == ("set", 2)
        assert (seen[2]["change"], seen[2]["index"], seen[2]["done"]) == (
            "done", 1, 1,
        )
        end = seen[3]
        assert end["ok"] is True and end["summary"] == "Fixed it."
        assert end["seconds"] >= 0

    def test_a_failed_web_turn_says_why(self, recorded):
        def down():
            raise ConnectionError("the model went away")

        # Fails as it is asked, as a model that has gone away does.
        FakeClient.scripts = [down]
        session = web.Session()
        session.send(session.new_chat(), "build it")
        seen = recorded(2)
        assert seen[-1]["event"] == "turn-end"
        assert seen[-1]["ok"] is False
        assert "the model went away" in seen[-1]["error"]

    def test_waiting_on_the_user(self, recorded):
        progress.begin("terminal", request="deploy")
        with theme.answer_from(lambda question: "y"):
            theme.remote_answer("Run this command?\nmake deploy")
        progress.end(True, summary="Deployed.")

        seen = recorded(3)
        assert [e["event"] for e in seen] == [
            "turn-start", "ask", "turn-end",
        ]
        assert seen[1]["question"].startswith("Run this command?")

    def test_a_terminal_error_is_the_reason(self, recorded, monkeypatch):
        monkeypatch.setattr(ai, "show_error", lambda text: None)
        progress.begin("terminal", request="hi")
        ai._print_backend_error("connection refused")
        progress.end(False)

        seen = recorded(2)
        assert seen[-1]["ok"] is False
        assert seen[-1]["error"] == "connection refused"

    def test_only_the_turn_on_its_own_thread(self, recorded):
        progress.begin("terminal", request="main work")

        def sub_agent():
            plan.set_steps(["elsewhere"])
            progress.ask("not the user's turn")

        worker = threading.Thread(target=sub_agent)
        worker.start()
        worker.join()
        progress.end(True, summary="Done.")

        recorded(2)
        time.sleep(0.3)
        seen = recorded(2)
        assert [e["event"] for e in seen] == ["turn-start", "turn-end"]

    def test_a_new_turn_ends_one_left_open(self, recorded):
        progress.begin("terminal", request="one")
        progress.begin("terminal", request="two")
        progress.end(True, summary="ok")

        seen = recorded(4)
        assert [e["event"] for e in seen] == [
            "turn-start", "turn-end", "turn-start", "turn-end",
        ]
        assert seen[1]["ok"] is False

    def test_nothing_without_a_subscriber(self, monkeypatch):
        sent = []
        monkeypatch.setattr(progress._queue, "put_nowait", sent.append)
        progress.begin("terminal", request="hi")
        progress.end(True)
        assert sent == []


class TestEnvironment:
    def test_a_python_extension_can_import_flash(self, tmp_path):
        added = install(tmp_path, {"run": ["python", "./e.py"]},
                        {"e.py": "pass\n"})
        env = extensions.environment(added)
        from pathlib import Path

        import flash

        here = str(Path(flash.__file__).resolve().parent.parent)
        assert env["PYTHONPATH"].split(__import__("os").pathsep)[0] == here
