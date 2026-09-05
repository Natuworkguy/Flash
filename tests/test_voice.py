# pylint: disable=C0114,C0115,C0116

import array
import json
import sys
import threading
import types
import zipfile

import pytest

from flash import voice
from flash.voice import (
    _heard_interruption,
    _load_speaker,
    ensure_models,
    for_speech,
    is_exit_phrase,
    listen,
    models_present,
    speak,
)


def _pcm(amplitude: int, frames: int = voice.BLOCK_FRAMES) -> bytes:
    """One block of audio at a constant level."""

    return array.array("h", [amplitude] * frames).tobytes()


class _Stream:
    """Stands in for a sounddevice stream, playing a scripted recording."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.written = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, frames):
        block = self.blocks.pop(0) if self.blocks else _pcm(0, frames)
        return block, False

    def write(self, data):
        self.written.append(bytes(data))

    def abort(self):
        self.aborted = True


class _Recognizer:
    def __init__(self, *_):
        self.fed = []

    def AcceptWaveform(self, data):
        self.fed.append(data)
        return False

    def FinalResult(self):
        return json.dumps({"text": "run the tests"})


def _fake_audio(monkeypatch, blocks=(), stream=None):
    """Install fake vosk and sounddevice modules for one test."""

    stream = stream or _Stream(blocks)

    vosk = types.ModuleType("vosk")
    vosk.SetLogLevel = lambda level: None
    vosk.Model = lambda path: f"model at {path}"
    vosk.KaldiRecognizer = _Recognizer

    sounddevice = types.ModuleType("sounddevice")
    sounddevice.RawInputStream = lambda **_: stream
    sounddevice.RawOutputStream = lambda **_: stream

    monkeypatch.setitem(sys.modules, "vosk", vosk)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setattr(voice, "_listener", None)
    monkeypatch.setattr(voice, "_speaker", None)

    return stream


def test_for_speech_drops_what_cannot_be_heard():
    spoken = for_speech(
        "## Findings\n\n"
        "Use `fetch` for **text**, see [the docs](https://example.com).\n\n"
        "```python\nprint('hi')\n```\n\n"
        "- first\n- second\n\n"
        "| a | b |\n| - | - |\n"
    )

    assert "```" not in spoken  # nosec B101
    assert "print" not in spoken  # nosec B101
    assert "https://" not in spoken  # nosec B101
    assert "**" not in spoken  # nosec B101
    assert "| a |" not in spoken  # nosec B101
    assert "Use fetch for text, see the docs." in spoken  # nosec B101
    assert "first" in spoken  # nosec B101


def test_for_speech_says_when_the_reply_was_only_code():
    assert for_speech("```sh\nls -la\n```") == voice.CODE_ONLY  # nosec B101


def test_for_speech_says_nothing_about_nothing():
    assert for_speech("   ") == ""  # nosec B101


def test_for_speech_stops_at_a_sentence():
    spoken = for_speech("A sentence. " * 100, limit=100)

    assert spoken.endswith(voice.CUT_SHORT)  # nosec B101
    assert "sentence. The rest" in spoken  # nosec B101
    assert len(spoken) < 140  # nosec B101


def test_for_speech_keeps_a_short_reply_whole():
    assert for_speech("All done.") == "All done."  # nosec B101


@pytest.mark.parametrize("said", [
    "voice off",
    "Voice off.",
    "exit voice mode",
    "stop listening",
    "  turn  off  voice  ",
])
def test_is_exit_phrase_hears_a_request_to_leave(said):
    assert is_exit_phrase(said)  # nosec B101


@pytest.mark.parametrize("said", [
    "",
    "what does voice off do",
    "turn the voice off in the video player",
    "off",
])
def test_is_exit_phrase_leaves_a_real_message_alone(said):
    assert not is_exit_phrase(said)  # nosec B101


def test_listen_passes_a_keyboard_interrupt_on(monkeypatch):
    """Ctrl+C is the user cancelling, not the microphone failing."""

    stream = _fake_audio(monkeypatch)

    def interrupted(_frames):
        raise KeyboardInterrupt

    stream.read = interrupted

    with pytest.raises(KeyboardInterrupt):
        listen(lambda _state: None)


def test_level_tells_speech_from_silence():
    assert voice._level(_pcm(0)) == 0  # nosec B101
    assert voice._level(_pcm(6000)) > voice.silence_threshold()  # nosec B101


def test_listen_returns_what_was_said(monkeypatch):
    speech = [_pcm(6000)] * 5
    quiet = [_pcm(0)] * 20
    stream = _fake_audio(monkeypatch, speech + quiet)

    states = []
    heard, why = listen(states.append)

    assert why == ""  # nosec B101
    assert heard == "run the tests"  # nosec B101
    assert states == ["listening", "transcribing"]  # nosec B101
    # It stops on the silence rather than reading the whole recording.
    assert len(stream.blocks) > 5  # nosec B101


def test_listen_gives_up_when_nobody_speaks(monkeypatch):
    _fake_audio(monkeypatch, [_pcm(0)] * 200)

    heard, why = listen(lambda _state: None)

    assert heard == ""  # nosec B101
    assert why == ""  # nosec B101


def test_install_hint_matches_how_flash_was_installed(monkeypatch):
    monkeypatch.setattr(
        voice.sys, "prefix", "/home/me/.local/pipx/venvs/flash",
    )
    assert voice._install_command().startswith("pipx inject")  # nosec B101

    monkeypatch.setattr(voice.sys, "prefix", "/usr/local")
    assert voice._install_command().startswith("pip install")  # nosec B101


def test_listen_explains_a_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "vosk", None)

    heard, why = listen(lambda _state: None)

    assert heard == ""  # nosec B101
    assert why == voice.INSTALL_HINT  # nosec B101


def test_listen_explains_a_missing_microphone(monkeypatch):
    _fake_audio(monkeypatch)
    sounddevice = sys.modules["sounddevice"]

    def no_microphone(**_):
        raise OSError("no default input device")

    sounddevice.RawInputStream = no_microphone

    heard, why = listen(lambda _state: None)

    assert heard == ""  # nosec B101
    assert "microphone" in why  # nosec B101


def _fake_piper(monkeypatch, tmp_path, *, streaming: bool):
    """Install a fake piper module and the voice files it loads."""

    onnx = tmp_path / f"{voice.piper_voice()}.onnx"
    onnx.write_bytes(b"network")
    onnx.with_suffix(".onnx.json").write_text(
        json.dumps({"audio": {"sample_rate": 16000}}), encoding="utf-8"
    )
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)

    class _Chunk:
        audio_int16_bytes = _pcm(1000, 8)

    class _Voice:
        @staticmethod
        def load(path):
            loaded = _Voice()
            if streaming:
                loaded.synthesize_stream_raw = lambda text: [_pcm(1000, 8)]
            return loaded

        def synthesize(self, text):
            return [_Chunk()]

    piper = types.ModuleType("piper")
    piper.PiperVoice = _Voice
    monkeypatch.setitem(sys.modules, "piper", piper)


@pytest.mark.parametrize("streaming", [True, False])
def test_speak_plays_audio_through_both_piper_apis(
    monkeypatch, tmp_path, streaming,
):
    stream = _fake_audio(monkeypatch)
    _fake_piper(monkeypatch, tmp_path, streaming=streaming)

    why, interrupted = speak("all done")

    assert why == ""  # nosec B101
    assert interrupted is False  # nosec B101
    assert stream.written == [_pcm(1000, 8)]  # nosec B101


def test_speak_says_nothing_about_an_empty_reply(monkeypatch):
    _fake_audio(monkeypatch)

    assert speak("  ") == ("", False)  # nosec B101


def test_models_present_needs_both_models(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)
    assert not models_present()  # nosec B101

    (tmp_path / voice.vosk_model() / "am").mkdir(parents=True)
    assert not models_present()  # nosec B101

    onnx = tmp_path / f"{voice.piper_voice()}.onnx"
    onnx.write_bytes(b"network")
    onnx.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")
    assert models_present()  # nosec B101


def test_ensure_models_downloads_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)
    asked = []

    def fake_download(url, out, label, on_progress):
        asked.append(label)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"downloaded")
        on_progress(label, 100)
        return ""

    def fake_unpack(archive, into):
        (into / voice.vosk_model() / "am").mkdir(parents=True)
        archive.unlink()
        return ""

    monkeypatch.setattr(voice, "_download", fake_download)
    monkeypatch.setattr(voice, "_unpack", fake_unpack)

    progress = []
    assert ensure_models(  # nosec B101
        lambda label, percent: progress.append((label, percent))
    ) == ""

    assert asked == [  # nosec B101
        "listening model", "voice", "voice settings",
    ]
    assert progress[-1] == ("voice settings", 100)  # nosec B101
    assert models_present()  # nosec B101


def test_ensure_models_reports_a_failed_download(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(
        voice,
        "_download",
        lambda url, out, label, on_progress: f"could not download {label}",
    )

    why = ensure_models(lambda label, percent: None)

    assert why == "could not download listening model"  # nosec B101


def test_ensure_models_does_nothing_when_both_are_there(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)
    (tmp_path / voice.vosk_model() / "am").mkdir(parents=True)
    onnx = tmp_path / f"{voice.piper_voice()}.onnx"
    onnx.write_bytes(b"network")
    onnx.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")

    def refuse(*_args, **_kwargs):
        raise AssertionError("nothing should be downloaded")

    monkeypatch.setattr(voice, "_download", refuse)

    assert ensure_models(lambda label, percent: None) == ""  # nosec B101


def test_unpack_refuses_a_path_outside_the_model_directory(tmp_path):
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.txt", "no")

    why = voice._unpack(archive, tmp_path / "models")

    assert "unsafe path" in why  # nosec B101
    assert not (tmp_path / "escaped.txt").exists()  # nosec B101


def test_piper_urls_follow_the_voice_name():
    onnx_url, config_url = voice._piper_urls()

    assert onnx_url.endswith(  # nosec B101
        f"/amy/medium/{voice.piper_voice()}.onnx"
    )
    assert config_url == f"{onnx_url}.json"  # nosec B101


@pytest.mark.parametrize("said", [
    "interrupt",
    "wait interrupt",
    "Interrupt!",
    "stop talking",
    "be quiet",
])
def test_heard_interruption_recognizes_being_talked_over(said):
    assert _heard_interruption(said)  # nosec B101


@pytest.mark.parametrize("said", [
    "",
    "the interrupted process",
    "tell me about interrupts",
    "stop the server",
])
def test_heard_interruption_ignores_ordinary_speech(said):
    assert not _heard_interruption(said)  # nosec B101


class _Interrupting:
    """Stands in for the barge-in listener, having already heard it."""

    def __init__(self):
        self.heard = threading.Event()
        self.heard.set()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_speak_stops_when_it_is_interrupted(monkeypatch, tmp_path):
    stream = _fake_audio(monkeypatch)
    _fake_piper(monkeypatch, tmp_path, streaming=False)
    monkeypatch.setattr(voice, "_Barge", _Interrupting)

    why, interrupted = speak("a long answer nobody wanted")

    assert why == ""  # nosec B101
    assert interrupted is True  # nosec B101
    assert stream.written == []  # nosec B101
    assert stream.aborted is True  # nosec B101


def test_barge_survives_a_machine_with_no_microphone(monkeypatch):
    _fake_audio(monkeypatch)
    sounddevice = sys.modules["sounddevice"]

    def no_microphone(**_):
        raise OSError("no default input device")

    sounddevice.RawInputStream = no_microphone

    with voice._Barge() as barge:
        pass

    assert not barge.heard.is_set()  # nosec B101


def test_settings_are_read_after_the_env_file_loads(monkeypatch):
    """~/.flash.env is loaded after this module is imported.

    Reading these at import time would mean /set VOICE_... never took
    effect, so every setting has to be read when it is used.
    """

    monkeypatch.setenv("VOICE_MAX_CHARS", "120")
    monkeypatch.setenv("VOICE_SILENCE_SECONDS", "0.5")
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_GB-alba-medium")

    assert voice.max_speech_chars() == 120  # nosec B101
    assert voice.silence_seconds() == 0.5  # nosec B101
    assert voice.piper_voice() == "en_GB-alba-medium"  # nosec B101


@pytest.mark.parametrize(("name", "value"), [
    ("VOICE_MAX_CHARS", "loads"),
    ("VOICE_SILENCE_SECONDS", ""),
    ("VOICE_SILENCE_THRESHOLD", "quiet please"),
    ("VOICE_NO_SPEECH_SECONDS", "8 seconds"),
])
def test_an_unreadable_setting_falls_back_instead_of_crashing(
    monkeypatch, name, value,
):
    monkeypatch.setenv(name, value)

    assert voice.max_speech_chars() == 700  # nosec B101
    assert voice.silence_seconds() == 1.2  # nosec B101
    assert voice.silence_threshold() == 500  # nosec B101
    assert voice.no_speech_seconds() == 8  # nosec B101


def test_a_setting_below_its_minimum_is_lifted_to_it(monkeypatch):
    monkeypatch.setenv("VOICE_SILENCE_SECONDS", "0")

    assert voice.silence_seconds() == 0.2  # nosec B101


@pytest.mark.parametrize(("name", "path"), [
    ("en_US-amy-medium", "en/en_US/amy/medium/en_US-amy-medium.onnx"),
    ("en_GB-alba-medium", "en/en_GB/alba/medium/en_GB-alba-medium.onnx"),
    ("de_DE-thorsten-low", "de/de_DE/thorsten/low/de_DE-thorsten-low.onnx"),
])
def test_piper_urls_follow_the_voice_locale(monkeypatch, name, path):
    """Every part of the address comes from the voice's own name."""

    monkeypatch.setenv("VOICE_PIPER_VOICE", name)

    onnx_url, config_url = voice._piper_urls()

    assert onnx_url == f"{voice.PIPER_BASE}/{path}"  # nosec B101
    assert config_url == f"{onnx_url}.json"  # nosec B101


