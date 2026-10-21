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
