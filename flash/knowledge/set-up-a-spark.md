---
name: set-up-a-spark
description: Set up ongoing or recurring work: watch something, check every morning, keep an eye on
---
Sparks are agents that keep working on a standing goal on a schedule while Flash runs, and report back.

- make_spark when the user asks for something ongoing: name, goal (what to watch and what counts as news), every (how often, like 1h or every morning), boundaries (what it must not do), events (start when an email, an iMessage or a connected app changes, instead of or as well as the schedule). The user is asked first.
- ask_spark asks one what it has found, without running it.
- give_spark gives one a job now, in the background.

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
