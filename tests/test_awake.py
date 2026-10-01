import threading

import pytest

from flash import awake


@pytest.fixture
def held(monkeypatch):
    """A stand-in for what holds sleep off, counting starts and stops."""

    monkeypatch.setenv("KEEP_AWAKE", "1")
    seen = {"started": 0, "stopped": 0}

    def start():
        seen["started"] += 1
        return threading.Event()

    def stop(holding):
        seen["stopped"] += 1

    monkeypatch.setattr(awake, "_start", start)
    monkeypatch.setattr(awake, "_stop", stop)
    monkeypatch.setattr(awake, "_working", set())
    monkeypatch.setattr(awake, "_holding", None)
    return seen


def test_held_while_anything_works_and_let_go_after(held):
    with awake.working("chat:a"):
        assert awake.holding()
        with awake.working("spark:b"):
            assert held == {"started": 1, "stopped": 0}
        assert awake.holding()

    assert not awake.holding()
    assert held == {"started": 1, "stopped": 1}


def test_letting_go_twice_or_of_nothing_is_harmless(held):
    awake.hold("terminal")
    awake.let_go("terminal")
    awake.let_go("terminal")
    awake.let_go("never")

    assert held == {"started": 1, "stopped": 1}


def test_off_holds_nothing(held, monkeypatch):
    monkeypatch.setenv("KEEP_AWAKE", "0")

    with awake.working("chat:a"):
        assert not awake.holding()

    assert held["started"] == 0


@pytest.mark.parametrize("value, on", [
    ("1", True), ("", True), ("0", False), ("off", False), ("false", False),
])
def test_the_setting(monkeypatch, value, on):
    monkeypatch.setenv("KEEP_AWAKE", value)

    assert awake.enabled() is on


def test_a_spark_shift_keeps_it_awake(held, monkeypatch):
    from flash import sparks

    seen = []
    monkeypatch.setattr(
        sparks, "_shift", lambda key, client=None: seen.append(
            awake.holding()
        ),
    )

    sparks.shift("anything")

    assert seen == [True] and not awake.holding()
