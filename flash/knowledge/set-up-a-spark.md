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
- Chatting with a spark (its Chat button, the roster in the sidebar, or + in the box) opens a private DM with it: a sheet that slides out at the side like a team chat, tinted in the spark's colour. Only the user and that spark see it, the spark answers every message there, and it reads the latest of the DM on its shifts. Older main-screen chats with a spark are still in the chat list. The Chat button closes the spark's window first. The mic button at the top of a DM starts a voice call with the spark: voice mode in the browser, what the user says sent to the DM and the spark's answers read aloud (not a phone call). The call has its own screen with the spark's name, colour and avatar: it says "Calling…" and rings until the spark's first answer arrives, then "Connected" with a timer. The spark picks up by itself: the call opens by sending it a short hello (shown in the DM as the user's message), so it answers before the user speaks. Only the spark's words are read out, without "<name> says". Voice mode runs inside the call screen; the DM's message box stays as it was. Its buttons mute the microphone and hang up. Saying "bye" does not hang up by itself: on the call the spark has an end_call tool, and it ends the call when the conversation is over (for example the user says bye), once its goodbye has been read out. Every hang-up plays a short falling chime. A spark gets two minutes to start answering before the call says "No one answered." The hello that starts a call goes to the spark but is not shown in its chat; only the spark's greeting is. The call screen shows no voice-mode orb, status text (Listening, Speaking…) or voice-mode close button: voice mode runs unseen underneath, and only the camera preview (when the camera is on) appears. The call's buttons are, left to right, mute, hang up and camera; the camera button looks like mute and turns the camera on and off as in voice mode. What is said on a call never shows in the DM itself, and does not count as unread: like Discord, the DM shows one call entry, "Started a call" (with "· ongoing" while it is live) and, once hung up, "Started a call that lasted N minutes", with a Call Log button that opens the call's transcript, who said what, in order. The call's messages are still kept (marked with the call's id), so the spark reads them as context, marked "[on the call]". While the user waits for a spark's answer on a 1-to-1 call, or for its speech to be made, the call plays one short filler ("Hmm…", "Let me think…", "One sec…" or "Okay…"), made once when the call starts. It plays at most once per answer, never the same one twice in a row, not during the greeting or while muted, and stops as soon as the answer is ready to be read out.
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
