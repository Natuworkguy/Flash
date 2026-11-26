---
name: remind-the-user-later
description: Remind the user at a time ("remind me at 6", "in 20 minutes", "later today") with Flash's own reminders
---
- remind_me adds, lists and cancels reminders Flash keeps itself. When one comes due, Flash shows a desktop notification and puts it in the briefing (which the web page and the watch show). They are kept in ~/.flash/timers.json, so a restart does not lose them.
- They fire while Flash or its sparks keeper is running; one that came due while nothing was running fires as soon as Flash starts.
- when is an exact local time (get_date first, so "tomorrow at 9" becomes 2026-11-03T09:00) or "in 20m", "in 2h", "in 1d".
- Sparks have remind_me too.
- These are not Apple Reminders: if the user wants it in the Reminders app, on their phone, use reminders instead (see use-reminders).

Example:
remind_me {"action": "add", "text": "Call Sam", "when": "in 20m"}
remind_me {"action": "list"}
remind_me {"action": "cancel", "id": "3f9a1c2e"}
