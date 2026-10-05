# pylint: disable=C0114,C0115,C0116

import struct

import pytest

from flash import midi, tools, workspace
from flash.theme import capture_tool_output
from flash.tools import make_midi, send_midi

SONG = [
    {"name": "Melody", "instrument": "flute",
     "notes": [["C5", 0, 1], ["E5", 1, 1], [["C5", "E5", "G5"], 2, 2, 70]]},
    {"name": "Bass", "instrument": "bass", "notes": [["C2", 0, 4]]},
    {"instrument": "drums",
     "notes": [["kick", 0, 0.5], ["snare", 1, 0.5], ["hihat", 0.5, 0.25]]},
]


def _chunks(data):
    """A MIDI file as its chunks: (kind, body)."""

    at, found = 0, []
    while at < len(data):
        kind, size = struct.unpack(">4sI", data[at:at + 8])
        found.append((kind, data[at + 8:at + 8 + size]))
        at += 8 + size
    return found


@pytest.fixture
def quiet(monkeypatch):
    """No player opens: the terminal's fallback says it went fine."""

    monkeypatch.setattr(tools, "_open_with_spinner", lambda path: "")


# --- Pitches -----------------------------------------------------------------


@pytest.mark.parametrize("given, number", [
    ("C4", 60), ("c4", 60), ("A4", 69), ("F#3", 54), ("Bb2", 46),
    ("C-1", 0), ("G9", 127), (64, 64), ("E♭4", 63),
])
def test_pitches_by_name_or_number(given, number):
    assert midi.parse_pitch(given) == number


def test_drums_by_name():
    assert midi.parse_pitch("kick", drums=True) == 36
    assert midi.parse_pitch("Snare", drums=True) == 38
    with pytest.raises(midi.MidiError, match="a drum like"):
        midi.parse_pitch("tuba", drums=True)


@pytest.mark.parametrize("given", ["H4", "", "C10", 200, True])
def test_bad_pitches(given):
    with pytest.raises(midi.MidiError):
        midi.parse_pitch(given)


def test_note_names():
    assert midi.note_name(60) == "C4"
    assert midi.note_name(61) == "C#4"


def test_instruments_by_name_or_number():
    assert midi.program_of("flute") == 73
    assert midi.program_of(None) == 0
    assert midi.program_of(41) == 41
    with pytest.raises(midi.MidiError, match="program number"):
        midi.program_of("theremin")


# --- Writing -----------------------------------------------------------------


def test_a_song_is_a_type_one_file_with_a_track_each():
    data = midi.to_midi(midi.build_tracks(SONG), 100, "3/4", "Tune")

    chunks = _chunks(data)
    assert chunks[0][0] == b"MThd"
    kind, count, division = struct.unpack(">HHH", chunks[0][1])
    assert (kind, count, division) == (1, 4, midi.TICKS_PER_BEAT)
    # The conductor track: 100 BPM is 600000 microseconds a beat, and
    # 3/4 is three of 2**2.
    conductor = chunks[1][1]
    assert b"\xff\x51\x03" + (600_000).to_bytes(3, "big") in conductor
    assert b"\xff\x58\x04\x03\x02" in conductor
    assert b"Tune" in conductor


def test_drums_go_on_channel_ten_and_the_rest_share_none():
    built = midi.build_tracks(SONG)

    assert [t.channel for t in built] == [0, 1, 9]
    assert built[2].drums and built[2].name == "Drums"
    assert built[0].program == 73


def test_a_chord_is_its_notes_together():
    built = midi.build_tracks([{"notes": [[["C4", "E4", "G4"], 0, 2]]}])

    assert sorted(n[0] for n in built[0].notes) == [60, 64, 67]
    assert {n[1] for n in built[0].notes} == {0}


def test_notes_take_objects_too():
    built = midi.build_tracks([{"notes": [
        {"pitch": "A4", "start": 1, "duration": 0.5, "velocity": 40},
    ]}])

    assert built[0].notes == [(69, 480, 240, 40)]


def test_a_repeated_note_lets_go_before_it_sounds_again():
    data = midi.to_midi(midi.build_tracks(
        [{"notes": [["C4", 0, 1], ["C4", 1, 1]]}],
    ))

    body = _chunks(data)[2][1]
    off = body.index(bytes((0x80, 60, 0)))
    first_on = body.index(bytes((0x90, 60)))
    second_on = body.index(bytes((0x90, 60)), first_on + 1)
    assert off < second_on


