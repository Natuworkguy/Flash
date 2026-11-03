---
name: set-up-a-spark
description: Set up ongoing or recurring work: watch something, check every morning, keep an eye on
---
Sparks are agents that keep working on a standing goal on a schedule while Flash runs, and report back.

- make_spark when the user asks for something ongoing: name, goal (what to watch and what counts as news), every (how often, like 1h or every morning), boundaries (what it must not do), events (start when an email, an iMessage, an incoming call ("call", or "call:<name>" for one person) or a connected app changes, instead of or as well as the schedule). The user is asked first.
- ask_spark asks one what it has found, without running it.
- give_spark gives one a job now, in the background.
- Refresh tools, at the top of the Sparks page, reads every spark's tools again at once: Flash's own tools and extensions, and the MCP servers (restarting any that failed). Skills are read fresh each time anyway. Use it after installing an extension, adding an app or saving a skill, when a spark says a tool is missing.
- Deleting a spark (its Delete button, /sparks remove, or an approved removal) moves it to the trash, at the bottom of the Sparks page. From there the user can restore it (it comes back paused, without a lead or team that is gone) or delete it forever.
- A spark on a team sees its team chat's latest messages, numbered, on each shift, and can point at one in its report by number, like #12. On the Sparks page that number shows as the team chat's quote; clicking it opens the team's chat at that message.
- Every spark sees whether the user is at the computer, in its status: online (active), idle (no mouse or keyboard input for 2 minutes), offline (the computer is locked), or unknown where the system does not say (read via ioreg on macOS, user32 on Windows, loginctl/xprintidle/D-Bus on Linux).

Write the goal so a fresh agent can act on it alone: what to check, where, what to report, and when to stay quiet. Never make a spark for something you can do right now.

## Examples

```
make_spark {"name": "Price Watch", "goal": "Check the price of the Sony WH-1000XM6 at the three big UK retailers. Report only if any is under 250 GBP, with the link.", "every": "9am daily", "boundaries": "Only read web pages; never buy or sign in."}
```

Started by events instead of a schedule.
```
make_spark {"name": "Inbox Triage", "goal": "When an email comes in, decide if it needs a reply today; if so, notify the user with a one-line summary.", "every": "on call", "events": "email"}
```

```
ask_spark {"spark": "Price Watch", "question": "What is the lowest price you have seen this week?"}
```

```
give_spark {"spark": "Price Watch", "job": "Check now, including Amazon."}
```
