"""Good mornings: a spark says good morning in its chat with the user at
its time each day, and a lead runs through what the user left
unfinished."""

from datetime import datetime

import pytest

from flash import checkins, mornings, notify, sparks, teamchat, workspace
from tests.test_spark_reminders import Now
from tests.test_sparks import FakeClient, _reply
from tests.test_teamchat import _said, model  # noqa: F401


def _at(text):
    return datetime.fromisoformat(text).timestamp()


@pytest.fixture(autouse=True)
def _quiet_desktop(monkeypatch):
    monkeypatch.setattr(notify, "desktop", lambda *a, **k: None)
    monkeypatch.setattr(mornings, "open_reminders", lambda now=None: [])


@pytest.mark.parametrize("said, kept", [("7", "07:00"), ("7:30am", "07:30"), ("7:30 PM", "19:30"), ("19:05", "19:05"),
                                        ("12am", "00:00"), ("off", ""), ("", "")])
def test_times_are_read_as_people_say_them(said, kept):
    assert mornings.parse_time(said) == kept


@pytest.mark.parametrize("bad", ["25:00", "7:75", "breakfast", "13pm"])
def test_a_time_that_is_not_one_is_refused(bad):
    with pytest.raises(ValueError):
        mornings.parse_time(bad)


def test_due_once_a_day_and_not_hours_late():
    assert not mornings.due("07:30", "", _at("2026-11-11T07:29"))
    assert mornings.due("07:30", "", _at("2026-11-11T07:30"))
    assert not mornings.due("07:30", "2026-11-11", _at("2026-11-11T08:00"))
    assert mornings.due("07:30", "2026-11-10", _at("2026-11-11T08:00"))
    assert not mornings.due("07:30", "", _at("2026-11-11T13:00"))   # Flash was off all morning
    assert not mornings.due("", "", _at("2026-11-11T08:00"))


@pytest.fixture
def team(model):  # noqa: F811
    lead = sparks.create("Lena", "Run the team.")
    helper = sparks.create("Hugo", "Help Lena.")
    t = sparks.create_team("Crew")
    for s in (lead, helper):
        sparks.set_team(s.id, t.id)
    sparks.set_lead(helper.id, lead.id)
    return lead, helper


def test_a_lead_says_what_is_left_by_default_and_a_member_does_not(team):
    lead, helper = team
    assert sparks.update(lead.id, morning="7:30").morning_tasks
    assert not sparks.update(helper.id, morning="8").morning_tasks
    assert sparks.update(helper.id, morning="8", morning_tasks=True).morning_tasks
    with pytest.raises(sparks.SparkError, match="Good morning at"):
        sparks.update(lead.id, morning="whenever")


def test_the_lead_says_good_morning_with_the_users_open_steps(team, monkeypatch):
    lead, _ = team
    workspace.save_chat({"id": "c1abc123", "title": "Launch site", "updated": _at("2026-11-11T06:00"),
                         "log": [{"type": "plan", "steps": [{"text": "Write the copy", "status": "done"},
                                                             {"text": "Fix the hero image", "status": "todo"},
                                                             {"text": "Deploy", "status": "todo"}]}]})
    sparks.take_on(lead.id, "Draft the launch email")
    sparks.update(lead.id, morning="7:30")
    client = FakeClient([_reply("Morning! Two things left on Launch site: the hero image, then deploy.")])
    said = sparks.fire_mornings(now=_at("2026-11-11T07:31"), client=client, start=Now)
    assert [s.id for s in said] == [lead.id]
    room = teamchat.dm_id(lead.id)
    assert _said(room, teamchat.SPARK)[-1]["text"].startswith("Morning!")
    told = str(client.calls[0]["messages"])
    assert "Fix the hero image" in told and "2 of 3 steps left" in told and "Draft the launch email" in told
    # once a day
    assert sparks.fire_mornings(now=_at("2026-11-11T08:00"), start=Now) == []


def test_not_in_quiet_hours(team, monkeypatch):
    lead, _ = team
    sparks.update(lead.id, morning="6:00")
    monkeypatch.setattr(checkins, "is_quiet", lambda now=None: True)
    assert sparks.fire_mornings(now=_at("2026-11-11T06:10"), start=Now) == []
    assert sparks.find(lead.id).morning_sent == ""


def test_a_spark_sets_its_own_from_its_chat(team):
    _, helper = team
    kit = sparks.ChatKit(helper)
    assert "good_morning" in kit.tools
    assert kit.tools["good_morning"]({"at": "7:15am", "tasks": True}).startswith("Set: a good morning every day at 07:15, with")
    assert kit.tools["good_morning"]({"at": "off"}) == "Good mornings are off."


def test_a_spark_keeps_the_shape_it_is_given(team):
    lead, _ = team
    assert sparks.update(lead.id, shape="Diamond").shape == "diamond"
    assert sparks.find(lead.id).shape == "diamond"
    with pytest.raises(sparks.SparkError, match="shape"):
        sparks.update(lead.id, shape="blob")
    assert sparks.update(lead.id, shape="").shape == ""
