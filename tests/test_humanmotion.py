"""The mouse and keys as a person works them: curves, Fitts's law, overshoot, a typist's rhythm."""

import math
import random

from flash import computeruse, humanmotion as hm


def _points(steps):
    return [s["m"] for s in steps if "m" in s]


def test_a_reach_ends_on_the_target_along_a_curve():
    rng = random.Random(3)
    steps = hm.move((100, 100), (700, 400), rng)
    pts = _points(steps)
    assert pts[-1] == [700, 400]
    # not a straight line: somewhere along it, well off the chord
    (x0, y0), (x1, y1) = (100, 100), (700, 400)
    off = max(abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / math.hypot(x1 - x0, y1 - y0) for x, y in pts)
    assert off > 5


def test_farther_and_smaller_targets_take_longer():
    assert hm.duration(800, 20) > hm.duration(200, 20) > hm.duration(200, 80)
    rng = random.Random(1)
    near = hm.total(hm.move((0, 0), (60, 0), rng))
    far = hm.total(hm.move((0, 0), (1200, 0), random.Random(1)))
    assert far > near and 0.1 < near < 1.0 and far < 2.5


def test_slow_at_both_ends_quick_in_the_middle():
    pts = _points(hm._reach((0, 0), (1000, 0), 1.0, random.Random(2), bend=0.0))
    gaps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    third = len(gaps) // 3
    assert max(gaps[third:2 * third]) > 3 * max(gaps[:5]) and max(gaps[third:2 * third]) > 3 * max(gaps[-5:])


def test_long_reaches_sometimes_overshoot_and_come_back():
    went_past = 0
    for seed in range(40):
        pts = _points(hm.move((0, 300), (900, 300), random.Random(seed)))
        if max(p[0] for p in pts) > 905:
            went_past += 1
        assert pts[-1] == [900, 300]
    assert 5 < went_past < 30


def test_a_click_rests_first_and_holds_the_button_a_moment():
    steps = hm.click((0, 0), (300, 200), random.Random(4), count=2)
    downs = [i for i, s in enumerate(steps) if "d" in s]
    assert len(downs) == 2 and [steps[i]["n"] for i in downs] == [1, 2]
    assert steps[downs[0] - 1]["w"] >= 0.06 and steps[downs[0] + 1]["w"] >= 0.065


def test_typing_has_a_typists_rhythm():
    text = "The quick brown fox jumps over the lazy dog. It was not amused."
    steps = hm.type_text(text, random.Random(5), wpm=70)
    assert "".join(s["t"] for s in steps if "t" in s) == text
    wpm = len(text) / 5 / (hm.total(steps) / 60)
    assert 45 < wpm < 95
    gaps = [s["w"] for s in steps if "w" in s]
    assert max(gaps) > 3 * min(gaps)


def test_batches_never_split_a_press_from_its_release():
    steps = hm.drag((0, 0), (100, 100), (900, 700), random.Random(6))
    for piece in hm.batches(steps, 0.2):
        assert sum("d" in s for s in piece) == sum("u" in s for s in piece)
    assert sum(len(p) for p in hm.batches(steps, 0.2)) == len(steps)


def test_a_click_is_played_as_steps_and_ends_where_it_was_asked(monkeypatch):
    sent = []
    monkeypatch.setattr(computeruse, "human", lambda: True)
    monkeypatch.setattr(computeruse, "fast", lambda: False)
    monkeypatch.setattr(computeruse, "pointer", lambda: (10.0, 10.0))
    monkeypatch.setattr(computeruse, "_send", lambda kind, cmd: sent.append(cmd) or {})
    monkeypatch.setattr(computeruse, "_shot", {"size": (1000, 800), "scale": 1.0})
    assert computeruse._act("left_click", {"x": 500, "y": 400}) == "Clicked."
    assert all(c["do"] == "steps" for c in sent)
    steps = [s for c in sent for s in c["steps"]]
    assert _points(steps)[-1] == [500, 400] and any("d" in s for s in steps)
