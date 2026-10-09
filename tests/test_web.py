"""Tests for the web UI's server: its locks, its API, and its turns."""

import base64
import http.client
import json
import os
import threading
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, sparks, tools, web, workspace
from flash.cli import parse_args

# --- A model that streams what each test scripts -------------------------


def part(content="", thinking="", calls=None, done=False, tokens=0):
    return SimpleNamespace(
        message=SimpleNamespace(
            content=content, thinking=thinking, tool_calls=calls
        ),
        done=done,
        eval_count=tokens,
        eval_duration=tokens * 100_000_000,
    )


def call(name, **arguments):
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments)
    )


class FakeClient:
    scripts: list = []
    requests: list = []

    def __init__(self, host=None):
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
    monkeypatch.setattr(ai.Config, "no_command_confirmation", False)
    monkeypatch.setattr(ai, "_session_system_prompt", lambda heard=False: "")
    monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
    monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)


def events_of(session):
    """Every event the session publishes, as they happen."""

    queue = session.hub.subscribe()
    seen = []

    def drain():
        while not queue.empty():
            seen.append(queue.get_nowait())
        return seen

    return drain


def run(session, chat, text, timeout=5):
    session.send(chat, text)
    deadline = time.monotonic() + timeout
    time.sleep(0.05)
    while chat.busy or chat.queued:
        assert time.monotonic() < deadline, "the turn never finished"
        time.sleep(0.02)


def types(events):
    return [e["type"] for e in events]


# --- Turns ---------------------------------------------------------------


