---
name: build-an-extension
description: Give Flash a new ability by writing a Flash extension, installing it and using it
---
When the user wants Flash to do something it has no tool or page for, and no extension or MCP server already does it (see connect-an-app), write one:

1. Look first: an extension on GitHub may exist (search the web for "flash-extension.json" and the task). Install a found one with request_extension {"source": "github@owner/repo"}.
2. Otherwise make a folder ~/.flash/extension-src/<name> (name: lowercase letters, digits, - and _). Put flash-extension.json at its top, and every file it names inside the folder.
3. Pick what it adds:
   - tools: a program the model calls, given its arguments as JSON on stdin; what it prints goes back. Python 3 standard library only ("run": ["python3", "./tool.py"]). Set "confirm": true on one that changes things.
   - commands: /name sending a prompt file, or running a program.
   - prompt: a short text file added to every turn.
   - ui (the web page), with "web": "web" as the folder its files live in:
     - panels: a page in the sidebar shown in place of the chat.
     - docks: a page beside the chat, opened from its button at the top.
     - widgets: cards on a new chat's empty page.
     - blocks: a ```lang fence in a reply drawn by its page, which reads JSON.parse(window.name).text; "about" tells the model when to write one.
     - buttons: "where" any of sidebar, header, composer, message, input, selection (text selected in a reply), palette, terminal; each does "prompt" ($TEXT is the text), "command" or "run"; "shortcut" like "alt+shift+w".
     - script: a .js file run in the page with window.Flash (send, insert, toast, notify, dialog, storage, registerBlock, addWidget, addCommand, addButton, openDock, setBadge, theme, on).
     - status, greeting, suggestions, placeholder.
4. Install it: request_extension {"source": "path@/home/<user>/.flash/extension-src/<name>", "why": "..."}. The install preview says what it adds and the user confirms, unless UltraAuto is on. A mistake in the manifest is refused with what is wrong: fix it and install again.
5. refresh_tools, then use the new tools. A page change shows once the user reloads the page.
6. To change it later, edit the folder and install the same path again: it replaces the installed copy.

Pitfalls:
- Paths are relative to the folder and cannot leave it. A prompt is a file name, not the text.
- A tool, command or block language another extension or Flash already has refuses the install.
- Keep page files small and self-contained: no CDN, the page may be offline.

## Examples

A tool that counts words.
```
write {"path": "~/.flash/extension-src/wordcount/flash-extension.json", "content": "{\"name\": \"wordcount\", \"description\": \"Counts words\", \"tools\": [{\"name\": \"count_words\", \"description\": \"How many words a text has.\", \"parameters\": {\"type\": \"object\", \"properties\": {\"text\": {\"type\": \"string\"}}, \"required\": [\"text\"]}, \"run\": [\"python3\", \"./count.py\"]}]}"}
```

```
write {"path": "~/.flash/extension-src/wordcount/count.py", "content": "import json, sys\nprint(len(json.load(sys.stdin)['text'].split()))\n"}
```

```
request_extension {"source": "path@~/.flash/extension-src/wordcount", "why": "So I can count words exactly."}
```

A block the replies can draw: "blocks": [{"lang": "habit", "file": "habit.html", "about": "a habit streak card: JSON {name, days}"}] in "ui", with "web": "web" and web/habit.html reading JSON.parse(window.name).text.
