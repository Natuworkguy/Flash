"""The wake word in the owner's voice: kept from a few sayings, and found again
in what is heard after."""

import numpy as np
import pytest

from flash import wake_voice


def _saying(seed: int, pitch: float = 140.0, stretch: float = 1.0,
            noise: float = .002) -> bytes:
    """Something like a two-syllable phrase: a voiced sound whose formants
    glide, then another, in quiet."""

    rng = np.random.default_rng(seed)
    rate = wake_voice.RATE
    n = int(.75 * stretch * rate)
    t = np.arange(n) / rate
    f0 = pitch * (1 + .06 * np.sin(2 * np.pi * 2 * t / stretch))
    phase = 2 * np.pi * np.cumsum(f0) / rate
    glide = np.where(t < n / rate / 2, 500 + 900 * t / stretch,
                     1400 - 700 * (t - .375 * stretch) / stretch)
    x = sum(np.sin(k * phase) * np.exp(-((k * f0 - glide) / 300) ** 2)
            for k in range(1, 30))
    x *= np.hanning(n)
    pad = np.zeros(int(.4 * rate))
    y = np.concatenate([pad, x / np.abs(x).max() * .5, pad])
    y += rng.normal(0, noise, len(y))
    return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


def _noise(seed: int, seconds: float = 2.0) -> bytes:
    rng = np.random.default_rng(seed)
    y = rng.normal(0, .1, int(seconds * wake_voice.RATE))
    return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wake_voice, "_home", lambda: tmp_path / "wake_voice")
    wake_voice._cache["at"] = None
    yield tmp_path
    wake_voice._cache["at"] = None


def test_untaught_says_nothing(home):
    assert not wake_voice.taught()
    assert wake_voice.score(_saying(1)) is None
    assert wake_voice.verdict(_saying(1)) == ""


def test_features_trim_the_quiet():
    f = wake_voice.features(_saying(1))
    secs = len(f) * wake_voice.HOP / wake_voice.RATE
    assert .7 < secs < 1.2
    assert f.shape[1] == wake_voice.CEPS - 1
    assert len(wake_voice.features(b"\x00\x00" * 16000)) == 0


def test_the_same_saying_is_near_and_noise_is_far():
    a = wake_voice.features(_saying(1))
    b = wake_voice.features(_saying(2, stretch=1.2))
    n = wake_voice.features(_noise(3), trim=False)
    assert wake_voice.distance(a, b) < wake_voice.distance(a, n)


def test_taught_then_found_in_a_longer_recording(home):
    kept = wake_voice.teach(
        [_saying(s, stretch=1 + .05 * s) for s in range(4)])
    assert kept["count"] == 4 and wake_voice.taught()
    # said again, a little quicker, inside a few seconds of listening
    heard = (_noise(9, .8)[: 2 * 4000] + _saying(7, stretch=.95)
             + _noise(10, .5)[: 2 * 2000])
    assert wake_voice.verdict(heard) in ("tight", "loose")
    quiet = b"\x00\x00" * 8000
    assert wake_voice.verdict(quiet + _saying(8) + quiet) == "tight"
    assert wake_voice.verdict(_noise(12, 3.0)) == "no"
    wake_voice.forget()
    assert not wake_voice.taught()


def test_teach_refuses_too_little(home):
    with pytest.raises(ValueError):
        wake_voice.teach([b"\x00\x00" * 8000] * 4)
    with pytest.raises(ValueError):
        wake_voice.teach([_saying(1)])
