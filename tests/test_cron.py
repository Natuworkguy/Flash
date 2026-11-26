# pylint: disable=C0114,C0115,C0116

import time

import pytest

from flash import cron


def at(year, month, day, hour=0, minute=0):
    """A local time as a timestamp, the way next_after() answers."""

    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


# 1 October 2026 is a Thursday.
THURSDAY_8AM = at(2026, 10, 1, 8, 0)


@pytest.mark.parametrize("said, line", [
    ("9am weekdays", "0 9 * * 1-5"),
    ("every weekday at 9:30", "30 9 * * 1-5"),
    ("mon, thu 08:30", "30 8 * * 1,4"),
    ("Fridays at 4:30 pm", "30 16 * * 5"),
    ("weekends 10am", "0 10 * * 0,6"),
    ("sat-sun 11", "0 11 * * 0,6"),
    ("mon-fri 7:15pm", "15 19 * * 1-5"),
    ("noon daily", "0 12 * * *"),
    ("daily at 6pm", "0 18 * * *"),
    ("at 9 and 5pm", "0 9,17 * * *"),
    ("the 1st at 9am", "0 9 1 * *"),
    ("15th 08:00", "0 8 15 * *"),
    ("monthly 9am", "0 9 1 * *"),
    ("0 9 * * 1-5", "0 9 * * 1-5"),
    ("*/30 * * * *", "*/30 * * * *"),
])
def test_it_reads_times_as_people_say_them(said, line):
    assert cron.parse(said) == line


@pytest.mark.parametrize("said, why", [
    ("tuesday", "time of day"),
    ("9:00 and 17:30", "share their minutes"),
    ("daily at 25:00", "could not read"),
    ("0 9 30 2 *", "never comes round"),
    ("0 9 * * 8", "outside"),
    ("", "empty"),
])
def test_it_says_what_it_could_not_read(said, why):
    with pytest.raises(cron.CronError, match=why):
        cron.parse(said)


def test_the_next_time_is_later_today_when_there_is_one():
    assert cron.next_after("0 9 * * 1-5", THURSDAY_8AM) == at(2026, 10, 1, 9)


def test_weekdays_skip_the_weekend():
    friday_10am = at(2026, 10, 2, 10)

    assert cron.next_after("0 9 * * 1-5", friday_10am) == at(2026, 10, 5, 9)


def test_a_time_just_reached_is_not_the_next_one():
    nine = at(2026, 10, 1, 9)

    assert cron.next_after("0 9 * * *", nine) == at(2026, 10, 2, 9)


def test_day_of_month_or_week_either_will_do():
    # Cron's rule: with both narrowed, a day matching either counts.
    # The 15th is a Thursday; the next Monday comes first.
    assert cron.next_after("0 9 15 * 1", at(2026, 10, 2)) == at(
        2026, 10, 5, 9
    )


def test_month_names_and_new_year():
    assert cron.next_after("0 0 1 jan *", THURSDAY_8AM) == at(2027, 1, 1)


def test_the_busiest_gap():
    assert cron.shortest_gap("*/15 * * * *", THURSDAY_8AM) == 15 * 60
    assert cron.shortest_gap("0 9,17 * * *", THURSDAY_8AM) == 8 * 3600


@pytest.mark.parametrize("line, words", [
    ("0 9 * * 1-5", "at 9am on weekdays"),
    ("30 8 * * 1,4", "at 8:30am on Mondays and Thursdays"),
    ("0 10 * * 0,6", "at 10am on weekends"),
    ("0 12 * * *", "at noon every day"),
    ("0 0 * * *", "at midnight every day"),
    ("0 9,17 * * *", "at 9am and 5pm every day"),
    ("0 9 1 * *", "at 9am on the 1st"),
    ("0 9 1,22 * *", "at 9am on the 1st and 22nd"),
    ("*/15 * * * *", "every 15 minutes"),
    ("5 * * * *", "at :05 past every hour"),
    ("0 0 1 jan *", "at midnight on the 1st in January"),
    ("0 9 * * 0", "at 9am on Sundays"),
])
def test_it_reads_back_in_words(line, words):
    assert cron.words(line) == words