def test_ensure_models_rejects_a_name_that_is_not_a_voice(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path)
    monkeypatch.setenv("VOICE_PIPER_VOICE", "amy")
    (tmp_path / voice.vosk_model() / "am").mkdir(parents=True)

    why = ensure_models(lambda label, percent: None)

    assert "not a Piper voice name" in why  # nosec B101


def test_a_multi_word_interrupt_setting_is_matched_as_a_phrase(monkeypatch):
    monkeypatch.setenv("VOICE_INTERRUPT_WORD", "hey flash")

    assert _heard_interruption("ok hey flash stop")  # nosec B101
    assert not _heard_interruption("interrupt")  # nosec B101


def test_the_speaker_is_reloaded_when_the_voice_changes(
    monkeypatch, tmp_path,
):
    # Loading leaves the voice cached, so the next test must not find it.
    monkeypatch.setattr(voice, "_speaker", None)
    _fake_piper(monkeypatch, tmp_path, streaming=False)
    loaded = _load_speaker()

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(voice, "MODELS_DIR", other)
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_GB-alba-medium")
    onnx = other / "en_GB-alba-medium.onnx"
    onnx.write_bytes(b"network")
    onnx.with_suffix(".onnx.json").write_text(
        json.dumps({"audio": {"sample_rate": 22050}}), encoding="utf-8"
    )

    assert _load_speaker()[1] == 22050  # nosec B101
    assert loaded[1] == 16000  # nosec B101
