"""The wake word in the owner's own voice.

Flash hears its wake word with a general listening model held to a few phrases,
then hears the last seconds again freely to be sure. A model like that has
never heard *you* say it, and it hears the shape of the phrase in plenty of
things that are not it. Siri and Alexa do two things it does not: a detector
trained on the phrase itself, and a check against the voice of the person who
set it up.

This is the second of those, done on this computer with nothing to download.
The owner says the wake word four times; each is kept as a template of how it
sounds (its MFCCs: the shape of the voice's spectrum, ten milliseconds at a
time, the room's colour taken out). Afterwards the last seconds heard are cut
where they go quiet, and each piece is compared with each template by dynamic
time warping, which lines up a slower or quicker saying of the same thing. How
far apart the four were from one another sets how close a new one has to be.

It is used two ways (see voice.hear_wake): a very close match wakes Flash even
when the listening model missed the phrase (fewer times saying it twice), and a
fairly close one stands in for the free hearing when the model heard the phrase
but the free hearing did not (fewer misses in a noisy room).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

import numpy as np

RATE = 16000
FRAME, HOP = 400, 160            # 25 ms windows, 10 ms apart
MELS, CEPS = 26, 13
SAMPLES_WANTED = 4
MIN_SECONDS, MAX_SECONDS = .3, 2.5
# times the owner's own spread: wake alone, or vouch for the listening model
TIGHT, LOOSE = .85, 1.12

_lock = threading.Lock()
_cache: dict = {"at": None, "data": None}


def _home() -> Path:
    from .paths import FLASH_DIR

    return Path(FLASH_DIR) / "wake_voice"


def _mel_bank() -> np.ndarray:
    def mel(f):
        return 2595 * np.log10(1 + f / 700)

    def hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    n_fft = 512
    pts = hz(np.linspace(mel(60), mel(7600), MELS + 2))
    bins = np.floor((n_fft + 1) * pts / RATE).astype(int)
    bank = np.zeros((MELS, n_fft // 2 + 1))
    for i in range(MELS):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        if b > a:
            bank[i, a:b] = (np.arange(a, b) - a) / (b - a)
        if c > b:
            bank[i, b:c] = (c - np.arange(b, c)) / (c - b)
    return bank


_BANK = _mel_bank()
_DCT = np.cos(np.pi / MELS * (np.arange(MELS) + .5)[None, :]
              * np.arange(CEPS)[:, None])


def _samples(pcm: bytes) -> np.ndarray:
    whole = pcm[:len(pcm) - len(pcm) % 2]
    return np.frombuffer(whole, "<i2").astype(np.float64) / 32768.0


def _frames(x: np.ndarray) -> np.ndarray:
    if len(x) < FRAME:
        return np.zeros((0, FRAME))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def _energy(fr: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.mean(fr * fr, axis=1) + 1e-10)


def speech_span(x: np.ndarray) -> Optional[tuple[int, int]]:
    """Where the speech is in samples X (start, end), from how loud each frame
    is against the quiet around it."""

    fr = _frames(x)
    if not len(fr):
        return None
    e = _energy(fr)
    floor = np.percentile(e, 15)
    loud = e > max(floor + 12, -50)
    if not loud.any():
        return None
    on = np.flatnonzero(loud)
    a, b = on[0], on[-1] + 1
    return max(0, a * HOP - 1600), min(len(x), b * HOP + FRAME + 1600)


def spoken_pieces(pcm: bytes) -> list[bytes]:
    """PCM cut where it goes quiet for a third of a second or more: each thing
    said on its own, as long as a wake word could be. Each keeps a little of
    the quiet either side, as a template does."""

    x = _samples(pcm)
    fr = _frames(x)
    if not len(fr):
        return []
    e = _energy(fr)
    floor = np.percentile(e, 15)
    loud = e > max(floor + 12, -50)
    runs, start, quiet = [], None, 0
    for i, on in enumerate(loud):
        if on:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= 33:
                runs.append((start, i - quiet + 1))
                start, quiet = None, 0
    if start is not None:
        runs.append((start, len(loud) - quiet))
    out = []
    for a, b in runs:
        secs = (b - a) * HOP / RATE
        if MIN_SECONDS * .7 <= secs <= MAX_SECONDS + .4:
            lo = max(0, a * HOP - 1600)
            hi = min(len(x), b * HOP + FRAME + 1600)
            out.append(pcm[2 * lo:2 * hi])
    return out


def features(pcm: bytes, trim: bool = True) -> np.ndarray:
    """MFCCs (without the loudness one), one row each 10 ms, with the mean of
    each taken away so the microphone and the room's colour do not count."""

    x = _samples(pcm)
    if trim:
        span = speech_span(x)
        if span is None:
            return np.zeros((0, CEPS - 1))
        x = x[span[0]:span[1]]
    x = np.append(x[0] if len(x) else 0.0, x[1:] - .97 * x[:-1])
    fr = _frames(x)
    if not len(fr):
        return np.zeros((0, CEPS - 1))
    spec = np.abs(np.fft.rfft(fr * np.hamming(FRAME), 512)) ** 2
    logmel = np.log(spec @ _BANK.T + 1e-10)
    c = logmel @ _DCT.T
    c = c[:, 1:]
    return c - c.mean(axis=0, keepdims=True)


