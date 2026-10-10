"""Tests for progress emails: a request's milestones, sent to your email."""

import json
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, mail, progress, web
from flash import progress_mail as pm


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
def setup(tmp_path, monkeypatch):
    FakeClient.scripts = []
    monkeypatch.setattr(ollama, "Client", FakeClient)
    monkeypatch.setattr(ai.Config, "model", "flash-test")
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
    monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)
    # Settings saved by a test are put back after it.
    for name in (pm.SETTING, pm.TO_SETTING, pm.FROM_SETTING,
                 pm.KINDS_SETTING):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv(pm.DRY_RUN, str(tmp_path / "outbox.jsonl"))
    mail.save("me@example.com", "app-pass", "imap.example.com",
              "smtp.example.com")
    yield
    progress.end(False)


def outbox(tmp_path, count=0, timeout=10):
    path = tmp_path / "outbox.jsonl"
    deadline = time.monotonic() + timeout
    while True:
        lines = path.read_text().splitlines() if path.exists() else []
        if len(lines) >= count or time.monotonic() > deadline:
            return [json.loads(line) for line in lines]
        time.sleep(0.05)


TURN = {"turn": "abc123", "source": "web", "chat": "c1",
        "title": "fix the login tests", "request": "fix the login tests"}


def event(name, **more):
    return {"event": name, **TURN, "at": 0, **more}


class TestWords:
    def test_each_milestone_has_a_short_subject(self):
        cases = [
            event("turn-start"),
            event("plan", change="set", done=0, total=2, index=0,
                  steps=[{"text": "Read the tests", "status": "active"},
                         {"text": "Fix them", "status": "todo"}]),
            event("plan", change="done", done=1, total=2, index=1,
                  steps=[{"text": "Read the tests", "status": "done"},
                         {"text": "Fix them", "status": "active"}]),
            event("ask", question="Run this command?\nrm -rf build"),
            event("turn-end", ok=True, summary="All 14 pass.", seconds=252),
            event("turn-end", ok=False, error="model went away", seconds=3),
        ]
        said = [pm.words(c) for c in cases]
        assert [s[0] for s in said] == [
            "▶ Flash started · fix the login tests",
            "☐ Plan, 2 steps · Read the tests",
            "☑ Step 1/2 · Read the tests",
            "❓ Flash needs you · Run this command?",
            "✓ Flash finished · fix the login tests (4m 12s)",
            "✗ Flash stopped · fix the login tests",
        ]
        assert "rm -rf build" in said[3][1]
        assert said[4][1].startswith("All 14 pass.")
        assert "model went away" in said[5][1]
        assert all("—" not in text for pair in said for text in pair)

    def test_a_long_request_is_cut(self):
        assert len(pm.words(event("turn-start", request="x" * 200))[0]) < 80


class TestSending:
    def test_off_until_turned_on(self, tmp_path):
        pm.handle(event("turn-start"))
        assert outbox(tmp_path) == []
        assert not pm.wants("turn-start")

    def test_to_your_own_address_in_one_thread(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pm.SETTING, "1")
        pm.handle(event("turn-start"))
        pm.handle(event("turn-end", ok=True, summary="Done.", seconds=5))

        sent = outbox(tmp_path)
        assert [m["to"] for m in sent] == ["me@example.com"] * 2
        assert sent[0]["in_reply_to"] == ""
        assert sent[1]["in_reply_to"] == "<flash-turn-abc123@flash.local>"

    def test_only_the_kinds_picked(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pm.SETTING, "1")
        monkeypatch.setenv(pm.KINDS_SETTING, "ask,end")
        for e in (event("turn-start"), event("ask", question="ok?"),
                  event("turn-end", ok=True, seconds=1)):
            pm.handle(e)
        assert [m["subject"][0] for m in outbox(tmp_path)] == ["❓", "✓"]
        assert not pm.wants("turn-start") and pm.wants("ask")

    def test_another_address(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pm.SETTING, "1")
        monkeypatch.setenv(pm.TO_SETTING, "watch@icloud.com")
        pm.handle(event("turn-start"))
        assert outbox(tmp_path)[0]["to"] == "watch@icloud.com"

    def test_a_failure_is_noted_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.setenv(pm.SETTING, "1")
        monkeypatch.delenv(pm.DRY_RUN)

        def refused(message):
            raise mail.MailError("smtp.example.com refused the login.")

        monkeypatch.setattr(mail, "send", refused)
        pm.handle(event("turn-start"))
        assert "refused the login" in pm.last_problem()

        monkeypatch.setattr(mail, "send", lambda message: message["To"])
        pm.handle(event("turn-end", ok=True, seconds=1))
        assert pm.last_problem() == ""


