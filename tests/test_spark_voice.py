# pylint: disable=C0114,C0115,C0116

import pytest

from flash import sparks, voice, web
from tests import test_extensions


def a_spark(**changes):
    made = sparks.create("Scout", "Watch the issues.")
    return sparks.update(made.id, **changes) if changes else made


def test_a_spark_with_no_choice_gets_one_picked(monkeypatch):
    made = a_spark()
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_US-amy-low")

    picked = voice.spark_voice(made.id)

    assert set(picked) == {"detune"}
    assert voice.spark_voice(made.id) == picked  # the same every call


def test_a_chosen_voice_and_pitch_are_used(tmp_path, monkeypatch):
    test_extensions.TestSpeechAndBriefing().speechy(tmp_path)
    made = a_spark(voice="@talky/aura", pitch=3)

    assert voice.spark_voice(made.id) == {"voice": "@talky/aura",
                                          "detune": 300}
    # It speaks in it, beside Flash's own voice.
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_US-amy-low")
    wav, why = voice.synthesize("hello there", speaker="@talky/aura")
    assert not why and wav[:4] == b"RIFF"


def test_a_voice_that_cannot_be_used_falls_back(monkeypatch):
    made = a_spark(voice="@gone/voice")
    monkeypatch.setenv("VOICE_PIPER_VOICE", "en_US-amy-low")

    assert "voice" not in voice.spark_voice(made.id)


def test_pitch_is_kept_in_range_and_must_be_a_number():
    made = a_spark(pitch=40)
    assert made.pitch == sparks.PITCH_MOST
    with pytest.raises(sparks.SparkError, match="semitones"):
        sparks.update(made.id, pitch="high")


def test_the_page_sets_them():
    made = a_spark()
    session = web.Session()

    web.command(session, {"name": "spark-update", "arg": made.id,
                          "voice": "en_US-ryan-high", "pitch": "-2"})

    kept = sparks.find(made.id)
    assert (kept.voice, kept.pitch) == ("en_US-ryan-high", -2)


def test_a_voice_is_tried_before_it_is_saved(tmp_path, monkeypatch):
    import base64
    import json

    from tests.test_web import request

    test_extensions.TestSpeechAndBriefing().speechy(tmp_path)
    heard = []
    monkeypatch.setattr(web.voice, "synthesize", lambda text, speaker="": (
        heard.append(speaker) or b"RIFF", ""))
    srv = web.Server(0)
    try:
        import threading

        threading.Thread(target=srv.serve_forever, daemon=True).start()
        status, body = request(srv, "POST", "/api/voice/say", {
            "text": "hi", "voice": "@talky/aura", "pitch": -3})
    finally:
        srv.shutdown()
        srv.server_close()

    said = json.loads(body)
    assert status == 200 and heard == ["@talky/aura"]
    assert said["detune"] == -300 and base64.b64decode(said["audio"])
