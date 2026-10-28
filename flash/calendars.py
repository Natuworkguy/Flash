"""Calendars: what is on, read from where you keep it, and changed.

Three kinds, all read together:

- Flash's own calendar, in ~/.flash/calendar.json: events the agent adds
  when there is nowhere else to put them.
- Feeds: any calendar's iCal address, read-only. Google Calendar gives
  each calendar one (Settings > the calendar > Secret address in iCal
  format), and so do Outlook, Apple's iCloud (a public calendar) and
  most others. Read again every few minutes.
- Google Calendar itself, both ways, once signed in: the agent reads
  every calendar on the account and adds, moves and deletes events on
  them. Signing in uses Google's own sign-in page in the browser, with an
  OAuth client of your own (a Desktop app one, from the Google Cloud
  console), and keeps only a refresh token, in the vault.

Times go in and come out as ISO 8601. One with no zone is taken to be in
this computer's.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx

from . import workspace

FILE = "calendar.json"
FEEDS_FILE = "calendar-feeds.json"
FEED_SECONDS = 10 * 60
MOST_EVENTS = 200
GOOGLE_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # nosec B105
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar"
# What the vault keeps them as.
CLIENT_ID = "GOOGLE_CALENDAR_CLIENT_ID"
CLIENT_SECRET = "GOOGLE_CALENDAR_CLIENT_SECRET"  # nosec B105
REFRESH = "GOOGLE_CALENDAR_REFRESH_TOKEN"  # nosec B105
TIMEOUT = 20

_lock = threading.Lock()
_feeds_cache: dict[str, tuple[float, list]] = {}
_access: dict[str, Any] = {"token": "", "until": 0.0}


class CalendarError(Exception):
    """Something a calendar would not do, in words."""


@dataclass
class Event:
    id: str
    title: str
    start: str
    end: str = ""
    all_day: bool = False
    where: str = ""
    notes: str = ""
    # "flash", "feed:<name>", or "google:<calendar id>".
    calendar: str = "flash"
    repeats: str = ""
    extra: dict = field(default_factory=dict)

    def summary(self) -> dict:
        found = asdict(self)
        found.pop("extra")
        return found


# Times ---------------------------------------------------------------------


def _local() -> Any:
    return datetime.now().astimezone().tzinfo


def parse_time(text: Any) -> datetime:
    """An ISO 8601 time or date, as an aware datetime (a date: its
    midnight here)."""

    text = str(text or "").strip()
    if not text:
        raise CalendarError("Give a time, like 2026-11-02T15:00.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            return datetime(day.year, day.month, day.day, tzinfo=_local())
        found = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CalendarError(f"{text!r} is not a time: use ISO 8601, like "
                            "2026-11-02T15:00.") from None
    return found if found.tzinfo else found.replace(tzinfo=_local())


def _iso(moment: datetime) -> str:
    return moment.astimezone(_local()).isoformat(timespec="minutes")


# Flash's own -----------------------------------------------------------------


def _own() -> list[Event]:
    found = workspace._read(FILE, [])
    out = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, dict) and item.get("id") and item.get("start"):
            out.append(Event(**{k: v for k, v in item.items()
                                if k in Event.__dataclass_fields__}))
    return out


def _save_own(events: list[Event]) -> None:
    workspace._write(FILE, [asdict(e) for e in events])


# Feeds ----------------------------------------------------------------------


def feeds() -> dict[str, str]:
    """The iCal feeds read, by name: {name: url}."""

    found = workspace._read(FEEDS_FILE, {})
    return {str(k): str(v) for k, v in found.items()} \
        if isinstance(found, dict) else {}


def add_feed(name: str, url: str) -> None:
    name = " ".join(str(name or "").split())[:40]
    url = str(url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not name or not re.match(r"^https?://", url):
        raise CalendarError("A feed needs a name and its iCal address "
                            "(https:// or webcal://).")
    with _lock:
        found = feeds()
        found[name] = url
        workspace._write(FEEDS_FILE, found)
    _feeds_cache.pop(url, None)


def remove_feed(name: str) -> bool:
    with _lock:
        found = feeds()
        gone = found.pop(name, None)
        workspace._write(FEEDS_FILE, found)
    return gone is not None


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _ics_time(value: str, params: dict) -> tuple[datetime, bool]:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        day = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return datetime(day.year, day.month, day.day,
                        tzinfo=_local()), True
    utc = value.endswith("Z")
    digits = value.rstrip("Z")
    moment = datetime(int(digits[:4]), int(digits[4:6]), int(digits[6:8]),
                      int(digits[9:11]), int(digits[11:13]),
                      int(digits[13:15] or 0), tzinfo=timezone.utc)
    if utc:
        return moment.replace(tzinfo=timezone.utc), False
    zone = params.get("TZID")
    if zone:
        try:
            from zoneinfo import ZoneInfo

            return moment.replace(tzinfo=ZoneInfo(zone)), False
        except Exception:  # noqa: BLE001  (a zone this computer lacks)
            return moment.replace(tzinfo=_local()), False
    return moment.replace(tzinfo=_local()), False


def parse_ics(text: str, calendar: str) -> list[dict]:
    """The events in an iCal file, each with its start and end as
    datetimes and its RRULE, if it repeats."""

    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current and "start" in current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, *rest = head.split(";")
        params = dict(p.split("=", 1) for p in rest if "=" in p)
        name = name.upper()
        try:
            if name == "DTSTART":
                current["start"], current["all_day"] = _ics_time(value, params)
            elif name == "DTEND":
                current["end"], _ = _ics_time(value, params)
        except ValueError:
            continue
        if name == "SUMMARY":
            current["title"] = _unescape(value)
        elif name == "LOCATION":
            current["where"] = _unescape(value)
        elif name == "DESCRIPTION":
            current["notes"] = _unescape(value)[:1000]
        elif name == "UID":
            current["uid"] = value
        elif name == "RRULE":
            current["rrule"] = value
        elif name == "STATUS" and value.upper() == "CANCELLED":
            current["cancelled"] = True
    return [{**e, "calendar": calendar} for e in events
            if not e.get("cancelled")]


def _expand(raw: dict, start: datetime, end: datetime) -> list[Event]:
    """RAW's occurrences between START and END."""

    length = (raw.get("end") or raw["start"]) - raw["start"]
    moments = [raw["start"]]
    if raw.get("rrule"):
        try:
            from dateutil.rrule import rrulestr

            rule = rrulestr(raw["rrule"], dtstart=raw["start"])
            moments = rule.between(start - length, end, inc=True)
        except Exception:  # noqa: BLE001  (a rule it cannot read: once)
            moments = [raw["start"]]
    out = []
    for moment in moments:
        if moment + length < start or moment > end:
            continue
        out.append(Event(
            id=f"{raw['calendar']}:{raw.get('uid', '')}:"
               f"{int(moment.timestamp())}",
            title=raw.get("title") or "(untitled)", start=_iso(moment),
            end=_iso(moment + length) if raw.get("end") else "",
            all_day=bool(raw.get("all_day")), where=raw.get("where", ""),
            notes=raw.get("notes", ""), calendar=raw["calendar"],
            repeats="yes" if raw.get("rrule") else ""))
    return out


