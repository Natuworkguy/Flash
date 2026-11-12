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
- Apple Calendar, both ways, on a Mac once switched on: every calendar
  the Calendar app shows (iCloud, Exchange, Google added there...), read
  and added to through AppleScript. Nothing to sign in to; macOS asks
  once to let Flash control Calendar.

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
APPLE_FILE = "calendar-apple.json"
APPLE_TIMEOUT = 60
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
    # "flash", "feed:<name>", "google:<calendar id>" or
    # "apple:<calendar name>".
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


# Apple Calendar --------------------------------------------------------------

# AppleScript's dates have no zone, so times cross as seconds from
# 2001-01-01 00:00 on this computer's clock, both ways: no date text to
# be read in the user's locale.
# A wall-clock time, with no zone on purpose: AppleScript's have none.
_WALL_2001 = datetime(2001, 1, 1)  # noqa: DTZ001
_APPLE_BASE = (
    "set b to current date",
    "set day of b to 1",
    "set year of b to 2001",
    "set month of b to January",
    "set time of b to 0",
)
_APPLE_TEXT = (
    "on t(x)",
    "if x is missing value then return \"\"",
    "return x as text",
    "end t",
)
_APPLE_READ = (
    *_APPLE_TEXT,
    "on run argv",
    *_APPLE_BASE,
    "set t0 to b + (item 1 of argv as number)",
    "set t1 to b + (item 2 of argv as number)",
    "set fs to character id 31",
    "set rs to character id 30",
    "set out to \"\"",
    'tell application "Calendar"',
    "repeat with c in calendars",
    "set cn to name of c",
    "repeat with e in (every event of c whose start date \u2264 t1 and "
    "end date \u2265 t0)",
    "set out to out & cn & fs & (uid of e) & fs & my t(summary of e) & fs "
    "& ((start date of e) - b) & fs & ((end date of e) - b) & fs & "
    "(allday event of e) & fs & my t(location of e) & fs & "
    "my t(description of e) & rs",
    "end repeat",
    "end repeat",
    "end tell",
    "return out",
    "end run",
)
_APPLE_CALENDARS = (
    "set out to \"\"",
    'tell application "Calendar"',
    "repeat with c in calendars",
    "set out to out & (name of c) & (character id 31) & (writable of c) "
    "& (character id 30)",
    "end repeat",
    "end tell",
    "return out",
)
_APPLE_ADD = (
    "on run argv",
    *_APPLE_BASE,
    'tell application "Calendar"',
    "if item 1 of argv is \"\" then",
    "set c to first calendar whose writable is true",
    "else",
    "set c to calendar (item 1 of argv)",
    "end if",
    "set e to make new event at end of events of c with properties "
    "{summary:item 2 of argv, start date:b + (item 3 of argv as number), "
    "end date:b + (item 4 of argv as number), "
    "allday event:(item 5 of argv is \"true\"), location:item 6 of argv, "
    "description:item 7 of argv}",
    "return (name of c) & (character id 31) & (uid of e)",
    "end tell",
    "end run",
)
_APPLE_DELETE = (
    "on run argv",
    'tell application "Calendar"',
    "delete (every event of calendar (item 1 of argv) whose uid is "
    "(item 2 of argv))",
    "end tell",
    "end run",
)


def apple_available() -> bool:
    from . import computer

    return computer.system() == "mac"


def apple_connected() -> bool:
    """Whether Apple Calendar is read and written: on a Mac, once the
    user has switched it on."""

    found = workspace._read(APPLE_FILE, {})
    return apple_available() and isinstance(found, dict) \
        and bool(found.get("on"))


def apple_connect(on: bool = True) -> None:
    if on and not apple_available():
        raise CalendarError("Apple Calendar is only on a Mac.")
    if on:
        # Asked once here, so macOS asks for Automation now, not mid-reply.
        apple_calendars()
    workspace._write(APPLE_FILE, {"on": bool(on)})


def _apple(script: tuple, *argv: str) -> str:
    from . import computer

    command = ["osascript"]
    for line in script:
        command += ["-e", line]
    try:
        return computer._run([*command, *argv], timeout=APPLE_TIMEOUT)
    except computer.ComputerError as exc:
        if computer._not_allowed(exc, "Calendar"):
            raise CalendarError(
                "macOS has to let the app Flash runs in (Terminal, iTerm "
                "and the like) control Calendar: System Settings > "
                "Privacy & Security > Automation, and Calendars.") from None
        raise CalendarError(f"Apple Calendar said no: {exc}") from None


def _records(text: str) -> list[list[str]]:
    return [r.split("\x1f") for r in text.split("\x1e") if r.strip()]


def _apple_seconds(moment: datetime) -> int:
    """MOMENT as seconds from 2001 on this computer's clock."""

    wall = moment.astimezone(_local()).replace(tzinfo=None)
    return int((wall - _WALL_2001).total_seconds())


