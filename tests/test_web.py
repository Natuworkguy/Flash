"""Tests for the web UI's server: its locks, its API, and its turns."""

import http.client
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from flash import ai, tools, web
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
    monkeypatch.setattr(web.ollama, "Client", FakeClient)
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

    def test_but_nothing_else_is(self, server):
        assert request(server, "GET", "/static/index.html",
                       token=False)[0] == 403
        assert request(server, "GET", "/static/../web.py",
                       token=False)[0] == 403

    def test_the_license_ships_beside_it(self):
        assert (web.WEB_DIR / "OFL-orbit.txt").is_file()


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
        monkeypatch.setattr(web.workspace, "host_up", lambda url: False)

        result = web.command(web.Session(), {"name": "host",
                                             "arg": "10.0.0.5"})

        assert saved == {"OLLAMA_HOST": "http://10.0.0.5:11434"}
        assert forgot == [True]
        assert result["host"] == "http://10.0.0.5:11434"
        assert result["up"] is False

    def test_add_list_remove(self, monkeypatch):
        monkeypatch.setattr(web.workspace, "host_up", lambda url: True)
        session = web.Session()

        added = web.command(session, {"name": "host-add",
                                      "arg": "10.0.0.5", "label": "Studio"})
        assert added == {"name": "Studio", "url": "http://10.0.0.5:11434",
                         "up": True}

        listed = web.command(session, {"name": "hosts"})["hosts"]
        assert [h["name"] for h in listed] == ["This computer", "Studio"]

        web.command(session, {"name": "host-remove", "arg": "10.0.0.5"})
        listed = web.command(session, {"name": "hosts"})["hosts"]
        assert [h["name"] for h in listed] == ["This computer"]

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
        assert cookie.startswith(cookie_of(server) + ";")
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

    def test_each_server_has_its_own_token(self):
        first, second = web.Server(0), web.Server(0)
        try:
            assert first.token != second.token
            assert first.cookie_name != second.cookie_name
        finally:
            first.server_close()
            second.server_close()


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

    def test_only_images_and_pdfs_are_kept(self, tmp_path):
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