def _feed(name: str, url: str) -> list[dict]:
    cached = _feeds_cache.get(url)
    if cached and time.time() - cached[0] < FEED_SECONDS:
        return cached[1]
    try:
        response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CalendarError(f"The feed {name} did not load: {exc}") from None
    found = parse_ics(response.text, f"feed:{name}")
    _feeds_cache[url] = (time.time(), found)
    return found


# Google --------------------------------------------------------------------


def google_connected() -> bool:
    from . import vault

    return bool(vault.get(REFRESH) and vault.get(CLIENT_ID))


def google_sign_in_url(client_id: str, client_secret: str,
                       redirect: str) -> tuple[str, str]:
    """Where to send the user to sign in, and the state to expect back.
    The client is kept in the vault, for the code to be traded with."""

    from . import vault

    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or \
            not client_secret:
        raise CalendarError(
            "Give the OAuth client's ID (it ends .apps.googleusercontent."
            "com) and its secret, from Google Cloud console > APIs & "
            "Services > Credentials > a Desktop app client, with the "
            "Google Calendar API turned on.")
    vault.set_secret(CLIENT_ID, client_id)
    vault.set_secret(CLIENT_SECRET, client_secret)
    state = secrets.token_urlsafe(16)
    _access["state"], _access["redirect"] = state, redirect
    return GOOGLE_AUTH + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect,
        "response_type": "code", "scope": GOOGLE_SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
    }), state


