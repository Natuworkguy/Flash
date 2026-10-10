"""Tests for side questions: /btw, answered apart from the conversation,
in the terminal and the web UI, even while a turn runs."""

import http.client
import json
import threading
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, btw, repl_input, web


def part(content="", done=False):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, thinking="", tool_calls=None),
        done=done, eval_count=0, eval_duration=0,
    )


class FakeClient:
    scripts: list = []
    requests: list = []

    def __init__(self, host=None, **_):
        pass

    def chat(self, **kwargs):
        FakeClient.requests.append(kwargs)
        script = FakeClient.scripts.pop(0)
        return iter(script() if callable(script) else script)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeClient.scripts = []
    FakeClient.requests = []
    monkeypatch.setattr(ollama, "Client", FakeClient)
    monkeypatch.setattr(ai.Config, "model", "flash-test")
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
    monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


class TestTheQuestion:
    @pytest.mark.parametrize("line, asked", [
        ("/btw what is this?", "what is this?"),
        ("  /btw   spaced  ", "spaced"),
        ("/btw", ""),
        ("/btwx no", None),
        ("hello /btw", None),
    ])
    def test_a_btw_line(self, line, asked):
        assert btw.question_of(line) == asked

    def test_the_context_holds_words_not_tool_traffic(self):
        history = [
            {"role": "user", "content": "fix the tests"},
            {"role": "assistant", "content": "", "tool_calls": [{}]},
            {"role": "tool", "content": "300 lines of output"},
            {"role": "assistant", "content": "Fixed two of them."},
        ]
        sent = btw.context(history, "which two?", "fix the rest", "Now")

        assert sent[0] == {"role": "system", "content": btw.PROMPT}
        assert [m["content"] for m in sent[1:3]] == [
            "fix the tests", "Fixed two of them.",
        ]
        assert "working on: fix the rest" in sent[3]["content"]
        assert "so far: Now" in sent[3]["content"]
        assert sent[-1] == {"role": "user", "content": "which two?"}

    def test_a_long_history_keeps_its_end(self):
        history = [
            {"role": "user", "content": f"message {n} " + "x" * 3000}
            for n in range(40)
        ]
        sent = btw.context(history, "?")
        words = "".join(m["content"] for m in sent)
        assert "message 39" in words and "message 0 " not in words
        assert len(words) < btw.HISTORY_CHARS + 2000

    def test_the_answer_streams_without_dashes(self):
        FakeClient.scripts = [[part("Two — the "), part("rest pass.")]]
        heard = []
        said = btw.ask(FakeClient(), "m", [], on_text=heard.append)
        assert "—" not in said and said.endswith("rest pass.")
        assert "".join(heard).strip() == said
        assert "tools" not in FakeClient.requests[0]


class TestTheTerminal:
    def test_a_btw_in_the_dock_goes_to_the_side(self):
        assert repl_input.route("/btw why?") == (repl_input.BTW, "/btw why?")
        assert repl_input.route("/model") == (repl_input.QUEUE, "/model")

    def test_it_answers_apart_from_the_conversation(self, monkeypatch):
        shown = []
        monkeypatch.setattr(ai.console, "print",
                            lambda *a, **k: shown.append(a[0] if a else ""))
        FakeClient.scripts = [[part("Because it is faster.")]]
        history = [{"role": "user", "content": "use a set"}]

        ai._btw("why a set?", history, "use a set")

        assert history == [{"role": "user", "content": "use a set"}]
        assert "why a set?" in str(shown[0])
        panel = shown[-1]
        assert "Because it is faster." in panel.renderable.markup
        assert FakeClient.requests[0]["messages"][-1]["content"] == \
            "why a set?"

    def test_nothing_asked_says_how(self, monkeypatch):
        warned = []
        monkeypatch.setattr(ai, "warn", warned.append)
        ai._btw("", [])
        assert warned == [btw.USAGE]


class TestTheWeb:
    def events_of(self, session):
        queue = session.hub.subscribe()
        seen = []

        def drain():
            while not queue.empty():
                seen.append(queue.get_nowait())
            return [e for e in seen if e["type"] == "btw"]

        return drain

    def test_answered_while_a_turn_runs_and_never_kept(self):
        gate = threading.Event()

        def held():
            gate.wait(5)
            yield from [part("Done with the work."), part(done=True)]

        FakeClient.scripts = [held, [part("It is "), part("the cache.")]]
        session = web.Session()
        drain = self.events_of(session)
        chat = session.new_chat()
        session.send(chat, "speed up the build")
        # The turn has asked its model, and is waiting on it.
        wait_for(lambda: chat.busy and FakeClient.requests)

        asked = session.btw(chat, "what is slow?")
        wait_for(lambda: any(e.get("done") for e in drain()))

        events = [e for e in drain() if e["id"] == asked]
        assert events[0]["question"] == "what is slow?"
        assert "".join(e.get("text", "") for e in events) == "It is the cache."
        sent = FakeClient.requests[-1]["messages"]
        assert "working on: speed up the build" in sent[-2]["content"]
        # The turn was never held up, and nothing of it is in the chat.
        assert chat.busy
        gate.set()
        wait_for(lambda: not chat.busy)
        said = [e.get("text") for e in chat.log]
        assert "what is slow?" not in said
        assert all("cache" not in (m.get("content") or "")
                   for m in chat.messages)

    def test_the_route(self):
        FakeClient.scripts = [[part("Yes.")]]
        srv = web.Server(0)
        threading.Thread(
            target=srv.serve_forever, kwargs={"poll_interval": 0.02},
            daemon=True,
        ).start()
        try:
            port = srv.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST", "/api/btw", body=json.dumps({"text": "ok?"}),
                headers={"Host": f"127.0.0.1:{port}",
                         "X-Flash-Token": srv.token,
                         "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read())
            conn.close()
        finally:
            srv.shutdown()
            srv.server_close()

        assert response.status == 200
        assert len(body["id"]) == 8 and body["chat"]

    def test_empty_is_refused(self):
        session = web.Session()
        with pytest.raises(ValueError):
            session.btw(session.new_chat(), "  ")
