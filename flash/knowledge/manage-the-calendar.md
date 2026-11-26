---
name: manage-the-calendar
description: Check, add, move or delete calendar events and plan around them
---
1. get_date first, so "next Tuesday" and "tomorrow at 3" become exact ISO 8601 times in the user's time zone.
2. calendar list shows what is on (today to a week ahead unless start and end say); query finds an event by words. Each event shows its id in [brackets].
3. calendar add with title, start, end (an hour later if left out), where and notes; all_day for all-day events. It goes to Google Calendar's main calendar when connected, else Apple Calendar when switched on, else Flash's own; calendar names another (flash, google, google:<id>, apple, apple:<calendar name>). Adding to Apple Calendar asks the user yes or no every time, even in autonomous mode; only UltraAuto skips it.
4. calendar update or delete takes the id from list. Apple Calendar events (ids apple:<calendar>:<uid>) can be deleted but not updated: delete and add again, or tell the user to change it in Calendar.
5. Booking a meeting with others: calendar find_time with minutes, a stretch (start, end) and working hours, plus whatever you know of the others: busy (their "start/end" busy times, from what they said or wrote) and their_calendars (an iCal or free/busy address they shared). It gives times free for everyone, at most two a day, and a choices block to offer them. When the user picks one, calendar invite with title, start, end, attendees (email addresses), where and notes: Google Calendar sends the invitations when connected, else an invitation goes by email from the user's account, with accept and decline in any mail app. Ask the user before inviting (the tool asks too).
6. calendar calendars lists them: Flash's own, iCal feeds (read-only), Google calendars, and Apple calendars.

Before adding, check list for clashes and say if there is one. Confirm what you did with the date and time written out ("Thursday 6 November, 15:00 to 16:00"). The user connects Google Calendar, switches on Apple Calendar (Mac only: every calendar the Calendar app shows, through AppleScript; macOS asks once for Automation) or adds iCal feeds under Settings, Connectors, Calendar. Apple Calendar repeating events show only their first occurrence. For to-dos rather than events, use reminders (see use-reminders).

## Examples

```
get_date {}
```

```
calendar {"action": "list", "start": "2026-11-03T00:00", "end": "2026-11-08T00:00"}
```

```
calendar {"action": "add", "title": "Dentist", "start": "2026-11-05T15:00", "end": "2026-11-05T15:45", "where": "12 High Street"}
```

Find an hour that suits the user and Sam, who said they are busy Tuesday afternoon, then book the one picked.
```
calendar {"action": "find_time", "minutes": 60, "start": "2026-11-16T00:00", "end": "2026-11-21T00:00", "busy": ["2026-11-17T12:00/2026-11-17T18:00"], "title": "Plan review"}
```

```
calendar {"action": "invite", "title": "Plan review", "start": "2026-11-18T10:00", "end": "2026-11-18T11:00", "attendees": ["sam@example.com"], "where": "Video call"}
```

Add to one Apple calendar by name.
```
calendar {"action": "add", "title": "Gym", "start": "2026-11-07T08:00", "calendar": "apple:Home"}
```

Move one by the id list showed.
```
calendar {"action": "update", "id": "google:abc123", "start": "2026-11-06T10:00", "end": "2026-11-06T10:45"}
```
