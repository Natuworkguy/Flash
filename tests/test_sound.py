"""Measuring sounds, and making them from a score."""

import pytest

from flash import sound, tools

np = pytest.importorskip("numpy")


def test_frequency_and_notes():
    assert sound.frequency("A4") == 440.0
    assert round(sound.frequency("C4"), 2) == 261.63
    assert sound.frequency("Eb5") == sound.frequency("D#5")
    assert sound.frequency("300") == 300.0
    assert sound.note_of(440.0) == "A4"
    assert sound.note_of(452.0).startswith("A4 +")
    with pytest.raises(sound.SoundError, match="not a note"):
        sound.frequency("H9")


def test_parse():
    steps = sound.parse("C4 rest:0.5, C4+E4+G4:2")
    assert [len(f) for f, _ in steps] == [1, 0, 3]
    assert [b for _, b in steps] == [1.0, 0.5, 2.0]
    for bad in ("", "C4:x", "C4:0"):
        with pytest.raises(sound.SoundError):
            sound.parse(bad)


@pytest.mark.parametrize("note,kind", [("A4", "sine"), ("C4", "saw"),
                                       ("E2", "square"), ("G5", "triangle")])
def test_it_hears_the_note_it_made(tmp_path, note, kind):
    made = sound.make(f"{note}:2", tmp_path / "n", kind, 120)
    assert made["path"].endswith("n.wav") and made["seconds"] == 1.0
    assert sound.listen(made["path"])["note"].split()[0] == note


def test_silence_parts_and_tempo(tmp_path):
    made = sound.make(" ".join(["C3:0.25", "rest:0.75"] * 16),
                      tmp_path / "beat.wav", "square", 100)
    found = sound.listen(made["path"])
    assert abs(found["tempo_bpm"] - 100) <= 2
    assert 0.6 < found["silent_share"] < 0.85
    assert len(found["parts"]) == 16
    words = sound.describe(found)
    assert "BPM" in words and "silence" in words


def test_noise_has_no_pitch(tmp_path):
    made = sound.make("A4:2", tmp_path / "x.wav", "noise")
    found = sound.listen(made["path"])
    assert found["note"] == "" and found["brightness_hz"] > 4000


def test_limits_and_errors(tmp_path):
    with pytest.raises(sound.SoundError, match="at most"):
        sound.make("A4:2000", tmp_path / "x.wav", bpm=20)
    with pytest.raises(sound.SoundError, match="wave"):
        sound.make("A4", tmp_path / "x.wav", "organ")
    with pytest.raises(sound.SoundError, match="no file"):
        sound.listen(str(tmp_path / "gone.wav"))


def test_tools(tmp_path):
    out = tools.FUNCTIONS["make_sound"](
        notes="C4 E4 G4 C5:2", out=str(tmp_path / "j"))
    assert out.startswith("Made ") and "2.5 s" in out
    heard = tools.FUNCTIONS["listen_to_audio"](path=str(tmp_path / "j.wav"))
    assert "2.5 s long" in heard
    assert tools.listen_to_audio(str(tmp_path / "no.wav")).startswith(
        "Error")
    assert tools.make_sound("Q9").startswith("Error")