def _apple_moment(seconds: str) -> datetime:
    wall = _WALL_2001 + timedelta(seconds=float(seconds))
    return wall.astimezone()


def apple_calendars() -> list[dict]:
    return [{"name": r[0], "writable": r[1:2] == ["true"]}
            for r in _records(_apple(_APPLE_CALENDARS))]


def _apple_events(start: datetime, end: datetime) -> list[Event]:
    out = []
    for row in _records(_apple(_APPLE_READ, str(_apple_seconds(start)),
                               str(_apple_seconds(end)))):
        if len(row) < 8:
            continue
        cal, uid, title, begin, finish, all_day, where, notes = row[:8]
        try:
            moment, stop = _apple_moment(begin), _apple_moment(finish)
        except ValueError:
            continue
        out.append(Event(
            id=f"apple:{cal}:{uid}", title=title or "(untitled)",
            start=_iso(moment), end=_iso(stop), all_day=all_day == "true",
            where=where, notes=notes[:1000], calendar=f"apple:{cal}"))
    return out


def _apple_add(cal: str, title: str, start: datetime, end: datetime,
               all_day: bool, where: str, notes: str) -> Event:
    said = _apple(_APPLE_ADD, cal, title, str(_apple_seconds(start)),
                  str(_apple_seconds(end)), "true" if all_day else "false",
                  where, notes)
    cal, _, uid = said.partition("\x1f")
    return Event(id=f"apple:{cal}:{uid}", title=title, start=_iso(start),
                 end=_iso(end), all_day=all_day, where=where, notes=notes,
                 calendar=f"apple:{cal}")


def _apple_parts(event_id: str) -> tuple[str, str]:
    # The calendar's name may hold a colon; the uid never ends in one.
    rest = event_id.split(":", 1)[1]
    cal, _, uid = rest.rpartition(":")
    return cal, uid


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
    if apple_connected():
        try:
            found += _apple_events(begin, finish)
        except CalendarError as exc:
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
    main one when connected, else Apple Calendar's first writable one
    when switched on, else Flash's own."""

    calendar = str(calendar or "").strip()
    if calendar in ("", "default"):
        return "google:primary" if google_connected() else \
            "apple:" if apple_connected() else "flash"
    if calendar == "google":
        return "google:primary"
    if calendar in ("apple", "apple:") or calendar.startswith("apple:"):
        if not apple_connected():
            raise CalendarError("Apple Calendar is not switched on: "
                                "Settings > Calendar.")
        return "apple:" if calendar == "apple" else calendar
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
    if target.startswith("apple:"):
        return _apple_add(target.split(":", 1)[1], title, begin, finish,
                          bool(all_day), where, notes)
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
    if event_id.startswith("apple:"):
        raise CalendarError("Flash cannot change an Apple Calendar event "
                            "yet: delete it and add it again, or change "
                            "it in Calendar.")
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
    if event_id.startswith("apple:"):
        _apple(_APPLE_DELETE, *_apple_parts(event_id))
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


# Finding a time, and inviting people ------------------------------------------

# An all-day event only blocks the day when it says so: a birthday or a reminder does not.
_AWAY = re.compile(r"\b(ooo|out of office|off|away|vacation|holiday|leave|sick|travel|trip|flight|wedding|busy)\b", re.I)


def _busy_ranges(text_ranges: Any, tz: Any) -> list[tuple[datetime, datetime]]:
    out = []
    for item in text_ranges or []:
        if isinstance(item, dict):
            a, b = item.get("start"), item.get("end")
        else:
            a, _, b = str(item).partition("/")
        try:
            out.append((parse_time(a), parse_time(b)))
        except (CalendarError, ValueError, TypeError):
            continue
    return out


