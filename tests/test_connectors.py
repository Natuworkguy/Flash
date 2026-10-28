# pylint: disable=C0114,C0115,C0116

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from flash import ai, connectors, tools, web

SERVER = Path(__file__).parent / "data" / "fake_mcp_server.py"


def ready(name="fake", seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = [s for s in connectors.listing() if s["name"] == name]
        if found and found[0]["state"] != connectors.STARTING:
            return found[0]
        time.sleep(0.05)
    raise AssertionError(f"{name} never finished starting")


@pytest.fixture
def fake():
    connectors.add("fake", f'"{sys.executable}" "{SERVER}"')
    return ready()


def test_a_server_s_tools_join_flash_s(fake):
    assert fake["state"] == connectors.READY  # nosec B101
    # Both pages of its tool list, followed.
    names = [t["function"]["name"] for t in tools.turn_tools()]
    for tool in ("add", "note", "fail", "pixel"):
        assert f"fake__{tool}" in names  # nosec B101
    offered = {t["function"]["name"]: t["function"]
               for t in connectors.tool_schemas()}
    assert offered["fake__add"]["description"] == (  # nosec B101
        "From the fake connector: Add two numbers."
    )
    # A tool with no schema is still offered with an empty one.
    assert offered["fake__fail"]["parameters"] == {  # nosec B101
        "type": "object", "properties": {},
    }


def test_the_config_is_the_usual_shape_and_private(fake):
    path = connectors.config_path()
    saved = json.loads(path.read_text())

    assert saved["mcpServers"]["fake"]["command"] == sys.executable  # nosec
    assert saved["mcpServers"]["fake"]["args"] == [str(SERVER)]  # nosec
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600  # nosec B101


def test_a_read_only_tool_runs_without_asking(fake, monkeypatch):
    def asked(question):
        raise AssertionError(f"asked: {question}")

    monkeypatch.setattr(tools, "remote_answer", asked)

    assert tools.run_tool(("fake__add", {"a": 2, "b": 3})) == "5"  # nosec


def test_any_other_tool_asks_first(fake, monkeypatch):
    questions = []

    def answer(question):
        questions.append(question)
        return answer.said

    monkeypatch.setattr(tools, "remote_answer", answer)
    answer.said = "n"
    assert tools.run_tool(("fake__note", {"text": "hi"})) == (  # nosec
        "Blocked by user"
    )
    answer.said = "y"
    assert "noted" in tools.run_tool(("fake__note", {"text": "hi"}))  # nosec
    assert questions == [  # nosec B101
        "Let the fake connector run note?",
    ] * 2

    # Autonomous mode asks nothing.
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    assert "noted" in tools.run_tool(("fake__note", {"text": "x"}))  # nosec
    assert len(questions) == 2  # nosec B101


def test_errors_and_images_come_back(fake, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    tools.take_pending_images()

    assert tools.run_tool(("fake__fail", {})) == "Error: it broke"  # nosec
    assert tools.run_tool(("fake__pixel", {})) == (  # nosec B101
        "(an image, attached)"
    )
    assert tools.take_pending_images() == [b"hi"]  # nosec B101


def test_a_server_that_cannot_start_says_why(tmp_path):
    connectors.add("nothing", str(tmp_path / "no-such-program"))
    gone = ready("nothing")
    assert gone["state"] == connectors.FAILED  # nosec B101
    assert "Could not start" in gone["why"]  # nosec B101

    connectors.add("quits", f'"{sys.executable}" -c "import sys; '
                            'print(\'no token\', file=sys.stderr); '
                            'sys.exit(2)"')
    quit_ = ready("quits")
    assert quit_["state"] == connectors.FAILED  # nosec B101
    assert "no token" in quit_["why"]  # nosec B101
    # And its last words stay, for Settings to show.
    assert "no token" in quit_["said"]  # nosec B101
    assert not [t for t in connectors.tool_schemas()  # nosec B101
                if t["function"]["name"].startswith(("nothing", "quits"))]


def test_off_and_removed_servers_take_their_tools(fake):
    connectors.switch("fake", False)
    assert ready()["state"] == connectors.OFF  # nosec B101
    assert connectors.read_config()["fake"]["disabled"] is True  # nosec
    assert not connectors.tool_schemas()  # nosec B101

    connectors.switch("fake", True)
    assert ready()["state"] == connectors.READY  # nosec B101
    connectors.remove("fake")
    assert connectors.listing() == []  # nosec B101
    assert tools.run_tool(("fake__add", {"a": 1, "b": 1})) == (  # nosec
        "Unknown tool: fake__add"
    )


def test_what_is_typed_becomes_a_server():
    assert connectors.spec_from(  # nosec B101
        "https://example.com/mcp", {"Authorization": "Bearer x"},
    ) == {"url": "https://example.com/mcp",
          "headers": {"Authorization": "Bearer x"}}
    assert connectors.spec_from(  # nosec B101
        'npx -y @scope/server "~/My Notes"', {"TOKEN": "t"},
    ) == {"command": "npx", "args": ["-y", "@scope/server", "~/My Notes"],
          "env": {"TOKEN": "t"}}
    for bad in ("", "has space", "slash/es", "x" * 33):
        with pytest.raises(connectors.ConnectorError):
            connectors.check_name(bad)


def test_settings_never_shows_a_key(monkeypatch):
    got = web.command(web.Session(), {
        "name": "connector-add", "arg": "fake",
        "target": f'"{sys.executable}" "{SERVER}"',
        "env": "SECRET_TOKEN=abc123\n\nOTHER=x",
    })

    assert "abc123" not in json.dumps(got)  # nosec B101
    shown = got["connectors"][0]
    assert shown["secrets"] == ["OTHER", "SECRET_TOKEN"]  # nosec B101
    kept = connectors.read_config()["fake"]["env"]
    assert kept["SECRET_TOKEN"] == "abc123"  # nosec B101
    with pytest.raises(ValueError, match="KEY=VALUE"):
        web.command(web.Session(), {
            "name": "connector-add", "arg": "two", "target": "x",
            "env": "no equals here",
        })
    got = web.command(web.Session(), {"name": "connector-remove",
                                      "arg": "fake"})
    assert got["connectors"] == []  # nosec B101


def test_the_terminal_adds_and_lists(capsys):
    ai._mcp_command(f'add fake "{sys.executable}" "{SERVER}"')
    ai._mcp_command("tools fake")
    ai._mcp_command("nonsense")

    said = capsys.readouterr().out
    assert "fake" in said and "4 tools" in said  # nosec B101
    assert "read-only" in said  # nosec B101
    assert "Usage: /mcp" in said  # nosec B101


class _Remote(BaseHTTPRequestHandler):
    """A server spoken to over HTTP: JSON for most answers, a stream of
    events for a call, as servers do."""

    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        message = json.loads(self.rfile.read(
            int(self.headers["Content-Length"])))
        _Remote.seen.append((message.get("method"),
                             self.headers.get("Mcp-Session-Id"),
                             self.headers.get("Authorization")))
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": connectors.PROTOCOL_VERSION,
                      "capabilities": {}, "serverInfo": {"name": "remote"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {
                "type": "object", "properties": {}}}]}
        else:
            text = message["params"]["arguments"]["text"]
            body = (
                "event: message\ndata: " + json.dumps({
                    "jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {}}) + "\n\n"
                "data: " + json.dumps({
                    "jsonrpc": "2.0", "id": message["id"],
                    "result": {"content": [{"type": "text",
                                            "text": text}]}}) + "\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"],
                           "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "s-1")
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        _Remote.seen.append(("DELETE", self.headers.get("Mcp-Session-Id"),
                             None))
        self.send_response(204)
        self.end_headers()


def test_a_server_elsewhere_over_http(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Remote)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Remote.seen = []
    try:
        connectors.add("remote", f"http://127.0.0.1:{server.server_port}/",
                       {"Authorization": "Bearer t"})
        assert ready("remote")["state"] == connectors.READY  # nosec B101
        found = connectors.find("remote__echo")
        assert connectors.call(*found, {"text": "hi"}) == (  # nosec B101
            "hi", [],
        )
        connectors.stop_all()
    finally:
        server.shutdown()
        server.server_close()
    methods = [m for m, _, _ in _Remote.seen]
    assert methods[:3] == [  # nosec B101
        "initialize", "notifications/initialized", "tools/list",
    ]
    # The session it was given goes with everything after, and the key
    # with everything.
    assert {s for m, s, _ in _Remote.seen if m != "initialize"} == {  # nosec
        "s-1",
    }
    assert {a for m, _, a in _Remote.seen if m != "DELETE"} == {  # nosec
        "Bearer t",
    }
    assert methods[-1] == "DELETE"  # nosec B101


# --- Sparks, and refreshing tools -------------------------------------------


class _Said:
    def __init__(self, content="", calls=()):
        self.content = content
        self.tool_calls = [
            type("Call", (), {"function": type("Fn", (), {
                "name": name, "arguments": args})()})()
            for name, args in calls
        ]


class _Model:
    """Says each of SAID in turn, and keeps the tools it was offered."""

    def __init__(self, *said):
        self.said = list(said)
        self.offered = []

    def chat(self, model, messages, tools=None, options=None):
        self.offered.append({t["function"]["name"] for t in tools or []})
        return type("Response", (), {"message": self.said.pop(0)})()


@pytest.fixture
def spark_model(monkeypatch):
    from flash import sparks

    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(sparks, "autonomous", lambda: False)
    return sparks


def test_a_spark_has_the_connected_servers_tools(fake, spark_model):
    sparks = spark_model
    made = sparks.create("Scout", "Add things up.")
    model = _Model(_Said("", [("fake__add", {"a": 2, "b": 3})]),
                   _Said("It is 5."))

    report = sparks.shift(made.id, client=model)

    assert "fake__add" in model.offered[0]  # nosec B101
    assert "refresh_tools" in model.offered[0]  # nosec B101
    assert report.text == "It is 5." and not report.approval  # nosec


def test_a_spark_asks_before_a_tool_that_changes_things(fake, spark_model):
    sparks = spark_model
    made = sparks.create("Scout", "Take notes.")

    report = sparks.shift(made.id, client=_Model(
        _Said("", [("fake__note", {"text": "hi"})])))

    assert report.approval  # nosec B101
    assert sparks.find(made.id).pending["tool"] == "fake__note"  # nosec


def test_refresh_tools_starts_a_server_that_failed(fake):
    server = connectors._servers["fake"]
    server.stop(keep_state=True)
    server.state = connectors.FAILED
    assert "fake__add" not in {  # nosec B101
        t["function"]["name"] for t in connectors.tool_schemas()}

    said = tools.refresh_tools()

    assert "fake: ready, with fake__add" in said  # nosec B101
    assert "fake__add" in {  # nosec B101
        t["function"]["name"] for t in connectors.tool_schemas()}


def test_refresh_tools_with_none_set_up_says_how_to_add_one():
    assert "Settings, Connectors" in tools.refresh_tools()  # nosec B101


def test_flash_starts_the_servers_when_it_starts(monkeypatch):
    started = []
    monkeypatch.setattr(web.connectors, "start", lambda: started.append(1))
    srv = web.Server(0)
    try:
        web._attach(srv, standalone=False)
    finally:
        srv.server_close()
        web._background = None

    assert started  # nosec B101


def test_a_team_chat_answer_has_the_reading_tools_and_refresh(fake,
                                                              spark_model):
    from flash import teamchat

    sparks = spark_model
    team = sparks.create_team("Ideas")
    made = sparks.create("Scout", "Find ideas.")
    sparks.set_team(made.id, team.id)
    model = _Model(_Said("", [("fake__add", {"a": 1, "b": 2}),
                              ("fake__note", {"text": "hi"})]),
                   _Said("It is 3."))

    teamchat._reply(sparks.find_team(team.id), sparks.find(made.id),
                    client=model)

    offered = model.offered[0]
    assert "fake__add" in offered and "refresh_tools" in offered  # nosec
    # Nowhere to ask in a team chat: a tool that changes things waits for
    # autonomous mode, and is refused if called anyway.
    assert "fake__note" not in offered  # nosec B101


STARTED_SERVER = """\
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
time.sleep(0.8)  # a server that takes a moment to come up

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        m = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in m:
            self.send_response(202); self.end_headers(); return
        if m["method"] == "initialize":
            r = {"protocolVersion": "2025-06-18", "capabilities": {},
                 "serverInfo": {"name": "mine"}}
        else:
            r = {"tools": [{"name": "hello", "inputSchema": {
                "type": "object", "properties": {}}}]}
        b = json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b.encode())

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def test_a_start_command_then_its_url(tmp_path, monkeypatch):
    import socket

    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    script = tmp_path / "server.py"
    script.write_text(STARTED_SERVER)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # As it would be said: run it, then connect.
    connectors.add("mine", f'run "{script}" {port} then connect to '
                           f"localhost:{port}/mcp")
    spec = connectors.read_config()["mine"]
    assert spec["url"] == f"http://localhost:{port}/mcp"
    assert spec["start"]["command"] == sys.executable
    found = ready("mine", 20)
    assert found["state"] == connectors.READY, found
    assert connectors.find("mine__hello") is not None
    assert found["target"] == spec["url"]
    assert str(script) in found["start"]
    running = next(s for s in connectors.servers() if s.name == "mine")
    process = running._process
    connectors.stop_all()
    assert process.poll() is not None


def test_a_start_command_that_dies_says_so(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("import sys\nprint('no port given')\nsys.exit(3)\n")
    connectors.add("broken", "http://127.0.0.1:9/mcp", None,
                   f'"{script}"')
    found = ready("broken", 20)
    assert found["state"] == connectors.FAILED
    assert "exit 3" in found["why"]
    assert "no port given" in found["said"]


def test_editing_keeps_secret_values():
    connectors.add("ed", "http://example.com/mcp", {"Authorization": "t"})
    connectors.edit("ed", "https://example.org/mcp", {"Authorization": ""},
                    rename="ed2")
    found = connectors.read_config()
    assert "ed" not in found
    assert found["ed2"] == {"url": "https://example.org/mcp",
                            "headers": {"Authorization": "t"}}
    with pytest.raises(connectors.ConnectorError):
        connectors.edit("nope", "x")
    connectors.remove("ed2")