class TestTurns:
    def test_a_reply_streams_token_by_token(self):
        FakeClient.scripts = [[
            part("Hel"), part("lo."), part(done=True, tokens=4),
        ]]
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        run(session, chat, "hi")

        seen = drain()
        tokens = [e["text"] for e in seen if e["type"] == "token"]
        assert tokens == ["Hel", "lo."]
        assert types(seen)[-4:] == ["assistant", "stats", "busy", "status"]
        assert chat.messages[-1] == {"role": "assistant", "content": "Hello."}
        stats = next(e for e in seen if e["type"] == "stats")
        assert (stats["tokens"], stats["rate"]) == (4, 10.0)

    def test_a_reply_that_promises_work_is_told_to_do_it(self):
        FakeClient.scripts = [
            [part(calls=[call("get_date")]), part(done=True)],
            # It sees the problem and says what it will do, then stops.
            [part("Wait, the block covers the text. I'll move the logo "
                  "text forward."), part(done=True)],
            [part(calls=[call("get_date")]), part(done=True)],
            [part("Fixed: the text is in front now."), part(done=True)],
        ]
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        run(session, chat, "make the minecraft logo")

        said = [e["text"] for e in drain()
                if e["type"] == "assistant" and e["text"]]
        assert said[-2:] == [
            "Wait, the block covers the text. I'll move the logo text "
            "forward.",
            "Fixed: the text is in front now.",
        ]
        # The model was told to go on, right after its promise.
        sent = FakeClient.requests[-1]["messages"]
        at = sent.index({"role": "system", "content": ai.PROMISE_NOTE})
        assert "I'll move the logo" in sent[at - 1]["content"]
        assert len(FakeClient.requests) == 4
        # The history keeps what the user saw, not Flash's nudge.
        kept = [m.get("content") for m in chat.messages]
        assert ai.PROMISE_NOTE not in kept
        assert kept[-1] == "Fixed: the text is in front now."
        assert any("I'll move the logo" in (c or "") for c in kept)

    def test_a_model_that_keeps_promising_is_let_go(self):
        promise = [part("I'll fix it."), part(done=True)]
        FakeClient.scripts = [promise, promise, promise]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "fix it")

        # Asked twice, then its word is taken as the answer.
        assert len(FakeClient.requests) == 1 + ai.MAX_PROMISE_NUDGES
        assert chat.messages[-1]["content"] == "I'll fix it."

    def test_an_offer_is_not_a_promise(self):
        FakeClient.scripts = [
            [part("Done. I'll add a roof if you'd like."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "make a house")

        assert len(FakeClient.requests) == 1

    def test_a_streamed_reply_comes_without_dashes(self):
        FakeClient.scripts = [[
            part("It works \u2014"), part(" mostly, pages 1\u2013"),
            part("3."), part(done=True, tokens=4),
        ]]
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        run(session, chat, "hi")

        shown = "".join(e["text"] for e in drain() if e["type"] == "token")
        assert shown == "It works, mostly, pages 1-3."
        assert chat.messages[-1]["content"] == shown

    def test_the_first_message_names_the_chat(self):
        FakeClient.scripts = [[part("ok", done=True)]]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "fix the login bug")

        assert chat.title == "fix the login bug"

    def test_thinking_streams_separately(self):
        FakeClient.scripts = [[
            part(thinking="hmm"), part("answer"), part(done=True),
        ]]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "q")

        final = [e for e in chat.log if e["type"] == "assistant"][-1]
        assert (final["text"], final["thinking"]) == ("answer", "hmm")

    def test_tools_run_and_report_through_the_sink(self):
        FakeClient.scripts = [
            [part(calls=[call("get_os")]), part(done=True)],
            [part("You are on a computer."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "what os")

        kinds = types(chat.log)
        assert kinds.index("tool") < kinds.index("result")
        assert kinds[-2:] == ["assistant", "stats"]
        roles = [m["role"] for m in chat.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
        # The second request carried the tool's answer.
        assert FakeClient.requests[1]["messages"][-1]["role"] == "tool"

    def test_a_notification_pops_up_on_an_open_page(self):
        session = web.Session()
        chat = session.new_chat()
        sink = web._sink(session, chat)

        # With no page open, it is left to the desktop.
        assert sink("notify", '{"title": "Job", "message": "Done"}', "") \
            is False

        drain = events_of(session)
        assert sink("notify", '{"title": "Job", "message": "Done"}', "")
        sent = [e for e in drain() if e["type"] == "notify"]
        assert sent and (sent[0]["title"], sent[0]["text"]) == ("Job", "Done")
        # A pop-up, not part of the conversation drawn again on reload.
        assert not [e for e in chat.log if e["type"] == "notify"]

    def test_a_question_waits_for_the_page(self, tmp_path):
        target = tmp_path / "never"
        FakeClient.scripts = [
            [part(calls=[call("shell", command=f"touch {target}")]),
             part(done=True)],
            [part("Understood."), part(done=True)],
        ]
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        session.send(chat, "make a file")
        deadline = time.monotonic() + 5
        while not session.asks:
            assert time.monotonic() < deadline
            time.sleep(0.02)

        ask = next(iter(session.asks.values()))
        assert ask.question.startswith("Run this command?")
        assert session.answer(ask.id, "no")
        while chat.busy:
            time.sleep(0.02)

        assert not target.exists()
        answered = [e for e in drain() if e["type"] == "answered"]
        assert answered[0]["answer"] == "n"
        assert "Command blocked by user" in json.dumps(chat.messages)

    @pytest.mark.skipif(
        os.name == "nt", reason="PowerShell has no touch command"
    )
    def test_allowing_runs_it(self, tmp_path):
        target = tmp_path / "made"
        FakeClient.scripts = [
            [part(calls=[call("shell", command=f"touch {target}")]),
             part(done=True)],
            [part("Done."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        def allow():
            while not session.asks:
                time.sleep(0.02)
            session.answer(next(iter(session.asks)), "y")

        threading.Thread(target=allow, daemon=True).start()
        run(session, chat, "make a file")

        assert target.exists()

    def test_stop_ends_a_stream(self):
        session = web.Session()
        chat = session.new_chat()

        def slow():
            yield part("partial ")
            chat.stop.set()
            yield part("never shown")

        FakeClient.scripts = [slow]

        run(session, chat, "long answer")

        final = [e for e in chat.log if e["type"] == "assistant"][-1]
        assert final["text"] == "partial "
        assert "never shown" not in json.dumps(chat.log)

    def test_stop_while_asking_denies(self):
        session = web.Session()
        chat = session.new_chat()
        FakeClient.scripts = [
            [part(calls=[call("shell", command="echo hi")]),
             part(done=True)],
        ]

        session.send(chat, "run it")
        while not session.asks:
            time.sleep(0.02)
        chat.stop.set()
        while chat.busy:
            time.sleep(0.02)

        assert [e["answer"] for e in chat.log if e["type"] == "answered"] == [
            "n"
        ]

    def test_reason_becomes_a_thought_on_the_page(self):
        FakeClient.scripts = [
            [part(calls=[call("reason", thought="Check the tests.")]),
             part(done=True)],
            [part("Done."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "go")

        thought = next(e for e in chat.log if e["type"] == "thought")
        assert thought["text"] == "Check the tests."

    def test_no_model_says_so(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "model", None)
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "hi")

        error = next(e for e in chat.log if e["type"] == "error")
        assert "No model is set" in error["text"]

    def test_a_backend_failure_is_shown_not_raised(self):
        FakeClient.scripts = []  # the chat call itself fails
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "hi")

        assert any(e["type"] == "error" for e in chat.log)
        assert not chat.busy


class TestState:
    def test_state_and_lite_state(self):
        FakeClient.scripts = [[part("ok", done=True)]]
        session = web.Session()
        chat = session.new_chat()
        run(session, chat, "hi")

        full = session.state()
        lite = session.state(lite=True)

        assert full["chats"][0]["log"]
        assert "log" not in lite["chats"][0]
        assert full["status"]["model"] == "flash-test"
        assert full["seq"] == session.hub.seq

    def test_tokens_are_not_kept_but_replies_are(self):
        FakeClient.scripts = [[part("a"), part("b"), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "hi")

        assert "token" not in types(chat.log)
        assert chat.partial == ""


class TestCommands:
    def test_chats(self):
        session = web.Session()

        chat_id = web.command(session, {"name": "new"})["chat"]
        web.command(session, {"name": "rename", "chat": chat_id,
                              "arg": "Refactor"})
        assert session.chat(chat_id).title == "Refactor"

        web.command(session, {"name": "delete", "chat": chat_id})
        assert chat_id not in session.chats

    def test_auto(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            ai, "set_config_var",
            lambda k, v: saved.update({k: v}),
        )

        result = web.command(web.Session(), {"name": "auto"})

        assert result == {"auto": True}
        assert saved == {"NO_COMMAND_CONFIRMATION": "1"}

    def test_unknown(self):
        with pytest.raises(ValueError):
            web.command(web.Session(), {"name": "explode"})


# --- HTTP ----------------------------------------------------------------


@pytest.fixture
def server():
    srv = web.Server(0)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None, token=True):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = {"Host": f"127.0.0.1:{port}"}
    if token:
        sent["X-Flash-Token"] = srv.token
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    conn.request(
        method, path,
        body=json.dumps(body) if body is not None else None,
        headers=sent,
    )
    response = conn.getresponse()
    data = response.read()
    conn.close()
    request.last = response
    return response.status, data


class TestLocks:
    def test_no_token_no_entry(self, server):
        assert request(server, "GET", "/", token=False)[0] == 403
        assert request(server, "GET", "/api/state", token=False)[0] == 403
        assert request(server, "POST", "/api/command", {"name": "new"},
                       token=False)[0] == 403

    def test_a_wrong_token(self, server):
        status, _ = request(server, "GET", "/api/state",
                            headers={"X-Flash-Token": "guess"})
        assert status == 403

    def test_the_token_in_the_link_opens_the_page(self, server):
        status, body = request(
            server, "GET", f"/?token={server.token}", token=False
        )
        assert status == 200
        assert b"<title>Flash</title>" in body

    def test_another_host_name_is_refused(self, server):
        # What a DNS rebinding attack looks like from here.
        status, _ = request(server, "GET", "/api/state",
                            headers={"Host": "evil.example:7433"})
        assert status == 403

    def test_another_site_cannot_post(self, server):
        status, _ = request(
            server, "POST", "/api/command", {"name": "new"},
            headers={"Origin": "https://evil.example"},
        )
        assert status == 403

    def test_its_own_page_can(self, server):
        port = server.server_address[1]
        status, body = request(
            server, "POST", "/api/command", {"name": "new"},
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )
        assert status == 200
        assert json.loads(body)["chat"]

    def test_bad_requests(self, server):
        assert request(server, "POST", "/api/nope", {})[0] == 400
        assert request(server, "POST", "/api/send",
                       {"chat": "missing", "text": "x"})[0] == 404
        assert request(server, "GET", "/nope")[0] == 404


class TestEventStream:
    def test_events_arrive_as_they_happen(self, server):
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/api/events?token={server.token}",
                     headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"
        assert response.fp.readline() == b": connected\n"
        response.fp.readline()

        server.session.new_chat()

        line = response.fp.readline()
        event = json.loads(line.decode().removeprefix("data: "))
        assert event["type"] == "chats"
        conn.close()


class TestFlags:
    def test_web_flags(self):
        args = parse_args(["--web", "--port", "9000", "--no-open"])

        assert (args.web, args.port, args.no_open) == (True, 9000, True)

    def test_port_needs_web(self):
        with pytest.raises(SystemExit):
            parse_args(["--port", "9000"])

    def test_the_page_ships_with_flash(self):
        pyproject = web.PAGE.parents[2] / "pyproject.toml"

        assert web.PAGE.is_file()
        assert '"web/*.html"' in pyproject.read_text()


def test_tools_confirm_in_the_terminal_without_a_page(monkeypatch):
    # No answerer on this thread: the old y/n prompt is still used.
    monkeypatch.setattr(tools, "typed", lambda: "n")

    assert tools.shell_tool("echo hi") == "Command blocked by user"


# --- The network, the font, the QR code ----------------------------------


@pytest.fixture
def lan_server():
    srv = web.Server(0, lan=True)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


class TestLan:
    def test_local_only_by_default(self, server):
        assert server.server_address[0] == "127.0.0.1"
        assert server.network_url is None
        # A phone asking by address is turned away.
        status, _ = request(server, "GET", "/api/state",
                            headers={"Host": f"192.168.1.20:{server.port}"})
        assert status == 403

    def test_lan_listens_everywhere(self, lan_server):
        assert lan_server.server_address[0] == "0.0.0.0"  # nosec B104
        assert lan_server.network_url.startswith("http://")
        assert lan_server.token in lan_server.network_url

    def test_a_phone_can_ask_by_address(self, lan_server):
        status, _ = request(
            lan_server, "GET", "/api/state",
            headers={"Host": f"192.168.1.20:{lan_server.port}"},
        )
        assert status == 200

    def test_the_token_still_guards_it(self, lan_server):
        status, _ = request(
            lan_server, "GET", "/api/state", token=False,
            headers={"Host": f"192.168.1.20:{lan_server.port}"},
        )
        assert status == 403

    def test_a_rebinding_domain_is_still_refused(self, lan_server):
        status, _ = request(
            lan_server, "GET", "/api/state",
            headers={"Host": f"attacker.example:{lan_server.port}"},
        )
        assert status == 403

    def test_its_own_page_on_the_network_can_post(self, lan_server):
        host = f"192.168.1.20:{lan_server.port}"
        status, _ = request(
            lan_server, "POST", "/api/command", {"name": "new"},
            headers={"Host": host, "Origin": f"http://{host}"},
        )
        assert status == 200

    def test_the_qr_code(self, lan_server, server):
        _, body = request(lan_server, "GET", "/api/qr")
        link = json.loads(body)
        assert link["lan"] and link["url"] == lan_server.network_url
        assert link["svg"].startswith("<svg")

        _, body = request(server, "GET", "/api/qr")
        assert json.loads(body) == {"lan": False, "url": "", "svg": ""}

    def test_the_terminal_gets_one_too(self):
        drawn = web.qr_text("http://192.168.1.20:7433/?token=abc")

        assert "▄" in drawn or "▀" in drawn


class TestFont:
    def test_served_without_the_token(self, server):
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/static/orbit.woff2",
                     headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()

        assert response.status == 200
        assert response.getheader("Content-Type") == "font/woff2"
        assert body[:4] == b"wOF2"

    def test_the_logo_is_served_too(self, server):
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/static/logo-icon.svg",
                     headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()

        assert response.status == 200
        assert response.getheader("Content-Type") == "image/svg+xml"
        assert body.startswith(b"<svg")
        assert b"/static/logo-icon.svg" in web.PAGE.read_bytes()

    def test_katex_is_served_for_math(self, server):
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/static/katex/katex.min.js",
                     headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()

        assert response.status == 200
        assert response.getheader("Content-Type").startswith(
            "text/javascript"
        )
        assert b"katex" in body[:400]
        assert b"/static/katex/katex.min.js" in web.PAGE.read_bytes()
        # The script alone: the browser draws the MathML itself.
        assert [n for n in web.STATIC if n.startswith("katex/")] == [
            "katex/katex.min.js"
        ]
        assert (web.WEB_DIR / "katex" / "LICENSE.txt").is_file()

    def test_three_js_is_served_for_3d_models(self, server):
        port = server.port
        for name in ("three.min.js", "viewer.js"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/static/three/{name}",
                         headers={"Host": f"127.0.0.1:{port}"})
            response = conn.getresponse()
            body = response.read()
            conn.close()

            assert response.status == 200
            assert response.getheader("Content-Type").startswith(
                "text/javascript"
            )
            assert body
        page = web.PAGE.read_bytes()
        assert b"/static/three/three.min.js" in page
        assert b"/static/three/viewer.js" in page
        assert (web.WEB_DIR / "three" / "LICENSE.txt").is_file()

    def test_but_nothing_else_is(self, server):
        assert request(server, "GET", "/static/index.html",
                       token=False)[0] == 403
        assert request(server, "GET", "/static/../web.py",
                       token=False)[0] == 403

    def test_the_license_ships_beside_it(self):
        assert (web.WEB_DIR / "OFL-orbit.txt").is_file()

    @pytest.mark.parametrize(
        "name", ["newsreader.woff2", "newsreader-italic.woff2"],
    )
    def test_the_reply_face_is_served(self, server, name):
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", f"/static/{name}",
                     headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()

        assert response.status == 200
        assert response.getheader("Content-Type") == "font/woff2"
        assert body[:4] == b"wOF2"

    def test_its_license_ships_too(self):
        text = (web.WEB_DIR / "OFL-newsreader.txt").read_text(
            encoding="utf-8",
        )
        assert "SIL Open Font License" in text


class TestInTheTerminal:
    def test_lan_flag(self):
        assert parse_args(["--web", "--lan"]).lan
        with pytest.raises(SystemExit):
            parse_args(["--lan"])

    def test_web_starts_and_stops_a_background_server(self, monkeypatch):
        monkeypatch.setattr(web, "DEFAULT_PORT", 0)
        shown = []
        monkeypatch.setattr(web, "announce", shown.append)
        try:
            ai._web_command("")
            server = shown[0]
            assert server is web._background
            status, _ = request(server, "GET", "/api/state")
            assert status == 200

            ai._web_command("")
            assert shown[1] is server  # the same one, not a second
        finally:
            ai._web_command("stop")

        assert web._background is None

    def test_web_lan_restarts_it_on_the_network(self, monkeypatch):
        monkeypatch.setattr(web, "DEFAULT_PORT", 0)
        shown = []
        monkeypatch.setattr(web, "announce", shown.append)
        try:
            ai._web_command("")
            ai._web_command("lan")
            assert not shown[0].lan and shown[1].lan
        finally:
            ai._web_command("stop")


# --- Projects, hosts, and chats that outlive the server -------------------


class TestProjectChats:
    def test_a_project_chat_runs_in_its_folder_with_its_instructions(
        self, tmp_path, monkeypatch
    ):
        from flash import workspace

        folder = tmp_path / "proj"
        folder.mkdir()
        made = workspace.create_project("Proj", str(folder), "Use tabs.")
        seen = {}

        def where(**kwargs):
            seen["cwd"] = os.getcwd()
            seen["system"] = kwargs["messages"][0]["content"]
            return iter([part("ok"), part(done=True)])

        monkeypatch.setattr(FakeClient, "chat", lambda self, **kw: where(**kw))
        session = web.Session()
        before = os.getcwd()
        chat = session.new_chat(made.id)

        run(session, chat, "hi")

        assert seen["cwd"] == str(folder.resolve())
        assert "=== Project: Proj ===" in seen["system"]
        assert "Use tabs." in seen["system"]
        assert os.getcwd() == before

    def test_a_missing_folder_is_an_error_not_a_crash(self, tmp_path):
        from flash import workspace

        folder = tmp_path / "gone"
        folder.mkdir()
        made = workspace.create_project("Gone", str(folder))
        folder.rmdir()
        session = web.Session()
        chat = session.new_chat(made.id)

        run(session, chat, "hi")

        error = next(e for e in chat.log if e["type"] == "error")
        assert "not there any more" in error["text"]

    def test_an_unknown_project_is_refused(self):
        with pytest.raises(KeyError):
            web.Session().new_chat("nope1234")

    def test_chats_outlive_the_server(self):
        FakeClient.scripts = [[part("remembered"), part(done=True)]]
        first = web.Session()
        chat = first.new_chat()
        run(first, chat, "keep this")

        again = web.Session()

        kept = again.chats[chat.id]
        assert kept.title == "keep this"
        assert kept.messages[-1]["content"] == "remembered"
        assert [e["type"] for e in kept.log][:1] == ["user"]
        assert not kept.busy

    def test_an_empty_chat_is_not_saved(self):
        session = web.Session()
        session.new_chat()

        assert web.Session().chats == {}

    def test_deleting_and_clearing_forget_the_file(self):
        FakeClient.scripts = [[part("a"), part(done=True)],
                              [part("b"), part(done=True)]]
        session = web.Session()
        gone = session.new_chat()
        run(session, gone, "one")
        wiped = session.new_chat()
        run(session, wiped, "two")

        session.delete_chat(gone.id)
        session.clear_chat(wiped)

        assert web.Session().chats == {}


class TestProjectCommands:
    def test_create_update_delete(self, tmp_path):
        session = web.Session()

        made = web.command(session, {
            "name": "project-new", "arg": str(tmp_path), "label": "App",
            "instructions": "Be brief.",
        })
        assert session.state()["projects"][0]["name"] == "App"

        web.command(session, {"name": "project-update", "arg": made["id"],
                              "instructions": "Be thorough."})
        assert session.state()["projects"][0]["instructions"] == (
            "Be thorough."
        )

        chat = web.command(session, {"name": "new", "project": made["id"]})
        assert session.chat(chat["chat"]).project == made["id"]

        web.command(session, {"name": "project-delete", "arg": made["id"]})
        assert session.state()["projects"] == []

    def test_a_project_with_more_folders(self, tmp_path):
        (tmp_path / "web").mkdir()
        (tmp_path / "api").mkdir()
        session = web.Session()

        made = web.command(session, {
            "name": "project-new", "arg": str(tmp_path / "web"),
            "label": "App", "folders": [str(tmp_path / "api")],
        })
        api = str((tmp_path / "api").resolve())
        assert made["folders"] == [api]
        prompt = web.project_prompt(workspace.project(made["id"]))
        assert "also takes in these folders" in prompt and api in prompt

        # Saved without them, its folders stay as they were.
        kept = web.command(session, {"name": "project-update",
                                     "arg": made["id"], "instructions": "x"})
        assert kept["folders"] == [api]
        cleared = web.command(session, {"name": "project-update",
                                        "arg": made["id"], "folders": []})
        assert cleared["folders"] == []

    def test_a_bad_folder_is_a_400(self, server, tmp_path):
        status, body = request(server, "POST", "/api/command", {
            "name": "project-new", "arg": str(tmp_path / "nope"),
        })

        assert status == 400
        assert "not a folder" in json.loads(body)["error"]

    def test_folder_suggestions(self, tmp_path):
        (tmp_path / "src").mkdir()

        result = web.command(web.Session(), {
            "name": "dirs", "arg": f"{tmp_path}/s",
        })

        assert result == {"dirs": [str(tmp_path / "src")]}


class TestHostCommands:
    def test_switching_saves_it_and_forgets_model_facts(self, monkeypatch):
        saved = {}
        forgot = []
        monkeypatch.setattr(ai, "set_config_var",
                            lambda k, v: saved.update({k: v}))
        monkeypatch.setattr(ai, "forget_model_facts",
                            lambda: forgot.append(True))
        monkeypatch.setattr(web.workspace, "host_state", lambda url: "down")

        result = web.command(web.Session(), {"name": "host",
                                             "arg": "10.0.0.5"})

        assert saved == {"OLLAMA_HOST": "http://10.0.0.5:11434"}
        assert forgot == [True]
        assert result["host"] == "http://10.0.0.5:11434"
        assert result["up"] is False

    def test_add_list_remove(self, monkeypatch):
        monkeypatch.setattr(web.workspace, "host_state", lambda url: "up")
        session = web.Session()

        added = web.command(session, {"name": "host-add",
                                      "arg": "10.0.0.5", "label": "Studio"})
        assert added == {"name": "Studio", "url": "http://10.0.0.5:11434",
                         "locked": False, "up": True, "refused": False}

        listed = web.command(session, {"name": "hosts"})["hosts"]
        assert [h["name"] for h in listed] == ["This computer", "Studio"]

        web.command(session, {"name": "host-remove", "arg": "10.0.0.5"})
        listed = web.command(session, {"name": "hosts"})["hosts"]
        assert [h["name"] for h in listed] == ["This computer"]

    def test_a_host_added_with_a_key(self, monkeypatch):
        monkeypatch.setattr(web.workspace, "host_state",
                            lambda url: "refused")
        session = web.Session()

        added = web.command(session, {
            "name": "host-add", "arg": "10.0.0.5", "label": "Studio",
            "key": "s3cret",
        })

        # The page learns there is a key, and that it was turned down,
        # but never the key itself.
        assert added["locked"] is True and added["refused"] is True
        assert "s3cret" not in json.dumps(added)
        listed = web.command(session, {"name": "hosts"})
        assert "s3cret" not in json.dumps(listed)
        assert web.workspace.api_key("10.0.0.5") == "s3cret"

    def test_a_bad_address_is_refused(self):
        with pytest.raises(ValueError):
            web.command(web.Session(), {"name": "host", "arg": "ftp://x"})

    def test_status_names_the_host(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "host", "http://10.0.0.5:11434")
        web.workspace.add_host("Studio", "10.0.0.5")

        assert web.status(ai)["host_name"] == "Studio"


# --- Staying signed in across a reload, and nowhere else -----------------


def cookie_of(srv):
    return f"{srv.cookie_name}={srv.token}"


class TestCookie:
    def test_the_link_leaves_a_locked_down_cookie(self, server):
        status, _ = request(server, "GET", f"/?token={server.token}",
                            token=False)
        cookie = request.last.getheader("Set-Cookie")

        assert status == 200
        # A key of this browser's own, not the link's token.
        assert cookie.startswith(server.cookie_name + "=")
        assert server.token not in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Expires" not in cookie and "Max-Age" not in cookie

    def test_a_reload_works_with_the_cookie_alone(self, server):
        status, body = request(server, "GET", "/", token=False,
                               headers={"Cookie": cookie_of(server)})

        assert status == 200
        assert b"<title>Flash</title>" in body

    def test_the_api_and_the_stream_take_it_too(self, server):
        jar = {"Cookie": cookie_of(server)}

        assert request(server, "GET", "/api/state", token=False,
                       headers=jar)[0] == 200
        assert request(server, "POST", "/api/command", {"name": "new"},
                       token=False, headers=jar)[0] == 200

    def test_an_old_cookie_gets_a_page_saying_so(self, server):
        status, body = request(
            server, "GET", "/", token=False,
            headers={"Cookie": f"{server.cookie_name}=from-last-time"},
        )

        assert status == 403
        assert b"This link has expired" in body
        assert request.last.getheader("Content-Type").startswith(
            "text/html")

    def test_no_cookie_no_link_is_still_refused(self, server):
        assert request(server, "GET", "/", token=False)[0] == 403
        assert request(server, "GET", "/api/state", token=False)[0] == 403

    def test_another_ports_cookie_does_not_count(self, server):
        other = f"flash_1={server.token}"

        status, _ = request(server, "GET", "/api/state", token=False,
                            headers={"Cookie": other})

        assert status == 403

    def test_a_garbled_cookie_header_is_just_no_cookie(self, server):
        status, _ = request(server, "GET", "/api/state", token=False,
                            headers={"Cookie": '"; ;;=\\x00'})

        assert status == 403

    def test_the_cookie_does_not_help_another_site(self, server):
        # A page elsewhere posting here: its Origin gives it away, even
        # if a browser were to send the cookie along.
        status, _ = request(
            server, "POST", "/api/command", {"name": "new"}, token=False,
            headers={"Cookie": cookie_of(server),
                     "Origin": "https://evil.example"},
        )

        assert status == 403

    def test_changes_must_be_json(self, server):
        # What an HTML form on another site would send.
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/api/command", body="name=new",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Cookie": cookie_of(server),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response = conn.getresponse()
        response.read()
        conn.close()

        assert response.status == 415
        assert server.session.chats == {}

    def test_a_port_in_use_says_so(self):
        first = web.Server(0)
        try:
            with pytest.raises(OSError, match="could not listen on port"):
                web._listen(first.port, lan=False)
        finally:
            first.server_close()

    def test_each_server_has_its_own_token(self):
        first, second = web.Server(0), web.Server(0)
        try:
            assert first.token != second.token
            assert first.cookie_name != second.cookie_name
        finally:
            first.server_close()
            second.server_close()


MAC_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


def sign_in(srv, agent=MAC_CHROME):
    """Open the link as a browser would, and give back its cookie."""

    status, _ = request(srv, "GET", f"/?token={srv.token}", token=False,
                        headers={"User-Agent": agent})
    assert status == 200
    return request.last.getheader("Set-Cookie").split(";")[0]


def as_browser(srv, jar, name, arg=""):
    status, body = request(srv, "POST", "/api/command",
                           {"name": name, "arg": arg}, token=False,
                           headers={"Cookie": jar})
    return status, json.loads(body)


class TestSignedInBrowsers:
    def test_the_cookie_alone_carries_a_browser_on(self, server):
        jar = sign_in(server)

        assert request(server, "GET", "/", token=False,
                       headers={"Cookie": jar})[0] == 200
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": jar})[0] == 200
        # A reload is the same browser, not another one.
        assert request.last.getheader("Set-Cookie") is None
        _, listed = as_browser(server, jar, "browsers")
        assert len(listed["browsers"]) == 1

    def test_each_browser_is_listed_and_knows_which_is_itself(self, server):
        mac = sign_in(server)
        sign_in(server, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac "
                        "OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/18.0 Mobile/15E148 Safari/604.1")

        _, listed = as_browser(server, mac, "browsers")
        first, second = listed["browsers"]

        assert first["current"] and first["device"] == "Chrome on macOS"
        assert not first["phone"] and first["here"]
        assert not second["current"] and second["device"] == "Safari on iPhone"
        assert second["phone"]
        assert all(len(b["id"]) == 8 for b in listed["browsers"])
        assert server.token not in json.dumps(listed)

    def test_signing_out_a_browser_locks_it_out_and_says_why(self, server):
        kept, other = sign_in(server), sign_in(server)
        _, listed = as_browser(server, kept, "browsers")
        other_id = next(b["id"] for b in listed["browsers"]
                        if not b["current"])

        status, result = as_browser(server, kept, "sign-out", other_id)

        assert status == 200 and result == {"signed_out": 1, "you": False}
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": other})[0] == 403
        status, page = request(server, "GET", "/", token=False,
                               headers={"Cookie": other})
        assert status == 403 and b"This browser was signed out" in page
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": kept})[0] == 200

    def test_signing_out_ends_that_browsers_event_stream(self, server):
        kept, other = sign_in(server), sign_in(server)
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/events",
                     headers={"Host": f"127.0.0.1:{port}", "Cookie": other})
        stream = conn.getresponse()
        assert stream.fp.readline() == b": connected\n"
        wait_for(lambda: len(server.session.hub.owners()) == 1)
        _, listed = as_browser(server, kept, "browsers")
        watching = [b for b in listed["browsers"] if b["active"]]
        assert len(watching) == 1 and not watching[0]["current"]

        as_browser(server, kept, "sign-out", watching[0]["id"])

        while stream.fp.readline():
            pass  # the announcement of the change, then the end
        conn.close()
        wait_for(lambda: not server.session.hub.owners())

    def test_signing_out_everywhere_else_also_ends_the_link(self, server):
        kept, other = sign_in(server), sign_in(server)
        old = server.token
        told = []
        server.session.relink = lambda: told.append(server.token)

        status, result = as_browser(server, kept, "sign-out-others")

        assert status == 200 and result == {"signed_out": 1, "you": False}
        assert server.token != old and told == [server.token]
        assert request(server, "GET", f"/?token={old}", token=False)[0] == 403
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": other})[0] == 403
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": kept})[0] == 200
        assert request(server, "GET", f"/?token={server.token}",
                       token=False)[0] == 200

    def test_a_browser_can_sign_itself_out(self, server):
        jar = sign_in(server)
        _, listed = as_browser(server, jar, "browsers")

        _, result = as_browser(server, jar, "sign-out",
                               listed["browsers"][0]["id"])

        assert result == {"signed_out": 1, "you": True}
        assert request(server, "GET", "/api/state", token=False,
                       headers={"Cookie": jar})[0] == 403

    def test_a_cookie_holding_the_token_is_signed_in_properly(self, server):
        # What a page opened before this version still has.
        status, _ = request(server, "GET", "/", token=False,
                            headers={"Cookie": cookie_of(server)})
        cookie = request.last.getheader("Set-Cookie")

        assert status == 200
        assert cookie.startswith(server.cookie_name + "=")
        assert server.token not in cookie

    def test_too_many_browsers_drops_the_stalest(self, monkeypatch):
        monkeypatch.setattr(web, "MAX_BROWSERS", 2)
        access = web.Access()
        first = access.sign_in("127.0.0.1", "")
        time.sleep(0.01)
        second = access.sign_in("127.0.0.1", "")
        time.sleep(0.01)
        access.browser(first, "127.0.0.1")
        access.sign_in("127.0.0.1", "")

        assert access.browser(first, "127.0.0.1") is not None
        assert access.browser(second, "127.0.0.1") is None
        assert access.signed_out(second)

    def test_a_restart_keeps_who_was_signed_in(self, monkeypatch):
        access = web.Access()
        key = access.sign_in("192.168.1.9", "Firefox/130.0 (Windows NT 10)")
        access.hand_over()

        kept = web.Access.kept()

        assert kept.token == access.token
        assert kept.browser(key, "192.168.1.9") is not None
        assert web.TOKEN_ENV not in os.environ
        assert web.BROWSERS_ENV not in os.environ

    def test_a_mangled_hand_over_starts_clean(self, monkeypatch):
        monkeypatch.setenv(web.TOKEN_ENV, "kept-token-from-before-1234")
        monkeypatch.setenv(web.BROWSERS_ENV, "{not json")

        kept = web.Access.kept()

        assert kept.token == "kept-token-from-before-1234"
        assert kept.listing("", set()) == []

    def test_an_odd_token_is_refused_not_a_crash(self, server):
        status, _ = request(server, "GET", "/api/state", token=False,
                            headers={"X-Flash-Token": "café"})

        assert status == 403

    @pytest.mark.parametrize("agent, device", [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/140.0 Safari/537.36 Edg/140.0",
         "Edge on Windows"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 "
         "Firefox/130.0", "Firefox on Linux"),
        ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36",
         "Chrome on Android"),
        ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1",
         "Chrome on iPad"),
        ("curl/8.7.1", "Unknown browser"),
    ])
    def test_describing_a_browser(self, agent, device):
        assert web.describe_agent(agent) == device


class TestStreamsEnd:
    def test_closing_the_server_ends_open_streams_at_once(self):
        srv = web.Server(0)
        threading.Thread(
            target=srv.serve_forever, kwargs={"poll_interval": 0.02},
            daemon=True,
        ).start()
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", f"/api/events?token={srv.token}",
                     headers={"Host": f"127.0.0.1:{srv.port}"})
        response = conn.getresponse()
        response.fp.readline()

        started = time.monotonic()
        srv.shutdown()
        srv.server_close()
        response.fp.read()  # returns once the server hangs up

        # Well before the 15s ping that used to be the only way out.
        assert time.monotonic() - started < 2
        conn.close()

    def test_a_hang_up_is_not_reported(self, capsys):
        srv = web.Server(0)
        try:
            try:
                raise ConnectionResetError(54, "Connection reset by peer")
            except ConnectionResetError:
                srv.handle_error(None, ("127.0.0.1", 1))
        finally:
            srv.server_close()

        assert capsys.readouterr().err == ""


# --- Search --------------------------------------------------------------


def said(session, title, *lines, updated=0.0):
    """A chat with LINES alternating you and Flash, saved as it would be."""

    chat = session.new_chat()
    chat.title = title
    chat.log = [
        {"type": "user" if i % 2 == 0 else "assistant", "text": line}
        for i, line in enumerate(lines)
    ]
    chat.updated = updated
    return chat


class TestSearch:
    def test_every_word_has_to_appear(self):
        session = web.Session()
        both = said(session, "Auth", "the login token expires",
                    "raise the TTL")
        said(session, "Docs", "document the login page")

        results = session.search("login token")

        assert [r["chat"] for r in results] == [both.id]

    def test_case_and_spacing_do_not_matter(self):
        session = web.Session()
        said(session, "x", "Refactor   the\nPARSER now")

        assert session.search("refactor PARSER")

    def test_a_hit_says_where_it_is(self):
        session = web.Session()
        said(session, "t", "hello", "the cache is stale", "ok", "clear cache")

        hits = session.search("cache")[0]["hits"]

        assert [(h["index"], h["type"]) for h in hits] == [
            (1, "assistant"), (3, "assistant"),
        ]

    def test_title_matches_rank_first_then_mentions_then_recency(self):
        session = web.Session()
        recent = said(session, "misc", "cache", updated=300)
        chatty = said(session, "misc", "cache cache cache", updated=100)
        titled = said(session, "Cache bug", "fixed", updated=0)
        older = said(session, "misc", "cache", updated=200)

        order = [r["chat"] for r in session.search("cache")]

        assert order == [titled.id, chatty.id, recent.id, older.id]

    def test_snippets_are_cut_at_words_and_marked(self):
        session = web.Session()
        long = ("alpha " * 40) + "NEEDLE " + ("omega " * 40)
        said(session, "t", long)

        snippet = session.search("needle")[0]["hits"][0]["snippet"]

        assert snippet.startswith("…alpha") and snippet.endswith("omega…")
        assert "NEEDLE" in snippet
        assert len(snippet) < 200

    def test_no_words_lists_recent_chats(self):
        session = web.Session()
        old = said(session, "old", "a", updated=1)
        new = said(session, "new", "b", updated=2)

        results = session.search("   ")

        assert [r["chat"] for r in results] == [new.id, old.id]
        assert all(r["hits"] == [] for r in results)

    def test_empty_chats_and_tool_output_are_not_searched(self):
        session = web.Session()
        session.new_chat()
        chat = said(session, "t", "run it")
        chat.log.append({"type": "result", "text": "secret-in-tool-output"})

        assert session.search("secret") == []

    def test_results_are_capped(self):
        session = web.Session()
        for n in range(web.SEARCH_LIMIT + 5):
            said(session, f"chat {n}", "same words")

        assert len(session.search("same")) == web.SEARCH_LIMIT

    def test_the_endpoint(self, server):
        said(server.session, "Deploy notes", "use fly deploy")

        status, body = request(server, "GET", "/api/search?q=fly%20deploy")

        assert status == 200
        assert json.loads(body)["results"][0]["title"] == "Deploy notes"

    def test_the_endpoint_needs_the_token(self, server):
        assert request(server, "GET", "/api/search?q=x",
                       token=False)[0] == 403


# --- Files the agent shows -------------------------------------------------


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf"
    b"\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class TestShownFiles:
    def test_send_pdf_checks_what_it_is_given(self, tmp_path):
        fake = tmp_path / "notes.pdf"
        fake.write_text("just text")
        wrong = tmp_path / "notes.txt"
        wrong.write_text("x")

        assert "does not start like a PDF" in tools.send_pdf(str(fake))
        assert "not a .pdf file" in tools.send_pdf(str(wrong))
        assert "no file" in tools.send_pdf(str(tmp_path / "gone.pdf"))

    def test_in_the_terminal_a_pdf_opens_in_the_viewer(
        self, tmp_path, monkeypatch
    ):
        doc = tmp_path / "report.pdf"
        doc.write_bytes(PDF)
        opened = []
        monkeypatch.setattr(tools, "_open_with_spinner",
                            lambda path: opened.append(path) or "")

        reply = tools.send_pdf(str(doc))

        assert opened == [doc]
        assert "opened in the user's PDF viewer" in reply

    def test_in_the_web_ui_files_go_to_the_page(self, tmp_path, monkeypatch):
        picture = tmp_path / "dot.png"
        picture.write_bytes(PNG)
        doc = tmp_path / "report.pdf"
        doc.write_bytes(PDF)
        opened = []
        monkeypatch.setattr(tools, "_open_with_spinner",
                            lambda path: opened.append(path) or "")
        FakeClient.scripts = [
            [part(calls=[call("send_image", path=str(picture))]),
             part(done=True)],
            [part(calls=[call("send_pdf", path=str(doc))]),
             part(done=True)],
            [part("Here they are."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "show me")

        shown = [e for e in chat.log if e["type"] == "file"]
        assert [(f["name"], f["kind"], f["size"]) for f in shown] == [
            ("dot.png", "image", len(PNG)), ("report.pdf", "pdf", len(PDF)),
        ]
        # Nothing opened on the machine running the server.
        assert opened == []
        # Kept as a copy, so it outlives the file it came from.
        picture.unlink()
        assert web.workspace.kept_file(shown[0]["id"])[0].read_bytes() == PNG

    def test_send_html_checks_what_it_is_given(self, tmp_path):
        wrong = tmp_path / "notes.txt"
        wrong.write_text("x")

        assert "not an .html file" in tools.send_html(str(wrong))
        assert "no file" in tools.send_html(str(tmp_path / "gone.html"))

    def test_in_the_terminal_a_page_opens_in_the_browser(
        self, tmp_path, monkeypatch
    ):
        page = tmp_path / "site.html"
        page.write_text("<h1>hi</h1>")
        opened = []
        monkeypatch.setattr(tools, "_open_with_spinner",
                            lambda path: opened.append(path) or "")

        reply = tools.send_html(str(page))

        assert opened == [page]
        assert "opened in the user's browser" in reply

    def test_in_the_web_ui_a_page_goes_to_the_page(self, tmp_path):
        page = tmp_path / "site.html"
        page.write_text("<h1>hi</h1>")
        FakeClient.scripts = [
            [part(calls=[call("send_html", path=str(page))]),
             part(done=True)],
            [part("There it is."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "make me a page")

        shown = [e for e in chat.log if e["type"] == "file"]
        assert [(f["name"], f["kind"]) for f in shown] == [
            ("site.html", "html"),
        ]

    def test_a_page_is_served_sandboxed(self, server, tmp_path):
        page = tmp_path / "site.html"
        page.write_text("<script>fetch('/api/state')</script>")
        kept = web.workspace.keep_file(str(page))

        status, _ = request(server, "GET", f"/api/files/{kept['id']}")
        headers = request.last

        assert status == 200
        assert headers.getheader("Content-Type") == "text/html"
        policy = headers.getheader("Content-Security-Policy")
        assert policy.startswith("sandbox")
        assert "allow-same-origin" not in policy

    def test_in_the_web_ui_a_3d_model_goes_to_the_page(
        self, server, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tools, "model_sees_images", lambda *_: False)
        opened = []
        monkeypatch.setattr(tools, "_open_with_spinner",
                            lambda path: opened.append(path) or "")
        target = tmp_path / "chair.glb"
        FakeClient.scripts = [
            [part(calls=[call("make_3d_model", path=str(target), parts=[
                {"shape": "box", "size": [0.5, 0.05, 0.5],
                 "position": [0, 0.45, 0]},
            ])]), part(done=True)],
            [part("There it is."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "make me a chair")

        shown = [e for e in chat.log if e["type"] == "file"]
        assert [(f["name"], f["kind"], f["mime"]) for f in shown] == [
            ("chair.glb", "model", "model/gltf-binary"),
        ]
        assert opened == []
        status, body = request(server, "GET", f"/api/files/{shown[0]['id']}")
        assert status == 200
        assert body == target.read_bytes()
        assert request.last.getheader("Content-Type") == "model/gltf-binary"

    def test_send_document_checks_what_it_is_given(self, tmp_path):
        wrong = tmp_path / "page.html"
        wrong.write_text("<p>x</p>")
        binary = tmp_path / "notes.txt"
        binary.write_bytes(b"\xff\xfe\x00junk")

        assert "not a .md or .txt file" in tools.send_document(str(wrong))
        assert "not UTF-8 text" in tools.send_document(str(binary))
        assert "no file" in tools.send_document(str(tmp_path / "gone.md"))

    def test_in_the_terminal_a_document_opens_in_its_app(
        self, tmp_path, monkeypatch
    ):
        doc = tmp_path / "plan.md"
        doc.write_text("# Plan")
        opened = []
        monkeypatch.setattr(tools, "_open_with_spinner",
                            lambda path: opened.append(path) or "")

        reply = tools.send_document(str(doc))

        assert opened == [doc]
        assert "default app" in reply

    def test_in_the_web_ui_a_document_goes_to_the_page(self, tmp_path):
        doc = tmp_path / "plan.md"
        doc.write_text("# Plan\n\nShip it.")
        FakeClient.scripts = [
            [part(calls=[call("send_document", path=str(doc))]),
             part(done=True)],
            [part("There it is."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "write me a plan")

        shown = [e for e in chat.log if e["type"] == "file"]
        assert [(f["name"], f["kind"]) for f in shown] == [("plan.md", "doc")]
        assert shown[0]["path"] == str(doc.resolve())
        tool_said = [m for m in chat.messages if m.get("role") == "tool"]
        assert "comments come to you" in tool_said[0]["content"]

    def test_flash_can_comment_on_a_document_it_sends(self, tmp_path):
        doc = tmp_path / "plan.md"
        doc.write_text("# Plan\n\n**Budget**: TBD\n\n- Record the demo\n")
        comments = [
            # Quoted with its Markdown marks, or as it reads: both are in.
            {"quote": "**Budget**: TBD", "note": "Needs a number."},
            {"quote": "Record the demo", "note": "Not done yet."},
            {"quote": "Hire a designer", "note": "Not in the file."},
            {"quote": "", "note": "A note on the whole document."},
            {"quote": "no note here"},
        ]
        FakeClient.scripts = [
            [part(calls=[call("send_document", path=str(doc),
                              comments=comments)]),
             part(done=True)],
            [part("There it is."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "write me a plan")

        shown = [e for e in chat.log if e["type"] == "file"][0]
        assert [c["note"] for c in shown["comments"]] == [
            "Needs a number.", "Not done yet.", "Not in the file.",
            "A note on the whole document.",
        ]
        tool_said = [m for m in chat.messages if m.get("role") == "tool"]
        said = tool_said[0]["content"]
        assert "Your 4 comments show" in said
        assert 'comment 3 quotes "Hire a designer"' in said
        assert "Record the demo" not in said

    def test_comments_as_a_json_string_still_arrive(self, tmp_path):
        doc = tmp_path / "plan.md"
        doc.write_text("Ship it.")
        from flash.theme import capture_tool_output
        sent = []
        with capture_tool_output(
            lambda kind, text, style: sent.append((kind, text))
        ):
            tools.send_document(
                str(doc), comments='[{"quote": "Ship it", "comment": "When?"}]'
            )

        shown = json.loads([t for k, t in sent if k == "document"][0])
        assert shown["comments"] == [{"quote": "Ship it", "note": "When?"}]

    def test_in_the_terminal_comments_print_under_the_document(
        self, tmp_path, monkeypatch, capsys
    ):
        doc = tmp_path / "plan.md"
        doc.write_text("Ship it.")
        monkeypatch.setattr(tools, "_open_with_spinner", lambda path: "")

        reply = tools.send_document(
            str(doc), comments=[{"quote": "Ship it", "note": "When?"}]
        )

        assert '"Ship it": When?' in capsys.readouterr().out
        assert "comments printed under it" in reply

    def test_a_document_is_served_as_text(self, server, tmp_path):
        doc = tmp_path / "plan.md"
        doc.write_text("# Plan <script>x</script>")
        kept = web.workspace.keep_file(str(doc))

        status, body = request(server, "GET", f"/api/files/{kept['id']}")

        assert status == 200 and body == b"# Plan <script>x</script>"
        assert request.last.getheader("Content-Type").startswith(
            "text/plain"
        )
        assert request.last.getheader("X-Content-Type-Options") == "nosniff"

    def test_an_edit_saves_to_the_file_and_is_told_next_turn(
        self, tmp_path,
    ):
        doc = tmp_path / "plan.md"
        doc.write_text("# Plan")
        kept = web.workspace.keep_file(str(doc))
        FakeClient.scripts = [[part("Noted."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()

        saved = web.command(session, {
            "name": "document-save", "arg": kept["id"], "chat": chat.id,
            "text": "# Plan\n\nMine now.",
        })
        run(session, chat, "what changed?")

        assert saved == {"size": 17, "path": str(doc.resolve())}
        assert doc.read_text() == "# Plan\n\nMine now."
        assert web.workspace.kept_file(kept["id"])[0].read_text() == (
            "# Plan\n\nMine now."
        )
        told = FakeClient.requests[-1]["messages"][-1]["content"]
        assert f"edited {doc.resolve()}" in told
        assert told.endswith("what changed?")
        # Told once, not on every turn after.
        assert session.edited == {}

    def test_a_copy_outlives_its_file(self, tmp_path):
        doc = tmp_path / "scratch.md"
        doc.write_text("draft")
        kept = web.workspace.keep_file(str(doc))
        doc.unlink()

        saved = web.workspace.save_document(kept["id"], "kept anyway")

        assert saved == {"size": 11, "path": ""}
        assert web.workspace.kept_file(kept["id"])[0].read_text() == (
            "kept anyway"
        )

    def test_only_documents_are_saved(self, tmp_path):
        page = tmp_path / "site.html"
        page.write_text("<p>x</p>")
        kept = web.workspace.keep_file(str(page))

        with pytest.raises(web.workspace.WorkspaceError):
            web.workspace.save_document(kept["id"], "<script>")
        with pytest.raises(web.workspace.WorkspaceError):
            web.workspace.save_document("../../etc", "x")

    def test_an_svg_is_never_kept(self, tmp_path):
        page = tmp_path / "page.svg"
        page.write_text("<svg onload='alert(1)'/>")

        with pytest.raises(web.workspace.WorkspaceError):
            web.workspace.keep_file(str(page))

    def test_served_with_the_token_as_what_it_is(self, server, tmp_path):
        doc = tmp_path / "report.pdf"
        doc.write_bytes(PDF)
        kept = web.workspace.keep_file(str(doc))

        status, body = request(server, "GET", f"/api/files/{kept['id']}")
        headers = request.last

        assert status == 200 and body == PDF
        assert headers.getheader("Content-Type") == "application/pdf"
        assert headers.getheader("X-Content-Type-Options") == "nosniff"
        assert headers.getheader("Content-Disposition") == "inline"

        request(server, "GET", f"/api/files/{kept['id']}?download=1")
        assert request.last.getheader("Content-Disposition") == "attachment"

    def test_not_without_it_and_not_by_path(self, server, tmp_path):
        doc = tmp_path / "report.pdf"
        doc.write_bytes(PDF)
        kept = web.workspace.keep_file(str(doc))

        assert request(server, "GET", f"/api/files/{kept['id']}",
                       token=False)[0] == 403
        assert request(server, "GET", "/api/files/../../chats")[0] == 404
        assert request(server, "GET", "/api/files/0123456789abcdef")[0] == 404


def test_the_qr_code_scales_to_its_box():
    svg = web.qr_svg("http://192.168.1.20:7433/?token=" + "x" * 32)

    assert "viewBox=" in svg
    assert 'width="' not in svg.split(">")[0]


# --- Sub-agents in the web UI ----------------------------------------------


class SubAgentModel(FakeClient):
    """Both models. Ollama is one module, so the web turn and the
    sub-agent share a Client: the turn streams, and the sub-agent, which
    answers its task in one go, does not."""

    answer = "Found 3 Python files."

    def chat(self, **kwargs):
        if kwargs.get("stream"):
            return super().chat(**kwargs)
        return self.answer_task(**kwargs)

    def answer_task(self, **kwargs):
        return SimpleNamespace(message=SimpleNamespace(
            content=SubAgentModel.answer, tool_calls=None,
        ))


@pytest.fixture
def subagent_world(monkeypatch):
    from flash import agent as subagents

    monkeypatch.setattr(subagents, "_agents", {})
    monkeypatch.setattr(ollama, "Client", SubAgentModel)
    monkeypatch.setattr(tools, "MODEL_NAME", "flash-test")
    monkeypatch.setattr(web, "WATCH_SECONDS", 0.02)
    sessions = []
    yield sessions
    for session in sessions:
        session.close()


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


class TestSubAgents:
    def test_a_finished_sub_agent_wakes_its_chat(self, subagent_world):
        FakeClient.scripts = [
            [part(calls=[call("agent", task="count the python files")]),
             part(done=True)],
            [part("Started a sub-agent; I'll report back."), part(done=True)],
            [part("The sub-agent found 3 Python files."), part(done=True)],
        ]
        session = web.Session()
        subagent_world.append(session)
        chat = session.new_chat()

        run(session, chat, "how many python files are there?")
        wait_for(lambda: any(
            e["type"] == "assistant" and "found 3" in e["text"]
            for e in chat.log
        ))

        # The turn that started it finished cleanly, stats and all.
        assert "error" not in types(chat.log)
        assert types(chat.log).count("stats") == 2
        notes = [e["text"] for e in chat.log if e["type"] == "note"]
        assert notes == [web.WAKE_TEXT]
        # The wake turn told the model what the sub-agent found.
        woke = FakeClient.requests[-1]["messages"][-1]["content"]
        assert "finished" in woke and SubAgentModel.answer in woke
        # Nobody typed the wake, so it is not shown as a message.
        assert [e["text"] for e in chat.log if e["type"] == "user"] == [
            "how many python files are there?"
        ]

    def test_news_stays_in_the_chat_that_asked(
        self, subagent_world, monkeypatch
    ):
        from flash import agent as subagents

        FakeClient.scripts = [
            [part(calls=[call("agent", task="slow task")]), part(done=True)],
            [part("Started."), part(done=True)],
            [part("Other chat reply."), part(done=True)],
        ]
        session = web.Session()
        subagent_world.append(session)
        # Held running, so nothing wakes and both chats stay quiet.
        release = threading.Event()
        monkeypatch.setattr(SubAgentModel, "answer_task", lambda self, **kw: (
            release.wait(5) and None
        ) or SimpleNamespace(message=SimpleNamespace(
            content="done", tool_calls=None)))
        try:
            first = session.new_chat()
            run(session, first, "start one")
            other = session.new_chat()
            run(session, other, "unrelated question")

            sent = FakeClient.requests[-1]["messages"][-1]["content"]
            assert "Sub-agent" not in sent
            assert session.owned(first.id) and not session.owned(other.id)
            assert session.agent_list(first.id)[0]["status"] == "running"
            assert session.agent_list(other.id) == []
        finally:
            release.set()
            wait_for(lambda: subagents.running_count() == 0)

    def test_waking_stops_after_three_in_a_row(
        self, subagent_world, monkeypatch
    ):
        from flash import agent as subagents

        # Never marked as seen, so the watcher keeps finding it.
        monkeypatch.setattr(subagents, "mark_delivered", lambda ids: None)
        FakeClient.scripts = [
            [part(calls=[call("agent", task="t")]), part(done=True)],
            [part("Started."), part(done=True)],
        ] + [[part(f"Report {n}."), part(done=True)] for n in range(6)]
        session = web.Session()
        subagent_world.append(session)
        chat = session.new_chat()

        run(session, chat, "go")
        wait_for(lambda: sum(e["type"] == "note" for e in chat.log) >= 3)
        time.sleep(0.5)

        assert sum(e["type"] == "note" for e in chat.log) == 3

    def test_agent_result_waits_without_drawing_in_the_terminal(
        self, subagent_world, monkeypatch
    ):
        from flash import agent as subagents
        from flash.theme import capture_tool_output

        def no_terminal(*args, **kwargs):
            raise AssertionError("drew a live view in the terminal")

        monkeypatch.setattr(subagents, "_live", no_terminal)
        agent_id = subagents.start("quick task")

        with capture_tool_output(lambda *event: None):
            answer = tools.agent_result(agent_id, 5)

        assert answer == SubAgentModel.answer

    def test_the_page_can_list_them(self, server, subagent_world):
        chat = server.session.new_chat()
        from flash import agent as subagents

        agent_id = subagents.start("list things")
        server.session.adopt(chat, {agent_id})
        subagent_world.append(server.session)
        wait_for(lambda: subagents.running_count() == 0)

        status, body = request(server, "GET", f"/api/agents?chat={chat.id}")

        assert status == 200
        listed = json.loads(body)["agents"]
        assert listed[0]["id"] == agent_id
        assert listed[0]["result"] == SubAgentModel.answer
        assert request(server, "GET", f"/api/agents?chat={chat.id}",
                       token=False)[0] == 403


class TestPlans:
    def test_a_plan_is_drawn_on_the_page_not_the_terminal(
        self, monkeypatch
    ):
        from flash import plan, theme

        terminal = []
        monkeypatch.setattr(
            theme.console, "print", lambda *a, **k: terminal.append(a)
        )
        monkeypatch.setattr(plan, "_steps", [])
        FakeClient.scripts = [
            [part(calls=[call("plan", steps=["Read it", "Fix it"])]),
             part(done=True)],
            [part(calls=[call("check_step", index=1)]), part(done=True)],
            [part("Planned."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "fix the bug")

        assert terminal == []
        drawn = [e["steps"] for e in chat.log if e["type"] == "plan"]
        assert drawn == [
            [{"text": "Read it", "status": "active"},
             {"text": "Fix it", "status": "todo"}],
            [{"text": "Read it", "status": "done"},
             {"text": "Fix it", "status": "active"}],
        ]
        assert "plan" in web.KEPT


# --- Settings: usage and memory ---------------------------------------------


def logged(*entries, updated=0.0):
    return SimpleNamespace(log=list(entries), updated=updated)


def at(day, hour=12):
    from datetime import datetime, timezone

    # Noon UTC is the same date almost everywhere a test runs.
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc).timestamp()


class TestUsage:
    def test_turns_are_counted_by_day_and_model(self):
        from datetime import date

        chats = [
            logged(
                {"type": "user", "text": "a"},
                {"type": "stats", "at": at(24), "model": "onyx",
                 "tokens": 100, "rate": 10, "seconds": 12, "tools": 2},
                {"type": "user", "text": "b"},
                {"type": "stats", "at": at(25), "model": "onyx",
                 "tokens": 50, "rate": 25, "seconds": 3, "tools": 0},
            ),
            logged(
                {"type": "user", "text": "c"},
                {"type": "stats", "at": at(26), "model": "qwen",
                 "tokens": 0, "rate": 0, "seconds": 1, "tools": 1},
            ),
            logged(),
        ]

        u = web.usage(chats, today=date(2026, 9, 26))

        assert (u["chats"], u["messages"], u["turns"]) == (2, 3, 3)
        assert (u["tokens"], u["tools"], u["seconds"]) == (150, 3, 16)
        # 150 tokens over 10s + 2s of generating.
        assert u["rate"] == 12.5
        assert u["days"] == {
            "2026-09-24": 1, "2026-09-25": 1, "2026-09-26": 1,
        }
        assert u["models"] == [("onyx", 2), ("qwen", 1)]
        assert (u["streak"], u["longest_streak"], u["active_days"]) == (
            3, 3, 3,
        )

    def test_an_older_turn_counts_on_its_chats_last_day(self):
        from datetime import date

        chat = logged({"type": "stats", "tokens": 5}, updated=at(20))

        u = web.usage([chat], today=date(2026, 9, 26))

        assert u["days"] == {"2026-09-20": 1}
        assert u["models"] == []
        assert u["streak"] == 0 and u["longest_streak"] == 1

    def test_a_streak_survives_until_a_whole_day_is_missed(self):
        from datetime import date

        days = {date(2026, 9, d) for d in (1, 2, 3, 20, 21, 22, 23, 25)}

        assert web._streaks(days, date(2026, 9, 26)) == (1, 4)
        assert web._streaks(days, date(2026, 9, 25)) == (1, 4)
        assert web._streaks(days, date(2026, 9, 27)) == (0, 4)

    def test_a_turn_records_when_and_on_what(self):
        FakeClient.scripts = [[part("Hi."), part(done=True, tokens=3)]]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "hello")

        stats = next(e for e in chat.log if e["type"] == "stats")
        assert stats["model"] == "flash-test"
        assert abs(stats["at"] - time.time()) < 60
        assert web.command(session, {"name": "usage"})["turns"] == 1


class TestMemoryCommands:
    REPLY = "```\n- Likes tea\n- Uses Vim\n```"

    def test_preview_saves_nothing(self):
        from flash import memory

        memory.add_memory("Uses Vim")

        shown = web.command(web.Session(), {
            "name": "memory-preview", "text": self.REPLY,
        })

        assert shown == {"new": ["Likes tea"], "known": 1}
        assert memory.list_memory() == ["Uses Vim"]

    def test_import_saves_and_reaches_the_next_prompt(self):
        from flash import learning

        session = web.Session()
        assert "Likes tea" not in learning.prompt_block()

        result = web.command(session, {
            "name": "memory-import", "text": self.REPLY,
        })

        assert result["added"] == ["Likes tea", "Uses Vim"]
        assert result["entries"] == ["Likes tea", "Uses Vim"]
        assert "Likes tea" in learning.prompt_block()

    def test_listing_and_forgetting(self):
        from flash import memory

        session = web.Session()
        web.command(session, {"name": "memory-import", "text": self.REPLY})

        listed = web.command(session, {"name": "memory"})
        assert listed["entries"] == ["Likes tea", "Uses Vim"]
        assert listed["prompt"] == memory.IMPORT_PROMPT

        left = web.command(session, {"name": "memory-forget", "arg": "1"})
        assert left["entries"] == ["Uses Vim"]
        with pytest.raises(ValueError, match="No memory at index 5"):
            web.command(session, {"name": "memory-forget", "arg": "5"})

    def test_adding_and_editing(self):
        from flash import learning

        session = web.Session()

        added = web.command(session, {
            "name": "memory-add", "text": "Uses\n npm",
        })
        assert added == {"entries": ["Uses npm"], "text": "Uses npm"}

        edited = web.command(session, {
            "name": "memory-edit", "arg": "1", "text": "Uses pnpm",
        })
        assert edited["entries"] == ["Uses pnpm"]
        assert "Uses pnpm" in learning.prompt_block()

        with pytest.raises(ValueError, match="No memory at index 3"):
            web.command(session, {
                "name": "memory-edit", "arg": "3", "text": "x",
            })
        with pytest.raises(ValueError, match="Nothing to remember"):
            web.command(session, {"name": "memory-add", "text": " "})

    def test_over_http_it_needs_the_token(self, server):
        status, body = request(server, "POST", "/api/command",
                               {"name": "memory-import", "text": self.REPLY},
                               token=False)

        assert status == 403
        from flash import memory
        assert memory.list_memory() == []


class TestCompactSetting:
    def test_off_by_default_and_switched_from_settings(self, monkeypatch):
        saved = {}

        def save(name, value):
            saved[name] = value
            ai.Config.auto_compact = value == "1"

        # Put back whatever the rest of the suite had, once this is done.
        monkeypatch.setattr(ai.Config, "auto_compact", ai.Config.auto_compact)
        monkeypatch.delenv("AUTO_COMPACT", raising=False)
        ai.Config.refresh()
        assert ai.Config.auto_compact is False
        assert web.status(ai)["compact"] is False

        monkeypatch.setattr(ai, "set_config_var", save)
        result = web.command(web.Session(), {
            "name": "compact-setting", "arg": "on",
        })

        assert result == {"compact": True}
        assert saved == {"AUTO_COMPACT": "1"}
        assert web.status(ai)["compact"] is True


class TestUpdates:
    @pytest.fixture
    def fake_updater(self, monkeypatch):
        from flash import updater

        state = {"latest": "9.9.9", "ok": True}

        def install(on_step=None, on_output=None):
            on_step("Downloading the latest version")
            on_output("Cloning into '/tmp/flash-update'...")
            on_step("Installing")
            on_output("installed package flash 9.9.9")
            if state["ok"]:
                return True, "Flash updated. Restart flash to use it."
            return False, "Could not install the update (exit code 1)."

        monkeypatch.setattr(updater, "fetch_latest_version",
                            lambda: state["latest"])
        monkeypatch.setattr(updater, "perform_update", install)
        return state

    def test_a_check_finds_a_newer_version(self, fake_updater):
        session = web.Session()

        found = web.command(session, {"name": "update-check"})

        assert found["available"] is True
        assert found["latest"] == "9.9.9"
        assert found["current"] == web.__version__
        assert session.state()["status"]["update"]["available"] is True

    def test_nothing_newer_is_not_an_update(self, fake_updater):
        fake_updater["latest"] = web.__version__
        found = web.command(web.Session(), {"name": "update-check"})

        assert found["available"] is False and found["checked"] is True

    def test_a_failed_check_says_so(self, fake_updater):
        fake_updater["latest"] = None
        found = web.command(web.Session(), {"name": "update-check"})

        assert found["checked"] is False
        assert "Could not check" in found["message"]

    def test_installing_streams_its_progress(self, fake_updater):
        session = web.Session()
        seen = events_of(session)

        web.command(session, {"name": "update"})
        wait_for(lambda: session.updates.state == "updated")

        snapshot = session.updates.snapshot()
        assert snapshot["log"] == [
            "Cloning into '/tmp/flash-update'...",
            "installed package flash 9.9.9",
        ]
        assert "Restart" in snapshot["message"]
        steps = {
            e["update"]["step"] for e in seen() if e["type"] == "update"
        }
        assert {"Downloading the latest version", "Installing"} <= steps

    def test_a_failed_install_keeps_the_reason(self, fake_updater):
        fake_updater["ok"] = False
        session = web.Session()

        web.command(session, {"name": "update"})
        wait_for(lambda: session.updates.state == "failed")

        assert "exit code 1" in session.updates.snapshot()["message"]

    def test_restart_needs_a_server_that_owns_its_process(self):
        with pytest.raises(ValueError, match="terminal session"):
            web.command(web.Session(), {"name": "update-restart"})

    def test_restart_waits_for_a_reply_to_finish(self):
        session = web.Session()
        session.restart = lambda: None
        session.new_chat().busy = True

        with pytest.raises(ValueError, match="Wait for the reply"):
            web.command(session, {"name": "update-restart"})

    def test_restart_runs_once_the_page_has_its_answer(self, monkeypatch):
        monkeypatch.setattr(web, "RESTART_DELAY", 0.01)
        session = web.Session()
        restarted = threading.Event()
        session.restart = restarted.set

        result = web.command(session, {"name": "update-restart"})

        assert result["state"] == "restarting"
        assert restarted.wait(2)

    def test_the_page_can_restart_the_server_with_a_new_link(
        self, monkeypatch
    ):
        monkeypatch.setattr(web, "RESTART_DELAY", 0.01)
        session = web.Session()
        key = session.access.sign_in("127.0.0.1", "test")
        old = session.access.token
        why = []
        restarted = threading.Event()
        session.restart = lambda *said: (why.extend(said), restarted.set())

        result = web.command(session, {"name": "server-restart"})

        assert restarted.wait(2)
        assert result["token"] == session.access.token != old
        assert not session.access.token_matches(old)
        assert "new link" in why[0]
        # This browser stays signed in through it.
        assert session.access.browser(key, "127.0.0.1") is not None

    def test_a_server_restart_is_refused_when_it_cannot_be_done(self):
        with pytest.raises(ValueError, match="terminal session"):
            web.command(web.Session(), {"name": "server-restart"})
        session = web.Session()
        session.restart = lambda *said: None
        old = session.access.token
        session.new_chat().busy = True

        with pytest.raises(ValueError, match="Wait for the reply"):
            web.command(session, {"name": "server-restart"})
        # Refused, so the link still works.
        assert session.access.token == old

    def test_a_restarted_server_keeps_its_token(self, monkeypatch):
        monkeypatch.setenv(web.TOKEN_ENV, "kept-token-from-before-1234")
        server = web.Server(0)
        try:
            assert server.token == "kept-token-from-before-1234"
            assert web.TOKEN_ENV not in os.environ
        finally:
            server.server_close()

    def test_restart_execs_flash_again_quietly(self, monkeypatch):
        ran = {}
        monkeypatch.setattr(web.os, "execv",
                            lambda path, argv: ran.update(argv=argv))
        monkeypatch.setattr(web.sys, "argv", ["flash", "--web"])
        server = web.Server(0)
        try:
            web._restart(server)
        finally:
            server.server_close()

        assert ran["argv"][1:] == ["-m", "flash", "--web", "--no-open"]
        assert os.environ.pop(web.TOKEN_ENV) == server.token


def test_the_loader_words_are_the_terminals():
    words = web.Session().state()["words"]

    assert words == [s["now"] for s in ai._load_thinking_states()]
    assert "Pondering" in words


class TestSkillsInThePage:
    def test_who_wrote_each_skill(self):
        from flash import skills

        tools.skill_manage_tool(
            "create", "by-the-model", description="Made in a chat",
            content="1. Step",
        )
        web.command(web.Session(), {
            "name": "skill-create", "arg": "by-the-user",
            "description": "Made in the page", "content": "1. Step",
        })

        listed = {
            s["name"]: s["by"]
            for s in web.command(web.Session(), {"name": "skills"})["skills"]
        }

        assert listed == {"by-the-model": "flash", "by-the-user": "you"}
        # Kept through an edit.
        web.command(web.Session(), {
            "name": "skill-save", "arg": "by-the-model",
            "description": "Still the model's", "content": "1. New step",
        })
        assert skills.find("by-the-model").by == "flash"

    def test_an_older_skill_the_review_wrote_is_flash_s(self):
        from flash import skills

        folder = skills.skills_dir() / "older"
        folder.mkdir(parents=True)
        (folder / skills.SKILL_FILE).write_text(
            "---\nname: older\ndescription: d\nmanaged: true\n---\n\nx\n"
        )
        (skills.skills_dir() / "hand").mkdir()
        (skills.skills_dir() / "hand" / skills.SKILL_FILE).write_text(
            "---\nname: hand\ndescription: d\n---\n\nx\n"
        )

        assert skills.find("older").by == "flash"
        assert skills.find("hand").by == ""

    def test_view_and_delete(self):
        session = web.Session()
        web.command(session, {
            "name": "skill-create", "arg": "deploy-site",
            "description": "Deploying the site", "content": "1. Push",
        })

        shown = web.command(session, {"name": "skill", "arg": "deploy-site"})
        assert shown["content"] == "1. Push"

        web.command(session, {"name": "skill-delete", "arg": "deploy-site"})
        with pytest.raises(ValueError, match="No skill"):
            web.command(session, {"name": "skill", "arg": "deploy-site"})

    def test_a_bad_skill_says_why(self):
        with pytest.raises(ValueError, match="description"):
            web.command(web.Session(), {
                "name": "skill-create", "arg": "no-description",
                "content": "1. Step",
            })


class TestExtensionsInThePage:
    def make(self, folder, name="weather"):
        folder.mkdir()
        (folder / "flash-extension.json").write_text(json.dumps({
            "name": name,
            "description": "Weather lookups",
            "version": "0.1.0",
            "commands": [{"name": "forecast", "prompt": "forecast.md"}],
        }))
        (folder / "forecast.md").write_text("Forecast for $ARGUMENTS")
        return folder

    def test_shown_first_and_installed_on_yes(self, tmp_path):
        from flash import extensions

        source = self.make(tmp_path / "weather")
        session = web.Session()

        preview = web.command(session, {
            "name": "extension-preview", "arg": f"path@{source}",
        })

        assert preview["name"] == "weather"
        assert preview["commands"] == ["/forecast"]
        assert preview["update"] is False
        assert extensions.find("weather") is None

        web.command(session, {
            "name": "extension-install", "arg": preview["id"],
        })

        listed = web.command(session, {"name": "extensions"})
        assert [e["name"] for e in listed["extensions"]] == ["weather"]
        assert session.staged is None

    def test_a_stale_preview_installs_nothing(self, tmp_path):
        from flash import extensions

        source = self.make(tmp_path / "weather")
        session = web.Session()
        web.command(session, {
            "name": "extension-preview", "arg": f"path@{source}",
        })

        with pytest.raises(ValueError, match="Check the extension again"):
            web.command(session, {
                "name": "extension-install", "arg": "not-the-id",
            })
        assert extensions.find("weather") is None

    def test_a_clash_is_refused_before_anything_installs(self, tmp_path):
        folder = self.make(tmp_path / "clash")
        manifest = json.loads((folder / "flash-extension.json").read_text())
        manifest["commands"][0]["name"] = "help"
        (folder / "flash-extension.json").write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="built-in command"):
            web.command(web.Session(), {
                "name": "extension-preview", "arg": f"path@{folder}",
            })

    def test_remove(self, tmp_path):
        source = self.make(tmp_path / "weather")
        session = web.Session()
        preview = web.command(session, {
            "name": "extension-preview", "arg": f"path@{source}",
        })
        web.command(session, {
            "name": "extension-install", "arg": preview["id"],
        })

        web.command(session, {"name": "extension-remove", "arg": "weather"})

        assert web.command(session, {"name": "extensions"})[
            "extensions"
        ] == []
        with pytest.raises(ValueError, match="No extension"):
            web.command(session, {
                "name": "extension-remove", "arg": "weather",
            })


class TestRemovingHosts:
    def test_the_host_in_use_cannot_be_removed(self, monkeypatch):
        web.workspace.add_host("Studio", "10.0.0.5")
        monkeypatch.setattr(ai.Config, "host", "http://10.0.0.5:11434")

        with pytest.raises(ValueError, match="in use"):
            web.command(web.Session(), {
                "name": "host-remove", "arg": "http://10.0.0.5:11434",
            })

    def test_another_saved_host_can(self, monkeypatch):
        web.workspace.add_host("Studio", "10.0.0.5")
        monkeypatch.setattr(ai.Config, "host", "http://127.0.0.1:11434")

        result = web.command(web.Session(), {
            "name": "host-remove", "arg": "http://10.0.0.5:11434",
        })

        assert result == {"removed": True}
        listed = web.command(web.Session(), {"name": "hosts"})
        assert [h["name"] for h in listed["hosts"]] == ["This computer"]
        # 127.0.0.1 is this computer, and the page is told so.
        assert listed["current"] == web.workspace.LOCAL_HOST

    def test_status_names_a_loopback_host_this_computer(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "host", "http://127.0.0.1:11434")

        shown = web.status(ai)

        assert shown["host_name"] == "This computer"
        assert shown["host_url"] == web.workspace.LOCAL_HOST


class TestQueueAndSteer:
    def held(self, gate, *parts):
        """A reply that waits on GATE, so a turn stays running."""

        def script():
            gate.wait(5)
            yield from parts

        return script

    def settle(self, session, chat):
        wait_for(lambda: not (chat.busy or chat.queued or chat.pending))

    def test_queued_messages_run_in_order_after_the_turn(self):
        gate = threading.Event()
        FakeClient.scripts = [
            self.held(gate, part("First answer."), part(done=True)),
            [part("Second answer."), part(done=True)],
            [part("Third answer."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "one")
        wait_for(lambda: chat.busy)
        assert session.send(chat, "two")["pending"]
        session.send(chat, "three")

        # Waiting, not yet in the conversation.
        assert [p["text"] for p in chat.pending] == ["two", "three"]
        assert [e["text"] for e in chat.log if e["type"] == "user"] == [
            "one"
        ]

        gate.set()
        self.settle(session, chat)

        said = [
            (e["type"], e["text"]) for e in chat.log
            if e["type"] in ("user", "assistant")
        ]
        assert said == [
            ("user", "one"), ("assistant", "First answer."),
            ("user", "two"), ("assistant", "Second answer."),
            ("user", "three"), ("assistant", "Third answer."),
        ]

    def test_a_steer_reaches_the_model_at_the_next_step(self):
        gate = threading.Event()
        FakeClient.scripts = [
            self.held(gate, part(calls=[call("get_os")]), part(done=True)),
            [part("Using Rust instead."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "write it in python")
        wait_for(lambda: chat.busy)
        session.send(chat, "actually, use rust", mode="steer")
        gate.set()
        self.settle(session, chat)

        second = FakeClient.requests[1]["messages"][-1]
        assert second["role"] == "user"
        assert second["content"].endswith("actually, use rust")
        assert web.STEER_NOTE in second["content"]
        steered = [e for e in chat.log if e.get("steer")]
        assert [e["text"] for e in steered] == ["actually, use rust"]
        # Shown after the tool it followed, before the answer to it.
        kinds = [e["type"] for e in chat.log]
        answer = next(
            i for i, e in enumerate(chat.log)
            if e["type"] == "assistant" and e["text"]
        )
        assert kinds.index("result") < chat.log.index(steered[0]) < answer
        # It was part of this turn, not a turn of its own.
        assert len(FakeClient.requests) == 2

    def test_a_steer_the_turn_never_reached_runs_next(self):
        gate = threading.Event()
        FakeClient.scripts = [
            self.held(gate, part("Done already."), part(done=True)),
            [part("Adding the tests."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "fix it")
        wait_for(lambda: chat.busy)
        session.send(chat, "and add tests", mode="steer")
        gate.set()
        self.settle(session, chat)

        assert [e["text"] for e in chat.log if e["type"] == "user"] == [
            "fix it", "and add tests",
        ]
        assert chat.log[-2]["text"] == "Adding the tests."

    def test_stopping_hands_waiting_messages_back(self):
        gate = threading.Event()
        FakeClient.scripts = [
            self.held(gate, part("Working."), part(done=True)),
        ]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "go")
        wait_for(lambda: chat.busy)
        session.send(chat, "then this")
        result = web.command(session, {"name": "stop", "chat": chat.id})
        gate.set()
        wait_for(lambda: not chat.busy)
        time.sleep(0.1)

        assert result == {"restored": ["then this"], "files": []}
        assert chat.pending == []
        assert len(FakeClient.requests) == 1

    def test_a_waiting_message_can_change_mode_or_go(self):
        gate = threading.Event()
        FakeClient.scripts = [
            self.held(gate, part("Working."), part(done=True)),
        ]
        session = web.Session()
        chat = session.new_chat()
        session.send(chat, "go")
        wait_for(lambda: chat.busy)
        first = session.send(chat, "a")["pending"]
        second = session.send(chat, "b")["pending"]

        web.command(session, {"name": "pending-mode", "chat": chat.id,
                              "arg": first, "mode": "steer"})
        left = web.command(session, {"name": "pending-remove",
                                     "chat": chat.id, "arg": second})

        assert left["pending"] == [
            {"id": first, "text": "a", "mode": "steer", "files": []}
        ]
        with pytest.raises(ValueError, match="already been sent"):
            web.command(session, {"name": "pending-remove",
                                  "chat": chat.id, "arg": second})
        FakeClient.scripts.append([part("Steered."), part(done=True)])
        gate.set()
        self.settle(session, chat)


class TestSwitchingLan:
    def test_the_server_reopens_with_the_same_port_token_and_chats(
        self, monkeypatch
    ):
        # The network side on loopback: a test never opens a real port.
        monkeypatch.setattr(web, "LAN_HOST", "127.0.0.1")
        monkeypatch.setattr(web, "SWITCH_DELAY", 0.01)
        monkeypatch.setattr(web, "announce", lambda server: None)
        first = web.Server(0)
        chat = first.session.new_chat()
        said(first.session, "Kept across the switch", "still here")
        web._attach(first, standalone=True)
        running = []
        threading.Thread(
            target=web._serve, args=(first, True, running), daemon=True,
        ).start()
        wait_for(lambda: running)

        try:
            result = web.command(first.session, {"name": "lan", "arg": "on"})
            wait_for(lambda: running[0] is not first)
            now = running[0]

            assert result == {"lan": True, "switching": True}
            assert now.lan is True and now.session.lan is True
            assert now.port == first.port
            assert now.token == first.token
            assert now.session is first.session
            status, body = request(now, "GET", "/api/state?lite=1")
            assert status == 200
            titles = [c["title"] for c in json.loads(body)["chats"]]
            assert "Kept across the switch" in titles
            assert json.loads(body)["status"]["lan"] is True
            assert chat.id in now.session.chats
            # Asking for what it already is changes nothing.
            again = web.command(now.session, {"name": "lan", "arg": "on"})
            assert again == {"lan": True, "switching": False}
        finally:
            running[0].shutdown()
            running[0].server_close()

    def test_only_a_server_that_can_reopen_offers_it(self):
        with pytest.raises(ValueError, match="cannot reopen"):
            web.command(web.Session(), {"name": "lan", "arg": "on"})

    def test_a_restart_keeps_the_current_choice(self, monkeypatch):
        ran = {}
        monkeypatch.setattr(web.os, "execv",
                            lambda path, argv: ran.update(argv=argv))
        monkeypatch.setattr(web.sys, "argv", ["flash", "--web"])
        monkeypatch.setattr(web, "LAN_HOST", "127.0.0.1")
        server = web.Server(0, lan=True)
        try:
            web._restart(server)
        finally:
            server.server_close()
            os.environ.pop(web.TOKEN_ENV, None)

        assert ran["argv"][3:] == ["--web", "--lan", "--no-open"]


class TestSlowHosts:
    def test_a_host_that_never_answers_is_not_waited_on(self, monkeypatch):
        release = threading.Event()

        class Silent:
            def __init__(self, host=None):
                pass

            def list(self):
                release.wait(5)
                return SimpleNamespace(models=[])

        monkeypatch.setattr(ollama, "Client", Silent)
        monkeypatch.setattr(web, "MODEL_LIST_SECONDS", 0.1)
        started = time.monotonic()
        try:
            result = web.command(web.Session(), {"name": "model"})
        finally:
            release.set()

        assert time.monotonic() - started < 1
        assert result["models"] == [] and result["reachable"] is False

    def test_a_host_that_answers_lists_its_models(self, monkeypatch):
        class Up:
            def __init__(self, host=None):
                pass

            def list(self):
                return SimpleNamespace(models=[
                    SimpleNamespace(model="b"), SimpleNamespace(model="a"),
                ])

        monkeypatch.setattr(ollama, "Client", Up)

        result = web.command(web.Session(), {"name": "model"})

        assert result["models"] == ["a", "b"] and result["reachable"]

    def test_switching_to_a_down_host_does_not_ask_it_for_models(
        self, monkeypatch
    ):
        asked = []

        class Tracked:
            def __init__(self, host=None):
                pass

            def list(self):
                asked.append(True)
                return SimpleNamespace(models=[])

        monkeypatch.setattr(ollama, "Client", Tracked)
        monkeypatch.setattr(web.workspace, "host_state", lambda url: "down")
        monkeypatch.setattr(ai, "set_config_var", lambda *a: None)
        monkeypatch.setattr(ai, "forget_model_facts", lambda: None)

        result = web.command(web.Session(), {
            "name": "host", "arg": "10.0.0.9",
        })

        assert result["up"] is False and result["models"] == []
        assert asked == []


class TestBackgrounds:
    def test_a_scene_goes_to_the_page_as_a_palette_and_pixels(self):
        from flash import background

        scene = web.scene_data("sunset")
        loaded = background.load(background.find("sunset"))

        assert scene["title"] == loaded.name
        assert (scene["width"], scene["height"]) == (
            loaded.width, loaded.height,
        )
        assert len(scene["pixels"]) == loaded.width * loaded.height
        # Every pixel names its colour through the palette, in order.
        assert scene["palette"][scene["pixels"][0]] == loaded.rows[0][0]
        assert scene["palette"][scene["pixels"][-1]] == loaded.rows[-1][-1]
        assert web.scene_data("no-such-scene") is None
        assert web.scene_data("") is None

    def test_the_bundled_scenes_are_listed(self):
        listed = web.command(web.Session(), {"name": "backgrounds"})

        names = [s["name"] for s in listed["scenes"]]
        assert {"forest", "midnight", "reef", "sunset"} <= set(names)

    def test_picking_one_is_the_terminal_setting_too(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(ai, "set_config_var",
                            lambda name, value: saved.update({name: value}))
        monkeypatch.setattr(ai, "unset_config_var",
                            lambda name: saved.update({name: None}))

        chosen = web.command(web.Session(), {
            "name": "background", "arg": "reef",
        })
        assert chosen["background"] == "reef"
        assert chosen["scene"]["name"] == "reef"
        assert saved == {"BACKGROUND": "reef"}

        off = web.command(web.Session(), {"name": "background", "arg": "off"})
        assert off == {"background": "", "scene": None}
        assert saved == {"BACKGROUND": None}

        with pytest.raises(ValueError, match="No background"):
            web.command(web.Session(), {"name": "background", "arg": "x"})

    def test_the_scene_rides_with_the_full_state_only(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "background", "forest")
        session = web.Session()

        assert session.state()["scene"]["name"] == "forest"
        assert session.state(lite=True)["scene"] is None
        assert session.state(lite=True)["status"]["background"] == "forest"


class TestAnExtensionsBackground:
    def test_removing_the_extension_takes_its_scene_away(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "scenery"
        (source / "scenes").mkdir(parents=True)
        (source / "flash-extension.json").write_text(json.dumps({
            "name": "scenery", "backgrounds": "scenes",
        }))
        (source / "scenes" / "dunes.scene").write_text(
            "name: Dunes\npalette:\n  . #c2a060\n  o #402010\npixels:\n"
            + "\n".join(["..oo" * 6] * 6) + "\n"
        )
        session = web.Session()
        preview = web.command(session, {
            "name": "extension-preview", "arg": f"path@{source}",
        })
        web.command(session, {
            "name": "extension-install", "arg": preview["id"],
        })
        monkeypatch.setattr(ai.Config, "background", "dunes")
        assert web.status(ai)["background"] == "dunes"
        assert web.scene_data("dunes")["title"] == "Dunes"
        seen = events_of(session)

        web.command(session, {
            "name": "extension-remove", "arg": "scenery",
        })

        # Open pages hear about it, and there is no scene to show now.
        assert "status" in types(seen())
        assert web.status(ai)["background"] == ""
        assert web.command(session, {"name": "background-scene"}) == {
            "scene": None,
        }


class TestAddresses:
    @pytest.mark.parametrize("path", [
        "/", "/c/0123abcd", "/p/89abcdef", "/projects", "/settings",
        "/settings/usage", "/settings/memory", "/skills", "/extensions",
        "/c/0123abcd/",
    ])
    def test_every_view_has_the_page(self, server, path):
        status, body = request(server, "GET", path)

        assert status == 200
        assert b"<!doctype html>" in body[:200].lower()
        assert "flash_" in request.last.getheader("Set-Cookie")

    @pytest.mark.parametrize("path", [
        "/c/not-an-id", "/c/0123ABCD", "/settings/secret", "/c",
        "/index.html", "/c/0123abcd/extra",
    ])
    def test_anything_else_is_not_found(self, server, path):
        assert request(server, "GET", path)[0] == 404

    def test_a_chat_address_without_the_token_is_the_expired_page(
        self, server
    ):
        status, body = request(server, "GET", "/c/0123abcd", token=False)

        assert status == 403
        assert b"<html" in body.lower()


class TestVoice:
    def test_what_the_browser_recorded_is_heard(self, server, monkeypatch):
        got = []

        def transcribe(pcm):
            got.append(pcm)
            return "stop voice", ""

        monkeypatch.setattr(web.voice, "transcribe", transcribe)
        audio = b"\x01\x02" * 800

        status, body = request(server, "POST", "/api/voice/hear", {
            "data": base64.b64encode(audio).decode(),
        })

        assert status == 200
        said = json.loads(body)
        assert said["text"] == "stop voice" and said["exit"] is True
        assert said["interrupt"] is False and said["resume"] is False
        assert got == [audio]

    def test_an_interruption_is_told_from_the_replys_own_echo(
        self, server, monkeypatch,
    ):
        monkeypatch.setattr(web.voice, "transcribe",
                            lambda pcm: ("interrupt", ""))

        def heard(speaking):
            return json.loads(request(server, "POST", "/api/voice/hear", {
                "data": "", "speaking": speaking,
            })[1])

        assert heard("Now the tests run.")["interrupt"] is True
        assert heard("Say interrupt to stop me.")["interrupt"] is False

    def test_a_turn_from_voice_asks_for_a_short_reply(self, monkeypatch):
        FakeClient.scripts = [[part("Short."), part(done=True)],
                              [part("Long."), part(done=True)]]
        seen = []
        monkeypatch.setattr(ai, "_session_system_prompt",
                            lambda heard=False: seen.append(heard) or "")
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "what time is it", heard=True)
        wait_for(lambda: not chat.busy and not chat.queued)
        session.send(chat, "and now in writing")
        wait_for(lambda: not chat.busy and not chat.queued
                 and len(FakeClient.requests) == 2)

        assert seen == [True, False]

    def test_a_voice_that_cannot_listen_says_why(self, server, monkeypatch):
        monkeypatch.setattr(web.voice, "transcribe",
                            lambda pcm: ("", "no listening model"))

        status, body = request(server, "POST", "/api/voice/hear",
                               {"data": ""})

        assert status == 400
        assert json.loads(body)["error"] == "no listening model"
        assert request(server, "POST", "/api/voice/hear",
                       {"data": "not base64!"})[0] == 400

    def test_a_long_turn_fits_but_no_more(self, server, monkeypatch):
        monkeypatch.setattr(web.voice, "transcribe", lambda pcm: ("hi", ""))
        minute = base64.b64encode(b"\x00" * web.voice.SAMPLE_RATE * 2 * 60)

        ok, _ = request(server, "POST", "/api/voice/hear",
                        {"data": minute.decode()})
        assert ok == 200
        assert len(minute) > web.MAX_BODY_BYTES

        # Anywhere else, the ordinary limit still holds. Refused on the
        # length it claims, before anything is read.
        port = server.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/api/voice/say")
        for name, value in (
            ("Host", f"127.0.0.1:{port}"), ("X-Flash-Token", server.token),
            ("Content-Type", "application/json"),
            ("Content-Length", str(web.MAX_BODY_BYTES + 1)),
        ):
            conn.putheader(name, value)
        conn.endheaders(b"{}")
        response = conn.getresponse()
        response.read()
        conn.close()
        assert response.status == 413

    def test_a_reply_is_spoken_as_wav(self, server, monkeypatch):
        monkeypatch.setattr(web.voice, "synthesize",
                            lambda text: (b"RIFF" + text.encode(), ""))

        status, body = request(server, "POST", "/api/voice/say",
                               {"text": "done"})

        assert status == 200
        assert base64.b64decode(json.loads(body)["audio"]) == b"RIFFdone"

    def test_the_voice_needs_are_reported(self, monkeypatch):
        monkeypatch.setattr(web.voice, "web_missing", lambda: ["vosk"])
        monkeypatch.setattr(web.voice, "models_present", lambda: False)

        said = web.command(web.Session(), {"name": "voice-status"})

        assert said["missing"] == ["vosk"] and said["ready"] is False
        assert "vosk" in said["install"]

    def test_a_spoken_line_says_links_as_links(self, server, monkeypatch):
        said = []
        monkeypatch.setattr(web.voice, "synthesize",
                            lambda text: (said.append(text) or b"RIFF", ""))

        request(server, "POST", "/api/voice/say",
                {"text": "Read  https://x.dev/a  now."})

        assert said == ["Read a link now."]

    def test_settings_lists_the_models_and_where_each_stands(
        self, monkeypatch,
    ):
        monkeypatch.setattr(web.voice, "listening_installed",
                            lambda name: name == web.voice.DEFAULT_VOSK_MODEL)
        monkeypatch.setattr(web.voice, "voice_installed", lambda name: False)
        monkeypatch.setattr(web.voice, "web_missing", lambda: [])

        listed = web.command(web.Session(), {"name": "voice-models"})

        first = listed["listening"][0]
        assert first["installed"] and first["current"]
        assert first["size"] == 41_205_931 and "weaker" in first["label"]
        assert not any(v["installed"] for v in listed["speaking"])
        assert listed["downloading"] is None

    def test_picking_an_installed_model_uses_it(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(ai, "set_config_var",
                            lambda name, value: saved.update({name: value}))
        monkeypatch.setattr(web.voice, "voice_installed", lambda name: True)

        web.command(web.Session(), {
            "name": "voice-model", "arg": "en_US-ryan-high",
            "kind": "speaking",
        })

        assert saved == {"VOICE_PIPER_VOICE": "en_US-ryan-high"}

    def test_picking_a_missing_model_downloads_then_uses_it(
        self, monkeypatch,
    ):
        saved = {}
        monkeypatch.setattr(ai, "set_config_var",
                            lambda name, value: saved.update({name: value}))
        monkeypatch.setattr(web.voice, "listening_installed", lambda n: False)

        def fetch(name, progress, stop=None):
            progress("listening model", 40)
            progress("listening model", 100)
            return ""

        monkeypatch.setattr(web.voice, "download_listening", fetch)
        session = web.Session()
        drain = events_of(session)

        web.command(session, {
            "name": "voice-model", "arg": "vosk-model-en-us-0.22",
            "kind": "listening",
        })
        wait_for(lambda: session.voice_job is None)
        time.sleep(0.05)

        told = [e for e in drain() if e["type"] == "voice-model"]
        assert [e.get("percent") for e in told[:2]] == [40, 100]
        assert told[-1]["done"] is True and told[-1]["error"] == ""
        assert saved == {"VOICE_VOSK_MODEL": "vosk-model-en-us-0.22"}

    def test_a_download_can_be_cancelled(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(ai, "set_config_var",
                            lambda name, value: saved.update({name: value}))
        monkeypatch.setattr(web.voice, "listening_installed", lambda n: False)
        started = threading.Event()

        def fetch(name, progress, stop=None):
            progress("listening model", 10)
            started.set()
            stop.wait(5)
            return web.voice.CANCELLED if stop.is_set() else ""

        monkeypatch.setattr(web.voice, "download_listening", fetch)
        session = web.Session()
        drain = events_of(session)

        web.command(session, {
            "name": "voice-model", "arg": "vosk-model-en-us-0.22",
            "kind": "listening",
        })
        started.wait(5)
        said = web.command(session, {"name": "voice-cancel"})
        wait_for(lambda: session.voice_job is None)
        time.sleep(0.05)

        assert said == {"cancelling": True}
        told = [e for e in drain() if e["type"] == "voice-model"][-1]
        assert told["done"] and told["cancelled"] and told["error"] == ""
        # Not the model in use: it never arrived.
        assert saved == {}
        # With nothing running, there is nothing to call off.
        assert web.command(session, {"name": "voice-cancel"}) == {
            "cancelling": False
        }

    def test_what_settings_refuses(self, monkeypatch):
        session = web.Session()
        in_use = web.voice.vosk_model()

        with pytest.raises(ValueError, match="in use"):
            web.command(session, {
                "name": "voice-model-remove", "arg": in_use,
                "kind": "listening",
            })
        with pytest.raises(ValueError, match="not a model Flash offers"):
            web.command(session, {
                "name": "voice-model", "arg": "../../x",
                "kind": "listening",
            })
        with pytest.raises(ValueError, match="listening or speaking"):
            web.command(session, {
                "name": "voice-model", "arg": in_use, "kind": "other",
            })

    def test_the_models_download_once_with_progress(self, monkeypatch):
        gate = threading.Event()

        def fetch(progress, stop=None):
            progress("voice", 50)
            progress("voice", 50)
            progress("voice", 100)
            gate.wait(2)
            return ""

        monkeypatch.setattr(web.voice, "models_present", lambda: False)
        monkeypatch.setattr(web.voice, "ensure_models", fetch)
        session = web.Session()
        drain = events_of(session)

        first = web.command(session, {"name": "voice-setup"})
        again = web.command(session, {"name": "voice-setup"})
        gate.set()
        wait_for(lambda: not session.voice_setup)
        time.sleep(0.05)

        assert first == again == {"ready": False}
        told = [e for e in drain() if e["type"] == "voice-setup"]
        assert [e.get("percent") for e in told[:2]] == [50, 100]
        assert told[-1]["done"] is True and told[-1]["error"] == ""
        assert len(told) == 3

    def test_ready_models_need_no_download(self, monkeypatch):
        monkeypatch.setattr(web.voice, "models_present", lambda: True)

        assert web.command(web.Session(), {"name": "voice-setup"}) == {
            "ready": True,
        }


class TestAttachments:
    def upload(self, server, name, data):
        return request(server, "POST", "/api/upload", {
            "name": name, "data": base64.b64encode(data).decode(),
        })

    def test_an_image_upload_is_kept_and_shown(self, server):
        status, body = self.upload(server, "../../dot.png", PNG)
        meta = json.loads(body)

        assert status == 200
        # The name loses its path: it cannot climb out of its folder.
        assert meta["name"] == "dot.png"
        assert meta["kind"] == "image" and meta["size"] == len(PNG)
        assert "path" not in meta
        status, served = request(server, "GET", f"/api/files/{meta['id']}")
        assert status == 200 and served == PNG

    def test_other_files_are_kept_but_not_served(self, server):
        status, body = self.upload(server, "notes.txt", b"hello")
        meta = json.loads(body)

        assert status == 200 and meta["kind"] == "file"
        assert request(server, "GET", f"/api/files/{meta['id']}")[0] == 404

    def test_a_bad_upload_says_why(self, server):
        status, body = request(server, "POST", "/api/upload",
                               {"name": "x.png", "data": "not base64!"})
        assert status == 400
        status, body = self.upload(server, "empty.txt", b"")
        assert status == 400 and b"empty" in body
        assert request(server, "POST", "/api/upload", {
            "name": "x", "data": "aGk=",
        }, token=False)[0] == 403

    def test_the_model_gets_the_image_and_the_files_path(self):
        image = web.workspace.keep_upload("chart.png", PNG)
        doc = web.workspace.keep_upload("data.csv", b"a,b\n1,2\n")
        FakeClient.scripts = [[part("Got them."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "what is this?", files=[image["id"], doc["id"]])
        wait_for(lambda: not (chat.busy or chat.queued))

        sent = FakeClient.requests[-1]["messages"][-1]
        assert sent["images"] == [
            web.workspace.upload_info(image["id"])["path"]
        ]
        assert "Attached file:" in sent["content"]
        assert sent["content"].endswith("data.csv")
        shown = next(e for e in chat.log if e["type"] == "user")
        assert [f["name"] for f in shown["files"]] == ["chart.png", "data.csv"]
        assert all("path" not in f for f in shown["files"])

    def test_an_image_alone_still_asks_something(self):
        image = web.workspace.keep_upload("cat.jpg", b"\xff\xd8\xff fake")
        FakeClient.scripts = [[part("A cat."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()

        session.send(chat, "", files=[image["id"]])
        wait_for(lambda: not (chat.busy or chat.queued))

        sent = FakeClient.requests[-1]["messages"][-1]
        assert sent["content"] == ai.DEFAULT_IMAGE_PROMPT
        assert chat.title == "cat.jpg"

    def test_unknown_ids_are_dropped(self):
        assert web.attachments(["0123456789abcdef", "../x"]) == []


# --- Chats with a spark --------------------------------------------------


class TestSparkChats:
    @pytest.fixture(autouse=True)
    def spark_prompt(self, monkeypatch):
        from flash import sparks

        monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")

    def _spark(self):
        from flash import sparks

        return sparks.create(
            "Scout", "Watch the issues.", "Only read.", "daily"
        )

    def test_a_chat_is_made_with_a_spark_and_kept_that_way(self):
        spark = self._spark()
        session = web.Session()

        chat = session.new_chat(spark="scout")

        assert chat.spark == spark.id
        assert chat.summary()["spark"] == spark.id
        assert web.Chat.restore(chat.saved()).spark == spark.id
        made = web.command(session, {"name": "new", "spark": spark.id})
        assert session.chat(made["chat"]).spark == spark.id

    def test_a_chat_with_a_spark_that_is_not_there_is_refused(self):
        with pytest.raises(ValueError, match="not here"):
            web.Session().new_chat(spark="ghost")

    def test_the_spark_answers_in_its_own_voice_and_keeps_its_lessons(
        self, monkeypatch
    ):
        from flash import learning, sparks

        spark = self._spark()
        reviewed = []
        monkeypatch.setattr(
            learning, "after_turn", lambda *a, **k: reviewed.append(a)
        )
        FakeClient.scripts = [
            [part(calls=[call("learn", lesson="Only crashes.")]),
             part(done=True)],
            [part("Got it: crashes only."), part(done=True, tokens=3)],
        ]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)
        seen = events_of(session)

        run(session, chat, "Only tell me about crashes.")

        first = FakeClient.requests[0]
        system = first["messages"][0]["content"]
        assert "You are Scout (@scout-spark)" in system
        assert "Watch the issues." in system and "Only read." in system
        offered = {t["function"]["name"] for t in first["tools"]}
        assert {"learn", "set_goal", "set_schedule", "keep_notes"} <= offered
        assert "remember" not in offered and "make_spark" not in offered
        events = seen()
        assert any(
            e["type"] == "tool" and e["label"] == "Learn(Only crashes.)"
            for e in events
        )
        assert [e["text"] for e in events if e["type"] == "assistant"][-1] \
            == "Got it: crashes only."
        assert sparks.find(spark.id).lessons == ["Only crashes."]
        # What Flash learns about the user is Flash's, not the spark's.
        assert reviewed == []

    def test_a_spark_changes_its_goal_when_asked(self):
        from flash import sparks

        spark = self._spark()
        FakeClient.scripts = [
            [part(calls=[
                call("set_goal", goal="Watch the pull requests."),
                call("set_schedule", every="2h"),
            ]), part(done=True)],
            [part("Done."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Watch the PRs instead, every two hours.")

        kept = sparks.find(spark.id)
        assert kept.goal == "Watch the pull requests."
        assert kept.every == 120

    def test_a_spark_saying_what_it_will_do_next_shift_is_not_nudged(self):
        spark = self._spark()
        FakeClient.scripts = [
            [part("I'll only tell you about crashes from now on."),
             part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Only crashes please.")

        assert len(FakeClient.requests) == 1

    def test_a_chat_whose_spark_was_removed_says_so(self):
        from flash import sparks

        spark = self._spark()
        session = web.Session()
        chat = session.new_chat(spark=spark.id)
        sparks.remove(spark.id)
        seen = events_of(session)

        run(session, chat, "Hello?")

        errors = [e["text"] for e in seen() if e["type"] == "error"]
        assert errors and "not here any more" in errors[0]
        assert FakeClient.requests == []

    def test_the_page_knows_each_spark_for_its_badge(self):
        spark = self._spark()

        listed = web.Session().state(lite=True)["sparks"]

        assert listed == [{
            "id": spark.id, "name": "Scout", "handle": "@scout-spark",
            "title": "", "colour": spark.colour, "project": "",
            "status": "idle", "paused": False, "waiting": False,
            "answering": False, "unread": 0, "activity": "",
            "next_run": spark.next_run, "pending": {},
        }]

    def test_a_spark_is_made_paused_from_the_page(self):
        session = web.Session()

        made = web.command(session, {
            "name": "spark-create", "arg": "Scout",
            "goal": "Watch the issues.", "model": "m", "paused": True,
        })["spark"]

        assert made["paused"] is True and made["name"] == "Scout"

    def test_a_report_is_rated_from_the_page(self):
        from flash import sparks
        from flash.sparks import Report

        made = sparks.create("Scout", "Watch the issues.")
        sparks._edit(made.id, lambda s: s.reports.append(
            Report(at=5.0, text="Two new issues."),
        ))

        rated = web.command(web.Session(), {
            "name": "spark-rate", "arg": made.id, "at": 5.0, "rating": 1,
        })["spark"]

        assert rated["reports"][0]["rating"] == 1

    def test_the_roster_sees_a_spark_waiting_and_its_title(self):
        from flash import sparks

        spark = sparks.create("Scout", "Tidy up.", title="Janitor")

        def wait(s):
            s.status = sparks.WAITING
            s.pending = {
                "label": "Run a command", "detail": "rm -r build",
                "messages": [{"role": "user", "content": "secret"}],
            }

        sparks._edit(spark.id, wait)

        listed = web.Session().state(lite=True)["sparks"][0]

        assert listed["title"] == "Janitor"
        assert listed["waiting"] is True
        # What waits, as the card shows it: not the shift behind it.
        assert listed["pending"] == {
            "label": "Run a command", "detail": "rm -r build",
        }


class TestSparkProjects:
    @pytest.fixture
    def project(self, tmp_path):
        from flash import workspace

        folder = tmp_path / "app"
        folder.mkdir()
        return workspace.create_project("App", str(folder), "Use pnpm.")

    def test_a_chat_with_a_spark_starts_in_its_project(self, project):
        from flash import sparks

        spark = sparks.create("Scout", "Watch the build.", project="App")
        session = web.Session()

        assert session.new_chat(spark=spark.id).project == project.id
        other = session.new_chat(project="", spark=spark.id)
        assert other.project == project.id

    def test_its_project_is_told_once_and_the_turn_runs_there(
        self, project, monkeypatch
    ):
        from flash import sparks

        monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
        spark = sparks.create("Scout", "Watch the build.", project="App")
        where = []

        def reply():
            where.append(os.getcwd())
            return [part("Hi."), part(done=True)]

        FakeClient.scripts = [reply]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Hello")

        system = FakeClient.requests[0]["messages"][0]["content"]
        assert system.count("Use pnpm.") == 1
        assert "=== Project: App ===" in system
        assert where == [project.path]

    def test_the_page_knows_each_sparks_project(self, project):
        from flash import sparks

        sparks.create("Scout", "Watch the build.", project="App")

        listed = web.Session().state(lite=True)["sparks"]
        assert listed[0]["project"] == project.id
        made = web.command(web.Session(), {
            "name": "spark-create", "arg": "Other", "goal": "Goal.",
            "project": project.id,
        })["spark"]
        assert made["project_name"] == "App"


def test_a_spark_is_edited_from_the_page_with_its_new_name(server):
    from flash import sparks

    spark = sparks.create("Scout", "Watch the build.")

    status, body = request(server, "POST", "/api/command", {
        "name": "spark-update", "arg": spark.id,
        "rename": "Lookout", "goal": "Watch the tests.", "every": "120",
    })

    assert status == 200, body
    kept = sparks.find(spark.id)
    assert (kept.name, kept.goal, kept.every) == (
        "Lookout", "Watch the tests.", 120,
    )


class TestMentions:
    @pytest.fixture(autouse=True)
    def spark_prompt(self, monkeypatch):
        from flash import sparks

        monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")

    def test_a_mentioned_spark_answers_in_a_chat_with_flash(self, monkeypatch):
        from flash import learning, sparks

        scout = sparks.create("Scout", "Watch the issues.")
        reviewed = []
        monkeypatch.setattr(
            learning, "after_turn", lambda *a, **k: reviewed.append(a)
        )
        FakeClient.scripts = [[part("Two new bugs."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()
        seen = events_of(session)

        run(session, chat, "@scout anything new?")

        system = FakeClient.requests[0]["messages"][0]["content"]
        assert "You are Scout" in system
        assert "conversation they are having with Flash" in system
        events = seen()
        assert any(
            e["type"] == "speaker" and e["spark"] == scout.id for e in events
        )
        replies = [e for e in events if e["type"] == "assistant"]
        assert replies[-1]["spark"] == scout.id
        assert chat.log[-2]["spark"] == scout.id
        note = chat.messages[-1]
        assert note["role"] == "system"
        assert note["content"].startswith(
            "[Spark called] The user @mentioned Scout (@scout-spark)"
        )
        assert note["content"].endswith("Two new bugs.")
        # Only the spark spoke: Flash's review has nothing of its own.
        assert reviewed == []

    def test_two_mentioned_sparks_answer_in_turn(self):
        from flash import sparks

        scout = sparks.create("Scout", "Watch the issues.")
        watch = sparks.create("Price Watch", "Watch the price.")
        FakeClient.scripts = [
            [part("Issues are quiet."), part(done=True)],
            [part("Price is flat."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "@scout @price-watch status?")

        answered = [
            e.get("spark") for e in chat.log if e["type"] == "assistant"
        ]
        assert answered == [scout.id, watch.id]
        second = FakeClient.requests[1]["messages"]
        assert "You are Price Watch" in second[0]["content"]
        # The second hears what the first said, marked as the first's.
        assert len(second) == 2
        assert "Already answered by others" in second[1]["content"]
        assert "Scout (@scout-spark)" in second[1]["content"]
        assert "Issues are quiet." in second[1]["content"]

    def test_a_mentioned_spark_is_shown_a_bit_of_the_chat(self):
        from flash import sparks

        sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [
            [part("Hello."), part(done=True)],
            [part("Nothing new."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "I am working on the login page.")
        run(session, chat, "@scout any bugs about that?")

        guest = FakeClient.requests[1]["messages"]
        assert [m["role"] for m in guest] == ["system", "user"]
        shown = guest[1]["content"]
        assert "User: I am working on the login page." in shown
        assert "Flash: Hello." in shown
        assert shown.endswith("@scout any bugs about that?")

    def test_flash_then_sees_the_spark_was_called_but_not_its_work(self):
        from flash import sparks

        sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [
            [part(calls=[call("fetch", url="https://example.com")]),
             part(done=True)],
            [part("Issue 12 is a crash."), part(done=True)],
            [part("I can fix that crash."), part(done=True)],
        ]
        monkeypatch_fetch = tools.FUNCTIONS["fetch"]
        tools.FUNCTIONS["fetch"] = lambda **kw: "(page)"
        try:
            session = web.Session()
            chat = session.new_chat()
            run(session, chat, "@scout anything new?")
            run(session, chat, "Flash, can you fix what Scout found?")
        finally:
            tools.FUNCTIONS["fetch"] = monkeypatch_fetch

        flash_saw = FakeClient.requests[2]["messages"]
        notes = [m for m in flash_saw if m["role"] == "system"
                 and m["content"].startswith("[Spark called]")]
        assert len(notes) == 1
        assert "using fetch" in notes[0]["content"]
        assert notes[0]["content"].endswith("Issue 12 is a crash.")
        # The spark's own tool calls are its work, not Flash's history.
        assert not any(m["role"] == "tool" for m in flash_saw)

    def test_no_mention_is_flash_as_ever(self):
        FakeClient.scripts = [[part("Hello."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "email me@scout about @nobody")

        system = FakeClient.requests[0]["messages"][0]["content"]
        assert "You are" not in system
        assert "spark" not in chat.log[-2]

    def test_a_spark_mentioned_in_its_own_chat_is_just_itself(self):
        from flash import sparks

        scout = sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [[part("Here."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat(spark=scout.id)

        run(session, chat, "@scout you there?")

        assert len(FakeClient.requests) == 1
        system = FakeClient.requests[0]["messages"][0]["content"]
        assert "You were mentioned" not in system
        assert chat.messages[-1]["content"] == "Here."

    def test_a_mentioned_spark_can_learn_there_too(self):
        from flash import sparks

        scout = sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [
            [part(calls=[call("learn", lesson="Skip docs.")]),
             part(done=True)],
            [part("Noted."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()

        run(session, chat, "@scout skip docs issues from now on")

        assert sparks.find(scout.id).lessons == ["Skip docs."]


def test_a_spark_answers_in_a_chat_on_its_own_model(monkeypatch):
    from flash import sparks

    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    sparks.create("Scout", "Watch the issues.", model="qwen3:8b")
    FakeClient.scripts = [
        [part("Scout here."), part(done=True)],
        [part("Flash here."), part(done=True)],
    ]
    session = web.Session()
    chat = session.new_chat()

    run(session, chat, "@scout hello")
    run(session, chat, "and you, Flash?")

    assert [r["model"] for r in FakeClient.requests] == [
        "qwen3:8b", "flash-test",
    ]
    stats = [e["model"] for e in chat.log if e["type"] == "stats"]
    assert stats == ["qwen3:8b", "flash-test"]


def test_a_mentioned_spark_knows_its_status(monkeypatch):
    from flash import sparks

    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    spark = sparks.create("Scout", "Watch the issues.")
    sparks.set_paused(spark.id, True)
    FakeClient.scripts = [[part("I am paused."), part(done=True)]]
    session = web.Session()
    chat = session.new_chat()

    run(session, chat, "@scout what are you up to?")

    system = FakeClient.requests[0]["messages"][0]["content"]
    assert "=== Your status right now ===" in system
    assert "Paused: no shifts run" in system


class TestSparkJobs:
    @pytest.fixture(autouse=True)
    def spark_prompt(self, monkeypatch):
        from flash import sparks

        monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
        monkeypatch.setattr(tools, "MODEL_NAME", "flash-test")

    class Shift:
        """A model for a shift, which does not stream."""

        def __init__(self, text):
            self.text = text

        def chat(self, **kwargs):
            return SimpleNamespace(message=SimpleNamespace(
                content=self.text, tool_calls=[],
            ))

    def test_a_job_asked_for_in_a_chat_comes_back_to_it(self):
        from flash import sparks

        spark = sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [
            [part(calls=[call("take_on", job="Sum up this week.")]),
             part(done=True)],
            [part("On it."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)
        run(session, chat, "Actually, can you sum up this week?")

        assert sparks.find(spark.id).inbox[0]["chat"] == chat.id
        sparks.shift(spark.id, client=self.Shift("Three new issues."))
        session.post_spark_reports()

        posted = chat.log[-1]
        assert posted["type"] == "assistant" and posted["shift"] is True
        assert posted["text"] == "Three new issues."
        assert posted["spark"] == spark.id
        note = chat.messages[-1]
        assert note["role"] == "system"
        assert "finished the job the user gave it" in note["content"]
        # Once only.
        session.post_spark_reports()
        assert sum(1 for e in chat.log if e.get("shift")) == 1

    def test_flash_gives_a_spark_a_job_from_its_own_chat(self):
        from flash import sparks

        spark = sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [
            [part(calls=[call(
                "give_spark", spark="scout", job="Check the login page.",
            )]), part(done=True)],
            [part("Scout is on it."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat()
        run(session, chat, "Get Scout to check the login page.")

        given = sparks.find(spark.id).inbox[0]
        assert given["chat"] == chat.id
        assert given["text"] == "Check the login page."
        sparks.shift(spark.id, client=self.Shift("It loads fine."))
        session.post_spark_reports()
        assert chat.log[-1]["text"] == "It loads fine."

    def test_flash_knows_the_sparks_it_can_ask(self):
        from flash import sparks

        sparks.create("Scout", "Watch the issues.")
        FakeClient.scripts = [[part("Hi."), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()
        run(session, chat, "hi")

        tools_offered = [
            t["function"]["name"] for t in FakeClient.requests[0]["tools"]
        ]
        assert "ask_spark" in tools_offered
        assert "give_spark" in tools_offered

    def test_a_chat_mid_reply_gets_it_after(self):
        from flash import sparks

        spark = sparks.create("Scout", "Watch the issues.")
        session = web.Session()
        chat = session.new_chat()
        sparks.take_on(spark.id, "Check the login page.", chat=chat.id)
        sparks.shift(spark.id, client=self.Shift("It loads fine."))
        chat.busy = True

        session.post_spark_reports()
        assert not any(e["type"] == "assistant" for e in chat.log)

        chat.busy = False
        session.post_spark_reports()
        assert chat.log[-1]["text"] == "It loads fine."

    def test_a_report_for_a_chat_that_is_gone_is_let_go(self):
        from flash import sparks

        spark = sparks.create("Scout", "Watch the issues.")
        sparks.take_on(spark.id, "Check.", chat="deadbeef")
        sparks.shift(spark.id, client=self.Shift("Checked."))

        web.Session().post_spark_reports()

        assert sparks.to_post() == []


def test_two_saves_of_one_chat_at_once_both_land():
    from flash import workspace

    session = web.Session()
    chat = session.new_chat()
    chat.log = [{"type": "note", "text": "hi"}]
    failed = []

    def save():
        try:
            for _ in range(50):
                session.save(chat)
        except OSError as exc:
            failed.append(exc)

    threads = [threading.Thread(target=save) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failed == []
    assert [c["id"] for c in workspace.load_chats()] == [chat.id]
    folder = workspace.store() / "chats"
    assert not list(folder.glob("*.partial")) + list(folder.glob(".*.partial"))


def test_the_page_reads_and_sets_the_rounds_a_shift_gets():
    session = web.Session()

    now = web.command(session, {"name": "spark-rounds"})
    changed = web.command(session, {"name": "spark-rounds", "arg": "20"})

    assert now["rounds"] == now["default"] == 12
    assert changed["rounds"] == 20
    with pytest.raises(ValueError):
        web.command(session, {"name": "spark-rounds", "arg": "500"})

    off = web.command(session, {"name": "spark-rounds", "arg": "unlimited"})
    back = web.command(session, {"name": "spark-rounds", "arg": "limited"})
    assert off["unlimited"] is True and off["rounds"] == 20
    assert back["unlimited"] is False and back["rounds"] == 20


def test_the_page_sets_the_model_new_sparks_are_made_on(monkeypatch):
    monkeypatch.setattr(
        web, "list_models", lambda ai: ["llama3.1:latest", "qwen3:8b"],
    )
    session = web.Session()

    picked = web.command(
        session, {"name": "spark-default-model", "arg": "qwen3:8b"},
    )
    assert picked["spark_default"] == "qwen3:8b"
    assert picked["spark_default_here"] is True
    assert picked["spark_model"] == "qwen3:8b"
    said = web.command(session, {"name": "model"})
    assert said["spark_default"] == "qwen3:8b"

    back = web.command(
        session, {"name": "spark-default-model", "arg": "flash"},
    )
    assert back["spark_default"] == ""
    assert back["spark_default_here"] is None


def test_the_page_hears_when_the_default_model_is_gone(monkeypatch):
    monkeypatch.setattr(ai.Config, "model", "llama3.1:latest")
    monkeypatch.setattr(web, "list_models", lambda ai: ["llama3.1:latest"])
    sparks.set_default_model("qwen3:8b")

    said = web.command(web.Session(), {"name": "spark-default-model"})

    assert said["spark_default_here"] is False
    # Never another in its place.
    assert said["spark_model"] == "qwen3:8b"
    assert "not in this computer's model list" in said["spark_default_note"]


def test_the_page_hears_when_no_host_answers(monkeypatch):
    monkeypatch.setattr(web, "list_models", lambda ai: None)
    sparks.set_default_model("qwen3:8b")

    said = web.command(web.Session(), {"name": "spark-default-model"})

    assert said["spark_default_here"] is None
    assert said["spark_model"] == "qwen3:8b"


def test_the_page_reads_and_sets_the_notification_limit():
    session = web.Session()

    now = web.command(session, {"name": "notify-limit"})
    changed = web.command(session, {"name": "notify-limit", "arg": "10/60"})
    off = web.command(session, {"name": "notify-limit", "arg": "off"})

    assert (now["count"], now["minutes"]) == (4, 15)
    assert (changed["count"], changed["minutes"]) == (10, 60)
    assert off["count"] == 0 and off["minutes"] == 60
    with pytest.raises(ValueError):
        web.command(session, {"name": "notify-limit", "arg": "999/15"})


# --- Screen recordings ---------------------------------------------------


class TestScreenRecordings:
    STILL = b"\xff\xd8\xff\xe0fake-jpeg"

    def _upload(self, monkeypatch, narration=b"", heard=None):
        import base64

        from flash import workspace

        said = iter(heard or [])
        monkeypatch.setattr(
            web.voice, "transcribe",
            lambda pcm: (next(said), "") if heard is not None
            else ("", "no listening model"),
        )
        body = {
            "name": "screen-recording.webm",
            "data": base64.b64encode(b"webm-bytes").decode(),
            "narration": base64.b64encode(narration).decode(),
            "stills": [
                {"data": base64.b64encode(self.STILL).decode(), "at": at}
                for at in (0, 4.2, 9.5)
            ],
        }
        stills, self.unheard = web.recording_stills(body)
        return workspace.keep_upload(body["name"], b"webm-bytes", stills)

    def test_a_recording_is_kept_with_its_stills(self, monkeypatch):
        from flash import workspace

        kept = self._upload(monkeypatch)

        assert kept["kind"] == "video" and kept["stills"] == 3
        stills = workspace.recording_stills(kept["id"])
        assert [s["at"] for s in stills] == [0, 4.2, 9.5]
        assert all(s["said"] == "" for s in stills)
        # The folder still holds the one file it is named by.
        assert workspace.upload_info(kept["id"])["name"] == (
            "screen-recording.webm"
        )

    def test_what_was_said_over_each_step_goes_with_it(self, monkeypatch):
        from flash import workspace

        # Twelve seconds of 16 kHz 16-bit audio.
        pcm = b"\x00\x01" * 16_000 * 12
        kept = self._upload(
            monkeypatch, pcm, ["open settings", "click privacy", "done"],
        )

        said = [s["said"] for s in workspace.recording_stills(kept["id"])]
        assert said == ["open settings", "click privacy", "done"]

    def test_the_model_sees_the_stills_and_is_asked_to_learn_it(
        self, monkeypatch,
    ):
        from flash import workspace

        pcm = b"\x00\x01" * 16_000 * 12
        kept = self._upload(monkeypatch, pcm, ["open settings", "", "done"])

        content, images = web.outgoing(ai, "", [{"id": kept["id"]}])

        assert content.startswith(web.RECORDING_PROMPT)
        assert len(images) == 3
        assert images == [
            s["path"] for s in workspace.recording_stills(kept["id"])
        ]
        assert '1. at 0:00, the user saying: "open settings"' in content
        assert "2. at 0:04\n" in content
        assert "skill_manage" in content

    def test_words_typed_with_it_are_the_ask(self, monkeypatch):
        kept = self._upload(monkeypatch)

        content, _ = web.outgoing(
            ai, "This is how I file expenses", [{"id": kept["id"]}],
        )

        assert content.startswith("This is how I file expenses")
        assert web.RECORDING_PROMPT not in content
        # Typed or not, it is told to keep what it learns as a skill.
        assert "skill_manage" in content

    def test_narration_with_nothing_to_hear_it_says_why(self, monkeypatch):
        pcm = b"\x00\x01" * 16_000 * 12

        self._upload(monkeypatch, pcm)

        assert self.unheard == "no listening model"

    def test_a_bad_still_is_left_out(self):
        stills, _ = web.recording_stills({
            "stills": [{"data": "%%%", "at": 1}, {"data": "", "at": 2}],
        })

        assert stills == []


def test_a_recording_sent_untyped_names_its_chat_for_what_it_is():
    session = web.Session()
    chat = session.new_chat()

    session._begin(chat, "", files=[
        {"id": "x", "name": "screen-recording-2026.webm", "kind": "video"},
    ])

    assert chat.title == "Screen recording"


def test_the_page_picks_how_the_voice_speaks(monkeypatch):
    monkeypatch.delenv("VOICE_STYLE", raising=False)
    session = web.Session()

    listed = web.voice_models(session)["styles"]
    picked = web.command(session, {"name": "voice-style", "arg": "calm"})

    assert [s["name"] for s in listed] == ["warm", "lively", "calm", "plain"]
    assert [s["name"] for s in listed if s["current"]] == ["warm"]
    assert [s["name"] for s in picked["styles"] if s["current"]] == ["calm"]
    with pytest.raises(ValueError):
        web.command(session, {"name": "voice-style", "arg": "shouty"})
    monkeypatch.delenv("VOICE_STYLE", raising=False)


def test_voice_mode_asks_for_warm_words():
    assert "warm, friendly person" in ai.VOICE_PROMPT
    assert "question mark lifts it" in ai.VOICE_PROMPT


class TestContextBar:
    def test_an_empty_chat_uses_none(self, monkeypatch):
        monkeypatch.setattr(ai, "_context_limit", lambda: 8192)
        session = web.Session()
        chat = session.new_chat()

        use = web.command(session, {"name": "context", "chat": chat.id})

        assert use == {"share": 0, "used": 0, "budget": 100_000,
                       "window": 8192}

    def test_it_counts_the_history_against_the_budget(self, monkeypatch):
        monkeypatch.setattr(ai, "_context_limit", lambda: None)
        monkeypatch.setattr(ai, "_history_budget", lambda: 1000)
        session = web.Session()
        chat = session.new_chat()
        chat.messages = [{"role": "user", "content": "x" * 2000}]

        use = web.command(session, {"name": "context", "chat": chat.id})

        assert 0 < use["used"]
        assert use["share"] == min(100, round(100 * use["used"] / 1000))
        assert use["window"] == 0

    def test_it_never_reads_above_full(self, monkeypatch):
        monkeypatch.setattr(ai, "_context_limit", lambda: None)
        monkeypatch.setattr(ai, "_history_budget", lambda: 100)
        session = web.Session()
        chat = session.new_chat()
        chat.messages = [{"role": "user", "content": "x" * 100_000}]

        use = web.command(session, {"name": "context", "chat": chat.id})

        assert use["share"] == 100


class TestSparkBudgets:
    @pytest.fixture(autouse=True)
    def spark_prompt(self, monkeypatch):
        from flash import sparks

        monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")

    def test_a_chat_with_a_spark_counts_against_its_month(self):
        from flash import sparks

        spark = sparks.create("Scout", "Look.")
        FakeClient.scripts = [[part("Hi."), part(done=True, tokens=40)]]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Hello")

        assert sparks.used_this_month(sparks.find("scout")) == 40

    def test_a_spark_out_of_budget_says_so_and_asks_no_model(self):
        from flash import sparks

        spark = sparks.create("Scout", "Look.")
        sparks.set_budget("scout", True, 1000)
        sparks.spend(spark.id, 1000)
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Hello")

        assert chat.messages[-1]["content"] == sparks.out_of_budget(
            sparks.find("scout")
        )
        assert FakeClient.requests == []

    def test_a_chat_with_a_spark_is_in_its_audit_log(self):
        from flash import sparks

        spark = sparks.create("Scout", "Look.")
        FakeClient.scripts = [
            [part(calls=[call("learn", lesson="Only crashes.")]),
             part(done=True)],
            [part("Got it."), part(done=True)],
        ]
        session = web.Session()
        chat = session.new_chat(spark=spark.id)

        run(session, chat, "Only crashes, please.")

        logged = sparks.audit_log(spark.id)["entries"][-1]
        assert (logged["kind"], logged["tool"], logged["during"]) == (
            "tool", "learn", "chat",
        )
