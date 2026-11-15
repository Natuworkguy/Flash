"""Tether: the game's rules, and its server."""

import json
import urllib.request

from flash import tether


def fresh():
    g = tether.Game()
    g.rng.seed(1)
    return g


def start(g):
    g.keys("you", [], True)
    g.keys("flash", [], True)
    assert g.phase == "countdown"
    g.step(3.1)
    assert g.phase == "play"
    g.drones, g.sparks = [], []


def place(g, you, flash):
    g.p["you"]["x"], g.p["you"]["y"] = you
    g.p["flash"]["x"], g.p["flash"]["y"] = flash


def test_it_starts_only_when_both_are_ready():
    g = fresh()
    g.keys("you", [], True)
    assert g.phase == "lobby"
    g.keys("flash", [], True)
    assert g.phase == "countdown"


def test_strangers_cannot_play():
    g = fresh()
    g.keys("someone", ["ArrowUp"], True)
    assert set(g.p) == {"you", "flash"} and g.phase == "lobby"


def test_the_rope_catches_a_spark_between_you():
    g = fresh()
    start(g)
    place(g, (300, 300), (600, 300))
    g.sparks = [{"x": 450, "y": 302}]
    g.step(0.01)
    assert g.score == 1 and g.run == 1


def test_a_cut_rope_catches_nothing_but_a_touch_still_does():
    g = fresh()
    start(g)
    place(g, (300, 300), (600, 300))
    g.snapped = 1.0
    g.sparks = [{"x": 450, "y": 300}, {"x": 305, "y": 300}]
    g.step(0.01)
    assert g.score == 1


def test_a_drone_on_the_rope_snaps_it_and_costs_three():
    g = fresh()
    start(g)
    place(g, (300, 300), (600, 300))
    g.score, g.run = 5, 4
    g.drones = [{"x": 450, "y": 305, "vx": 0, "vy": 0}]
    g.step(0.01)
    assert g.score == 2 and g.run == 0 and g.snaps == 1 and g.snapped > 0
    g.step(0.01)
    assert g.snaps == 1  # cut once, not again while it's down


def test_the_rope_drags_the_other_along():
    g = fresh()
    start(g)
    place(g, (200, 300), (200 + tether.ROPE, 300))
    g.keys("you", ["ArrowLeft"], False)
    for _ in range(10):
        g.step(0.05)
    you, flash = g.p["you"], g.p["flash"]
    assert flash["x"] < 200 + tether.ROPE  # pulled
    assert abs((flash["x"] - you["x"]) - tether.ROPE) < 1


def test_a_drone_stuns_whoever_it_hits():
    g = fresh()
    start(g)
    place(g, (300, 300), (300, 500))
    g.snapped = 5  # keep the rope out of it
    g.drones = [{"x": 300, "y": 300, "vx": 0, "vy": 0}]
    g.step(0.01)
    assert g.p["you"]["stun"] > 0 and g.p["flash"]["stun"] == 0


def test_the_round_ends_with_a_mark():
    g = fresh()
    start(g)
    g.score = tether.MARKS[1]
    g.step(tether.ROUND + 1)
    assert g.phase == "over" and g.mark() == "great"
    assert g.state()["mark"] == "great"


def test_server_serves_the_page_state_and_keys():
    url = tether.start()
    try:
        page = urllib.request.urlopen(url, timeout=5).read().decode()
        assert "<title>Tether</title>" in page
        body = json.dumps({"keys": ["ArrowUp"], "ready": False}).encode()
        req = urllib.request.Request(
            url + "keys?as=you", data=body, method="POST"
        )
        assert urllib.request.urlopen(req, timeout=5).status == 204
        raw = urllib.request.urlopen(url + "state", timeout=5).read()
        state = json.loads(raw)
        assert state["players"]["you"]["here"] is True
        assert state["marks"] == list(tether.MARKS)
        assert tether.address() == url
    finally:
        tether.stop()
    assert tether.address() is None


def test_the_reflex_plays_as_a_teammate_at_a_persons_pace():
    assert 'const LEVEL = "fair"' in tether.REFLEX
    for colour in ("#ff5a36", "#2f6bff", "#d7ff3a", "#141210"):
        assert colour in tether.REFLEX
