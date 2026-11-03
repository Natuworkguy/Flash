---
name: help-with-flash
description: Answer questions about Flash itself: commands, settings, features, setup
---
Point the user to the right place, briefly:
- Models: /model to pick, /fallbacks to set models to try when one fails, /compare a vs b: question for a blind test, /host to switch Ollama server.
- How Flash works: /auto on|off|ultra (ask first, autonomous, or UltraAuto), /think auto on|off, /instructions for custom instructions, /systemone for a model that reviews autonomous commands.
- Memory and learning: /memory, /forget N, /skills, /good and /bad on the last reply.
- Conversations: /resume, /fork, /forks, /compact, /context, /clear, /undo (take back the last turn's file changes), /btw (a side question while it works).
- Apps: /mcp (connectors), /extension, /email, /calendar, /computer, /voice, /secret (keys Flash uses by name).
- Ongoing work: /sparks, /agents, /plan, /notify.
- Web page: /web (lan for a phone on the same network, tunnel for anywhere, port N).
- Health: /doctor (and /doctor fix), /version, /update, /stats.
- Settings are saved in ~/.flash.env with /set NAME VALUE; the web page has Settings for most of them.
- Dashes and emojis: Settings has "Remove em dashes" (on by default, REMOVE_EM_DASHES) and "Remove emojis" (off by default, REMOVE_EMOJIS). On, Flash strips them from everything it writes, replies and every tool call alike; code is kept as written.
- Calls: Settings, under Calls, has "Call greeting" (CALL_GREETING_MODE): the standard greeting (default, "Hi, this is an automated assistant calling on behalf of" the user's first name), the user's own (CALL_GREETING, where {name} is their first name), or "Let Flash write its own".
- Extension panels that talk: a panel page can reach Flash's model through a "run" button with "where": ["terminal"] (hidden on the web page), called from the page as parent.Flash.request("ext-button", id, {extension, text}); the program can import flash (it is on PYTHONPATH) and print the reply. Stickbuddy (~/Desktop/Projects/flash-stickbuddy, install with /extension install path@that folder) does this: a stickman panel you hover, poke, tickle and drag, with lines from the model and canned ones when it is slow.
- Printing: on a Mac, Flash can list printers and print a file or a made document (list_printers, print), asking the user yes or no every time (see print-a-file).
- Apple apps on a Mac: Apple Calendar is a calendar choice beside Google and iCal feeds (Settings, Connectors, Calendar; see manage-the-calendar), the reminders tool lists, adds and ticks off Apple Reminders (see use-reminders), and send_imessage can send a file while read_imessages names attachments (see send-messages). Adding or changing anything asks the user first.
- Location: with "Let Flash see my location" on in the Apple Watch app's Settings (off by default), the watch tells Flash where the user is and the user_location tool gives it (see know-where-the-user-is). Off, nothing is collected or sent.

If something is broken, suggest /doctor first.

## Examples

Most answers here are text. To check something on the user's setup:
```
shell {"command": "flash --version"}
```
