---
name: manage-the-calendar
description: Check, add, move or delete calendar events and plan around them
---
1. get_date first, so "next Tuesday" and "tomorrow at 3" become exact ISO 8601 times in the user's time zone.
2. calendar list shows what is on (today to a week ahead unless start and end say); query finds an event by words. Each event shows its id in [brackets].
3. calendar add with title, start, end (an hour later if left out), where and notes; all_day for all-day events. It goes to Google Calendar's main calendar when connected, else Flash's own; calendar names another.
4. calendar update or delete takes the id from list.
5. calendar calendars lists them: Flash's own, iCal feeds (read-only), and Google calendars.

Before adding, check list for clashes and say if there is one. Confirm what you did with the date and time written out ("Thursday 6 November, 15:00 to 16:00"). The user connects Google Calendar or adds iCal feeds under Settings, Connectors, Calendar.

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

Move one by the id list showed.
```
calendar {"action": "update", "id": "google:abc123", "start": "2026-11-06T10:00", "end": "2026-11-06T10:45"}
```
