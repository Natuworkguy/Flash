"""MIDI files: written from the notes the agent describes, and read back.

The agent describes a piece as tracks of notes, each note a pitch (a
name like C4 or F#3, or a MIDI number), when it starts and how long it
lasts in beats, and how hard it is played. A note can be a chord, a list
of pitches together, and a drum track names its sounds (kick, snare,
hihat). This writes that as a standard MIDI file, which any player, DAW
or notation program opens, and which the web UI plays in its own player.

Pure Python, like the 3D models: a MIDI file is a few small chunks of
bytes, and a song is a few thousand notes at most.
"""

import json
import re
import struct
from pathlib import Path
from typing import Any, Optional

TICKS_PER_BEAT = 480
MAX_TRACKS = 16
MAX_NOTES = 20_000
MAX_BEATS = 20_000
MAX_MIDI_BYTES = 5 * 1024 * 1024
MIDI_SUFFIXES = (".mid", ".midi")
DRUM_CHANNEL = 9

# General MIDI programs by the names a model reaches for first. Any
# other is its number, 0 to 127.
INSTRUMENTS = {
    "piano": 0, "bright piano": 1, "electric piano": 4,
    "harpsichord": 6, "celesta": 8, "glockenspiel": 9, "music box": 10,
    "vibraphone": 11, "marimba": 12, "xylophone": 13, "bells": 14,
    "organ": 16, "rock organ": 18, "church organ": 19, "accordion": 21,
    "harmonica": 22, "guitar": 24, "nylon guitar": 24, "steel guitar": 25,
    "electric guitar": 27, "clean guitar": 27, "muted guitar": 28,
    "overdriven guitar": 29, "distortion guitar": 30,
    "acoustic bass": 32, "bass": 33, "electric bass": 33,
    "fretless bass": 35, "slap bass": 36, "synth bass": 38,
    "violin": 40, "viola": 41, "cello": 42, "contrabass": 43,
    "tremolo strings": 44, "pizzicato": 45, "harp": 46, "timpani": 47,
    "strings": 48, "slow strings": 49, "synth strings": 50,
    "choir": 52, "voice": 53, "orchestra hit": 55,
    "trumpet": 56, "trombone": 57, "tuba": 58, "muted trumpet": 59,
    "french horn": 60, "horn": 60, "brass": 61, "synth brass": 62,
    "soprano sax": 64, "alto sax": 65, "sax": 65, "tenor sax": 66,
    "baritone sax": 67, "oboe": 68, "english horn": 69, "bassoon": 70,
    "clarinet": 71, "piccolo": 72, "flute": 73, "recorder": 74,
    "pan flute": 75, "whistle": 78, "ocarina": 79,
    "square lead": 80, "lead": 80, "saw lead": 81, "synth lead": 81,
    "pad": 88, "warm pad": 89, "choir pad": 91, "sitar": 104,
    "banjo": 105, "shamisen": 106, "koto": 107, "kalimba": 108,
    "bagpipe": 109, "fiddle": 110, "steel drums": 114,
}

# General MIDI drum sounds, on channel 10, by name.
DRUMS = {
    "kick": 36, "bass drum": 36, "rimshot": 37, "side stick": 37,
    "snare": 38, "clap": 39, "electric snare": 40,
    "low tom": 41, "floor tom": 43, "hihat": 42, "closed hihat": 42,
    "pedal hihat": 44, "tom": 45, "mid tom": 47, "open hihat": 46,
    "high tom": 50, "crash": 49, "ride": 51, "china": 52,
    "ride bell": 53, "tambourine": 54, "splash": 55, "cowbell": 56,
    "crash 2": 57, "vibraslap": 58, "ride 2": 59, "high bongo": 60,
    "low bongo": 61, "high conga": 63, "low conga": 64, "timbale": 65,
    "agogo": 67, "cabasa": 69, "maracas": 70, "whistle": 71,
    "guiro": 73, "claves": 75, "wood block": 76, "triangle": 81,
    "shaker": 82,
}

_NOTE = re.compile(r"^([A-Ga-g])([#b♯♭]*)(-?\d+)$")
_STEPS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#",
              "B")


class MidiError(ValueError):
    """A piece that cannot be written or a file that cannot be read."""


def note_name(number: int) -> str:
    """60 as C4, middle C."""

    return f"{NOTE_NAMES[number % 12]}{number // 12 - 1}"


