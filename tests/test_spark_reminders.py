"""A spark's own reminders: set in a shift or a chat, fired by the keeper,
said by the spark in its chat with the user (a happy birthday, say)."""

import time
from datetime import datetime

import pytest

from flash import sparks, teamchat
from tests.test_sparks import FakeClient, _reply
from tests.test_teamchat import _said, model  # noqa: F401


class Now:
    """A thread that runs at once, for the test to see what it did."""

    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def maya(model):  # noqa: F811
    return sparks.create("Maya", "Keep me company.")


def _at(text):
    return datetime.fromisoformat(text).timestamp()


def test_a_birthday_reminder_is_set_for_every_year(maya):
    now = _at("2026-11-10T12:00")
    said = sparks.remind_self("maya", "add", "wish them a happy birthday",
                              "2027-03-14T09:00", "year", now=now)
    assert said.startswith("Set (")
    (r,) = sparks.find("maya").reminders
    assert r["every"] == "year" and r["at"] == _at("2027-03-14T09:00")
    assert "every year" in sparks.remind_self("maya", "list", now=now)


def test_a_reminder_in_the_past_or_without_words_is_refused(maya):
    now = _at("2026-11-10T12:00")
    assert sparks.remind_self("maya", "add", "x", "2026-01-01T09:00",
                              now=now).startswith("Error")
    assert sparks.remind_self("maya", "add", "", "in 2h",
                              now=now).startswith("Error")
    assert sparks.remind_self("maya", "add", "x", "in 2h", "fortnight",
                              now=now).startswith("Error")
    assert sparks.find("maya").reminders == []


def test_cancel_takes_one_off(maya):
    now = _at("2026-11-10T12:00")
    sparks.remind_self("maya", "add", "check in", "in 1d", now=now)
    rid = sparks.find("maya").reminders[0]["id"]
    assert sparks.remind_self("maya", "cancel", reminder_id=rid) == f"Cancelled {rid}."
    assert sparks.find("maya").reminders == []


@pytest.mark.parametrize("start, every, then", [
    ("2027-03-14T09:00", "year", "2028-03-14T09:00"),
    ("2028-02-29T09:00", "year", "2029-02-28T09:00"),
    ("2027-01-31T09:00", "month", "2027-02-28T09:00"),
    ("2027-12-15T09:00", "month", "2028-01-15T09:00"),
    ("2027-03-14T09:00", "week", "2027-03-21T09:00"),
])
def test_the_next_time_round(start, every, then):
    assert sparks.next_time(_at(start), every) == _at(then)


def test_a_due_birthday_is_said_in_its_chat_and_comes_round_next_year(maya):
    now = _at("2026-11-10T12:00")
    sparks.remind_self("maya", "add", "wish them a happy birthday",
                       "2027-03-14T09:00", "year", now=now)
    client = FakeClient([_reply("Happy birthday! Hope today is a good one.")])

    fired = sparks.fire_reminders(now=_at("2027-03-14T09:00"), client=client,
                                  start=Now)

    assert [r["text"] for _, r in fired] == ["wish them a happy birthday"]
    room = teamchat.dm_id(maya.id)
    said = _said(room, teamchat.SPARK)
    assert said[-1]["text"].startswith("Happy birthday")
    # it was told why, out of sight
    asked = client.calls[0]["messages"]
    assert any("happy birthday" in str(m.get("content", "")) for m in asked)
    assert not [e for e in _said(room, teamchat.USER) if not e.get("hidden")]
    (r,) = sparks.find("maya").reminders
    assert r["at"] == _at("2028-03-14T09:00")


def test_a_once_reminder_fires_once(maya):
    now = time.time()
    sparks.remind_self("maya", "add", "say good luck", "in 1h", now=now)
    fired = sparks.fire_reminders(now=now + 3601, client=FakeClient([_reply("Good luck!")]),
                                  start=Now)
    assert len(fired) == 1
    assert sparks.find("maya").reminders == []
    assert sparks.fire_reminders(now=now + 7200, start=Now) == []


def test_a_spark_can_set_one_from_its_chat(maya):
    kit = sparks.ChatKit(maya)
    assert "remind_self" in kit.tools
    assert any(t["function"]["name"] == "remind_self" for t in kit.schemas)