def _cost(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = ((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)
    return np.sqrt(np.maximum(d, 0))


def distance(template: np.ndarray, heard: np.ndarray,
             anywhere: bool = False) -> float:
    """How far apart TEMPLATE and HEARD sound, per frame of the template, after
    lining them up (dynamic time warping). ANYWHERE: the template may be found
    anywhere inside HEARD, as in a few seconds of listening."""

    n, m = len(template), len(heard)
    if n < 3 or m < 3:
        return float("inf")
    c = _cost(template, heard)
    inf = np.inf
    D = np.full(m, inf)
    if anywhere:
        D = c[0].copy()
    else:
        D[0] = c[0, 0]
    for i in range(1, n):
        prev = D
        # the heard one slower: the template moves on, it stays
        stay = prev
        step = np.r_[inf, prev[:-1]]
        # the heard one up to twice as quick
        skip = np.r_[inf, inf, prev[:-2]]
        D = c[i] + np.minimum(np.minimum(stay, step), skip)
    if anywhere:
        return float(D.min() / n)
    return float(D[-1] / n)


def teach(clips: list[bytes]) -> dict:
    """Keep the owner's sayings of the wake word. Returns what was kept, or
    raises ValueError saying why not."""

    feats = []
    for clip in clips:
        f = features(clip)
        secs = len(f) * HOP / RATE
        if secs < MIN_SECONDS:
            raise ValueError("One of those was too quiet or too short to "
                             "hear. Say it a little louder.")
        if secs > MAX_SECONDS + .4:
            raise ValueError("One of those ran on too long: say only the "
                             "wake word, then stop.")
        feats.append(f)
    if len(feats) < 3:
        raise ValueError(f"Say it {SAMPLES_WANTED} times.")
    pairs = [distance(a, b) for i, a in enumerate(feats)
             for j, b in enumerate(feats) if i != j]
    spread = float(np.mean(pairs) + 1.5 * np.std(pairs))
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    np.savez(home / "templates.npz", *feats)
    (home / "voice.json").write_text(
        json.dumps({"spread": spread, "count": len(feats)}))
    with _lock:
        _cache["at"] = None
    return {"count": len(feats), "spread": round(spread, 3)}


def forget() -> None:
    home = _home()
    for name in ("templates.npz", "voice.json"):
        try:
            (home / name).unlink()
        except FileNotFoundError:
            pass
    with _lock:
        _cache["at"] = None


def _load() -> Optional[tuple[list, float]]:
    home = _home()
    meta = home / "voice.json"
    try:
        stamp = meta.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _cache["at"] == stamp:
            return _cache["data"]
        try:
            spread = float(json.loads(meta.read_text())["spread"])
            with np.load(home / "templates.npz") as z:
                templates = [z[k] for k in z.files]
        except (OSError, ValueError, KeyError):
            return None
        _cache["at"], _cache["data"] = stamp, (templates, spread)
        return _cache["data"]


def taught() -> bool:
    return _load() is not None


def score(pcm: bytes) -> Optional[float]:
    """How close the last seconds heard (PCM) come to the owner saying the wake
    word: the nearest template's distance over the owner's own spread, so 1 is
    as close as their sayings were to one another. None when no voice has been
    taught."""

    kept = _load()
    if kept is None:
        return None
    templates, spread = kept
    pieces = [features(piece) for piece in spoken_pieces(pcm)]
    pieces = [f for f in pieces if len(f) >= 10]
    if not pieces:
        return float("inf")
    best = min(distance(t, f) for t in templates for f in pieces)
    return best / max(spread, 1e-6)


def verdict(pcm: bytes) -> str:
    """One of "tight" (it is the owner saying it), "loose" (close enough
    to vouch for the listening model) and "no"; "" with no voice taught."""

    s = score(pcm)
    if s is None:
        return ""
    if s <= TIGHT:
        return "tight"
    if s <= LOOSE:
        return "loose"
    return "no"
