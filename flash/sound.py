"""Ears and a voice box for sounds that are not speech.

listen() measures an audio file and says what is in it: how long and
how loud, how much is silence, whether it clips, the note it centers on,
how bright it is, its tempo when it has a beat, and where the sound
starts and stops. A model cannot hear, but it can read that.

make() writes a WAV from a short score: notes, rests and chords, on a
sine, square, saw or triangle wave, or noise. "C4 E4 G4 C5:2" is a
rising arpeggio ending on a held note.

WAV files are read directly; anything else (mp3, m4a, ogg, flac, a
video's soundtrack) through ffmpeg. Measuring needs numpy.
"""

from __future__ import annotations

import array
import math
import random
import re
import shutil
import subprocess  # nosec B404
import wave
from pathlib import Path
from typing import Any

RATE = 22050
OUT_RATE = 44100
MOST_SECONDS = 600
MOST_MADE_SECONDS = 300
SILENT_DB = -50.0
WAVES = ("sine", "square", "saw", "triangle", "noise")
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


class SoundError(Exception):
    """A sound that could not be read or made, in words."""


# Reading ----------------------------------------------------------------


def _numpy() -> Any:
    try:
        import numpy
    except ImportError:
        raise SoundError("Listening needs numpy: pip install numpy") \
            from None
    return numpy