def free_slots(minutes: int = 30, start: Any = "", end: Any = "", day_start: str = "09:00", day_end: str = "18:00",
               weekends: bool = False, busy: Any = None, feeds_of_others: Any = None, limit: int = 6,
               problems: Optional[list] = None) -> list[tuple[datetime, datetime]]:
    """Times MINUTES long, between START and END (from the next half hour to a week on), inside working hours
    DAY_START to DAY_END, when nothing is on in the user's calendars, nor in BUSY (ranges others gave: "start/end"
    or {start, end}), nor in the iCal feeds of the others (FEEDS_OF_OTHERS: their free/busy or calendar addresses).
    Spread over the days, the earliest first, so the choice is a real one."""

    tz = _local()
    now = datetime.now(tz)
    begin = parse_time(start) if start else now
    if begin < now:
        begin = now
    finish = parse_time(end) if end else begin + timedelta(days=7)
    minutes = max(5, min(int(minutes or 30), 600))
    taken: list[tuple[datetime, datetime]] = []
    for e in events(begin - timedelta(days=1), finish, problems=problems):
        a = parse_time(e.start)
        b = parse_time(e.end) if e.end else a + timedelta(hours=1)
        if e.all_day and not _AWAY.search(f"{e.title} {e.notes}"):
            continue
        taken.append((a, b))
    taken += _busy_ranges(busy, tz)
    for url in feeds_of_others or []:
        try:
            for raw in _feed("their calendar", str(url)):
                for ev in _expand(raw, begin, finish):
                    a = parse_time(ev.start)
                    taken.append((a, parse_time(ev.end) if ev.end else a + timedelta(hours=1)))
        except CalendarError as exc:
            if problems is not None:
                problems.append(str(exc))
    h0, m0 = (int(x) for x in day_start.split(":"))
    h1, m1 = (int(x) for x in day_end.split(":"))
    step = timedelta(minutes=30 if minutes >= 30 else 15)
    length = timedelta(minutes=minutes)
    found: list[tuple[datetime, datetime]] = []
    per_day: dict = {}
    day = begin.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= finish and len(found) < limit * 4:
        if weekends or day.weekday() < 5:
            t = day.replace(hour=h0, minute=m0)
            close = day.replace(hour=h1, minute=m1)
            if t < begin:  # from the next whole step
                mins = int(step.total_seconds() // 60)
                t = begin.replace(second=0, microsecond=0)
                t += timedelta(minutes=(-t.minute) % mins or (mins if begin.second or begin.microsecond else 0))
            while t + length <= close and t + length <= finish:
                if not any(a < t + length and t < b for a, b in taken):
                    found.append((t, t + length))
                    per_day[day.date()] = per_day.get(day.date(), 0) + 1
                    t += max(length, timedelta(hours=1))
                else:
                    t += step
        day += timedelta(days=1)
    # at most two a day, so a week's worth of options shows
    picked, counted = [], {}
    for a, b in found:
        if counted.get(a.date(), 0) < 2:
            picked.append((a, b))
            counted[a.date()] = counted.get(a.date(), 0) + 1
        if len(picked) >= limit:
            break
    return picked


def invitation(title: str, start: datetime, end: datetime, organizer: str, attendees: list[str], where: str = "",
               notes: str = "", uid: str = "") -> str:
    """An iCalendar invitation (METHOD:REQUEST) that any mail app shows with accept and decline buttons."""

    def esc(text: str) -> str:
        return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def utc(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VCALENDAR", "PRODID:-//Flash//Calendar//EN", "VERSION:2.0", "CALSCALE:GREGORIAN", "METHOD:REQUEST",
             "BEGIN:VEVENT", f"UID:{uid or uuid.uuid4().hex}@flash", f"DTSTAMP:{utc(datetime.now(timezone.utc))}",
             f"DTSTART:{utc(start)}", f"DTEND:{utc(end)}", f"SUMMARY:{esc(title)}",
             f"ORGANIZER:mailto:{organizer}"]
    lines += [f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{a}" for a in attendees]
    if where:
        lines.append(f"LOCATION:{esc(where)}")
    if notes:
        lines.append(f"DESCRIPTION:{esc(notes)}")
    lines += ["SEQUENCE:0", "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def invite(title: str, start: Any, end: Any = "", attendees: Any = None, where: str = "", notes: str = "",
           calendar: str = "") -> tuple[Event, str]:
    """Put the event in the user's calendar and invite ATTENDEES (email addresses): through Google Calendar when it is
    connected (Google sends the invitations), else as an email with the invitation attached from the user's mail
    account. The event, and who was invited how."""

    people = [a.strip() for a in (attendees if isinstance(attendees, list) else str(attendees or "").split(",")) if "@" in str(a)]
    begin = parse_time(start)
    finish = parse_time(end) if end else begin + timedelta(hours=1)
    target = _where_to(calendar)
    if target.startswith("google:") and people:
        cal = target.split(":", 1)[1]
        body = _google_body(title, begin, finish, False, where, notes)
        body["attendees"] = [{"email": a} for a in people]
        made = _google("POST", f"/calendars/{quote(cal, safe='')}/events?sendUpdates=all", json=body)
        found = _from_google(made, cal)
        if found is None:
            raise CalendarError("Google made it, but sent back nothing.")
        return found, f"Google Calendar sent the invitation to {', '.join(people)}."
    made = add(title, begin, finish, False, where, notes, calendar)
    if not people:
        return made, "No one to invite."
    from . import mail  # deferred: mail is only needed to invite

    if not mail.configured():
        return made, "Added, but no email account is set up to send the invitation: connect one, or share the time."
    message = mail.compose(to=", ".join(people), subject=f"Invitation: {title}",
                           body=(f"{title}\n{describe(made)}\n" + (f"\n{notes}\n" if notes else "")))
    ics = invitation(title, begin, finish, str(message["From"]), people, where, notes, made.id)
    message.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics",
                           params={"method": "REQUEST"})
    sent = mail.send(message)
    return made, f"Invitation emailed to {sent}, with the event attached for their calendars."
