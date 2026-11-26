---
name: use-reminders
description: List, add or tick off the user's Apple Reminders on their Mac
---
- reminders list shows what is not done yet, in every list or the one named in list; done: true shows done ones too. Each shows its id in [brackets].
- reminders add with title, and due (ISO 8601, local), notes and list if given; left out, it goes to the default list.
- reminders complete ticks one off by the id list gave.
- reminders lists names the lists.
- Adding and completing ask the user yes or no every time, even in autonomous mode; only UltraAuto skips it. A no means do not try again.
- Only on a Mac, through AppleScript: macOS asks once to let Flash control Reminders (System Settings > Privacy & Security > Automation). If it says no, tell the user where to turn it on.
- get_date first, so "tomorrow at 9" becomes an exact time. Events with a start and end belong in the calendar instead (see manage-the-calendar).

Example:
reminders {"action": "list"}
reminders {"action": "add", "title": "Water the plants", "due": "2026-11-03T18:00", "list": "Home"}
reminders {"action": "complete", "id": "x-apple-reminder://1A2B3C"}
