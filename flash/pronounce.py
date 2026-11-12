"""Pronunciation, heard: the learner's own recording judged by its sound.

Two listenings of the same audio. First, an acoustic model of the language
(Vosk's, downloaded once per language) hears the recording with only the
phrase's words to choose from, and beside each of them the words a sound
away from it ("fox" with "box", "fax" and "fix"; "lazy" with "lady"): a
word said as itself comes out as itself with high confidence, one said
wrong comes out as the neighbour it sounded like, or not at all. That is
the sound judged, not a transcript guessed. Second, the recording is lined
up against Flash's own voice saying each word (dynamic time warping over
mel cepstra), which says which words were rushed or dragged.

Measured on three different voices: the right phrase scores high, and
"box" for "fox", "lady" for "lazy" and "log" for "dog" are each caught.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Callable
from typing import Any

RATE = 16000
FRAME, HOP = 400, 160          # 25 ms windows every 10 ms
MELS, CEPS = 26, 13
GAP = 0.08                     # seconds of silence between the words of the reference
GOOD, BAD = 0.12, 0.0          # how much closer to its own word than to the others reads as 100, and as 0


class PronounceError(Exception):
    pass


def _np():
    try:
        import numpy
    except ImportError:
        raise PronounceError("comparing the sound needs numpy: pip install numpy") from None
    return numpy


def from_wav(data: bytes):
    """Mono float samples at RATE from WAV bytes."""

    np = _np()
    with wave.open(io.BytesIO(data)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    kind = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=kind).astype(np.float64)
    if width == 1:
        x -= 128
    x /= float(2 ** (8 * width - 1))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return resample(x, rate)


def from_pcm16(data: bytes):
    np = _np()
    return np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0


def resample(x, rate: int):
    np = _np()
    if rate == RATE or not len(x):
        return x
    n = int(len(x) * RATE / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)


def trim(x):
    """X without the quiet before and after the speech."""

    np = _np()
    if len(x) < FRAME:
        return x
    frames = len(x) // HOP
    energy = np.array([np.sqrt(np.mean(x[i * HOP:i * HOP + FRAME] ** 2) + 1e-12) for i in range(frames)])
    floor = max(energy.max() * 0.06, 1e-4)
    on = np.where(energy > floor)[0]
    if not len(on):
        return x[:0]
    return x[max(0, on[0] * HOP - HOP * 3): min(len(x), on[-1] * HOP + FRAME + HOP * 3)]


_bank: dict = {}


def features(x):
    """Mel cepstra of X, a row a frame, each recording's mean and spread taken out."""

    np = _np()
    if len(x) < FRAME:
        return np.zeros((0, CEPS - 1))
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hamming(FRAME)
    power = np.abs(np.fft.rfft(frames, 512)) ** 2 / 512
    if "mel" not in _bank:
        mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
        hz = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
        points = hz(np.linspace(mel(80), mel(7600), MELS + 2))
        bins = np.floor(513 * points / RATE).astype(int)
        bank = np.zeros((MELS, 257))
        for m in range(1, MELS + 1):
            a, b, c = bins[m - 1], bins[m], bins[m + 1]
            for k in range(a, b):
                bank[m - 1, k] = (k - a) / max(1, b - a)
            for k in range(b, c):
                bank[m - 1, k] = (c - k) / max(1, c - b)
        dct = np.cos(np.pi / MELS * (np.arange(MELS)[None, :] + 0.5) * np.arange(CEPS)[:, None])
        _bank["mel"], _bank["dct"] = bank, dct
    logmel = np.log(power @ _bank["mel"].T + 1e-10)
    ceps = (logmel @ _bank["dct"].T)[:, 1:]
    return (ceps - ceps.mean(axis=0)) / (ceps.std(axis=0) + 1e-6)


def dtw(a, b, band: float = 0.2):
    """The cheapest way through A against B, kept within BAND of the diagonal (so one sound cannot be stretched over
    the whole recording): (the pairs of frames on it, each pair's distance, the mean distance along it)."""

    np = _np()
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)) / np.sqrt(a.shape[1])
    n, m = cost.shape
    width = max(3, int(band * max(n, m)) + abs(n - m))
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        centre = int(i * m / n)
        lo, hi = max(1, centre - width), min(m, centre + width)
        prev, row = acc[i - 1], acc[i]
        for j in range(lo, hi + 1):
            row[j] = cost[i - 1, j - 1] + min(prev[j - 1], prev[j], row[j - 1])
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        step = int(np.argmin((acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1])))
        i, j = (i - 1, j - 1) if step == 0 else (i - 1, j) if step == 1 else (i, j - 1)
    path.reverse()
    return path, cost, float(np.mean([cost[p] for p in path])) if path else 9.0


def score_of(margin: float) -> int:
    """How much better a stretch matched its own word than the others, as 0 to 100."""

    return int(round(100 * min(1.0, max(0.0, (margin - BAD) / (GOOD - BAD)))))


