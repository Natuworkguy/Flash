"""What to tell the user about, how often, and quiet hours."""

import datetime as dt

import pytest

from flash import checkins, notify, sparks, teamchat, tools


def at(hour, minute=0):
    return dt.datetime(2026, 11, 10, hour, minute).timestamp()


@pytest.mark.parametrize("said, span", [
    ("10pm-7am", (22 * 60, 7 * 60)),
    ("22:00 to 07:00", (22 * 60, 7 * 60)),
    ("11:30pm until 6", (23 * 60 + 30, 6 * 60)),
    ("10-7am", (22 * 60, 7 * 60)),
    ("1pm - 2pm", (13 * 60, 14 * 60)),
    ("off", None),
    ("", None),
])
def test_quiet_hours_read_as_people_say_them(said, span):
    assert checkins.parse_quiet(said) == span


@pytest.mark.parametrize("said", ["sometimes", "25:00-7am", "9pm-9pm"])
def test_quiet_hours_it_cannot_read_say_why(said):
    with pytest.raises(checkins.CheckinError):
        checkins.parse_quiet(said)


def test_quiet_hours_wrap_past_midnight():
    checkins.set_quiet_hours("10pm-7am")

    assert checkins.is_quiet(at(23))
    assert checkins.is_quiet(at(3))
    assert not checkins.is_quiet(at(7))
    assert not checkins.is_quiet(at(12))
    assert checkins.quiet_left(at(6, 30)) == 30 * 60
    assert checkins.quiet_left(at(12)) == 0
    assert checkins.quiet_words() == "10pm to 7am"


def test_quiet_hours_hold_notifications_until_they_end(monkeypatch):
    checkins.set_quiet_hours("10pm-7am")

    assert notify.wait_to_notify(at(6)) == 3600
    assert notify.wait_to_notify(at(8)) == 0


def test_no_spark_calls_in_quiet_hours(monkeypatch):
    checkins.set_quiet_hours("10pm-7am")
    monkeypatch.setattr(checkins, "is_quiet", lambda now=None: True)
    spark = sparks.Spark(id="s1", name="Scout", goal="watch")

    with pytest.raises(teamchat.TeamChatError, match="quiet hours"):
        teamchat.ring(spark, "the build broke")


def test_a_shift_reads_what_to_tell_and_when_not(monkeypatch):
    assert checkins.prompt_block() == ""

    checkins.set_interests("New issues on flash, hourly. Nothing about "
                           "newsletters.")
    checkins.set_quiet_hours("10pm-7am")
    block = checkins.prompt_block(at(23))

    assert "New issues on flash, hourly" in block
    assert "10pm to 7am" in block
    assert "quiet hours NOW" in block
    assert "not quiet hours now" in checkins.prompt_block(at(12))


def test_the_tool_reads_and_writes_it():
    said = tools.FUNCTIONS["checkin_preferences"]()
    assert "(nothing set)" in said and "Quiet hours: off" in said

    said = tools.FUNCTIONS["checkin_preferences"](
        interests="The weather each morning", quiet_hours="11pm-6am")
    assert said.startswith("Saved.")
    assert checkins.interests() == "The weather each morning"
    assert checkins.quiet_words() == "11pm to 6am"
    assert "Error" in tools.FUNCTIONS["checkin_preferences"](
        quiet_hours="whenever")