class TestARealTurn:
    def test_a_web_request_is_emailed_as_it_goes(self, tmp_path,
                                                 monkeypatch):
        monkeypatch.setenv(pm.SETTING, "1")
        FakeClient.scripts = [
            [part(calls=[call("plan", steps=["Look", "Fix"])]),
             part(done=True)],
            [part(calls=[call("check_step", index=1)]), part(done=True)],
            [part("Fixed it.\nMore detail."), part(done=True)],
        ]
        session = web.Session()
        session.send(session.new_chat(), "fix the login tests")

        sent = outbox(tmp_path, 4)
        assert [m["subject"].split(" ·")[0] for m in sent] == [
            "▶ Flash started", "☐ Plan, 2 steps", "☑ Step 1/2",
            "✓ Flash finished",
        ]
        assert sent[-1]["body"].startswith("Fixed it.")
        assert len({m["in_reply_to"] for m in sent[1:]}) == 1


class TestTerminal:
    @pytest.fixture
    def said(self, monkeypatch):
        lines = []
        monkeypatch.setattr(ai.console, "print",
                            lambda *a, **k: lines.append(str(a[0])))
        monkeypatch.setattr(ai, "warn", lambda text: lines.append(text))
        return lines

    def test_on_to_only_and_test(self, said, tmp_path):
        ai._email_command("progress on")
        assert pm.settings()["on"]
        assert any("to me@example.com" in line for line in said)

        ai._email_command("progress to watch@icloud.com")
        ai._email_command("progress only ask, end")
        assert pm.settings()["kinds"] == ["ask", "end"]
        assert pm.recipient() == "watch@icloud.com"

        ai._email_command("progress test")
        assert outbox(tmp_path)[0]["to"] == "watch@icloud.com"

        ai._email_command("progress to me")
        assert pm.recipient() == "me@example.com"
        ai._email_command("progress off")
        assert not pm.settings()["on"]

    def test_the_status(self, said):
        ai._email_command("progress")
        text = "\n".join(said)
        assert "Progress emails are off" in text
        assert "start" in text and "Needs" not in text

    def test_nonsense_is_a_warning(self, said):
        ai._email_command("progress only lunch")
        ai._email_command("progress from who@x.com")
        assert any("Pick from" in line for line in said)
        assert any("not a connected account" in line for line in said)


class TestWeb:
    def test_the_settings_command(self, tmp_path):
        session = web.Session()

        def change(**body):
            return web.command(
                session, {"name": "progress-email", **body},
            )["progress_email"]

        shown = change()
        assert shown["on"] is False and shown["recipient"] == "me@example.com"
        shown = change(on=True, kinds=["end"], to="watch@icloud.com")
        assert (shown["on"], shown["kinds"], shown["recipient"]) == (
            True, ["end"], "watch@icloud.com",
        )
        assert change(test=True)["sent"] == "watch@icloud.com"
        assert change(to="")["recipient"] == "me@example.com"
        with pytest.raises(ValueError):
            change(to="not an address")
        with pytest.raises(ValueError):
            change(account="who@x.com")
