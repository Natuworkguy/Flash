# pylint: disable=C0114,C0116

import json
import platform
import shutil
import subprocess

import pytest

from flash import siri, voice, web

# The real one, taken before conftest swaps it for none.
REAL_VOICES = siri.voices

NORA = {"id": "com.apple.siri.natural.Nora", "name": "siri-natural.Nora",
        "label": "Siri Voice 4, en-US", "language": "en-US"}


@pytest.fixture
def nora(monkeypatch):
    """A Mac with one Siri voice, speaking a second of silence."""

    monkeypatch.setattr(siri, "voices", lambda refresh=False: [NORA])
    said = []

    def speak(name, text, pace=1.0):
        said.append((name, text, pace))
        return b"\x00\x00" * 4800, 48000

    monkeypatch.setattr(siri, "speak_pcm", speak)
    return said


def test_a_voice_is_named_by_what_follows_siris_prefix():
    assert siri.name_of("com.apple.siri.natural.Nora") == "siri-natural.Nora"
    assert siri.identifier("siri-natural.Nora") == NORA["id"]
    assert siri.is_siri("siri-natural.Nora")
    assert not siri.is_siri("kokoro-af_heart")


def test_the_voices_come_from_the_helper(monkeypatch):
    monkeypatch.setattr(siri, "voices", REAL_VOICES)
    monkeypatch.setattr(siri, "_listed", (0.0, []))
    monkeypatch.setattr(siri, "tools_installed", lambda: True)
    monkeypatch.setattr(siri, "_ask", lambda request, seconds: (
        {"ok": True, "voices": [
            {"id": "com.apple.siri.natural.Quinn", "name": "Voice 5",
             "language": "en-US"},
            {"id": "com.apple.siri.natural.Nora", "name": "Voice 4",
             "language": "en-US"},
            {"id": "com.apple.siri.bad name", "name": "x", "language": "en"},
            {"id": "com.apple.speech.Fred", "name": "Fred", "language": "en"},
        ]}, b""))

    found = siri.voices(refresh=True)

    assert [v["name"] for v in found] == ["siri-natural.Nora",
                                          "siri-natural.Quinn"]
    assert found[0]["label"] == "Siri Voice 4, en-US"


def test_no_command_line_tools_means_no_voices_and_says_how(monkeypatch):
    monkeypatch.setattr(siri, "voices", REAL_VOICES)
    monkeypatch.setattr(siri, "_listed", (0.0, []))
    monkeypatch.setattr(siri, "tools_installed", lambda: False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    assert siri.voices(refresh=True) == []
    assert "xcode-select --install" in siri.note()


def test_with_none_downloaded_it_says_where_to_get_them(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(siri, "tools_installed", lambda: True)

    assert "Manage Voices" in siri.note()


def test_nothing_is_said_off_a_mac_or_with_voices(monkeypatch, nora):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(siri, "tools_installed", lambda: True)
    assert siri.note() == ""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(siri, "voices", lambda refresh=False: [])
    assert siri.note() == ""


def test_settings_offers_them_last_under_their_own_engine(monkeypatch, nora):
    from flash import apple_listen

    monkeypatch.setattr(apple_listen, "available", lambda: False)
    names = [c.name for c in voice.offered("speaking")]

    assert names[-1] == "siri-natural.Nora"
    assert voice.engine_label("siri-natural.Nora") == \
        "Siri, built into this Mac"
    assert voice.voice_installed("siri-natural.Nora")
    assert not voice.voice_installed("siri-natural.Gone")
    assert [c.name for c in voice.offered("listening")] == [
        c.name for c in voice.LISTENING]


def test_a_siri_voice_needs_no_voice_package(monkeypatch, nora):
    monkeypatch.setenv("VOICE_PIPER_VOICE", "siri-natural.Nora")

    assert "piper-tts" not in voice.missing_packages()


def test_it_speaks_as_a_wav_the_page_can_play(monkeypatch, nora):
    monkeypatch.setenv("VOICE_PIPER_VOICE", "siri-natural.Nora")

    wav, why = voice.synthesize("Hello there. How are you?")

    assert not why and wav[:4] == b"RIFF"
    assert [name for name, _, _ in nora] == ["siri-natural.Nora"] * len(nora)
    assert nora and "Hello there." in nora[0][1]


def test_a_spark_can_speak_in_it_beside_flashs_own(monkeypatch, nora):
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_US-amy-low")

    assert voice.can_speak_with("siri-natural.Nora")
    wav, why = voice.synthesize("Hello there.", speaker="siri-natural.Nora")

    assert not why and wav[:4] == b"RIFF" and nora


def test_a_voice_taken_out_of_macos_says_so(monkeypatch):
    monkeypatch.setenv("VOICE_PIPER_VOICE", "siri-natural.Gone")

    wav, why = voice.synthesize("Hello there.")

    assert not wav and "not installed" in why


def test_it_is_not_deleted_or_downloaded_from_flash(nora):
    with pytest.raises(ValueError, match="belongs to macOS"):
        voice.remove_model("speaking", "siri-natural.Nora")
    assert "Manage Voices" in voice.download_voice(
        "siri-natural.Gone", lambda *_: None)


def test_settings_marks_it_as_the_macs_own(monkeypatch, nora):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(siri, "tools_installed", lambda: True)

    listed = web.voice_models(web.Session())

    siri_row = next(m for m in listed["speaking"]
                    if m["name"] == "siri-natural.Nora")
    assert siri_row["system"] and siri_row["installed"]
    assert siri_row["engine"] == "Siri, built into this Mac"
    assert siri_row["size"] == 0
    # the Mac's own speech recognition is its one built-in way to listen
    assert [m["name"] for m in listed["listening"] if m["system"]] == ["apple"]
    assert listed["siri_note"] == ""


def test_the_page_picks_it_like_any_voice(monkeypatch, nora):
    session = web.Session()

    web.command(session, {"name": "voice-model", "arg": "siri-natural.Nora",
                          "kind": "speaking"})

    assert voice.piper_voice() == "siri-natural.Nora"


@pytest.mark.skipif(
    platform.system() != "Darwin" or not shutil.which("swift"),
    reason="the helper runs with swift, on a Mac")
def test_the_helper_answers_a_list_and_an_unknown_voice():
    if not siri.tools_installed():
        pytest.skip("no command line tools")
    helper = subprocess.Popen(  # nosec B603
        ["swift", str(siri.SOURCE)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        def ask(request):
            helper.stdin.write((json.dumps(request) + "\n").encode())
            helper.stdin.flush()
            head = json.loads(helper.stdout.readline())
            return head, helper.stdout.read(head["bytes"])

        head, _ = ask({"cmd": "list"})
        assert head["ok"] and isinstance(head["voices"], list)
        assert all(v["id"].startswith("com.apple.siri.")
                   for v in head["voices"])
        head, audio = ask({"cmd": "say", "voice": "com.apple.siri.none",
                           "text": "Hi"})
        assert not head["ok"] and not audio
    finally:
        helper.stdin.close()
        helper.wait(timeout=10)
