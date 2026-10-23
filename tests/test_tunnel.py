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
    said.write_text(
        "INF Requesting new quick Tunnel on trycloudflare.com...\n"
        "INF |  Your quick Tunnel has been created! Visit it at (it may "
        "take some time to be reachable):  |\n"
        f"INF |  {ADDRESS}  |\n"
        "INF Registered tunnel connection connIndex=0 protocol=quic\n"
    )
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
    # What cloudflared says when it cannot get one: its API's address is
    # not the tunnel's.
    program.write_text(
        "INF Requesting new quick Tunnel on trycloudflare.com...\n"
        'failed to request quick Tunnel: Post "https://api.trycloudflare'
        '.com/tunnel": dial tcp: lookup api.trycloudflare.com: no such '
        "host\n")
    found = tunnel.Tunnel()

    with pytest.raises(tunnel.TunnelError, match="no such host"):
        found.start("cloudflare", 7433, wait=1)
    assert found.state == tunnel.FAILED and found.host == ""  # nosec


def test_the_address_counts_only_once_the_tunnel_is_connected(program):
    program.write_text(f"INF |  {ADDRESS}  |\n")

    with pytest.raises(tunnel.TunnelError, match="no address"):
        tunnel.Tunnel().start("cloudflare", 7433, wait=1)


def test_a_tunnel_that_does_not_reach_flash_is_taken_down(program,
                                                          monkeypatch):
    monkeypatch.setattr(tunnel, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(tunnel, "CHECK_SECONDS", 0.1)
    found = tunnel.Tunnel()

    with pytest.raises(tunnel.TunnelError, match="answered 502"):
        found.start("cloudflare", 7433, wait=10,
                    check=lambda url: "the service answered 502")
    assert found.state == tunnel.FAILED and found.host == ""  # nosec

    tried = []
    found.start("cloudflare", 7433, wait=10,
                check=lambda url: tried.append(url) or "")
    assert tried == [ADDRESS] and found.state == tunnel.ON  # nosec
    found.stop()


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
        server, program, monkeypatch):
    public = "quiet-river-1234.trycloudflare.com"
    assert ask(server, public) == 403  # nosec B101

    monkeypatch.setattr(web, "reaches", lambda session: lambda url: "")
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


def test_flash_knows_its_own_answer_through_a_tunnel(server):
    check = web.reaches(server.session)
    here = f"http://127.0.0.1:{server.port}"

    assert check(here) == ""  # nosec B101
    # The code is good for that one look only.
    assert server.session.tunnel_nonce == ""  # nosec B101
    assert "answered 403" in check(here + "/nothing")  # nosec B101


def test_closing_the_server_takes_the_tunnel_down(program):
    srv = web.Server(0)
    srv.session.port = srv.port
    srv.session.tunnel.start("cloudflare", srv.port, wait=10)

    srv.server_close()

    assert srv.session.tunnel.state == tunnel.OFF  # nosec B101


def test_a_refused_post_does_not_spill_into_the_next_request(server):
    import socket

    body = b'{"id":"zr2jzaht3e","data":"AAAA"}'
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    # Refused (no token), and the page's next request on the same
    # connection right behind it.
    sock.sendall(
        b"POST /api/voice/wake HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        + b"GET /api/state?lite=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    said = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        said += chunk
    sock.close()

    assert said.startswith(b"HTTP/1.1 403")  # nosec B101
    assert b"501" not in said and b"Unsupported method" not in said
