# pylint: disable=C0114,C0115,C0116

import http.client
import sys
import threading

import pytest

from flash import tunnel, web

ADDRESS = "https://quiet-river-1234.trycloudflare.com"


@pytest.fixture
def program(monkeypatch, tmp_path):
    """A stand-in for the service's program: says what said.txt holds,
    as cloudflared does, then waits to be stopped."""

    said = tmp_path / "said.txt"
    said.write_text(f"INF Requesting new quick Tunnel\n| {ADDRESS} |\n")
    script = tmp_path / "cloudflared"
    script.write_text(
        f"#!{sys.executable}\n"
        "import time\n"
        f"print(open({str(said)!r}).read(), flush=True)\n"
        "time.sleep(60)\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(
        tunnel.shutil, "which",
        lambda name: str(script) if name == "cloudflared" else None,
    )
    return said


def test_a_tunnel_gives_its_public_address_and_stops(program):
    found = tunnel.Tunnel()

    status = found.start("cloudflare", 7433, wait=10)

    assert status["state"] == tunnel.ON  # nosec B101
    assert status["url"] == ADDRESS  # nosec B101
    assert found.host == "quiet-river-1234.trycloudflare.com"  # nosec
    found.stop()
    assert found.state == tunnel.OFF and found.host == ""  # nosec B101


def test_a_service_not_installed_says_how_to_get_it(program):
    with pytest.raises(tunnel.TunnelError, match="ngrok.com/download"):
        tunnel.Tunnel().start("ngrok", 7433)
    with pytest.raises(tunnel.TunnelError, match="One of"):
        tunnel.Tunnel().start("carrier pigeon", 7433)


def test_a_tunnel_that_gives_no_address_fails_and_says_why(program):
    program.write_text("ERR failed to sign in\n")
    found = tunnel.Tunnel()

    with pytest.raises(tunnel.TunnelError):
        found.start("cloudflare", 7433, wait=1)
    assert found.state == tunnel.FAILED and found.host == ""  # nosec


def test_the_services_say_which_are_installed(program):
    listed = {s["name"]: s for s in tunnel.services()}
    assert listed["cloudflare"]["installed"]  # nosec B101
    assert not listed["ngrok"]["installed"]  # nosec B101


@pytest.fixture
def server():
    srv = web.Server(0)
    srv.session.port = srv.port
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def ask(srv, host):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.request("GET", "/api/state?lite=1", headers={
        "Host": host, "X-Flash-Token": srv.token,
    })
    return conn.getresponse().status


def test_requests_through_the_tunnel_are_let_in_only_while_it_is_on(
        server, program):
    public = "quiet-river-1234.trycloudflare.com"
    assert ask(server, public) == 403  # nosec B101

    said = web.command(server.session, {"name": "tunnel",
                                        "arg": "cloudflare"})
    assert said["tunnel"]["state"] == tunnel.ON  # nosec B101
    assert said["tunnel"]["link"] == (  # nosec B101
        f"{ADDRESS}/?token={server.token}")
    assert said["tunnel"]["svg"].startswith("<svg")  # nosec B101
    assert ask(server, public) == 200  # nosec B101
    # Only its own name: not another under the same service.
    assert ask(server, "other-name.trycloudflare.com") == 403  # nosec

    web.command(server.session, {"name": "tunnel", "arg": "off"})
    assert ask(server, public) == 403  # nosec B101


def test_closing_the_server_takes_the_tunnel_down(program):
    srv = web.Server(0)
    srv.session.port = srv.port
    srv.session.tunnel.start("cloudflare", srv.port, wait=10)

    srv.server_close()

    assert srv.session.tunnel.state == tunnel.OFF  # nosec B101
