# pylint: disable=C0114,C0116

import random
import time

import pytest

from flash import ai, greetings, web


def _at(hour, day=8):
    # 8 October 2026 is a Thursday.
    return time.localtime(time.mktime((2026, 10, day, hour, 0, 0, 0, 0, -1)))


def _all(hour):
    return {greetings.pick(_at(hour), random.Random(n)) for n in range(200)}


@pytest.mark.parametrize("hour, part", [
    (0, "late"), (4, "late"), (5, "morning"), (11, "morning"),
    (12, "afternoon"), (17, "afternoon"), (18, "evening"), (21, "evening"),
    (22, "night"), (23, "night"),
])
def test_each_hour_has_its_part_of_the_day(hour, part):
    assert greetings.part_of_day(hour) == part


def test_late_at_night_it_asks():
    said = _all(2)

    assert "Up late?" in said
    assert said <= set(greetings.POOLS["late"])


def test_in_the_daytime_any_greeting_may_come_and_names_the_day():
    said = _all(9)

    assert "Good morning" in said and "Back at it" in said
    assert "Happy Thursday" in said
    assert not any("{" in line for line in said)


def test_greetings_never_use_a_name():
    lines = [line for pool in greetings.POOLS.values() for line in pool]

    assert not any("{n}" in line or ", " in line.replace("Hello, ", "")
                   for line in lines)


def test_the_welcome_box_says_hello(monkeypatch):
    monkeypatch.setattr(greetings, "pick", lambda: "Up late?")

    title = ai._banner_lines()[0].plain
    assert title.startswith("Up late?\nFlash CLI v")


def test_the_page_gets_the_same_greetings():
    assert web.status(ai)["greetings"] == greetings.POOLS
