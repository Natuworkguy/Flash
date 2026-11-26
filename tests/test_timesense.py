"""A sense of time: what time it is now, and how long ago something was,
in the words a spark reads."""

import re
from datetime import datetime

import pytest

from flash import timesense


def _at(text):
    return datetime.fromisoformat(text).timestamp()


NOW = _at("2026-11-26T14:42")


@pytest.mark.parametrize("hour, words", [
    (4, "at night"), (5, "in the morning"), (11, "in the morning"),
    (12, "in the afternoon"), (16, "in the afternoon"),
    (17, "in the evening"), (20, "in the evening"), (21, "at night"),
])
def test_the_part_of_the_day(hour, words):
    assert timesense.part_of_day(hour) == words


@pytest.mark.parametrize("then, words", [
    ("2026-11-26T09:05", "today at 09:05"),
    ("2026-11-25T23:59", "yesterday at 23:59"),
    ("2026-11-27T08:00", "tomorrow at 08:00"),
    ("2026-11-24T09:00", "on Tue 24 Nov at 09:00"),
    ("2025-12-31T18:30", "on Wed 31 Dec 2025 at 18:30"),
])
def test_when_says_the_day_as_it_is_from_now(then, words):
    assert timesense.when(_at(then), NOW) == words


@pytest.mark.parametrize("seconds, words", [
    (20, "just now"),
    (60, "1 minute ago"),
    (45 * 60, "45 minutes ago"),
    (5 * 3600, "5 hours ago"),
    (30 * 3600, "30 hours ago"),
    (3 * 86400, "3 days ago"),
    (21 * 86400, "3 weeks ago"),
    (120 * 86400, "4 months ago"),
])
def test_how_long_ago(seconds, words):
    assert timesense.ago(NOW - seconds, NOW) == words


def test_the_time_now_is_read_each_time_not_kept():
    block = timesense.now_block(NOW)

    assert "Thursday 26 November 2026, 14:42 in the afternoon" in block
    assert "2026-11-26T14:42" in block
    assert "(2026)" in block
    assert re.search(r"UTC[+-]\d\d:\d\d", block)
    later = timesense.now_block(_at("2026-11-29T07:10"))
    assert "Sunday 29 November 2026, 07:10 in the morning" in later
    assert "Thursday" not in later


def test_a_stamp_and_a_day_line():
    assert timesense.stamp(_at("2026-11-26T09:05"), NOW) == \
        "[Thu 26 Nov 09:05]"
    assert timesense.stamp(_at("2025-11-26T09:05"), NOW) == \
        "[Wed 26 Nov 2025 09:05]"
    assert timesense.day_header(_at("2026-11-26T09:05"), NOW) == \
        "--- Today, Thursday 26 November ---"
    assert timesense.day_header(_at("2026-11-25T09:05"), NOW) == \
        "--- Yesterday, Wednesday 25 November ---"
    assert timesense.day_header(_at("2026-11-20T09:05"), NOW) == \
        "--- Friday 20 November ---"


@pytest.mark.parametrize("text, kept", [
    ("[14:05] On it.", "On it."),
    ("[Thu 26 Nov 14:05] On it.", "On it."),
    ("#12 [14:05] On it.", "On it."),
    ("On it [14:05].", "On it [14:05]."),
    ("[x] done", "[x] done"),
    ("", ""),
])
def test_a_stamp_copied_to_the_start_is_taken_off(text, kept):
    assert timesense.unstamp(text) == kept
