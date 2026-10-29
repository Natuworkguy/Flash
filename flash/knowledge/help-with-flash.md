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

If something is broken, suggest /doctor first.

## Examples

Most answers here are text. To check something on the user's setup:
```
shell {"command": "flash --version"}
```
