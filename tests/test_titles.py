"""Tests for chat titles the model writes."""

import threading
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, titles, web


def part(content="", done=False):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, thinking="", tool_calls=None),
        done=done, eval_count=0, eval_duration=0,
    )


class FakeClient:
    """Streams the chat's replies; answers a call that is not streamed,
    the title, with TITLE once GATE is open."""

    requests: list = []
    title = "Q3 revenue report"
    gate = threading.Event()

    def __init__(self, host=None, **_):
        pass

    def chat(self, **kwargs):
        FakeClient.requests.append(kwargs)
        if kwargs.get("stream") is False:
            FakeClient.gate.wait(5)
            return SimpleNamespace(
                message=SimpleNamespace(content=FakeClient.title),
            )
        return iter([part("Revenue was up 12%."), part(done=True)])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeClient.requests = []
    FakeClient.title = "Q3 revenue report"
    FakeClient.gate = threading.Event()
    FakeClient.gate.set()
    monkeypatch.setattr(ollama, "Client", FakeClient)
    monkeypatch.setattr(ai.Config, "model", "flash-test")
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
    monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)
    monkeypatch.setenv(titles.SETTING, "1")


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def first_turn(session, text="how did we do last quarter on revenue?"):
    chat = session.new_chat()
    session.send(chat, text)
    wait_for(lambda: not chat.busy and not chat.queued)
    return chat


@pytest.mark.parametrize("said, title", [
    ("Q3 revenue report", "Q3 revenue report"),
    ('"Q3 Revenue Report."', "Q3 Revenue Report"),
    ("Title: Trip to Lisbon", "Trip to Lisbon"),
    ("## **Fixing the login tests**\nHere you go", "Fixing the login tests"),
    ("\n\n  `Pack list`  ", "Pack list"),
    ("A title that goes on far too long to fit in the sidebar at all",
     "A title that goes on far too long to fit in the"),
    ("", ""),
])
def test_what_the_model_said_becomes_a_title(said, title):
    assert titles.clean(said, 48) == title


def test_the_model_names_a_chat_after_its_first_reply():
    chat = first_turn(web.Session())

    wait_for(lambda: chat.title == "Q3 revenue report")
    asked = FakeClient.requests[-1]
    assert asked["stream"] is False
    shown = asked["messages"][-1]["content"]
    assert "how did we do last quarter" in shown
    assert "Revenue was up 12%." in shown


def test_only_once():
    session = web.Session()
    chat = first_turn(session)
    wait_for(lambda: chat.title == "Q3 revenue report")

    session.send(chat, "and the quarter before?")
    wait_for(lambda: not chat.busy and not chat.queued)
    time.sleep(0.1)
    assert sum(r.get("stream") is False for r in FakeClient.requests) == 1


def test_off_keeps_the_first_message(monkeypatch):
    monkeypatch.setenv(titles.SETTING, "0")
    chat = first_turn(web.Session())

    time.sleep(0.1)
    assert chat.title == "how did we do last quarter on revenue?"
    assert all(r.get("stream") is not False for r in FakeClient.requests)


def test_never_over_a_name_the_user_gave_it():
    FakeClient.gate.clear()
    session = web.Session()
    chat = first_turn(session)
    wait_for(lambda: any(r.get("stream") is False
                         for r in FakeClient.requests))

    web.command(session, {"name": "rename", "chat": chat.id,
                          "arg": "Money"})
    FakeClient.gate.set()
    time.sleep(0.2)
    assert chat.title == "Money"


def test_a_model_that_fails_leaves_the_first_message(monkeypatch):
    FakeClient.title = ""
    chat = first_turn(web.Session())

    time.sleep(0.2)
    assert chat.title == "how did we do last quarter on revenue?"


def test_the_switch(monkeypatch):
    saved = {}
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: saved.update({name: value}))
    session = web.Session()

    assert web.command(session, {"name": "titles-setting", "arg": "off"}) \
        == {"model_titles": False}
    assert saved == {titles.SETTING: "0"}
    web.command(session, {"name": "titles-setting", "arg": "on"})
    assert saved == {titles.SETTING: "1"}
