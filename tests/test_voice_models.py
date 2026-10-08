# pylint: disable=C0114,C0116

import io

import pytest

from flash import ai, voice


@pytest.fixture
def printed(monkeypatch):
    lines = []

    def keep(*args, **_):
        if args:
            out = ai.Console(width=200, file=io.StringIO())
            out.print(args[0])
            lines.append(out.file.getvalue())

    monkeypatch.setattr(ai.console, "print", keep)
    monkeypatch.setattr(ai, "warn", lambda text: lines.append(f"warn: {text}"))
    monkeypatch.setattr(ai, "show_error",
                        lambda text: lines.append(f"error: {text}"))
    return lines


@pytest.mark.parametrize("query, kind, name", [
    ("en_US-ryan-high", "speaking", "en_US-ryan-high"),
    ("RYAN", "speaking", "en_US-ryan-high"),
    ("lgraph", "listening", "vosk-model-en-us-0.22-lgraph"),
    ("vosk-model-en-us-0.22", "listening", "vosk-model-en-us-0.22"),
])
def test_part_of_a_name_finds_a_model(query, kind, name):
    found_kind, choice = voice.find_model(query)

    assert (found_kind, choice.name) == (kind, name)


@pytest.mark.parametrize("query, why", [
    ("amy", "could be en_US-amy-low, en_US-amy-medium"),
    ("klingon", "No voice model called"),
    ("", "Name a model"),
])
def test_a_name_that_finds_none_or_several_says_so(query, why):
    with pytest.raises(ValueError, match=why):
        voice.find_model(query)


def test_models_lists_both_kinds_and_marks_the_ones_in_use(printed):
    ai._voice_models()

    shown = "".join(printed)
    assert "Listening, speech to text" in shown
    assert "Speaking, text to speech" in shown
    assert "●  vosk-model-small-en-us-0.15" in shown
    assert "●  en_US-amy-medium" in shown
    assert "1.9 GB" in shown and "41 MB" in shown


def test_pull_downloads_and_switches(monkeypatch, printed):
    fetched = []

    def download(kind, name, on_progress, stop):
        on_progress("voice", 50)
        fetched.append((kind, name))
        (voice.MODELS_DIR).mkdir(parents=True, exist_ok=True)
        for path in voice.piper_paths(name):
            path.write_text("model")
        return ""

    monkeypatch.setattr(voice, "download_model", download)

    ai._voice_pull("ryan")

    assert fetched == [("speaking", "en_US-ryan-high")]
    assert voice.piper_voice() == "en_US-ryan-high"
    assert "Flash speaks with en_US-ryan-high now." in printed[-1]

    # Already in: it only switches.
    ai._voice_pull("amy-medium")
    assert voice.piper_voice() == "en_US-amy-medium"


def test_a_stopped_pull_switches_nothing(monkeypatch, printed):
    monkeypatch.setattr(voice, "download_model",
                        lambda *_: voice.CANCELLED)

    ai._voice_pull("lessac")

    assert voice.piper_voice() == voice.DEFAULT_PIPER_VOICE
    assert "Download stopped" in printed[-1]


def test_a_failed_pull_says_why(monkeypatch, printed):
    monkeypatch.setattr(voice, "download_model",
                        lambda *_: "could not download voice: offline")

    ai._voice_pull("lessac")

    assert printed[-1] == "error: could not download voice: offline"


def test_remove_keeps_the_one_in_use(printed):
    ai._voice_remove("amy-medium")
    assert "is in use" in printed[-1]

    for path in voice.piper_paths("en_US-ryan-high"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("model")
    ai._voice_remove("ryan")
    assert printed[-1].strip() == "Removed en_US-ryan-high."
    assert not voice.voice_installed("en_US-ryan-high")
