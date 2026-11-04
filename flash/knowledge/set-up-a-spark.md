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
- Chatting with a spark (its Chat button, the roster in the sidebar, or + in the box) opens a private DM with it: a sheet that slides out at the side like a team chat, tinted in the spark's colour. Only the user and that spark see it, the spark answers every message there, and it reads the latest of it on its shifts. Older main-screen chats with a spark are still in the chat list. The Chat button closes the spark's window first.
- Calls: the phone button at the top of a DM starts a voice call with the spark; in a team's chat it starts a group call with the team, each spark answering in a voice of its own. It is voice mode in the browser, not a phone call. The call screen shows the spark's name, colour and avatar, says "Calling…" and rings until the first answer, then "Connected" with a timer. The spark picks up by itself and speaks first. Its buttons are mute, hold, hang up, share the screen and the camera; what the camera or the shared screen shows goes to the spark with each thing said, so it can see it. A live caption shows who is saying what. Talking over the spark cuts it off and is what it hears next; "hold on" is said to the spark (the Hold button holds the call). The minimise button turns the call into a small bar at the top, and closing the chat keeps the call going, so the user can carry on using Flash. The spark hangs up with its end_call tool when the conversation is over, after its goodbye. While the spark thinks, the call plays one short filler ("Hmm…").
- A call keeps itself alive while its page is open. Closed without hanging up (a closed tab, a lost device), it ends by itself after five quiet minutes, and closing the tab hangs it up straight away; starting a new call ends any left open.
- In the chat a call is one entry, like Discord: "You started a call that lasted 42 seconds", "· no answer", "didn't connect", with a Call Log button (who said what, read from the chat, every line) once it was talked on. What is said on a call never shows as messages and never counts as unread, and it is kept apart from the chat, so a long call never pushes the chat's own messages out. After a call that was really talked on, the spark posts a short recap of it in the chat. An answer that fails on a call is said as "Sorry, something went wrong on my side", the error kept for the Call Log.
- Sparks can call the user: on a shift a spark has call_user, for what cannot wait or is easier talked through, at most once in 30 minutes. It rings for 45 seconds wherever Flash is open: every web page shows "is calling you" with the reason, Answer and Decline, and rings; with no page open, the computer rings through its speakers with a prompt to answer (a dialog on macOS, a notification with buttons on Linux and Windows) whose Answer opens Flash on the call; the terminal rings its bell and shows the call, picked up with Enter or /answer, or turned down with /decline. With no Flash open at all, the keeper rings the computer the same way. Answered, the spark says first why it called. Unanswered, the DM shows "Missed call from …" with the reason and a Call back button, and counts as unread; declined shows the same with "You declined". In the terminal, /call <spark> calls a spark, by voice when voice is set up and typed when not ("hang up" or /hangup ends it).
- On the Apple Watch app (the watch extension), a spark's page has a Call button and a Private chat row (its DM). Call opens a full-screen call like the web one: the spark's initial on its colour, "Calling…" with taps on the wrist until the first answer, then "Connected" with a timer; buttons are mute and hang up only (a watch has no camera). The call starts and ends with team-chat-call, so the DM gets the same single "You started a call that lasted N minutes" entry; the same hidden hello opens it; the watch records each thing the user says, Flash's own listening model on the computer turns it into words (/api/voice/hear), and it is sent with the call's id, so it stays out of the DM. The spark's answers are read out in Flash's voice (or the watch's), and its end_call tool hangs up after its goodbye. Muting stops the watch listening. Saying an exit phrase like "goodbye" also hangs up there.
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
