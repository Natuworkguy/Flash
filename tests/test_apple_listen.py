"""Hearing with the Mac's own speech recognition, through a small app Flash builds and opens for each thing said."""

import json
import plistlib
import subprocess
import wave

import pytest

from flash import apple_listen, voice


@pytest.fixture
def mac(monkeypatch, tmp_path):
    """A Mac that can build the helper, the build and the opening faked; what was opened, kept."""

    opened = []
    monkeypatch.setattr(apple_listen, "available", lambda: True)
    monkeypatch.setattr(apple_listen, "_home", lambda: tmp_path / "AppleListen.app")

    def run(argv, **kw):
        if argv[0].endswith("swiftc"):
            out = argv[argv.index("-o") + 1]
            open(out, "wb").write(b"binary")
        elif argv[0] == "open":
            args = argv[argv.index("--args") + 1:]
            with wave.open(args[0]) as w:
                frames = w.getnframes()
                assert w.getframerate() == 16000 and w.getnchannels() == 1
            opened.append((argv, frames))
            answer = {"ok": True, "text": "Turn the lights off in the kitchen."} if frames else {"ok": True, "text": ""}
            open(args[1], "w").write(json.dumps(answer))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apple_listen.subprocess, "run", run)
    return opened


def test_the_helper_is_an_app_that_says_why_it_listens(mac, tmp_path):
    home = apple_listen.app()
    info = plistlib.loads((home / "Contents" / "Info.plist").read_bytes())
    assert "NSSpeechRecognitionUsageDescription" in info and info["LSUIElement"] is True
    assert (home / "Contents" / "MacOS" / "AppleListen").is_file()
    # built once: asked again, nothing is rebuilt
    stamp = (home / "Contents" / "Resources" / "source").stat().st_mtime_ns
    apple_listen.app()
    assert (home / "Contents" / "Resources" / "source").stat().st_mtime_ns == stamp


def test_what_was_said_is_heard_by_opening_the_app(mac):
    pcm = b"\x01\x00" * 16000
    assert apple_listen.transcribe(pcm) == "Turn the lights off in the kitchen."
    argv, frames = mac[-1]
    # opened as an app of its own, so macOS asks about it and not the terminal
    assert argv[:2] == ["open", "-n"] and "-a" in argv and frames == 16000


def test_it_is_a_listening_choice_and_hears_through_voice(mac, monkeypatch):
    assert "apple" in {c.name for c in voice.offered("listening")}
    assert voice.engine_label("apple") == "Apple, built into this Mac"
    monkeypatch.setattr(voice, "vosk_model", lambda: "apple")
    assert voice.transcribe(b"\x01\x00" * 8000) == ("Turn the lights off in the kitchen.", "")
    with pytest.raises(ValueError):
        voice.remove_model("listening", "apple")


def test_a_refusal_is_said_not_crashed(mac, monkeypatch):
    def refuse(argv, **kw):
        if argv[0] == "open":
            open(argv[argv.index("--args") + 2], "w").write(json.dumps({"ok": False, "error": "Speech recognition is off for Flash."}))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(apple_listen.subprocess, "run", refuse)
    h = apple_listen.AppleHearing()
    h.feed(b"\x01\x00" * 100)
    assert h.final() == "" and "off for Flash" in h.error


def test_not_offered_off_a_mac(monkeypatch):
    monkeypatch.setattr(apple_listen, "available", lambda: False)
    assert "apple" not in {c.name for c in voice.offered("listening")}