def parse_pitch(value: Any, drums: bool = False) -> int:
    """A pitch as its MIDI number: from a number, a name like C4 or Bb3,
    or on a drum track a drum's name like kick or snare."""

    if isinstance(value, bool):
        raise MidiError(f"pitch {value!r} is not a note")
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        text = str(value or "").strip()
        if drums and text.lower() in DRUMS:
            return DRUMS[text.lower()]
        found = _NOTE.match(text)
        if not found:
            hint = (
                "a drum like " + ", ".join(list(DRUMS)[:6]) if drums
                else "a note like C4, F#3 or Bb2, or a MIDI number"
            )
            raise MidiError(f"pitch {value!r} is not {hint}")
        letter, marks, octave = found.groups()
        number = _STEPS[letter.lower()] + (int(octave) + 1) * 12
        number += sum(1 if m in "#♯" else -1 for m in marks)
    if not 0 <= number <= 127:
        raise MidiError(f"pitch {value!r} is outside MIDI's 0 to 127")
    return number


def _number(value: Any, what: str, *, low: float = 0.0,
            high: Optional[float] = None, above: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MidiError(f"{what} {value!r} is not a number") from None
    if number != number or (above and number <= low) or number < low or (
        high is not None and number > high
    ):
        bound = f"more than {low:g}" if above else f"at least {low:g}"
        if high is not None:
            bound += f" and at most {high:g}"
        raise MidiError(f"{what} must be {bound}, not {value!r}")
    return number


def program_of(value: Any) -> int:
    """An instrument as its General MIDI program number."""

    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(_number(value, "instrument", high=127))
    text = str(value).strip().lower()
    if text.isdigit():
        return int(_number(text, "instrument", high=127))
    if text in INSTRUMENTS:
        return INSTRUMENTS[text]
    raise MidiError(
        f"instrument {value!r} is not one Flash knows by name; use a "
        "General MIDI program number 0 to 127, or one of: "
        + ", ".join(sorted(INSTRUMENTS))
    )


class Track:
    """One track's notes, as (pitch, start, length, velocity) in ticks."""

    def __init__(self, index: int, spec: Any) -> None:
        if not isinstance(spec, dict):
            raise MidiError(f"track {index + 1} must be an object")
        where = f"track {index + 1}"
        self.name = str(spec.get("name") or "").strip()[:80]
        if self.name:
            where += f" ({self.name})"
        instrument = spec.get("instrument")
        self.drums = bool(spec.get("drums")) or (
            isinstance(instrument, str)
            and instrument.strip().lower() in ("drums", "drum kit", "kit",
                                               "percussion")
        )
        try:
            self.program = 0 if self.drums else program_of(instrument)
            self.notes = self._notes(spec.get("notes"))
            self.volume = round(_number(
                spec.get("volume", 1.0), "volume", high=1.0,
            ) * 127)
        except MidiError as exc:
            raise MidiError(f"{where}: {exc}") from None
        self.name = self.name or (
            "Drums" if self.drums else f"Track {index + 1}"
        )
        self.channel = 0

    def _notes(self, notes: Any) -> list[tuple[int, int, int, int]]:
        if not isinstance(notes, list) or not notes:
            raise MidiError("notes must be a list with at least one note")
        found = []
        for n, note in enumerate(notes):
            if isinstance(note, dict):
                pitch = note.get("pitch", note.get("pitches"))
                start, length = note.get("start"), note.get(
                    "duration", note.get("length"),
                )
                velocity = note.get("velocity", 90)
            elif isinstance(note, (list, tuple)) and len(note) >= 3:
                pitch, start, length = note[0], note[1], note[2]
                velocity = note[3] if len(note) > 3 else 90
            else:
                raise MidiError(
                    f"notes[{n}] must be [pitch, start, duration] or "
                    "[pitch, start, duration, velocity], in beats"
                )
            try:
                begin = _number(start, "start", high=MAX_BEATS)
                beats = _number(length, "duration", high=MAX_BEATS,
                                above=True)
                loud = int(_number(velocity, "velocity", low=1, high=127))
                pitches = pitch if isinstance(pitch, list) else [pitch]
                if not pitches:
                    raise MidiError("a chord needs at least one pitch")
                for one in pitches:
                    found.append((
                        parse_pitch(one, self.drums),
                        round(begin * TICKS_PER_BEAT),
                        max(1, round(beats * TICKS_PER_BEAT)), loud,
                    ))
            except MidiError as exc:
                raise MidiError(f"notes[{n}]: {exc}") from None
        return found


def _vlq(value: int) -> bytes:
    """A number as MIDI's variable-length quantity."""

    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack(">I", len(data)) + data


def _meta(kind: int, data: bytes) -> bytes:
    return bytes((0xFF, kind)) + _vlq(len(data)) + data


def build_tracks(tracks: Any) -> list[Track]:
    """Every track read, or a MidiError naming the first bad one."""

    if isinstance(tracks, str):
        try:
            tracks = json.loads(tracks)
        except json.JSONDecodeError as exc:
            raise MidiError(f"tracks is not valid JSON: {exc}") from None
    if isinstance(tracks, dict):
        tracks = tracks.get("tracks", [tracks])
    if not isinstance(tracks, list) or not tracks:
        raise MidiError("tracks must be a list with at least one track")
    if len(tracks) > MAX_TRACKS:
        raise MidiError(f"a piece can have at most {MAX_TRACKS} tracks")
    built = [Track(i, spec) for i, spec in enumerate(tracks)]
    total = sum(len(t.notes) for t in built)
    if total > MAX_NOTES:
        raise MidiError(f"the piece has {total} notes; the limit is "
                        f"{MAX_NOTES}")
    # Each melodic track its own channel, skipping the drums' tenth.
    free = [c for c in range(16) if c != DRUM_CHANNEL]
    melodic = [t for t in built if not t.drums]
    if len(melodic) > len(free):
        raise MidiError(f"a piece can have at most {len(free)} tracks "
                        "that are not drums")
    for track, channel in zip(melodic, free):
        track.channel = channel
    for track in built:
        if track.drums:
            track.channel = DRUM_CHANNEL
    return built


def _time_signature(value: Any) -> tuple[int, int]:
    found = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", str(value or "4/4"))
    if not found:
        raise MidiError(f"time signature {value!r} is not like 4/4 or 6/8")
    top, bottom = int(found.group(1)), int(found.group(2))
    if not 1 <= top <= 32 or bottom not in (1, 2, 4, 8, 16, 32):
        raise MidiError(f"time signature {value!r} is not one MIDI holds")
    return top, bottom


def to_midi(tracks: list[Track], tempo: Any = 120, time_signature: Any =
            "4/4", title: str = "") -> bytes:
    """The tracks as a type 1 standard MIDI file."""

    bpm = _number(tempo, "tempo", low=20, high=400)
    top, bottom = _time_signature(time_signature)
    conductor = b"".join((
        b"\x00" + _meta(0x03, (title or "Flash").encode("utf-8")[:120]),
        b"\x00" + _meta(0x51, round(60_000_000 / bpm).to_bytes(3, "big")),
        b"\x00" + _meta(0x58, bytes((
            top, bottom.bit_length() - 1, 24, 8,
        ))),
        b"\x00" + _meta(0x2F, b""),
    ))
    chunks = [_chunk(b"MTrk", conductor)]
    for track in tracks:
        channel = track.channel
        events: list[tuple[int, int, bytes]] = []
        for pitch, start, length, velocity in track.notes:
            # Offs sort before ons at the same tick, so a note repeated
            # right after itself sounds twice instead of being cut.
            events.append((start, 1, bytes((0x90 | channel, pitch,
                                            velocity))))
            events.append((start + length, 0, bytes((0x80 | channel, pitch,
                                                     0))))
        events.sort(key=lambda e: (e[0], e[1]))
        body = bytearray()
        body += b"\x00" + _meta(0x03, track.name.encode("utf-8")[:120])
        body += b"\x00" + bytes((0xB0 | channel, 7, track.volume))
        if not track.drums:
            body += b"\x00" + bytes((0xC0 | channel, track.program))
        now = 0
        for tick, _, data in events:
            body += _vlq(tick - now) + data
            now = tick
        body += b"\x00" + _meta(0x2F, b"")
        chunks.append(_chunk(b"MTrk", bytes(body)))
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks),
                                         TICKS_PER_BEAT))
    return header + b"".join(chunks)