def load(path: Path) -> tuple[Any, int]:
    """The sound at PATH as mono samples from -1 to 1, and their rate."""

    np = _numpy()
    path = Path(path).expanduser()
    if not path.is_file():
        raise SoundError(f"There is no file at {path}.")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wav:
                width, channels = wav.getsampwidth(), wav.getnchannels()
                rate = wav.getframerate()
                raw = wav.readframes(min(wav.getnframes(),
                                         rate * MOST_SECONDS))
            if width in (1, 2, 4):
                kind = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
                data = np.frombuffer(raw, dtype=kind).astype(np.float64)
                if width == 1:
                    data = (data - 128) / 128
                else:
                    data /= float(2 ** (8 * width - 1))
                return data.reshape(-1, channels).mean(axis=1), rate
        except (wave.Error, EOFError):
            pass
    if not shutil.which("ffmpeg"):
        raise SoundError("Reading that needs ffmpeg: install it (brew "
                         "install ffmpeg, apt install ffmpeg, or winget "
                         "install ffmpeg).")
    done = subprocess.run(  # nosec B603 B607 -- a fixed program
        ["ffmpeg", "-v", "error", "-t", str(MOST_SECONDS), "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=120, check=False)
    if done.returncode or not done.stdout:
        raise SoundError("ffmpeg could not read it: "
                         + done.stderr.decode(errors="replace").strip()[-200:])
    return np.frombuffer(done.stdout, dtype=np.int16) / 32768.0, RATE


def _db(value: float) -> float:
    return 20 * math.log10(value) if value > 1e-9 else -120.0


def note_of(freq: float) -> str:
    """The nearest note to FREQ, and how far off in cents: "A4 +3"."""

    if freq <= 0:
        return ""
    semis = 12 * math.log2(freq / 440.0) + 57
    nearest = round(semis)
    cents = round((semis - nearest) * 100)
    name = f"{NOTE_NAMES[nearest % 12]}{nearest // 12}"
    return name + (f" {cents:+d}" if cents else "")


def _pitch(np: Any, chunk: Any, rate: int) -> float:
    """The fundamental of CHUNK by autocorrelation, 50 Hz to 2 kHz, or 0
    when nothing in it repeats clearly."""

    chunk = chunk - chunk.mean()
    if not chunk.any():
        return 0.0
    size = 1 << (2 * len(chunk) - 1).bit_length()
    spec = np.fft.rfft(chunk, size)
    corr = np.fft.irfft(spec * np.conj(spec))[:len(chunk)]
    corr /= corr[0]
    low, high = int(rate / 2000), int(rate / 50)
    if high >= len(corr):
        return 0.0
    lag = low + int(np.argmax(corr[low:high]))
    if corr[lag] < 0.5:
        return 0.0
    # The first peak nearly as high as the best: not an octave below.
    for shorter in range(low, lag):
        if corr[shorter] > 0.9 * corr[lag] and \
                corr[shorter] >= corr[shorter - 1] and \
                corr[shorter] >= corr[shorter + 1]:
            lag = shorter
            break
    # Between samples: the peak of a parabola through the three around it.
    left, mid, right = corr[lag - 1], corr[lag], corr[lag + 1]
    bend = left - 2 * mid + right
    exact = lag + (0.5 * (left - right) / bend if bend else 0.0)
    return rate / exact


def _tempo(np: Any, energy: Any, hop_seconds: float) -> float:
    """Beats a minute from how the loudness rises, 60 to 200, or 0."""

    rise = np.maximum(0, np.diff(energy))
    if len(rise) < 16 or not rise.any():
        return 0.0
    rise = rise - rise.mean()
    corr = np.correlate(rise, rise, "full")[len(rise) - 1:]
    if corr[0] <= 0:
        return 0.0
    corr /= corr[0]
    low = max(1, int(60 / 200 / hop_seconds))
    high = min(len(corr) - 1, int(60 / 60 / hop_seconds))
    if high <= low:
        return 0.0
    lag = low + int(np.argmax(corr[low:high]))
    return 60 / (lag * hop_seconds) if corr[lag] > 0.25 else 0.0


def listen(path: str) -> dict:
    """What is in the sound at PATH, measured."""

    np = _numpy()
    data, rate = load(Path(path))
    seconds = len(data) / rate if rate else 0
    if not len(data):
        raise SoundError("It holds no sound.")
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data ** 2)))
    hop = max(1, int(rate * 0.02))
    frames = len(data) // hop
    energy = np.sqrt(np.mean(data[:frames * hop].reshape(frames, hop) ** 2,
                             axis=1)) if frames else np.array([rms])
    loud = energy > 10 ** (SILENT_DB / 20)
    parts, start = [], None
    for i, on in enumerate(list(loud) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if (i - start) * hop / rate >= 0.05:
                parts.append((round(start * hop / rate, 2),
                              round(i * hop / rate, 2)))
            start = None
    window = data[:min(len(data), rate * 30)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    freqs = np.fft.rfftfreq(len(window), 1 / rate)
    centroid = float((spectrum * freqs).sum() / spectrum.sum()) \
        if spectrum.sum() else 0.0
    # The pitch where it is loudest: a tenth of a second there.
    at = int(np.argmax(energy)) * hop if frames else 0
    chunk = data[at:at + max(int(rate * 0.1), 2 * int(rate / 50) + 2)]
    pitch = _pitch(np, chunk, rate) if len(chunk) > rate / 25 else 0.0
    tempo = _tempo(np, energy, hop / rate) if seconds >= 4 else 0.0
    return {
        "seconds": round(seconds, 2), "rate": rate,
        "peak_db": round(_db(peak), 1), "loudness_db": round(_db(rms), 1),
        "clipping": int(np.sum(np.abs(data) >= 0.999)),
        "silent_share": round(1 - float(loud.mean()), 2) if frames else 0.0,
        "pitch_hz": round(pitch, 1), "note": note_of(pitch),
        "brightness_hz": round(centroid),
        "tempo_bpm": round(tempo), "parts": parts[:40],
    }


def describe(found: dict) -> str:
    """The measurements, in words."""

    bright = found["brightness_hz"]
    tone = "dark and muffled" if bright < 800 else \
        "warm" if bright < 2000 else "bright" if bright < 4500 else \
        "harsh, or mostly noise"
    lines = [
        f"{found['seconds']} s long; peaks at {found['peak_db']} dBFS, "
        f"average {found['loudness_db']} dBFS.",
        f"{found['silent_share']:.0%} of it is silence." if
        found["silent_share"] else "No silence in it.",
        f"Its tone is {tone} (centered near {bright} Hz).",
    ]
    if found["clipping"]:
        lines.append(f"It clips: {found['clipping']} samples hit the top.")
    if found["note"]:
        lines.append(f"Where loudest, it is pitched at {found['pitch_hz']} "
                     f"Hz, {found['note']}.")
    else:
        lines.append("Where loudest, it has no clear pitch (speech, noise, "
                     "or many notes at once).")
    if found["tempo_bpm"]:
        lines.append(f"It has a beat at about {found['tempo_bpm']} BPM.")
    if found["parts"]:
        lines.append("Sound from " + ", ".join(
            f"{a}-{b} s" for a, b in found["parts"][:12])
            + (" and more" if len(found["parts"]) > 12 else "") + ".")
    return "\n".join(lines)


# Making -----------------------------------------------------------------


def frequency(name: str) -> float:
    """The frequency of a note like A4, C#3 or Eb5, or a number in Hz."""

    name = name.strip()
    try:
        return float(name)
    except ValueError:
        pass
    found = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", name)
    if not found:
        raise SoundError(f"{name!r} is not a note: write one like A4, C#3 "
                         "or Eb5, or a number of Hz.")
    index = NOTE_NAMES.index(found.group(1).upper()) + \
        {"#": 1, "b": -1}.get(found.group(2), 0)
    semis = index + 12 * int(found.group(3)) - 57
    return 440.0 * 2 ** (semis / 12)


def parse(score: str) -> list[tuple[list[float], float]]:
    """A score as (frequencies, beats) steps. Notes are separated by
    spaces or commas; "C4+E4+G4" is a chord, ":2" a length in beats
    (1 if left out), and "rest" or "-" silence."""

    steps = []
    for token in re.split(r"[\s,]+", str(score or "").strip()):
        if not token:
            continue
        sound, _, beats = token.partition(":")
        try:
            length = float(beats) if beats else 1.0
        except ValueError:
            raise SoundError(f"{token!r}: the length after : is a number "
                             "of beats.") from None
        if length <= 0:
            raise SoundError(f"{token!r}: a length is more than nothing.")
        if sound.lower() in ("rest", "-", "_"):
            steps.append(([], length))
        else:
            steps.append(([frequency(n) for n in sound.split("+")], length))
    if not steps:
        raise SoundError("Write the notes to play, like: C4 E4 G4 C5:2")
    return steps


def _sample(kind: str, phase: float, rng: random.Random) -> float:
    cycle = phase % 1.0
    if kind == "square":
        return 1.0 if cycle < 0.5 else -1.0
    if kind == "saw":
        return 2 * cycle - 1
    if kind == "triangle":
        return 4 * abs(cycle - 0.5) - 1
    if kind == "noise":
        return rng.uniform(-1, 1)
    return math.sin(2 * math.pi * cycle)


def make(score: str, out: Path, wave_kind: str = "sine", bpm: float = 120,
         volume: float = 0.5) -> dict:
    """SCORE played at BPM on WAVE_KIND, written to OUT as a WAV."""

    wave_kind = (wave_kind or "sine").lower()
    if wave_kind not in WAVES:
        raise SoundError("The wave is one of " + ", ".join(WAVES) + ".")
    bpm = max(20.0, min(400.0, float(bpm or 120)))
    volume = max(0.0, min(1.0, float(volume if volume is not None else 0.5)))
    steps = parse(score)
    beat = 60.0 / bpm
    total = sum(b for _, b in steps) * beat
    if total > MOST_MADE_SECONDS:
        raise SoundError(f"That is {total:.0f} s: at most "
                         f"{MOST_MADE_SECONDS} s.")
    rng = random.Random(7)  # nosec B311 -- noise for a sound, not secrets
    samples = array.array("h")
    fade = int(OUT_RATE * 0.01)
    for freqs, beats in steps:
        count = int(round(beats * beat * OUT_RATE))
        if not freqs:
            samples.extend([0] * count)
            continue
        for i in range(count):
            # Faded in and out, so notes do not click.
            env = min(1.0, i / fade if fade else 1.0,
                      (count - i) / fade if fade else 1.0)
            t = i / OUT_RATE
            value = sum(_sample(wave_kind, f * t, rng) for f in freqs)
            samples.append(int(32767 * volume * env * value / len(freqs)))
    out = Path(out).expanduser()
    if out.suffix.lower() != ".wav":
        out = out.with_suffix(".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(OUT_RATE)
        wav.writeframes(samples.tobytes())
    return {"path": str(out), "seconds": round(len(samples) / OUT_RATE, 2),
            "notes": sum(1 for f, _ in steps if f)}
