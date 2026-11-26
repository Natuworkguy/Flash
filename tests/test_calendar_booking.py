"""Finding a time that suits everyone, and inviting them."""

from datetime import datetime, timedelta
from email import message_from_bytes

import pytest

from flash import calendars, tools


@pytest.fixture
def week(monkeypatch):
    tz = calendars._local()
    monday = datetime(2030, 1, 7, 0, 0, tzinfo=tz)   # a Monday
    events = [calendars.Event(id="1", title="Standup", start=(monday + timedelta(hours=9)).isoformat(),
                              end=(monday + timedelta(hours=10)).isoformat()),
              calendars.Event(id="2", title="Mum's birthday", start=monday.isoformat(), all_day=True)]
    monkeypatch.setattr(calendars, "events", lambda *a, **k: events)
    return monday


def test_free_time_skips_meetings_nights_and_weekends(week):
    slots = calendars.free_slots(60, week.isoformat(), (week + timedelta(days=7)).isoformat())
    assert slots[0][0] == week + timedelta(hours=10)          # after standup; the birthday blocks nothing
    assert all(a.weekday() < 5 and 9 <= a.hour and b.hour <= 18 for a, b in slots)
    assert max(sum(1 for a, _ in slots if a.date() == d.date()) for d, _ in slots) <= 2


def test_others_busy_times_count(week):
    busy = [f"{(week + timedelta(hours=10)).isoformat()}/{(week + timedelta(hours=12)).isoformat()}"]
    slots = calendars.free_slots(60, week.isoformat(), (week + timedelta(days=1)).isoformat(), busy=busy)
    assert slots[0][0] == week + timedelta(hours=12)


def test_an_invitation_any_mail_app_can_answer(week):
    ics = calendars.invitation("Plan review", week + timedelta(hours=14), week + timedelta(hours=15),
                               "me@example.com", ["sam@example.com"], "Room 4; floor 2")
    assert "METHOD:REQUEST" in ics and "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:sam@example.com" in ics
    assert "LOCATION:Room 4\; floor 2" in ics and ics.endswith("\r\n")


def test_invite_emails_the_invitation(week, monkeypatch):
    from flash import mail

    sent = []
    monkeypatch.setattr(calendars, "google_connected", lambda: False)
    monkeypatch.setattr(calendars, "apple_connected", lambda: False)
    monkeypatch.setattr(calendars, "_own", lambda: [])
    monkeypatch.setattr(calendars, "_save_own", lambda events: None)
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "_account", lambda name="": {"address": "me@example.com"})
    monkeypatch.setattr(mail, "send", lambda m: sent.append(m) or str(m["To"]))
    made, how = calendars.invite("Plan review", (week + timedelta(hours=14)).isoformat(), "", ["sam@example.com"])
    assert made.title == "Plan review" and "sam@example.com" in how
    parsed = message_from_bytes(sent[0].as_bytes())
    part = [p for p in parsed.walk() if p.get_content_type() == "text/calendar"][0]
    assert "METHOD:REQUEST" in part.get_payload(decode=True).decode()


def test_the_tool_offers_the_times_as_choices(week, monkeypatch):
    said = tools.calendar_tool("find_time", start=week.isoformat(), end=(week + timedelta(days=2)).isoformat(), minutes=30, title="Sync")
    assert '"type": "choices"' in said and "Book Mon Jan 07, 10:00-10:30" in said