@pytest.mark.parametrize("tracks, message", [
    ([], "at least one track"),
    ([{"notes": []}], "at least one note"),
    ([{"notes": [["C4", 0, 0]]}], "duration"),
    ([{"notes": [["C4", -1, 1]]}], "start"),
    ([{"notes": [["C4", 0]]}], "pitch, start, duration"),
    ([{"name": "Lead", "notes": [["Q4", 0, 1]]}], r"track 1 \(Lead\)"),
    ([{"notes": [["C4", 0, 1]]}] * 17, "at most 16"),
])
def test_bad_tracks_say_which_and_why(tracks, message):
    with pytest.raises(midi.MidiError, match=message):
        midi.build_tracks(tracks)


@pytest.mark.parametrize("tempo, signature", [(5, "4/4"), (120, "4/5"),
                                              (120, "four")])
def test_bad_tempo_or_signature(tempo, signature):
    with pytest.raises(midi.MidiError):
        midi.to_midi(midi.build_tracks(SONG), tempo, signature)


# --- Reading -----------------------------------------------------------------


def test_reading_it_back():
    data = midi.to_midi(midi.build_tracks(SONG), 120)

    found = midi.read(data)
    assert [t["name"] for t in found["tracks"]] == ["Melody", "Bass", "Drums"]
    assert [t["notes"] for t in found["tracks"]] == [5, 1, 3]
    # Four beats at 120 BPM.
    assert found["seconds"] == pytest.approx(2.0)
    assert found["bpm"] == 120


def test_describe_gives_what_to_check():
    text = midi.describe(midi.to_midi(midi.build_tracks(SONG), 120))

    assert text.startswith("3 tracks, 9 notes, 0:02 long at 120 BPM.")
    assert "- Melody (flute): 5 notes, C5 to G5" in text
    assert "- Drums (drums): 3 notes" in text


def test_a_file_that_is_not_midi():
    with pytest.raises(midi.MidiError, match="does not start like"):
        midi.read(b"RIFF....")


# --- The tools ---------------------------------------------------------------


def test_make_midi_writes_and_describes(tmp_path, quiet):
    target = tmp_path / "songs" / "tune"

    result = make_midi(str(target), SONG, tempo=90, title="Tune")

    written = tmp_path / "songs" / "tune.mid"
    assert midi.is_midi(written.read_bytes())
    assert f"Saved {written}" in result
    assert "3 tracks, 9 notes" in result


def test_make_midi_writes_nothing_for_a_bad_track(tmp_path, quiet):
    result = make_midi(str(tmp_path / "x.mid"), [{"notes": [["Z9", 0, 1]]}])

    assert result.startswith("Error:") and "Nothing was written" in result
    assert not (tmp_path / "x.mid").exists()


def test_make_midi_leaves_a_file_that_is_not_midi(tmp_path, quiet):
    other = tmp_path / "notes.mid"
    other.write_text("my notes")

    assert "left alone" in make_midi(str(other), SONG)
    assert other.read_text() == "my notes"


def test_send_midi_checks_what_it_is_given(tmp_path, quiet):
    fake = tmp_path / "fake.mid"
    fake.write_text("not music")
    wrong = tmp_path / "song.mp3"
    wrong.write_bytes(b"ID3")
    real = tmp_path / "real.midi"
    real.write_bytes(midi.to_midi(midi.build_tracks(SONG)))

    assert "does not start like" in send_midi(str(fake))
    assert "not a .mid or .midi" in send_midi(str(wrong))
    assert "no file" in send_midi(str(tmp_path / "gone.mid"))
    sent = send_midi(str(real))
    assert sent.startswith("Sent real.midi") and "3 tracks" in sent


def test_in_the_web_ui_it_goes_to_the_player(tmp_path):
    shown = []
    with capture_tool_output(lambda kind, text, style: shown.append(kind)):
        result = make_midi(str(tmp_path / "tune.mid"), SONG)

    assert "file" in shown
    assert "piano roll" in result


def test_kept_for_the_page_as_midi(tmp_path):
    song = tmp_path / "tune.mid"
    song.write_bytes(midi.to_midi(midi.build_tracks(SONG)))

    kept = workspace.keep_file(str(song))

    assert kept["kind"] == "midi" and kept["mime"] == "audio/midi"


def test_the_midi_tools_are_offered():
    names = {t["function"]["name"] for t in tools.tools}

    assert {"make_midi", "send_midi"} <= names
    assert tools.FUNCTIONS["make_midi"] is make_midi
    assert tools.FUNCTIONS["send_midi"] is send_midi