def compare(words: list[str], recording, say: Callable[[str], Any]) -> dict:
    """RECORDING (samples at RATE) against WORDS said by SAY (a word -> its samples at RATE): a score for each word
    and for the whole, and whether each was rushed or dragged."""

    np = _np()
    pieces, bounds, at = [], [], 0
    gap = np.zeros(int(GAP * RATE))
    for w in words:
        s = trim(say(w))
        if not len(s):
            s = gap
        pieces += [s, gap]
        start = at // HOP
        at += len(s)
        bounds.append((start, max(start + 1, at // HOP)))
        at += len(gap)
    ref = features(np.concatenate(pieces))
    mine = features(trim(recording))
    if len(mine) < 10:
        raise PronounceError("too little was heard to compare: say it again, closer to the microphone")
    path, cost, whole = dtw(ref, mine)
    singles = [features(trim(say(w))) for w in words]
    out, own_all = [], []
    for k, (w, (a, b)) in enumerate(zip(words, bounds)):
        mapped = sorted({j for i, j in path if a <= i < b})
        if len(mapped) < 3 or len(singles[k]) < 3:
            out.append({"word": w, "score": 0, "pace": "missed"})
            continue
        stretch = mine[mapped[0]:mapped[-1] + 1]
        # its own sound against the other words of the phrase: a stretch that matches its own word no better than
        # the others was not that word
        own = dtw(singles[k], stretch, 0.35)[2]
        others = [dtw(singles[o], stretch, 0.35)[2] for o in range(len(words))
                  if words[o].lower() != w.lower() and len(singles[o]) >= 3]
        rival = float(np.median(others)) if others else own * 1.35
        margin = (rival - own) / max(rival, 1e-6)
        ratio = len(mapped) / max(1, b - a)
        pace = "rushed" if ratio < 0.55 else "dragged" if ratio > 1.9 else ""
        out.append({"word": w, "score": score_of(margin), "pace": pace, "margin": round(margin, 3)})
        own_all.append(margin)
    total = int(round(float(np.mean([o["score"] for o in out])))) if out else 0
    return {"words": out, "score": total, "distance": round(whole, 3)}


def reference(say_wav: Callable[[str], bytes]) -> Callable[[str], Any]:
    """A word -> its samples, from a function giving WAV bytes (Flash's voice), each word said once."""

    kept: dict = {}

    def say(word: str):
        if word not in kept:
            wav = say_wav(word)
            if not wav:
                raise PronounceError("Flash's voice is not set up, so there is nothing to compare with")
            kept[word] = from_wav(wav)
        return kept[word]

    return say


# ----- judged by an acoustic model of the language ------------------------------

import json as _json
import string as _string
import threading as _threading

_models: dict = {}
_model_lock = _threading.Lock()
LETTERS = {"en": _string.ascii_lowercase, "fr": _string.ascii_lowercase + "éèêëàâîïôûùüçœ", "es": _string.ascii_lowercase + "áéíóúñü",
           "de": _string.ascii_lowercase + "äöüß", "it": _string.ascii_lowercase + "àèéìòù", "pt": _string.ascii_lowercase + "áâãàéêíóôõúç"}


def _vosk():
    try:
        import vosk  # type: ignore
    except ImportError:
        raise PronounceError("judging the sound needs vosk, Flash's listening model: pip install \"flash[voice]\"") from None
    vosk.SetLogLevel(-1)
    return vosk


def model_for(lang: str):
    """The acoustic model for LANG ("fr-FR", "es"...): Flash's own listening model for English, else Vosk's small model
    for the language, downloaded the first time."""

    vosk = _vosk()
    code = (lang or "en").split("-")[0].lower()
    with _model_lock:
        if code not in _models:
            if code == "en":
                try:
                    from . import voice

                    _models[code] = voice._load_listener()  # pylint: disable=W0212
                except Exception:  # noqa: BLE001  (not set up: Vosk's own)
                    _models[code] = vosk.Model(lang="en-us")
            else:
                try:
                    _models[code] = vosk.Model(lang=code)
                except Exception as exc:  # noqa: BLE001
                    raise PronounceError(f"no listening model for {lang} could be had: {exc}") from None
        return _models[code]


def _clean(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalpha() or ch in "'-").strip("'-")


def neighbours(word: str, known: Callable[[str], bool], lang: str = "en") -> set:
    """The words a sound away from WORD that the model knows: one letter changed, added or dropped."""

    letters = LETTERS.get(lang.split("-")[0].lower(), _string.ascii_lowercase)
    out = set()
    for i in range(len(word) + 1):
        for c in letters:
            out.add(word[:i] + c + word[i:])
            if i < len(word):
                out.add(word[:i] + c + word[i + 1:])
        if i < len(word):
            out.add(word[:i] + word[i + 1:])
    # a doubled letter or a silent e added is the same word spelt otherwise, not a rival
    same = {word + "e", word[:-1] if word.endswith("e") else ""} | {word[:i] + word[i] + word[i:] for i in range(len(word))}
    return {w for w in out if w != word and len(w) > 1 and w not in same and known(w)}


def hear_words(phrase: list[str], pcm16: bytes, lang: str, model=None) -> list[dict]:
    """What the acoustic model heard in PCM16 (16 kHz mono), choosing only among the phrase's words, their
    neighbours, and "[unk]": each {word, conf, start, end}."""

    vosk = _vosk()
    model = model or model_for(lang)
    has = getattr(model, "vosk_model_find_word", None) or getattr(model, "find_word", None)
    known = (lambda w: has(w) >= 0) if has else (lambda w: True)
    targets = [_clean(w) for w in phrase if _clean(w)]
    grammar = set(t for t in targets if known(t))
    for t in targets:
        grammar |= neighbours(t, known, lang)
    rec = vosk.KaldiRecognizer(model, RATE, _json.dumps(sorted(grammar) + ["[unk]"]))
    rec.SetWords(True)
    rec.AcceptWaveform(pcm16)
    return _json.loads(rec.FinalResult()).get("result", [])


def judge(phrase: list[str], heard: list[dict]) -> list[dict]:
    """Each word of PHRASE marked from what was HEARD: right (said as itself), close (itself, but not surely), or
    missed (heard as another word, or not at all), with what it sounded like."""

    targets = [_clean(w) for w in phrase]
    said = [(_clean(h.get("word", "")), float(h.get("conf", 0))) for h in heard]
    # line the heard words up with the phrase, matching a word to itself or to a neighbour of it
    n, m = len(targets), len(said)
    near = [{x for x in (targets[i],)} for i in range(n)]
    fit = lambda i, j: 0 if said[j][0] == targets[i] else 1 if said[j][0] != "[unk]" and _close(said[j][0], targets[i]) else 2  # noqa: E731
    acc = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        acc[i][0] = i * 2
    for j in range(1, m + 1):
        acc[0][j] = j * 1
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i][j] = min(acc[i - 1][j - 1] + fit(i - 1, j - 1), acc[i - 1][j] + 2, acc[i][j - 1] + 1)
    i, j, pairs = n, m, {}
    while i > 0 and j > 0:
        if acc[i][j] == acc[i - 1][j - 1] + fit(i - 1, j - 1):
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif acc[i][j] == acc[i - 1][j] + 2:
            i -= 1
        else:
            j -= 1
    out = []
    for k, w in enumerate(phrase):
        j = pairs.get(k)
        if j is None or said[j][0] == "[unk]" or not _close(said[j][0], targets[k]) and said[j][0] != targets[k]:
            out.append({"word": w, "mark": "missed", "heard": "" if j is None or said[j][0] == "[unk]" else said[j][0], "conf": 0.0})
            continue
        word, conf = said[j]
        if word == targets[k]:
            out.append({"word": w, "mark": "right" if conf >= 0.7 else "close", "heard": word, "conf": round(conf, 2)})
        elif 0.42 <= conf <= 0.58:
            # an even split with a neighbour is two spellings of one sound ("quick", "quik"), not a wrong one
            out.append({"word": w, "mark": "right", "heard": targets[k], "conf": round(1 - conf, 2)})
        elif conf < 0.42:
            out.append({"word": w, "mark": "close", "heard": word, "conf": round(conf, 2)})
        else:
            out.append({"word": w, "mark": "missed", "heard": word, "conf": round(conf, 2)})
    return out


def _close(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def assess(text: str, lang: str, pcm16: bytes, say_wav: Callable[[str], bytes] | None = None, model=None) -> dict:
    """TEXT said in PCM16 (16 kHz mono, 16-bit), judged by its sound: each word right, close or missed, what it
    sounded like when it was not itself, rushed or dragged against Flash's own voice, and a score out of 100."""

    phrase = [w for w in str(text or "").split() if _clean(w)]
    if not phrase:
        raise PronounceError("no phrase to compare with")
    if len(pcm16) < RATE * 2 * 0.3:
        raise PronounceError("too little was heard: say it again, closer to the microphone")
    marks = judge(phrase, hear_words(phrase, pcm16, lang, model))
    if say_wav is not None:
        try:
            timed = compare([_clean(w) for w in phrase], from_pcm16(pcm16), reference(say_wav))
            for mark, t in zip(marks, timed["words"]):
                if t.get("pace") in ("rushed", "dragged"):
                    mark["pace"] = t["pace"]
        except PronounceError:
            pass
    points = {"right": 1.0, "close": 0.6, "missed": 0.0}
    score = int(round(100 * sum(points[m["mark"]] for m in marks) / len(marks)))
    return {"words": marks, "score": score}
