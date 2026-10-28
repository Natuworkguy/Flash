# Extensions

An extension adds to Flash without changing Flash. It can bring:

- **Slash commands** that send a prompt to the model, or run a program.
- **Tools** the model can call, answered by a program.
- **System prompt text** sent on every turn.
- **Backgrounds** for `/background`.
- **Model providers**: models from somewhere other than Ollama, such as
  another local server or a hosted API.

## Installing

From the shell:

```bash
flash install github@owner/repo
flash install github@owner/repo#dev   # another branch
flash install git@https://gitlab.com/owner/repo.git
flash update <name>                    # pull it again; every one with no name
flash list
flash remove <name>
```

Add `-y` to answer yes to every question, for scripts and setup.

Or from inside a session:

```text
/extension install github@owner/repo
/extension update <name>
/extension                      # list what is installed
/extension remove <name>
```

`github@owner/repo#branch` installs from that branch instead of the
default one, and updating it later pulls the same branch.

`git@` takes any repository git can clone: `git@https://host/x.git`,
`git@ssh://host/x.git`, or `git@github.com:owner/repo.git` as a host
shows it to copy, each with `#branch` if you want one.

`path@/some/folder` installs from a folder on disk instead of GitHub,
which is how you try out an extension while you are writing it.

`path@/some/file.bundle` installs from a git bundle, the single file
`git bundle create` makes, which is handy for an extension passed
around without a repository to clone. Any path with `.bundle` in its
file name is taken as one, and Flash says so before you confirm. It
installs the branch the bundle's `HEAD` points at, or else `main`,
`master`, or the first branch it holds. To make one:

```bash
git bundle create my-extension.bundle main
```

To update, install a newer bundle the same way.

Before anything is installed, Flash shows what the extension adds and
asks you to confirm. Installed extensions live in
`~/.flash/extensions/<name>`. To update one, use `flash update <name>`
or its Update button on the Extensions page, or install it again. The
copy you have is never deleted first: the new one is put beside it and
swapped in, and if anything goes wrong on the way the old one stays. If an extension's commands or tools clash with a
built-in, or with another installed extension, the install is refused.

Extensions run programs on your machine as you. Only install extensions
you trust.

## Writing one

An extension is a folder, usually a GitHub repository, with a
`flash-extension.json` at the top:

```json
{
  "name": "weather",
  "description": "Weather lookups",
  "version": "0.1.0",
  "prompt": "prompt.md",
  "commands": [
    {
      "name": "forecast",
      "description": "ask for a forecast",
      "prompt": "commands/forecast.md"
    },
    {
      "name": "radar",
      "description": "open the radar map",
      "run": ["./scripts/radar.sh"]
    }
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "Current weather for a city.",
      "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      },
      "run": ["python", "./tools/weather.py"]
    }
  ],
  "backgrounds": "scenes",
  "themes": "themes.json",
  "loaders": [
    {"name": "rain", "label": "Rain", "interval": 0.12,
     "frames": ["╷  ", " ╷ ", "  ╷"], "ascii": [".  ", " . ", "  ."]}
  ]
}
```

Only `name` is required, plus at least one command, tool, prompt,
backgrounds folder, provider, theme, loader, events, voice, or ui. Every path is relative to the extension's folder
and cannot point outside it.

| Field | Meaning |
| --- | --- |
| `name` | Lowercase letters, digits, `-` and `_`. It is also the install folder's name. |
| `description`, `version` | Shown when installing and in `/extension`. |
| `prompt` | A text file added to the system prompt on every turn, up to 8000 characters. Keep it short: local models have small context windows. |
| `commands` | Slash commands. See below. |
| `tools` | Tools the model can call. See below. |
| `backgrounds` | A folder of `.scene` files, added to `/background`. |
| `providers` | Model providers. See below. |
| `themes` | Colour themes for the web UI. See below. |
| `loaders` | Loading animations for the terminal and the web UI. See below. |
| `events` | A program told when a turn starts, plans, waits on you, and ends. See below. |
| `voice` | A program in the middle of voice mode: told what is said both ways, and able to change it. See below. |
| `settings` | Choices the user makes for the extension, in its own settings panel. See below. |
| `ui` | What it changes of the web page and the terminal: its own stylesheet and script, panels, buttons, a status line, the greeting, the banner. See below. |

### Commands

Each command has a `name` (typed as `/name`), an optional
`description` for the completion menu and `/help`, and exactly one
of:

