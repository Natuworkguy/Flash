# pylint: disable=C0114,C0115,C0116

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