# --- Reading one back --------------------------------------------------------


def is_midi(data: bytes) -> bool:
    return data[:4] == b"MThd"


def read(data: bytes) -> dict:
    """What a MIDI file holds: its tracks' names, instruments and notes,
    and how long it plays, from its own tempo changes."""

    if not is_midi(data) or len(data) < 14:
        raise MidiError("it does not start like a MIDI file")
    _, count, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise MidiError("it keeps time in SMPTE frames, which Flash does "
                        "not read")
    at = 8 + struct.unpack(">I", data[4:8])[0]
    # (tick, microseconds a beat), 120 BPM until the file says otherwise.
    tempos: list[tuple[int, int]] = [(0, 500_000)]
    tracks = []
    for _ in range(count):
        if data[at:at + 4] != b"MTrk":
            break
        size = struct.unpack(">I", data[at + 4:at + 8])[0]
        tracks.append(_read_track(data[at + 8:at + 8 + size], tempos))
        at += 8 + size
    # By tick only, so a file's own tempo at the start follows the
    # default and wins.
    tempos.sort(key=lambda t: t[0])
    last = max((t["end"] for t in tracks), default=0)

    def seconds(tick: int) -> float:
        total, since, rate = 0.0, 0, 500_000
        for when, value in tempos:
            if when >= tick:
                break
            total += (when - since) * rate / division / 1e6
            since, rate = when, value
        return total + (tick - since) * rate / division / 1e6

    return {
        "tracks": [t for t in tracks if t["notes"]],
        "seconds": seconds(last),
        "bpm": round(60_000_000 / [v for w, v in tempos if w == 0][-1]),
    }