- `"prompt": "file.md"` sends the file's text to the model as if you
  typed it. `$ARGUMENTS` is replaced with whatever follows the command,
  so `/forecast Paris` fills in `Paris`. If the file has no
  `$ARGUMENTS`, the arguments are added on a line of their own at the
  end.
- `"run": [...]` runs a program, with the words typed after the command
  added as arguments. Its output streams to the terminal. The model sees
  it on your next message, the same as a `!` command.

### Tools

Each tool has a `name`, a `description` for the model, a JSON-schema
`parameters` object (by default, no parameters), and a `run` program.
The program gets the model's arguments as a JSON object on stdin.
Whatever it prints to stdout goes back to the model. A non-zero exit
reports the exit code and stderr instead.

Set `"confirm": true` on a tool that changes things. Flash then asks
before each call, unless autonomous mode (`/auto`) is on.

Extension tools are available to the main conversation only, not to
sub-agents. A tool named after a built-in tool is ignored.

### Running programs

`run` is a list of arguments, not a shell command line, so it works the
same on every platform and needs no quoting.

- An argument that starts with `./` refers to a file in the extension's
  folder.
- `python` or `python3` as the program means the Python that Flash runs
  on, so Python scripts work on Windows too. Only the standard library
  is available.
- Programs run in your current working directory, with
  `FLASH_EXTENSION_DIR` set to the extension's folder, and its
  [settings](#settings) in `FLASH_SETTINGS`.
- `timeout` sets a limit in seconds (default 30, maximum 600).

A tool in Python:

```python
import json
import sys

args = json.load(sys.stdin)
print(f"It is sunny in {args['city']}.")
```

### Settings

An extension can have settings of its own. List them in `settings`, and
the Extensions page gives it a Settings button that opens its panel,
each saved as it changes; in the terminal, `/extension settings <name>`
lists them and `/extension set <name> <setting> <value>` changes one.

```json
"settings": [
  {"id": "banners", "label": "Banners", "type": "switch",
   "default": true, "help": "A banner when Flash finishes."},
  {"id": "color", "label": "Colour", "type": "text", "default": "d97757"},
  {"id": "every", "label": "Check every", "type": "number",
   "default": 5, "min": 1, "max": 60},
  {"id": "size", "label": "Size", "type": "select",
   "options": ["small", "large"], "default": "small"},
  {"id": "api_key", "label": "API key", "secret": true,
   "help": "Overrides WEATHER_API_KEY."}
]
```

| Field | Meaning |
| --- | --- |
| `id` | Required: lowercase letters, digits and `_`, starting with a letter. |
| `label` | Its name in the panel. The id if left out. |
| `type` | `switch`, `text` (the default), `number` or `select`. |
| `default` | What it is until the user changes it: off, empty, 0, or a select's first option if left out. |
| `help` | A line under its name. |
| `options` | A select's choices. |
| `min`, `max` | A number's bounds. |
| `secret` | `true` for a text setting that is a secret, such as an API key. It shows as bullets in the panel, with a Clear button, and is never sent back to the page or printed in the terminal, only whether it is set. Its file can only be read by you. |

Any text setting can also be one of the secrets Flash keeps (`/secret`):
pick it from the "Use a secret" menu beside the box, or set it to
`{{secret:NAME}}`. The panel shows the secret's name on a chip, and the
extension's programs are handed the secret itself.

Every program the extension runs is handed them: all of them as a JSON
object in `FLASH_SETTINGS`, and each one in `FLASH_SETTING_<ID>` (the id
in capitals), `1` or `0` for a switch. They are kept in
`~/.flash/extension-settings`, so updating the extension keeps them;
removing it drops them. Up to 24 settings.

### Themes

A theme is a set of the web UI's colours for dark mode, light mode, or
both. It shows in Settings, under Colors, beside the built-in palettes,
with the extension's name on it:

```json
"themes": [
  {
    "name": "nord",
    "label": "Nord",
    "dark": {"bg": "#2e3440", "sidebar": "#272c36", "surface": "#3b4252",
             "text": "#eceff4", "accent": "#88c0d0"},
    "light": {"bg": "#eceff4", "surface": "#ffffff", "accent": "#5e81ac"}
  }
]
```

`name` is lowercase letters, digits and `-`. A mode may set any of
`bg`, `sidebar`, `surface`, `text`, `text-2`, `text-3`, `accent`,
`accent-text`, `border`, `border-strong`, `hover`, `pressed`, `code-bg`,
`add`, `del`, `warn`, `working`, `invert-bg` and `invert-text`, each to a
colour (`#hex`, `rgb()`, `rgba()`, `hsl()` or `hsla()`). Anything left
out keeps Flash's own. `"themes"` can also name a JSON file holding the
list.

### Loaders

A loader is the animation beside "Thinking" while Flash works. Each is
a list of text frames, shown one after another every `interval`
seconds (0.04 to 1), in the terminal and in the browser alike. Pick one
in Settings, under Loading animation, or with `/loader <name>`.

```json
"loaders": [
  {"name": "rain", "label": "Rain", "description": "Drops falling",
   "interval": 0.12, "frames": ["╷  ", " ╷ ", "  ╷"],
   "ascii": [".  ", " . ", "  ."]}
]
```

Up to 60 frames of up to 12 characters each. `ascii` is what a
terminal that can't show the frames' characters draws instead. Left
out, every non-ASCII character becomes `*`. A name a built-in loader
already has is skipped. `"loaders"` can also name a JSON file holding
the list.

### Events

An extension can follow the turn you are waiting on, to tell you about
it somewhere else: a phone, a watch, a chat. Flash runs its program once
for each milestone, with the event as JSON on stdin:

```json
"events": {"run": ["python", "./events.py"],
           "on": ["turn-start", "plan", "ask", "turn-end", "mention"],
           "timeout": 30}
```

`on` defaults to all of them; a name this Flash does not know yet is
left out, so an extension made for a newer Flash still installs. Every event carries `event`, `turn` (an id
shared by one turn's events), `source` (`terminal` or `web`), `chat`,
`title`, `request` (what was asked) and `at` (a Unix time). Then:

| Event | Also carries |
| --- | --- |
| `turn-start` | nothing more |
| `plan` | `steps` (each `{"text", "status"}`), `done`, `total`, and `change`: `set` for a new checklist, `done` with the step's `index` when one is ticked |
| `ask` | `question`: what the turn is waiting on your yes or no for |
| `turn-end` | `ok`, `summary` (the reply's first line), `error` (why not, if not ok) and `seconds` |
| `mention` | a spark wrote `@user`, in its report or in its team's chat, whenever that is: `spark` (its id), `name`, `handle`, `colour`, `where` (`report` or `team`), `team` and `text` (the words around it). It is no turn's, so it carries only `event` and `at` besides. |

Only the turn you are waiting on is followed: a sub-agent's or a spark's
work is not. Events go out one at a time, in order, on a thread of their
own, so a slow program never holds a turn up; one too slow to keep up
misses events rather than queueing them forever. A program run on
Flash's own Python (`"python"`) can `import flash` and use what Flash
has, such as `flash.mail` and the email account set up with `/email`.

### Voice

An extension can sit in the middle of voice mode in the web UI, from
"Hey Flash" (Settings, Voice) or the voice button: told of each moment
of the conversation, to show it somewhere else, and able to change what
is said both ways, or answer for Flash. Flash runs its program once for
each moment, with the moment as JSON on stdin:

```json
"voice": {"run": ["python", "./voice.py"],
          "on": ["wake", "start", "state", "words", "heard", "reply", "end"],
          "timeout": 5}
```

`on` defaults to all seven, and `timeout` to 5 seconds, 30 at most.
Every moment carries `event`, `conversation` (an id shared by one
conversation's moments), `source` and `at` (a Unix time). `source` is
`web` for voice mode in the page, and `server` for a conversation Flash
held on the microphone of the computer it runs on, after "Hey Flash"
with no page open.

Three are the extension's to decide. Flash waits for the program and
reads what it prints as a JSON object; printing nothing changes nothing:

| Moment | Carries | It may print |
| --- | --- | --- |
| `wake` | nothing more: "Hey Flash" was just heard | `{"ignore": true}` to keep voice mode off |
| `heard` | `text`: what was said | `{"text": ...}` to ask Flash that instead, `{"reply": ...}` to answer it, read aloud, without asking Flash, or `{"end": true}` to end voice mode |
| `reply` | `text`: what Flash is about to say, as it will be read out | `{"text": ...}` to read that out instead |

The other four it is only told of, in order, from a thread of their own,
so a slow program never holds the conversation up:

| Moment | Carries |
| --- | --- |
| `start` | `how`: `wake` or `button` |
| `state` | `state`: `listening`, `hearing`, `thinking`, `speaking`, `paused` or `asking` |
| `words` | `text`: what is being said, as it is said |
| `end` | nothing more |

With several extensions installed, each is asked in turn, seeing the
text as the one before left it; once one answers a `heard`, the rest
are not asked. A program that fails, or takes longer than its timeout,
changes nothing. Only conversations with Flash itself go through: voice
mode in a team's chat does not.

### Speech

An extension can add voices to speak with and models to listen with.
They show first in voice settings, named `@<extension>/<name>`, with
nothing to download.

```json
"speech": [
  {"name": "aura", "kind": "speaking", "label": "Aura, bright",
   "rate": 24000, "run": ["python", "./speak.py"]},
  {"name": "ears", "kind": "listening", "label": "Cloud listening",
   "run": ["python", "./hear.py"]}
]
```

A voice is given `{"text": "...", "pace": 1.0}` as JSON on stdin (pace
above 1 is slower) and writes the speech to stdout: a WAV file, or raw
16-bit mono audio at `rate`. A listening model is given 16 kHz mono
16-bit audio on stdin once the speaker stops, and writes what was said.
"Hey Flash" still listens with a Vosk model.

### Briefing

An extension can add cards to the briefing. Its program runs when the
briefing is read, and writes a JSON list:

```json
"briefing": {"run": ["python", "./brief.py"], "timeout": 10}
```

```json
[{"title": "Build passed", "body": "All 412 tests green.",
  "id": "build-881", "url": "https://ci.example.com/881"}]
```

Each card has a `title` and `body`, and maybe an `id` (so a dismissed
card stays dismissed), `at` (a Unix time), `url`, and `label` (the
extension's name if left out). A Python program can also add a card
whenever it likes, since it can import Flash:
`from flash import briefing; briefing.notice("extension", title, body)`.

### Providers

A provider brings models that Flash does not get from Ollama. Its
models are named `@provider/model`, such as `@example/big-model`, and are used
like any other: `/model @example/big-model`, the web UI's model menu, a
spark's model, or `MODEL` in `~/.flash.env`. They are listed beside
Ollama's, and are never downloaded.

```json
{
  "name": "example",
  "providers": [
    {
      "name": "example",
      "description": "A hosted model service",
      "run": ["python", "./provider.py"],
      "timeout": 300
    }
  ]
}
```

Each provider has a `name` (lowercase letters, digits, `-` and `_`,
unique among installed extensions), an optional `description`, a `run`
program, and a `timeout` in seconds for one answer (default 300).

Flash runs the program once per request, with a JSON request on stdin,
and reads JSON from stdout. There are two requests.

`{"action": "models"}` asks what it offers. Answer with its models, each
a name or an object; `capabilities` (Ollama's words: `completion`,
`tools`, `vision`, `thinking`) default to completion and tools, and
`context` is the window in tokens, if you want Flash to show it:

```json
{"models": [{"name": "big-model", "capabilities": ["completion", "tools", "vision"], "context": 400000}]}
```

`{"action": "chat", ...}` asks for an answer. The request carries
`model` (without the `@provider/`), `messages`, `tools`, `options`,
`think` and `stream`, all in Ollama's shapes, with images in base64.
Answer with the assistant's message, and the token counts if you have
them:

```json
{
  "message": {
    "role": "assistant",
    "content": "Here it is.",
    "thinking": "",
    "tool_calls": [{"function": {"name": "read", "arguments": {"path": "a.txt"}}}]
  },
  "prompt_eval_count": 1200,
  "eval_count": 85
}
```

Tool call arguments may also be a JSON string, as some APIs give them.
To show an answer as it is written, print it in pieces, one JSON object
a line, each with part of the message, and `"done": true` on the last.
To fail, print `{"error": "what went wrong"}` or exit non-zero with the
reason on stderr: Flash shows it the way it shows Ollama's errors.

A provider that needs an API key reads it from the environment. Set it
with `/set EXAMPLE_API_KEY ...`, which saves it to `~/.flash.env`, and
the program sees it on its next run. Provider models are never counted
as running on your own machine in `/stats`, since Flash cannot see where
a provider sends them.

A provider for an API that speaks the common chat-completions format, using only the
standard library:

```python
import json
import os
import sys
import urllib.request

BASE = "https://api.example.com/v1"
KEY = os.environ.get("EXAMPLE_API_KEY", "")


def call(path, body=None):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=280) as response:
        return json.load(response)


ask = json.load(sys.stdin)
if not KEY:
    print(json.dumps({"error": "set EXAMPLE_API_KEY with /set"}))
elif ask["action"] == "models":
    listed = call("/models")["data"]
    print(json.dumps({"models": [m["id"] for m in listed]}))
else:
    messages = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in ask["messages"]
    ]
    reply = call("/chat/completions", {
        "model": ask["model"],
        "messages": messages,
        "tools": ask.get("tools") or None,
    })
    message = reply["choices"][0]["message"]
    print(json.dumps({
        "message": {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": [
                {"function": {"name": c["function"]["name"],
                              "arguments": c["function"]["arguments"]}}
                for c in message.get("tool_calls") or []
            ],
        },
        "prompt_eval_count": reply["usage"]["prompt_tokens"],
        "eval_count": reply["usage"]["completion_tokens"],
    }))
```

This sketch sends text only. A full provider would also turn tool
results and images into the API's own format.

### UI

`ui` changes the web page and the terminal: what they say, buttons of
its own, whole pages of its own, and, on the web, a stylesheet and a
script with an API.

```json
"ui": {
  "web": "web",
  "css": "style.css",
  "script": "main.js",
  "panels": [
    {"id": "radar", "label": "Radar", "icon": "globe", "file": "radar.html"}
  ],
  "buttons": [
    {"id": "tldr", "label": "TL;DR", "icon": "bolt", "where": ["message"],
     "prompt": "Sum this up in one line: $TEXT"},
    {"id": "now", "label": "Weather now", "icon": "sun",
     "where": ["header", "composer", "terminal"], "key": "m-w",
     "run": ["python3", "./scripts/now.py"]},
    {"id": "forecast", "label": "Forecast", "where": ["sidebar"],
     "command": "forecast"}
  ],
  "status": {"run": ["python3", "./scripts/status.py"], "every": 30},
  "greeting": "What's the sky doing?",
  "suggestions": ["Forecast for Paris", "Will it rain today?"],
  "placeholder": "Ask about the weather",
  "banner": "banner.txt"
}
```

| Field | Meaning |
| --- | --- |
| `web` | The folder the page loads the extension's files from, served at `/ext/<name>/` to signed-in browsers only. Only `.css`, `.js`, `.html`, `.json`, images and fonts in it are served. |
| `css` | A stylesheet in `web`, added to the page: change any of it, hide what you don't want, bring your own fonts. |
| `script` | A script in `web`, run in the page with `window.Flash` (below). |
| `panels` | Up to 6 pages of the extension's own (`.html` in `web`), each a row in the sidebar that shows it in place of the chat. A panel reaches the same API as `parent.Flash`. |
| `buttons` | Up to 12. See below. |
| `status` | A program whose first line of output, up to 80 characters, shows under the message box on the web and in the terminal's status line, read again every `every` seconds (5 to 3600). |
| `greeting`, `suggestions` | A new chat's greeting, and up to 8 suggestions offered under the box (and in the terminal's empty box). |
| `placeholder` | What the empty box says, on the web and in the terminal. |
| `banner` | A text file (up to 20 lines) shown under Flash's welcome box in the terminal. |

**Buttons.** Each has an `id`, a `label`, an `icon` (one of the page's
own icon names, such as `bolt`, `sun`, `globe`, `cube`, `mail`; a
puzzle piece otherwise), and `where` it goes: `sidebar`, `header` (by
the chat's title), `composer` (the + menu), `message` (under each
reply), or `terminal` (a key in the terminal's box, given as `key`: `c-`
for Ctrl or `m-` for Alt, and a letter Flash doesn't use itself).

A button does one of three things:

- `prompt`: sends it as a message, with `$TEXT` replaced by the reply it
  was pressed on, or what is typed in the box. In the terminal, it fills
  the box for you to send.
- `command`: one of the extension's commands, given what is typed.
- `run`: a program, given `{"button", "text", "chat"}` as JSON on stdin.
  What it prints is shown. Printing JSON with `say` (shown), `insert`
  (put in the box) or `send` (sent as a message) does that instead.

**The page's API.** A `script`, and a panel as `parent.Flash`, gets:

```js
Flash.on("assistant", (event) => { /* every event the page hears; "*" for all */ });
Flash.send("Will it rain?");       // send a message in the open chat
Flash.insert("Draft: ");           // put text in the box
Flash.toast("Saved");
Flash.chat();                      // {id, title, log} of the open chat
Flash.addButton({id: "hi", label: "Say hi", icon: "smile",
                 where: ["composer", "header"],
                 onClick: ({text}) => Flash.insert("Hi! " + text)});
Flash.openPanel("/ext/weather/radar.html", "Radar");
Flash.request("pin", "on");        // any of the page's own commands
Flash.icon("sun");                 // an icon's SVG
```

A script and a panel run as part of the page, with everything it can
do, so the install preview says plainly when an extension has one.
