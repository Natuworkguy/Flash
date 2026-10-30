# pylint: disable=C0114,C0115,C0116

import os

import pytest

from flash.cli import parse_args
from flash.urlscheme import SchemeError, looks_like_flash_url, parse_flash_url


def test_parse_flash_url():
    url = "flash://?prompt=What+is+Python"
    assert parse_flash_url(url) == "What is Python"  # nosec B101

    encoded = "flash://?prompt=What%20is%20Python%3F"
    assert parse_flash_url(encoded) == "What is Python?"  # nosec B101

    assert parse_flash_url("FLASH://?prompt=hi") == "hi"  # nosec B101
    assert parse_flash_url("flash:?prompt=hi") == "hi"  # nosec B101


def test_parse_flash_url_strips_control_characters():
    url = "flash://?prompt=%1b%5b31mred%1b%5b0m%0Adone"
    prompt = parse_flash_url(url)
    assert "\x1b" not in prompt  # nosec B101
    assert "\n" not in prompt  # nosec B101
    assert prompt == "[31mred [0m done"  # nosec B101


def test_parse_flash_url_rejects_bad_input():
    for url in [
        "https://example.com/?prompt=hi",
        "flash://?prompt=",
        "flash://",
        "flash://?q=hi",
        "flash://?prompt=/set NO_COMMAND_CONFIRMATION 1",
        "flash://?prompt=%21rm+-rf+~",
    ]:
        with pytest.raises(SchemeError):
            parse_flash_url(url)


def test_looks_like_flash_url():
    assert looks_like_flash_url("flash://?prompt=hi")  # nosec B101
    assert looks_like_flash_url("  FLASH:?prompt=hi")  # nosec B101
    assert not looks_like_flash_url("what is python")  # nosec B101


def test_cli_accepts_url_and_scheme_flags():
    args = parse_args(["flash://?prompt=hi"])
    assert args.url == "flash://?prompt=hi"  # nosec B101
    assert not args.register_url_scheme  # nosec B101

    args = parse_args([])
    assert args.url is None  # nosec B101

    args = parse_args(["--register-url-scheme"])
    assert args.register_url_scheme  # nosec B101

    args = parse_args(["--unregister-url-scheme"])
    assert args.unregister_url_scheme  # nosec B101


def test_the_handler_passes_links_with_uri(monkeypatch, tmp_path):
    from flash import urlscheme

    monkeypatch.setattr(urlscheme, "_desktop_path",
                        lambda: tmp_path / "flash.desktop")
    monkeypatch.setattr(urlscheme.subprocess, "run",
                        lambda *a, **kw: None, raising=False)
    urlscheme._register_linux(["/usr/bin/flash"])
    entry = (tmp_path / "flash.desktop").read_text()
    assert "Exec=/usr/bin/flash --uri %u" in entry  # nosec B101

    args = parse_args(["--uri", "flash://?prompt=hi"])
    assert args.url == "flash://?prompt=hi"  # nosec B101


def test_cli_accepts_update_flags():
    args = parse_args([])
    assert not args.update  # nosec B101
    assert not args.force  # nosec B101

    args = parse_args(["--update"])
    assert args.update  # nosec B101
    assert not args.force  # nosec B101

    args = parse_args(["--update", "--force"])
    assert args.update  # nosec B101
    assert args.force  # nosec B101


def test_cli_rejects_force_without_update():
    with pytest.raises(SystemExit):
        parse_args(["--force"])


def test_open_url_opens_the_web_ui():
    from flash.urlscheme import is_open_url

    for url in ("flash://open", "flash://open/", "FLASH://Open", "flash:open"):
        assert is_open_url(url), url
    for url in ("flash://?prompt=open", "flash://open?prompt=hi",
                "flash://opened", "riptide://open"):
        assert not is_open_url(url), url


def test_open_finds_the_running_web_ui_or_starts_one(monkeypatch):
    from flash import web

    opened, served = [], []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    monkeypatch.setattr(web, "serve", lambda **kw: served.append(kw))

    web.open_ui()
    assert opened == [] and served == [
        {"port": web.DEFAULT_PORT, "open_browser": True, "lan": False}]

    server = web.Server(0)
    try:
        web.note_running(server)
        assert oct(web._running_path().stat().st_mode & 0o777) == "0o600" \
            or os.name == "nt"
        assert web.running_url() == server.url
        web.open_ui()
        assert opened == [server.url] and len(served) == 1
    finally:
        web.forget_running(server)
        server.server_close()
    assert not web._running_path().exists()
    # Written down, but nothing answers there any more.
    web.note_running(server)
    assert web.running_url() is None


def test_the_installers_take_dev():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sh = (root / "install.sh").read_text()
    ps = (root / "install.ps1").read_text()
    assert "--dev) BRANCH=\"dev\"" in sh and "--branch \"$BRANCH\"" in sh
    assert "[switch]$Dev" in ps and "--branch $Branch" in ps