def google_finish(code: str, state: str) -> None:
    """Trade the code Google sent back for a refresh token, kept."""

    from . import vault

    if not state or state != _access.get("state"):
        raise CalendarError("That sign-in was not the one Flash started.")
    response = httpx.post(GOOGLE_TOKEN, data={
        "code": code, "client_id": vault.get(CLIENT_ID),
        "client_secret": vault.get(CLIENT_SECRET),
        "redirect_uri": _access.get("redirect"),
        "grant_type": "authorization_code",
    }, timeout=TIMEOUT)
    said = response.json() if response.content else {}
    if response.status_code != 200 or not said.get("refresh_token"):
        raise CalendarError("Google did not sign Flash in: "
                            + str(said.get("error_description")
                                  or said.get("error") or response.text))
    vault.set_secret(REFRESH, said["refresh_token"])
    _access.update(token=said.get("access_token", ""),
                   until=time.time() + float(said.get("expires_in", 0)) - 60)


def google_sign_out() -> None:
    from . import vault

    for name in (REFRESH, CLIENT_ID, CLIENT_SECRET):
        vault.delete(name)
    _access.update(token="", until=0.0)


def _google_token() -> str:
    from . import vault

    if _access["token"] and time.time() < _access["until"]:
        return _access["token"]
    if not google_connected():
        raise CalendarError("Google Calendar is not connected: /calendar "
                            "google, or Settings > Calendar.")
    response = httpx.post(GOOGLE_TOKEN, data={
        "client_id": vault.get(CLIENT_ID),
        "client_secret": vault.get(CLIENT_SECRET),
        "refresh_token": vault.get(REFRESH), "grant_type": "refresh_token",
    }, timeout=TIMEOUT)
    said = response.json() if response.content else {}
    if response.status_code != 200:
        raise CalendarError("Google would not let Flash in again: "
                            + str(said.get("error_description")
                                  or said.get("error")) + ". Sign in again.")
    _access.update(token=said["access_token"],
                   until=time.time() + float(said.get("expires_in", 0)) - 60)
    return _access["token"]