def _read_track(data: bytes, tempos: list) -> dict:
    at, tick, status = 0, 0, 0
    name, programs, drums, notes = "", set(), False, 0
    pitches: list[int] = []

    def vlq() -> int:
        nonlocal at
        value = 0
        while at < len(data):
            byte = data[at]
            at += 1
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        return value

    while at < len(data):
        tick += vlq()
        if at >= len(data):
            break
        if data[at] & 0x80:
            status = data[at]
            at += 1
        kind = status & 0xF0
        if status == 0xFF:
            meta = data[at]
            at += 1
            size = vlq()
            body = data[at:at + size]
            at += size
            if meta == 0x03 and not name:
                name = body.decode("utf-8", "replace").strip()
            elif meta == 0x51 and size == 3:
                tempos.append((tick, int.from_bytes(body, "big")))
        elif status in (0xF0, 0xF7):
            at += vlq()
        elif kind in (0xC0, 0xD0):
            if kind == 0xC0:
                programs.add(data[at])
            at += 1
        else:
            if kind == 0x90 and data[at + 1] > 0:
                notes += 1
                pitches.append(data[at])
                drums = drums or (status & 0x0F) == DRUM_CHANNEL
            at += 2
    return {
        "name": name, "notes": notes, "end": tick, "drums": drums,
        "program": min(programs) if programs else 0,
        "low": min(pitches) if pitches else 0,
        "high": max(pitches) if pitches else 0,
    }


def describe(data: bytes) -> str:
    """A MIDI file in numbers the agent can check its intent against."""

    found = read(data)
    total = sum(t["notes"] for t in found["tracks"])
    length = found["seconds"]
    count = len(found["tracks"])
    lines = [
        f"{count} track{'s' if count != 1 else ''}, {total} notes, "
        f"{int(length // 60)}:{int(length % 60):02d} long at "
        f"{found['bpm']} BPM."
    ]
    names = {v: k for k, v in reversed(INSTRUMENTS.items())}
    for track in found["tracks"]:
        what = "drums" if track["drums"] else names.get(
            track["program"], f"program {track['program']}",
        )
        span = "" if track["drums"] else (
            f", {note_name(track['low'])} to {note_name(track['high'])}"
        )
        lines.append(
            f"- {track['name'] or 'Untitled'} ({what}): {track['notes']} "
            f"notes{span}"
        )
    return "\n".join(lines)


def check_midi_file(path: Path) -> Optional[str]:
    """Why a file cannot be played as MIDI, or None when it can."""

    if path.suffix.lower() not in MIDI_SUFFIXES:
        return f"{path.name} is not a .mid or .midi file"
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError as exc:
        return f"could not read {path}: {exc}"
    if size > MAX_MIDI_BYTES:
        return f"{path.name} is over {MAX_MIDI_BYTES // (1024 * 1024)} MB"
    if not is_midi(head):
        return f"{path.name} does not start like a MIDI file"
    return None
