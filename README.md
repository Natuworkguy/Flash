# FLASH CLI

FLASH (**F**ast **L**ocal **A**gent **SH**ell) CLI is an AI-powered command-line interface that allows you to interact with local (or remote) [Ollama](https://ollama.com) models while having the ability to execute shell commands directly or through the AI.

[Watch the original video on YouTube](https://www.youtube.com/watch?v=padyQR3tPUs)

## Features

- **Interactive AI Chat**: Chat with local or self-hosted models served by Ollama, directly from your terminal.
- **Switchable Backend**: Point Flash at `localhost` or any remote Ollama server via a single config option.
- **Precise File Edits**: The AI changes a file by naming the exact lines that change, not by retyping the file. A one-line fix in a thousand-line file costs one line of output, so big files stop being out of reach and long writes stop truncating half way. Several changes to the same file go in one call that either lands whole or not at all.
- **Undo**: `/undo` puts back every file the last turn changed, including deleting the ones it created. Snapshots are taken the moment you approve an edit, so a yes you regret costs you one command instead of your afternoon.
- **Context That Lasts**: History is measured against the model's real context window instead of a fixed message count, and dropped in whole exchanges so a tool result is never left without the call that produced it. When it overflows, the model summarizes what is falling off and keeps the summary, so a long session keeps its thread. `/context` shows the usage, `/compact` summarizes on demand.
- **Shell Command Execution**:
  - AI can use a `shell` tool to execute commands and see their output.
  - Manually execute shell commands using the `!` prefix.
- **`flash://` Links**: Open Flash from a browser or another app with a prompt ready to go (`flash://?prompt=What+is+Python`).
- **Image Recognition**: Send a local image to a vision-capable model with `/image <path> [prompt]`, or let the AI open one itself with its `view_image` tool.
- **Page Screenshots**: The AI renders a page it built in a headless browser with its `screenshot` tool and looks at the result, so it can see a broken layout instead of guessing from the HTML.
- **Page Control**: The AI opens a page with `open_page` and then clicks buttons, fills forms, presses keys, and runs JavaScript on it with `interact`, seeing a fresh screenshot, the page's elements, and its console errors after every step, so it can debug what a page *does*, not just how it looks.
- **Voice Mode**: `/voice on` downloads a Vosk speech model and a Piper voice, then lets you talk to Flash and hear its replies, with typing still available at any time.
- **Visible Plans**: For a multi-step task the AI posts a checklist up front and ticks each box as it finishes that step, so you can see where it is instead of waiting for the wall of text at the end.
- **Knows What Just Broke**: With `/hook install`, Flash sees the commands you run in VS Code's terminal and whether they failed, so "why did that fail?" works without pasting anything.
- **VS Code Aware**: Run from VS Code's terminal, Flash opens its edits as side-by-side diffs while it waits for your yes, clears them away once you have answered, and opens files at the line it's talking about.
- **Async Sub-agents**: The AI can spawn background sub-agents with the `agent` tool to work on independent pieces of a task at the same time, then collect each one's answer with `agent_result` once it's needed.
- **Context Management**: Automatic history trimming to stay within token limits.
- **Markdown Support**: Rich formatting for AI responses in the terminal.

## Installation

### Quick install (pipx)

Install Flash with a single command. The script clones this repo into a temporary directory, installs it with [pipx](https://pipx.pypa.io/), and cleans up after itself:

```bash
curl -fsSL https://raw.githubusercontent.com/Natuworkguy/Flash/main/install.sh | bash
```

Once installed, run it with:

```bash
flash
```

To uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/Natuworkguy/Flash/main/install.sh | bash -s -- --uninstall
```

Or, if you already have the repo cloned locally:

```bash
./install.sh --uninstall
```

### Manual install

1. **Clone the repository**:

   ```bash
   git clone https://github.com/Natuworkguy/Flash
   cd Flash
   ```

2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

3. **Install and start Ollama**:

   Flash talks to an [Ollama](https://ollama.com) server. Install Ollama, start it, and pull a model that supports tool calling:

   ```bash
   ollama pull llama3.1
   ```

   By default, Flash connects to a local server at `http://localhost:11434`. To use a remote server, set `OLLAMA_HOST` (see [Configuration](#configuration)).

### Flash Onyx (recommended model)

**Flash Onyx** is a series of custom Ollama models built for Flash: a base
model with Flash's persona and tuned parameters baked in. Each one lives in a
single Modelfile under `models/` that declares its name and sizes at the top,
and `models/build.py` builds whatever a Modelfile declares.

The current release, **Flash Onyx 2.5**, is `gemma4` in two sizes. `12b` runs on
consumer hardware; `31b` is the flagship and wants a bigger GPU.

```bash
python3 models/build.py models/flash-onyx-2.5.Modelfile             # every size
python3 models/build.py models/flash-onyx-2.5.Modelfile --size 31b  # just one
```

**Flash Onyx 2.4** is the previous release, also built on `gemma4`:

```bash
python3 models/build.py models/flash-onyx-2.4.Modelfile
```

Then set `MODEL` to whichever you built (`flash-onyx-2.5:31b`,
`flash-onyx-2.4:12b`, and so on) in `~/.flash.env` or your environment.

### Run

```bash
python3 run.py
```

## Configuration

FLASH CLI is configured through environment variables. You can create a `.flash.env` file in your home directory:

```env
MODEL=llama3.1
OLLAMA_HOST=http://localhost:11434
```

### Environment Variables

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Switching servers

- **Local (default):** leave `OLLAMA_HOST` unset, or set it to `http://localhost:11434`.
- **Remote server:** set `OLLAMA_HOST` to the other machine, e.g. `OLLAMA_HOST=http://192.168.1.50:11434` or `OLLAMA_HOST=https://ollama.example.com`.

Make sure the target server is reachable and that `MODEL` has been pulled on it.

## Usage

Start the CLI by running:

```bash
python run.py
```

### Internal Commands

- `/help` or `/?`: Display the help message.
- `/model`: Pick from the models on this machine, or type a name to
  download one. `/model <name>` switches straight to one.
- `/plan`: Show the checklist the model is working through.
- `/hook [install|remove]`: Let Flash see the commands you run in VS
  Code's terminal (zsh and bash).
- `/agents`: Watch sub-agents work live. `/agents <id>` shows one in full,
  with its answer once it is done.
- `/clear`: Clear the conversation history.
- `/undo`: Take back the file changes from the last turn.
- `/compact`: Summarize the conversation to free up room.
- `/context`: Show how much of the context window is in use.
- `/image <path> [prompt]`: Send a local image to the model.
- `/extension [install <source>|remove <name>]`: List, install, or
  remove extensions.
- `/version`: Show the current version and check GitHub for updates.
- `/update`: Update Flash to the latest version (requires pipx).
- `/bye`: Exit the application.

### Keys

- `Enter` sends. `Alt+Enter`, or `\` at the end of a line followed by
  `Enter`, starts a new line instead.
- `Up` / `Down` step through earlier messages, saved across sessions in
  `~/.flash/history`. `/set` lines are never saved, since that is how
  API keys get typed in. `Ctrl+R` searches them.
- `Shift+Tab` toggles autonomous mode, the same as `/auto`.
- `Ctrl+O` prints in full any tool output that was cut short this turn.
  Output longer than 12 lines shows its first 5 lines, or its last 5 if
  the command failed, since the error is usually at the end. The model
  always sees all of it.
- `Ctrl+C` stops the model mid-answer.

Type `@` anywhere in a message to pick a file out of a dropdown, e.g.
`why does @flash/theme.py fall back to ASCII?`. Arrow keys and Tab pick
one, `/` walks into a directory, and the model reads whatever you point
it at. Dot-entries stay hidden until you type the leading dot.

### Plans

When a request takes several steps, the model posts a checklist before it
starts and ticks each box as that step lands:

```console
> add latex rendering to the response renderer

⏺ Plan(2/4 done)
  ⎿  ☒ Read the response renderer
     ☒ Add the LaTeX module
     ☐ Wire it into the render path
     ☐ Add tests
```

Each tick redraws the list in place of the previous one, so the terminal
shows the run as it happens. `/plan` reprints the current checklist at any
time, and `/clear` drops it along with the conversation. Short tasks skip
the plan entirely.

### VS Code

Run Flash in VS Code's integrated terminal and it works with the editor
around it, through VS Code's own `code` command:

- When Flash wants to change a file and waits for your yes, the change
  opens as a side-by-side diff in the editor, so you can review it there.
  Once you have answered, Flash drops the files behind that diff, so a
  decided change stops sitting in the editor looking like it is still
  waiting for you. Whether the tab itself closes is VS Code's call: set
  `workbench.editor.closeOnFileDelete` to `true` and it closes with
  them. Flash says so once per session if it is off.
- The model can open a file at the line it is talking about.

`/hook install` goes one step further: it adds three lines to your
`~/.zshrc` or `~/.bashrc` (after asking) that load a small hook in VS
Code's terminal only. From then on, the commands you run there travel
with your next message:

```console
❯ why did that fail?
```

```text
=== Commands the user ran in VS Code's terminal since their last message ===
✓ · 3s · ~/proj · npm install
✗ exit 1 · 12s · ~/proj · npm test
```

A shell hook sees each command and its exit code, never its output, so
when you ask about a failure Flash re-runs the command to read the error
if it is safe to repeat (a build, test, or lint; it still asks first
unless autonomous mode is on), and asks you to paste it otherwise.
Commands you start with a space are not recorded, anything that looks
like a secret (`TOKEN=…`, `--password …`, credentials in URLs) is
redacted before the model sees it, and the log in
`~/.flash/terminal.log` is readable only by you and keeps the last 500
commands. `/hook remove` takes the lines back out.

### Sub-agents

For work that splits into independent pieces, the model can start
sub-agents with its `agent` tool. Each one runs on a background thread
against the same model, and the model usually just ends its turn: when a
sub-agent finishes, Flash wakes the model with the answer so it can
report back, without you typing anything.

```console
⏺ Sub-agent 28a965 finished
LK-99 did not hold up: the replications traced its resistance drop to
copper sulfide impurities, ...
```

Flash only wakes it while the prompt is empty, so a half-typed message is
never taken from you; the answer rides along with what you send instead.
Wakes stop after three in a row without you writing, so a chain of
sub-agents cannot run on its own forever.

When the model needs an answer before it can go on, it calls
`agent_result`, which draws the sub-agent's progress live while it waits:

```console
⏺ AgentResult(4b86ea)
  ⎿  ⠹ Running Read(README.md) · round 4 · 21s
     ✓ Reason  Primer is GitHub's design system
     ✓ Glob(*.md) in docs  1 match
     ✓ Grep(Features) in README.md  1 match in 1 file
     … Read(README.md) lines 1-20
```

Sub-agents keep running after a reply, and `/agents` watches all of them
update in place (Ctrl+C goes back to the prompt). They cannot talk to you,
so they get no tool that asks first: `shell` and `write` are only theirs
in autonomous mode (`/auto on`).

### Image Recognition

`/image <path> [prompt]` attaches a local image (`.png`, `.jpg`, `.jpeg`,
`.webp`, `.gif`, `.bmp`) to your next message and sends both to the model.
If you leave off the prompt, Flash asks it to describe the image. This
requires a vision-capable model  text-only models will ignore the image
or error. Pull one and switch to it first, e.g.:

```bash
ollama pull llama3.2-vision
```

Flash Onyx 2+ is vision capable.

```console
/model llama3.2-vision
/image ~/Pictures/screenshot.png What's going on in this UI?
```

The model can also open an image on its own with the `view_image` tool, so
you can just name the file in a normal message and let it look:

```prompt
Why does the legend in ~/Desktop/plot.png overlap the bars?
```

It accepts the same file types (up to 20 MB) and sees the image for that
turn only, calling `view_image` again later if it needs another look.

### Voice mode

`/voice on` turns Flash into something you can talk to. The first time it
runs it downloads the two models it needs into `~/.flash/models`: a Vosk
speech-recognition model for listening (about 40 MB) and a Piper voice for
speaking (about 60 MB). After that everything runs locally, with no audio
leaving the machine.

```FLASH
/voice on
```

With voice mode on, press Enter on an empty prompt to start talking. Flash
records until you stop, prints what it heard, and sends it as your message;
the reply is printed as usual and read aloud. It then listens again on its
own, so a conversation carries on hands-free with no keypress between
turns. Say nothing for eight seconds (`VOICE_NO_SPEECH_SECONDS`), or press
Ctrl+C, and it hands the prompt back. Typing works exactly as before, so
slash commands and long paths can still be typed rather than dictated.

Say **"interrupt"** while Flash is talking and it stops mid-sentence and
listens for what you say next, so you never have to sit through an answer
that started off wrong. "stop talking" and "be quiet" work too, as does
Ctrl+C, and the word can be changed with `VOICE_INTERRUPT_WORD`. It is
matched as a whole word, so "the interrupted process" is just a message.

Say **"voice off"** (or "stop listening", "exit voice mode") to end the
conversation. That hands the prompt back but leaves voice mode armed, so
pressing Enter starts talking again without re-enabling anything. To turn
the feature off altogether, type `/voice off`; the setting is saved in
`~/.flash.env` as `VOICE`, so voice mode survives a restart either way.

Only the prose of a reply is spoken. Code blocks, tables, and URLs are
skipped, because they are on screen already and unpleasant to listen to,
and a long answer is cut at a sentence once it passes `VOICE_MAX_CHARS`.
Flash also tells the model that it is being heard rather than read, so
replies in voice mode come back shorter and plainer.

Voice mode needs three extra packages: `vosk`, `piper-tts`, and
`sounddevice`. `install.sh` and `install.ps1` install them for you, so
this is only needed if you installed Flash some other way. Flash installed
with pipx keeps its own environment, so the packages go in with `inject`:

```bash
pipx inject flash vosk piper-tts sounddevice
```

For a plain pip install of Flash it is the extra instead:

```bash
pip install "flash[voice]"
```

`/voice on` prints whichever of the two commands fits your install. On
Linux
`sounddevice` also needs PortAudio from the system (`apt install
libportaudio2`); the macOS and Windows wheels bundle it. The voice and the
listening model can be swapped with `VOICE_PIPER_VOICE` and
`VOICE_VOSK_MODEL`, and the microphone's sensitivity tuned with
`VOICE_SILENCE_THRESHOLD`; see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Page screenshots

The `screenshot` tool renders a local `.html` file or a URL in a headless
Chromium and attaches the picture, so a vision-capable model can check
what it built rather than trusting its own source:

```prompt
Build me a pricing page in ~/Desktop/pricing.html, then check how it
looks on a phone.
```

It takes a viewport `width` and `height`, captures the whole scrollable
page with `full_page`, and reports any JavaScript errors the page threw
while rendering, which is usually what explains a section that came out
empty.

### Clicking through a page

A screenshot is a still picture, so for a page with buttons or a form the
AI opens it with `open_page` and then drives it with `interact`, one
action per call:

```prompt
Open ~/Desktop/signup.html, fill in the form, submit it, and tell me why
the confirmation never shows up.
```

The browser stays open between calls, so the page keeps its state while
the AI works through a flow. `interact` takes an `action` (`click`,
`fill`, `press`, `hover`, `select`, `scroll`, `wait`, `eval`, `back`,
`reload`, `close`) and a `selector`, which can be the number Flash prints
beside each element, a CSS selector, or the text on the element itself.
Every call answers with where the page is now, what can be clicked or
typed into next, and the JavaScript errors the page threw, with a
screenshot attached. The `eval` action runs JavaScript against the live
page and returns the result, which is how the AI inspects state a picture
cannot show.

Both tools need Playwright's Chromium, which `install.sh` and
`install.ps1` download for you. Installing Flash another way means
running it yourself:

```bash
playwright install chromium
```

### Updates

Flash checks `main` on GitHub for a newer version on startup and shows it
in the banner if one is available. Run `/version` anytime to check on
demand, or `/update` to install it. Flash re-runs the same pipx-based
steps `install.sh` uses, so it needs pipx on PATH. If you cloned the repo
manually, update with `git pull` instead.

You can also check and update from outside the REPL:

```bash
flash --update          # check for a newer version and, if found, confirm and install it
flash --update --force  # reinstall from `main` unconditionally, no confirmation
```

On Windows the install cannot run while Flash is open, because Windows
holds a lock on every running program and pipx has to replace two of
them: `flash.exe` and the Python it starts. Flash downloads the update,
then hands the install to a PowerShell window that waits for Flash to
close and finishes there. Quit Flash and the update completes on its
own.

### Extensions

Extensions add slash commands, tools the model can call, system prompt
text, and backgrounds. Install one from GitHub:

```bash
flash --extension-install github@username/my-ext
```

or with `/extension install github@username/my-ext` in a session. Flash
shows what the extension adds and asks before installing it.
`flash --extension-list` and `flash --extension-remove <name>` do the
rest. See [docs/EXTENSIONS.md](docs/EXTENSIONS.md) to write your own.

### Direct Shell Execution

You can run shell commands directly without AI intervention:

- `!ls -la`
- `!git status`
- `!echo "Hello"`

### `flash://` Links

Flash can open from a link. `install.sh` and `install.ps1` register the handler
for you; after a manual install, register it once yourself:

```bash
flash --register-url-scheme
```

Then a link like `flash://?prompt=What+is+Python` starts a Flash session with
that prompt queued. Pass the same URL on the command line to test it without a
browser:

```bash
flash "flash://?prompt=What+is+Python"
```

The prompt is URL-encoded, so use `+` or `%20` for spaces. Flash always shows
the prompt and asks before sending it to the model. Any web page can open a
`flash://` link, so nothing runs unattended. For the same reason, URL prompts
may not start with `/` or `!`: they carry questions for the model, never Flash
commands or shell escapes.

To remove the handler (the uninstallers do this too):

```bash
flash --unregister-url-scheme
```

Registration is per-user: it writes `HKCU\Software\Classes\flash` on Windows and
`~/.local/share/applications/flash-url.desktop` on Linux/BSD. It cannot be
installed on macOS, which resolves URL schemes from application bundles only.
Passing a `flash://` URL on the command line still works everywhere.

### AI Interaction

Simply type your request. If the AI needs to see the contents of a file or run a command to answer your question, it can invoke the shell tool automatically. It can also look at an image file with the `view_image` tool, search the web via Duck Duck Go, and show it's reasoning.

## License

The Flash CLI, the Modelfiles under `models/`, and the system prompts in them
are MIT licensed. See [LICENSE](LICENSE).

A published Flash Onyx model is a derivative of the base model it is built on,
and that base license travels with it. The MIT license above covers the
Modelfile and the prompt, not the weights underneath:

- **Flash Onyx 2.x** is built on `gemma4`, which Ollama ships under the Apache
  License 2.0.
- **Flash Onyx 1** is built on `llama3.1`, which ships under the
  [Llama 3.1 Community License](https://www.llama.com/llama3_1/license/). Its
  terms include a naming requirement for any derivative model you distribute.

`models/build.py` copies this repository's `LICENSE` into every model it
builds, together with a pointer to the base model's own terms, so
`ollama show --license flash-onyx-2.2:12b` prints both. Check the base model's
license with `ollama show --license gemma4` before publishing a build.