def _google(method: str, path: str, **kwargs: Any) -> Any:
    response = httpx.request(
        method, GOOGLE_API + path, timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {_google_token()}"}, **kwargs)
    if response.status_code >= 400:
        try:
            why = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            why = response.text[:200]
        raise CalendarError(f"Google Calendar said no: {why}")
    return response.json() if response.content else {}


def google_calendars() -> list[dict]:
    found = _google("GET", "/users/me/calendarList").get("items") or []
    return [{"id": c["id"], "name": c.get("summaryOverride")
             or c.get("summary") or c["id"],
             "writable": c.get("accessRole") in ("owner", "writer"),
             "primary": bool(c.get("primary"))} for c in found]


def _from_google(item: dict, calendar: str) -> Optional[Event]:
    if item.get("status") == "cancelled":
        return None
    begin, finish = item.get("start") or {}, item.get("end") or {}
    all_day = "date" in begin
    try:
        start = parse_time(begin.get("dateTime") or begin.get("date"))
        end = parse_time(finish.get("dateTime") or finish.get("date")) \
            if finish else None
    except CalendarError:
        return None
    return Event(
        id=f"google:{calendar}:{item['id']}",
        title=item.get("summary") or "(untitled)", start=_iso(start),
        end=_iso(end) if end else "", all_day=all_day,
        where=item.get("location") or "",
        notes=(item.get("description") or "")[:1000],
        calendar=f"google:{calendar}",
        repeats="yes" if item.get("recurringEventId") else "")


def _google_events(start: datetime, end: datetime) -> list[Event]:
    out = []
    for cal in google_calendars():
        items = _google(
            "GET", f"/calendars/{quote(cal['id'], safe='')}/events",
            params={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                    "singleEvents": "true", "orderBy": "startTime",
                    "maxResults": 250}).get("items") or []
        out += [e for e in (_from_google(i, cal["id"]) for i in items) if e]
    return out


def _google_body(title: str, start: datetime, end: datetime, all_day: bool,
                 where: str, notes: str) -> dict:
    def when(moment: datetime) -> dict:
        return {"date": moment.date().isoformat()} if all_day else \
            {"dateTime": moment.isoformat()}

    return {"summary": title, "start": when(start), "end": when(end),
            **({"location": where} if where else {}),
            **({"description": notes} if notes else {})}


# Reading and changing -----------------------------------------------------


def events(start: Any = "", end: Any = "", query: str = "",
           problems: Optional[list] = None) -> list[Event]:
    """Every event from START to END (today to a week on, if left out),
    from every calendar, soonest first; QUERY, if given, in its words. A
    calendar that fails is named in PROBLEMS."""

    begin = parse_time(start) if start else datetime.now(_local()).replace(
        hour=0, minute=0, second=0, microsecond=0)
    finish = parse_time(end) if end else begin + timedelta(days=7)
    found: list[Event] = []
    for event in _own():
        moment = parse_time(event.start)
        stop = parse_time(event.end) if event.end else moment
        if stop >= begin and moment <= finish:
            found.append(event)
    for name, url in feeds().items():
        try:
            for raw in _feed(name, url):
                found += _expand(raw, begin, finish)
        except CalendarError as exc:
            if problems is not None:
                problems.append(str(exc))
    if google_connected():
        try:
            found += _google_events(begin, finish)
        except (CalendarError, httpx.HTTPError) as exc:
            if problems is not None:
                problems.append(str(exc))
    if query:
        words = query.lower().split()
        found = [e for e in found if all(
            w in f"{e.title} {e.where} {e.notes}".lower() for w in words)]
    found.sort(key=lambda e: parse_time(e.start))
    return found[:MOST_EVENTS]


def _where_to(calendar: str) -> str:
    """Which calendar a new event goes in: the one named, else Google's
    main one when connected, else Flash's own."""

    calendar = str(calendar or "").strip()
    if calendar in ("", "default"):
        return "google:primary" if google_connected() else "flash"
    if calendar == "google":
        return "google:primary"
    if calendar.startswith("feed:"):
        raise CalendarError("A feed is read-only: add it to the calendar "
                            "it comes from, or to Google or Flash's own.")
    return calendar


def add(title: str, start: Any, end: Any = "", all_day: bool = False,
        where: str = "", notes: str = "", calendar: str = "") -> Event:
    title = " ".join(str(title or "").split())[:200]
    if not title:
        raise CalendarError("Give the event a title.")
    begin = parse_time(start)
    finish = parse_time(end) if end else begin + (
        timedelta(days=1) if all_day else timedelta(hours=1))
    if finish < begin:
        raise CalendarError("It ends before it starts.")
    target = _where_to(calendar)
    if target.startswith("google:"):
        cal = target.split(":", 1)[1]
        made = _google("POST", f"/calendars/{quote(cal, safe='')}/events",
                       json=_google_body(title, begin, finish, all_day,
                                         where, notes))
        found = _from_google(made, cal)
        if found is None:
            raise CalendarError("Google made it, but sent back nothing.")
        return found
    event = Event(id=uuid.uuid4().hex[:10], title=title, start=_iso(begin),
                  end=_iso(finish), all_day=bool(all_day), where=where,
                  notes=notes)
    with _lock:
        _save_own([*_own(), event])
    return event


def _google_parts(event_id: str) -> tuple[str, str]:
    _, cal, item = event_id.split(":", 2)
    return cal, item


def update(event_id: str, **changes: Any) -> Event:
    """Change an event: any of title, start, end, all_day, where,
    notes."""

    changes = {k: v for k, v in changes.items() if v not in (None, "")}
    if event_id.startswith("google:"):
        cal, item = _google_parts(event_id)
        path = f"/calendars/{quote(cal, safe='')}/events/{quote(item)}"
        now = _google("GET", path)
        body: dict = {}
        if "title" in changes:
            body["summary"] = changes["title"]
        if "where" in changes:
            body["location"] = changes["where"]
        if "notes" in changes:
            body["description"] = changes["notes"]
        was_all_day = "date" in (now.get("start") or {})
        all_day = bool(changes.get("all_day", was_all_day))
        for key in ("start", "end"):
            if key in changes:
                moment = parse_time(changes[key])
                body[key] = {"date": moment.date().isoformat()} if all_day \
                    else {"dateTime": moment.isoformat()}
        made = _google("PATCH", path, json=body)
        found = _from_google(made, cal)
        if found is None:
            raise CalendarError("Google changed it, but sent back nothing.")
        return found
    if event_id.startswith("feed:"):
        raise CalendarError("That event is in a feed, which is read-only.")
    with _lock:
        own = _own()
        found = next((e for e in own if e.id == event_id), None)
        if found is None:
            raise CalendarError(f"There is no event {event_id!r}.")
        for key in ("title", "where", "notes", "all_day"):
            if key in changes:
                setattr(found, key, changes[key])
        for key in ("start", "end"):
            if key in changes:
                setattr(found, key, _iso(parse_time(changes[key])))
        _save_own(own)
    return found


def delete(event_id: str) -> None:
    if event_id.startswith("google:"):
        cal, item = _google_parts(event_id)
        _google("DELETE",
                f"/calendars/{quote(cal, safe='')}/events/{quote(item)}")
        return
    if event_id.startswith("feed:"):
        raise CalendarError("That event is in a feed, which is read-only.")
    with _lock:
        own = _own()
        kept = [e for e in own if e.id != event_id]
        if len(kept) == len(own):
            raise CalendarError(f"There is no event {event_id!r}.")
        _save_own(kept)


def describe(event: Event) -> str:
    """One line for the model, or the terminal."""

    start = parse_time(event.start)
    day = f"{start:%a %b} {start.day}"
    when = day if event.all_day else f"{day}, {start:%H:%M}"
    if event.end and not event.all_day:
        when += "-" + parse_time(event.end).strftime("%H:%M")
    bits = [f"{when}: {event.title}"]
    if event.where:
        bits.append(f"at {event.where}")
    if event.repeats:
        bits.append("(repeats)")
    bits.append(f"[{event.id}]")
    return " ".join(bits)


def coming_up(hours: float = 24) -> list[Event]:
    """What starts in the next HOURS, for the briefing; nothing if a
    calendar cannot be read."""

    now = datetime.now(_local())
    return [e for e in events(now.isoformat(),
                              (now + timedelta(hours=hours)).isoformat())
            if parse_time(e.start) >= now - timedelta(minutes=5)]


def as_json(found: list[Event]) -> str:
    return json.dumps([e.summary() for e in found])
