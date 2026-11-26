# pylint: disable=C0114,C0116,W0621

import json
import threading
import time

import pytest

from flash import web
from tests.test_web import as_browser, request, sign_in


@pytest.fixture
def remembering(monkeypatch):
    monkeypatch.setenv(web.REMEMBER_SETTING, "1")


def _next_server():
    """Another Flash, started later: a new token, the same folder."""

    srv = web.Server(0)
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True,
    )
    thread.start()
    return srv


@pytest.fixture
def server():
    srv = _next_server()
    yield srv
    srv.shutdown()
    srv.server_close()


def _key(jar):
    return jar.split("=", 1)[1]


def test_off_by_default_a_new_flash_knows_no_one(server):
    jar = sign_in(server)
    later = _next_server()
    try:
        status, _ = request(later, "GET", "/", token=False, headers={
            "Cookie": f"{later.cookie_name}={_key(jar)}",
        })
    finally:
        later.shutdown()
        later.server_close()

    assert status == 403
    assert not web._remembered_path().exists()


def test_on_a_browser_opens_the_next_flash_without_the_link(
    remembering, server,
):
    jar = sign_in(server)
    # Kept by a fingerprint of the key, never the key.
    kept = web._remembered_path().read_text(encoding="utf-8")
    assert _key(jar) not in kept
    assert web._hashed(_key(jar)) in json.loads(kept)

    later = _next_server()
    try:
        status, body = request(later, "GET", "/", token=False, headers={
            "Cookie": f"{later.cookie_name}={_key(jar)}",
        })
        # The same browser, not a new one, and its cookie renewed.
        cookie = request.last.getheader("Set-Cookie")
        _, listed = as_browser(later, f"{later.cookie_name}={_key(jar)}",
                               "browsers")
    finally:
        later.shutdown()
        later.server_close()

    assert status == 200 and b"<title>Flash</title>" in body
    assert _key(jar) in cookie
    assert f"Max-Age={web.REMEMBER_DAYS * 86400}" in cookie
    assert len(listed["browsers"]) == 1 and listed["browsers"][0]["current"]
    assert listed["remember"] is True


def test_signed_out_is_forgotten_on_disk_too(remembering, server):
    jar = sign_in(server)
    _, listed = as_browser(server, jar, "browsers")

    as_browser(server, sign_in(server), "sign-out",
               listed["browsers"][0]["id"])

    assert web._hashed(_key(jar)) not in json.loads(
        web._remembered_path().read_text(encoding="utf-8")
    )


def test_a_browser_long_unseen_is_not_remembered(remembering):
    old = time.time() - (web.REMEMBER_DAYS + 1) * 86400
    web._remembered_path().parent.mkdir(parents=True, exist_ok=True)
    web._remembered_path().write_text(json.dumps({
        web._hashed("stale"): {"id": "a1", "since": old, "seen": old},
        web._hashed("fresh"): {"id": "b2", "since": old,
                               "seen": time.time()},
    }), encoding="utf-8")

    access = web.Access.kept()

    assert access.browser("fresh", "127.0.0.1") is not None
    assert access.browser("stale", "127.0.0.1") is None


def test_the_switch_writes_down_and_forgets(server):
    jar = sign_in(server)

    _, said = as_browser(server, jar, "remember-browsers", "on")
    assert said == {"remember": True}
    assert web._hashed(_key(jar)) in json.loads(
        web._remembered_path().read_text(encoding="utf-8")
    )

    _, said = as_browser(server, jar, "remember-browsers", "off")
    assert said == {"remember": False}
    assert not web._remembered_path().exists()
    # Still signed in until this Flash stops.
    assert request(server, "GET", "/", token=False,
                   headers={"Cookie": jar})[0] == 200
