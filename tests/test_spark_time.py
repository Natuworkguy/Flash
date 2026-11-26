"""Sparks have a sense of time: each shift and each answer is told the
time as it is now, how long ago it last worked and when it works next,
whether it starts late after Flash was closed or the computer asleep,
and when each message it reads was said."""

import time

import pytest

from flash import sparks, teamchat, timesense, tools
from tests.test_sparks import FakeClient, _reply
from tests.test_teamchat import model, team  # noqa: F401


def _system(client):
    return client.calls[0]["messages"][0]["content"]


def _shift(spark, *replies):
    client = FakeClient(list(replies) or [_reply("All quiet.")])
    sparks.shift(spark.id, client=client)
    return client


@pytest.fixture
def scout(model):  # noqa: F811
    return sparks.create("Scout", "Watch the issues.", every="1h")


def test_a_shift_is_told_the_time_now_not_when_flash_started(
        scout, monkeypatch):
    monkeypatch.setattr(tools, "CURRENT_DATE_PROMPT",
                        "Today's real-world date is 2020-01-01.")

    system = _system(_shift(scout))

    assert "=== The time now ===" in system
    assert timesense.local().strftime("%Y-%m-%d") in system
    assert "2020-01-01" not in system


def test_the_first_shift_knows_it_is_the_first(scout):
    system = _system(_shift(scout))

    assert "This is your first shift." in system
    assert "Your next one is due" in system


def _last_ran(spark, ago_seconds, due_ago_seconds):
    now = time.time()

    def change(s):
        s.last_run = now - ago_seconds
        s.next_run = now - due_ago_seconds
        s.runs = 3

    return sparks._edit(spark.id, change)


def test_a_shift_on_time_says_when_the_last_was_and_the_next_is(scout):
    kept = _last_ran(scout, 2 * 3600, 10)

    system = _system(_shift(kept))

    assert (f"Your last shift was {timesense.when(kept.last_run)}, "
            "2 hours ago.") in system
    assert "Your next one is due" in system
    assert "late (Flash was closed" not in system


def test_a_late_shift_is_told_how_late_and_what_never_ran(scout):
    kept = _last_ran(scout, 6 * 3600 + 600, 5 * 3600 + 600)

    system = _system(_shift(kept))

    assert f"This one was due {timesense.when(kept.next_run)}" in system
    assert "starts 5 hours late" in system
    assert "5 more of your shifts came due meanwhile and never ran" in system
    assert "Cover everything since your last shift" in system


def test_a_shift_asked_for_is_not_called_late(scout):
    kept = _last_ran(scout, 6 * 3600 + 600, 5 * 3600 + 600)
    sparks.run_now(kept.id)

    system = _system(_shift(kept))

    assert "late (Flash was closed" not in system


def test_a_paused_spark_is_told_nothing_starts_by_itself(scout):
    sparks.set_paused(scout.id, True)

    assert "You are paused" in sparks.shift_timing(sparks.find(scout.id))


def test_missed_shifts_are_counted_on_its_schedule(scout):
    now = time.time()

    assert sparks.missed_shifts(scout, now - 3 * 3600 - 60, now) == 3
    assert sparks.missed_shifts(scout, now - 60, now) == 0
    on_call = sparks.create("Fixer", "Fix.", every="on call")
    assert sparks.missed_shifts(on_call, now - 9 * 3600, now) == 0


def test_a_shift_knows_how_old_its_notes_and_last_report_are(scout):
    _shift(scout,
           _reply("", ("keep_notes", {"notes": "Issue 12 seen."})),
           _reply("Issue 12 is a bug."))
    kept = sparks.find(scout.id)
    assert kept.notes == "Issue 12 seen." and kept.notes_at > 0

    system = _system(_shift(kept))

    assert ("written by you for you (last changed "
            f"{timesense.when(kept.notes_at)}):") in system
    assert f"Your last report ({timesense.when(kept.reports[-1].at)}, " \
        in system
    assert "Mind the time" in system


def test_what_was_handed_over_says_how_long_ago():
    now = time.time()
    opening = sparks._opening("", [
        {"from": "Lead", "text": "Check issue 12.", "at": now - 7200},
        {"from": "the user", "text": "Look at the logs.", "at": now - 300,
         "job": True},
    ], now)

    assert "- [2 hours ago] From Lead: Check issue 12." in opening
    assert "- [5 minutes ago] Look at the logs." in opening


def test_its_status_says_when_by_the_clock(scout):
    _shift(scout)

    status = sparks.status_block(sparks.find(scout.id))

    assert "Last shift: 0 minutes ago (today at " in status
    upcoming = status.split("Next shift: in ", 1)[1].splitlines()[0]
    assert "(today at " in upcoming or "(tomorrow at " in upcoming


def test_in_its_chat_it_is_told_the_time_and_copies_no_stamp(scout):
    client = FakeClient([_reply("[Thu 26 Nov 14:05] Nothing new yet.")])

    reply = sparks.say(scout.id, "Anything?", client=client)

    assert reply.text == "Nothing new yet."
    assert "=== The time now ===" in _system(client)
    assert "Mind the time" in _system(client)
    asked = client.calls[0]["messages"][-1]["content"]
    assert asked.startswith("[") and asked.endswith("] Anything?")


def test_the_team_chat_shows_when_each_message_was_said(team):  # noqa: F811
    said = teamchat.say("desk", "@scout morning, any news?")
    client = FakeClient([_reply("[09:00] Morning! Two new issues.")])

    posted = teamchat.answer("desk", said, client)

    seen = client.calls[0]["messages"][-1]["content"]
    assert "--- Today, " in seen
    assert (f"[{timesense.clock(said['at'])}] #{said['id']} The user: "
            "@scout morning") in seen
    assert posted[-1]["text"] == "Morning! Two new issues."
    assert "=== The time now ===" in _system(client)
