# pylint: disable=C0114,C0116

from datetime import datetime, timedelta

import httpx
import pytest

from flash import briefing, calendars, tools, vault

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:standup
DTSTART;TZID=America/New_York:20261102T093000
DTEND;TZID=America/New_York:20261102T094500
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4
SUMMARY:Standup\\, team
LOCATION:Room 2
END:VEVENT
BEGIN:VEVENT
UID:trip
DTSTART;VALUE=DATE:20261105
DTEND;VALUE=DATE:20261107
SUMMARY:Trip
DESCRIPTION:Pack the
 charger
END:VEVENT
BEGIN:VEVENT
UID:gone
DTSTART:20261103T120000Z
SUMMARY:Cancelled lunch
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


def test_ics_is_read_and_repeats_expand():
    raw = calendars.parse_ics(ICS, "feed:work")
    assert [r["title"] for r in raw] == ["Standup, team", "Trip"]
    assert raw[1]["notes"] == "Pack thecharger"
    start = calendars.parse_time("2026-11-01")
    end = calendars.parse_time("2026-11-15")
    found = [e for r in raw for e in calendars._expand(r, start, end)]
    standups = [e for e in found if e.title.startswith("Standup")]
    assert len(standups) == 4
    assert all(e.repeats for e in standups)
    trip = next(e for e in found if e.title == "Trip")
    assert trip.all_day


def test_feeds_are_read_through_events(monkeypatch):
    calendars.add_feed("Work", "webcal://example.com/work.ics")
    assert calendars.feeds() == {"Work": "https://example.com/work.ics"}

    def get(url, **kw):
        return httpx.Response(200, text=ICS, request=httpx.Request("GET",
                                                                   url))

    monkeypatch.setattr(calendars.httpx, "get", get)
    found = calendars.events("2026-11-01", "2026-11-15", "standup")
    assert len(found) == 4 and found[0].calendar == "feed:Work"
    with pytest.raises(calendars.CalendarError, match="read-only"):
        calendars.delete(found[0].id)
    assert calendars.remove_feed("Work")


def test_flash_s_own_calendar():
    made = calendars.add("Dentist", "2026-11-04T15:00", where="Main St")
    assert made.end.startswith("2026-11-04T16:00")
    found = calendars.events("2026-11-04", "2026-11-05")
    assert [e.title for e in found] == ["Dentist"]
    calendars.update(made.id, start="2026-11-04T16:00",
                     end="2026-11-04T17:00")
    assert "16:00" in calendars.describe(calendars._own()[0])
    calendars.delete(made.id)
    assert calendars.events("2026-11-04", "2026-11-05") == []
    with pytest.raises(calendars.CalendarError):
        calendars.add("Backwards", "2026-11-04T15:00", "2026-11-04T14:00")


def test_the_tool(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    said = tools.calendar_tool("add", start="2026-11-09T10:00",
                               title="Review")
    assert said.startswith("Added: Mon Nov 9, 10:00")
    listed = tools.calendar_tool("list", start="2026-11-09",
                                 end="2026-11-10")
    assert "Review" in listed
    event_id = listed.split("[")[1].rstrip("]")
    monkeypatch.setattr(tools, "_user_allows", lambda q, **_: True)
    assert tools.calendar_tool("delete", id=event_id).startswith("Deleted")
    assert "flash (Flash's own)" in tools.calendar_tool("calendars")


def test_coming_up_in_the_briefing():
    soon = datetime.now().astimezone() + timedelta(hours=2)
    calendars.add("Call Sam", soon.isoformat())
    cards = [c for c in briefing.cards() if c["id"].startswith("reminder:cal")]
    assert cards and cards[0]["title"] == "Call Sam"


def test_google_both_ways(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "a",
                                             "expires_in": 3600})
        if request.url.path.endswith("/calendarList"):
            return httpx.Response(200, json={"items": [
                {"id": "me@x.com", "summary": "Me", "accessRole": "owner",
                 "primary": True}]})
        if request.method == "POST":
            return httpx.Response(200, json={
                "id": "e1", "summary": "Lunch",
                "start": {"dateTime": "2026-11-10T12:00:00-05:00"},
                "end": {"dateTime": "2026-11-10T13:00:00-05:00"}})
        return httpx.Response(200, json={"items": [{
            "id": "e1", "summary": "Lunch",
            "start": {"dateTime": "2026-11-10T12:00:00-05:00"},
            "end": {"dateTime": "2026-11-10T13:00:00-05:00"}}]})

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def call(method, url, **kw):
        with real(transport=transport) as client:
            return client.request(method, url, **kw)

    monkeypatch.setattr(calendars.httpx, "request", call)
    monkeypatch.setattr(calendars.httpx, "post",
                        lambda url, **kw: call("POST", url, **kw))
    vault.set_secret(calendars.CLIENT_ID, "id.apps.googleusercontent.com")
    vault.set_secret(calendars.CLIENT_SECRET, "s")
    vault.set_secret(calendars.REFRESH, "r")
    calendars._access.update(token="", until=0.0)
    made = calendars.add("Lunch", "2026-11-10T12:00")
    assert made.id == "google:primary:e1"
    found = calendars.events("2026-11-10", "2026-11-11")
    assert [e.title for e in found] == ["Lunch"]
    assert found[0].calendar == "google:me@x.com"
    assert ("POST", "/calendar/v3/calendars/primary/events") in seen
    calendars.google_sign_out()
    assert not calendars.google_connected()


def test_signing_in_checks_its_state():
    url, state = calendars.google_sign_in_url(
        "id.apps.googleusercontent.com", "s", "http://127.0.0.1:9/cb")
    assert "access_type=offline" in url and state in url
    with pytest.raises(calendars.CalendarError, match="not the one"):
        calendars.google_finish("code", "wrong")
    with pytest.raises(calendars.CalendarError, match="Desktop app"):
        calendars.google_sign_in_url("nope", "", "x")
