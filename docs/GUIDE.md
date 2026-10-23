The Flash guide
===============

Everything Flash does, in full. The [README](../README.md) is the short
version; [CONFIGURATION.md](CONFIGURATION.md) lists every setting and
[EXTENSIONS.md](EXTENSIONS.md) shows how to write an extension.

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
- **3D Models**: Ask for a chair, a vase, or a floor plan and the AI builds it with its `make_3d_model` tool out of boxes, cylinders, spheres, lathed profiles, extruded outlines, and blocky pixel-font text for logos and signs, each in its own colour and, where the real thing has one, a texture: wood, brick, tiles, marble, grass, fabric, stripes, dots, a checkerboard, or a stone-like noise, drawn to tile without a seam and sized in metres so bricks on a long wall match bricks on a short one, or any .png or .jpg, fitted to the part (a poster, a screen, a world map that wraps a sphere into a globe). It is saved as a `.glb` that opens in Blender and every game engine. In the web UI it appears in a 3D viewer beside the chat that you can turn, zoom, and download; a vision model also gets a render of it, so it can see a leg floating off the table and fix it. `send_3d_model` shows an existing `.glb`, `.stl`, or `.obj`.
- **Music**: Ask for a tune, a chord progression, or a drum beat and the AI writes it with its `make_midi` tool as tracks of notes (note names like C4, chords, drum names like kick and snare, any General MIDI instrument), saved as a standard `.mid` that opens in any DAW or notation program. In the web UI it opens beside the chat in a player of Flash's own: a piano roll of every track in its own colour, with play and pause (Space), seeking by clicking the roll, speed from half to double, loop, volume, and muting a track by its chip. It plays with a built-in synth, so it works offline. `send_midi` plays a `.mid` that already exists.
- **Live Documents**: A Markdown or text document Flash shows opens beside the chat, where you can read it, edit it, and comment on passages. Saving wakes Flash: it reads what you changed and does whatever you left for it, such as a placeholder like `[insert the Q3 revenue here]`, a note to it ("Flash: add a pack list here"), a question, or a section you started and didn't finish. No button needed. If the save asks nothing of it, Flash stays quiet and the chat only notes that you saved. A save made while Flash is busy is read once it finishes. When Flash, a sub-agent or a spark edits the file, the change types itself into the open document and stays marked in green for a moment. Without autonomous mode, each change shows in the document first, marked in green, with Allow and Deny beside it; that is the same question the chat asks, and answering either one answers both. In autonomous mode changes go straight in. If Flash changes the file while you have unsaved edits, you choose whether to keep editing or load its version.
- **Page Screenshots**: The AI renders a page it built in a headless browser with its `screenshot` tool and looks at the result, so it can see a broken layout instead of guessing from the HTML.
- **Page Control**: The AI opens a page with `open_page` and then clicks buttons, fills forms, presses keys, and runs JavaScript on it with `interact`, seeing a fresh screenshot, the page's elements, and its console errors after every step, so it can debug what a page *does*, not just how it looks.
- **Read Aloud**: In the web UI, the play button under any reply, or under a spark's report, reads it out a sentence at a time, marking each as it is said: in Flash's own voice when voice mode is set up, and in the browser's otherwise.
- **Waits**: In the web UI, Flash can set a task aside until something happens, then pick it up in the same chat: "when CI goes green, deploy", "if Sam hasn't replied by Friday, follow up". Waits outlast restarts.
- **Record a Meeting**: In the web UI, + > Record a meeting writes down what is said as it is said, on this computer, then hands the transcript to the model in a new chat for the summary, decisions and to-dos.
- **Connectors**: Plug in any MCP server (GitHub, Notion, a database, your files) with `/mcp add` or Settings > Connectors, and its tools join Flash's own. Read-only tools run straight away; anything else asks first.
- **Acts on Your Computer**: With `/computer on`, or the switch in the web UI's Settings, "stop the music", "turn it down" or "open Spotify" just happens: Flash plays, pauses and skips music, sets the volume, opens and quits apps, opens links and files, locks the screen, copies to the clipboard and switches dark mode. On a Mac, "text Sam I'm running late" sends an iMessage once you say yes. Off by default, and it never clicks, types or runs commands this way.
- **Voice Mode**: `/voice on` downloads a Vosk speech model and a Piper voice, then lets you talk to Flash and hear its replies, with typing still available at any time. Flash talks warmly, like a person rather than a document, and its voice has a style of its own: Warm (the default: unhurried, friendly, a breath between sentences, questions that lift and exclamations with a little bounce), Lively, Calm, or Plain, picked with `/voice style` or under Voice models in the web UI's Settings, where you can hear it first. Kristin and HFC join the voices on offer, for a warmer sound still.
- **Visible Plans**: For a multi-step task the AI posts a checklist up front and ticks each box as it finishes that step, so you can see where it is instead of waiting for the wall of text at the end.
- **Knows What Just Broke**: With `/hook install`, Flash sees the commands you run in VS Code's terminal and whether they failed, so "why did that fail?" works without pasting anything.
- **VS Code Aware**: Run from VS Code's terminal, Flash opens its edits as side-by-side diffs while it waits for your yes, clears them away once you have answered, and opens files at the line it's talking about.
- **Async Sub-agents**: The AI can spawn background sub-agents with the `agent` tool to work on independent pieces of a task at the same time, then collect each one's answer with `agent_result` once it's needed.
- **Teach by Showing**: In the web UI, + > Record your screen (or Record and narrate) records you doing something, then attaches the recording to your message. While it records, the page catches a still each time the screen changes and settles; narrated, it keeps what you say over each step too (written down by voice mode's listening model, when that is set up). Flash sees the stills in order with your words, works out the procedure, and saves it as a skill, so next time you can just ask. Recordings run up to four minutes and play back in the chat.
- **Sparks**: Agents that don't stop when the chat does. Give a spark a name, a standing goal, how often to work, and the lines it must not cross ("every morning, check my repo for new issues and tell me which look like bugs; never comment on anything"), and it works on it, even with Flash closed if you turn on `/sparks always on`, keeps its own notes between shifts, and files a report when there is something to say. Chat with one to ask what it has found, change its goal, or tell it what to do differently, and every later shift remembers. In the web UI your sparks sit in the sidebar as a team, each with its face, name and job title and what it is doing right now, glowing purple while it works (the chevron beside Sparks folds the list away, and it stays folded); one that needs your approval says so there, and in a chat with it the approval card waits above the box. Each one is a flat circle in its own colour with two oval eyes, and nothing else; what it is doing shows in how it moves: it floats, blinks and glances about while it waits, squints happily when you hover it, bounces with its eyes darting while it works, shuts its eyes and drifts off in z's while paused, tilts its head at a "?" when it needs your approval, and droops its eyes after a failed shift. Pet it and it pops out hearts (five quick pets for heart eyes), a new one pops into being, and one with news jumps for joy. Left to themselves they have lives of their own: they blink when they like (now and then twice), glance about, watch your pointer when it comes near and look right at you when it is on them, and every so often hop, wiggle, roll over, yawn, peek to one side, hum a little ♪ or spot a sparkle; a hop can set the one beside it off too. One waiting on you tries to catch your eye, one whose shift failed sighs, and a sleeping one stirs. With nobody about for a while the idle ones doze off, and they wake with a little start when you come back. None of it runs if your system asks for reduced motion.
- **System One**: In autonomous mode, a small, quick model ([Ollama v0.35+](https://ollama.com/download)) looks over each command and edit before it runs and stops the ones that look unsafe or off task, and the model can ask it quick yes/no, choice, and score questions for a second opinion. Pick its model, or None, with `/systemone model` or in the web UI's model menu, which finds the System One models on the machine.
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
   ollama pull <model>
   ```

   By default, Flash connects to a local server at `http://localhost:11434`. To use a remote server, set `OLLAMA_HOST` (see [Configuration](#configuration)).

### Flash Onyx (recommended model)

**Flash Onyx** is a series of custom Ollama models built for Flash: a base
model with Flash's persona and tuned parameters baked in. Each one lives in a
single Modelfile under `models/` that declares its name and sizes at the top,
and `models/build.py` builds whatever a Modelfile declares.

Recent releases come in two sizes (the README's license notes say what
they are built on): `12b` runs on consumer
hardware, and `31b` is the flagship and wants a bigger GPU. The highest
version under `models/` is the newest; older ones stay there to build too.

```bash
python3 models/build.py models/flash-onyx-<version>.Modelfile             # every size
python3 models/build.py models/flash-onyx-<version>.Modelfile --size 31b  # just one
```

Then set `MODEL` to whichever you built (`flash-onyx-<version>:31b`, or
`:12b`) in `~/.flash.env` or your environment.

### Run

```bash
python3 run.py
```

## Configuration

FLASH CLI is configured through environment variables. You can create a `.flash.env` file in your home directory:

```env
MODEL=my-model:8b
OLLAMA_HOST=http://localhost:11434
```

### Environment Variables

See [docs/CONFIGURATION.md](CONFIGURATION.md).

### Switching servers

- **Local (default):** leave `OLLAMA_HOST` unset, or set it to `http://localhost:11434`.
- **Remote server:** set `OLLAMA_HOST` to the other machine, e.g. `OLLAMA_HOST=http://192.168.1.50:11434` or `OLLAMA_HOST=https://ollama.example.com`.

Make sure the target server is reachable and that `MODEL` has been pulled on it.

Or switch from inside Flash with `/host`. It lists the saved hosts with
whether each one answers, and Enter switches to the one picked; typing
an address that isn't saved switches to that. `/host <name|url>`
switches straight away, `/host add <url> [name]` saves one (asking for
an API key, hidden, if its server wants one) and switches to it,
`/host remove <name>` forgets one, and `/host list` prints them. These
are the same hosts as the web UI's, so one added in either shows in
both.

## Usage

Start the CLI by running:

```bash
python run.py
```

### Internal Commands

- `/help` or `/?`: Display the help message.
- `/model`: Pick from the models on this machine, or type a name to
  download one. `/model <name>` switches straight to one.
- `/btw <question>`: Ask something on the side, even while Flash is
  working. It is answered at once from what the conversation already
  holds, with no tools, and shown apart from the chat: neither the
  question nor the answer joins the conversation, and the work in
  progress carries on undisturbed. The web UI takes `/btw` too, and
  shows the answer in a card above the message box until you close it.
- `/loader [name]`: Pick the animation that turns while Flash works,
  from a list showing each one's frames. `/loader morph on` has it turn
  into another, at random, every 10 seconds. Where the terminal can't
  show a loader's characters, it draws a plain ASCII version.
- `/host [name|url]`: Pick or switch the Ollama server. `add <url>
  [name]`, `remove <name>`, and `list` manage the saved hosts.
- `/auto [on|off]`: Toggle autonomous mode: commands and edits run
  without asking first.
- `/systemone [on|off|model [name]]`: Show, switch, or pick the model for
  [System One](#system-one), which reviews autonomous commands.
- `/computer [on|off] [name]`: Let Flash do [simple things on your
  computer](#your-computer), like pausing the music, or stop it; with a
  name (`imessage`, `music`), switch just that one.
- `/mcp [add <name> <command or URL> | env <name> KEY=VALUE | remove |
  on | off | tools <name> | reload]`: List and change the
  [connectors](#connectors) whose tools Flash has.
- `/skills [show|remove <name>]`: List, read, or delete what Flash has
  learned.
- `/plan`: Show the checklist the model is working through.
- `/hook [install|remove]`: Let Flash see the commands you run in VS
  Code's terminal (zsh and bash).
- `/agents`: Watch sub-agents work live. `/agents <id>` shows one in full,
  with its answer once it is done.
- `/sparks [new|<name>]`: List your sparks, make one, or read one's
  reports. `/sparks chat <name> [message]` talks with one. `/sparks teach <name> <lesson>`, `like <name>`, `dislike <name> [why]`, `run`, `pause`, `resume`,
  `every <name> <30m|2h|daily>`, `title <name> <title>` and `remove` look after them.
  `/sparks always on|off` keeps them working with Flash closed.
  `/sparks rounds [1-100|unlimited]` shows or sets how many rounds of
  tools a shift gets before it writes its report (12 unless changed;
  also in Settings in the web UI, where the limit can be switched off).
  With no limit, a shift calls tools until it writes its report or you
  stop it.
- `/clear`: Clear the conversation history.
- `/undo`: Take back the file changes from the last turn.
- `/compact`: Summarize the conversation to free up room.
- `/context`: Show how much of the context window is in use.
- `/stats`: What Flash has done for you so far, in the terminal and the
  web UI together: replies, tokens, and how much of it ran on a local
  model.
- `/email [connect|test|inbox|default|progress|disconnect]`: Your email
  accounts, for Flash and your sparks to read and send from, and
  [progress emails](#progress-emails).
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
- While Flash works, the box and the status bar stay at the foot of the
  screen. `Enter` there steers: the message goes to the model before its
  next step, or, if the turn ends first, runs as the next turn. `Tab`
  queues one to run as a turn of its own once this one is done, and a
  `/command` or a `!` line always waits its turn. What is waiting is
  listed above the box; after a `Ctrl+C` it goes back into the box, to
  send again or not. `FLASH_NO_DOCK=1` turns the box off while Flash
  works.
- Type `!` to run a shell command; `Backspace` on the bare `!` goes back
  to typing a message.

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

### System One

Autonomous mode means nobody says yes or no to a command before it runs.
System One puts someone back in that seat: a small, quick model, served
by Ollama's System One endpoint (`POST /v1/systemone`, new in Ollama
v0.35), that judges in a moment what a big model would take a whole turn
to.

```bash
ollama pull nimble
```

Then pick it under System One in the web UI's model menu, or run
`/systemone on` in the terminal (or switch it on in Settings > General >
System One). Each checks the server's version first. An Ollama
older than v0.35 has no System One, so Flash says so and leaves it off
until you update Ollama. It does nothing outside autonomous mode, where
you answer each command yourself.

With it on and autonomous mode on, it does two things:

- **Reviews tool calls.** Each call that would have asked you first
  (`shell`, `write`, `edit`, `multi_edit`, making a spark, and extension
  tools that ask) is checked before it runs: is it safe to run unchecked,
  and does it serve what you asked for? One that fails either question is
  not run. The model is told why, and takes a safer way or asks you.
  Sub-agents and spark shifts are reviewed the same way, against their
  task or goal. If System One cannot answer (the server is down, the
  model is not pulled), the call is not run, rather than slipping through
  unreviewed.
- **Answers questions.** The model gets an `ask_system_one` tool for a
  quick second opinion: a yes/no ("does this output show every test
  passing?"), a choice between options, or a score on a scale, each
  answered with probabilities.

There is no fixed list of System One models: Flash asks the server
which of its models list `decision` among their capabilities (Ollama's
`/api/show`), so any one you pull shows up. `/systemone model` lists
them, and `/systemone model <name>` picks one, offering to download it
first; `/systemone model none` switches System One off. In the web UI
they are under System One in the model menu, with None first, and the
model button shows the one in use beside the chat model. Models that
can only judge are left out of the chat models above them. `/systemone`
shows how many calls it has reviewed and stopped this session, and the
status bar reads `auto + S1` while it is at work.

### Sparks

A sub-agent does one job and is gone. A spark keeps a job. Make one with
`/sparks new`, from the Sparks page in the web UI, or just by asking
("make a spark that checks the price of this every six hours and tells
me if it drops under $900"): Flash calls its `make_spark` tool and asks
you first. A spark can also be made paused, to set up now and start
later: tick "Make it paused" in its form (or in Add shared), answer `n`
to "Start it now?" in `/sparks new`, add a template or code with
`/sparks add <template|code> paused`, or ask Flash for one paused.
Nothing runs until you resume it, and then its first shift starts.

Each spark has:

- optionally, a **project** from the web UI's Projects: its shifts work
  on that folder and follow the project's instructions, a chat with it
  starts in the project, and the project's page lists it. Pick one in
  the spark's form, or with `/sparks project scout <project|none>`.

And always:

- a **name** and a handle to go with it (`Scout` is `@scout-spark`),
- optionally, a **title**, its job as a teammate's ("Repo watcher"):
  shown under its name, and it knows it
  (`/sparks title scout Bug triager`),
- a **model**, chosen when you make it: every shift and every chat with
  it runs on that one, whatever Flash itself is set to
  (`/sparks model scout my-model:8b` changes it; `/sparks model scout,
  writer my-model:8b` or `/sparks model all my-model:8b` changes many,
  `/sparks teams model Desk my-model:8b` a whole team, and `/sparks models`
  lists them all; on the Sparks page, Change models does the same),
- a **goal**, the standing assignment it works toward,
- a **schedule**, from every 15 minutes to weekly,
- **boundaries**, what it must never do whatever the goal seems to need.

For as long as Flash runs, in the terminal or as `flash --web`, sparks
that are due work one at a time. A shift reads the goal, the notes the
spark kept for itself last time, its last report, and everything you
have taught it; works with the same tools a sub-agent gets; then either
files a report or, when nothing is new, a quiet one that doesn't count
as news. New reports show up at your prompt (`● Scout has news ·
/sparks scout`) and as a count beside Sparks in the web UI's sidebar.

```text
/sparks teach scout Only tell me about issues labelled bug.
```

A lesson like that, or feedback typed under a report in the web UI, is
kept and read by every later shift.

Reports can also be liked or disliked, with the thumbs on each one in
the web UI or `/sparks like scout` and `/sparks dislike scout [why]`
(for its latest report). Every shift is shown its last few rated
reports, liked and disliked, with anything you said about them, and
told to do more of the one and less of the other. A dislike in the web
UI asks what was wrong with it; what you say is kept as a lesson.

You can also just talk to a spark. In the web UI, open it on the Sparks
page and press Chat, or start a new chat and pick it under + > Sparks:
the chat is on the main screen like any other, with the spark's badge
in the box and beside the chat in the sidebar. In the terminal,
`/sparks chat scout` (an empty line ends it). It answers as itself,
from its goal, its notes and its reports, and can use its tools to
check something now; in the web UI it can also use the ones that ask
you first.

A spark can also be brought into any other chat in the web UI: type
`@scout` (an `@` offers your sparks) and that message is for it. It
answers there, under its own name, in place of Flash; mention two and
both answer in turn. It is shown the latest dozen messages of the chat
as a transcript, who said what, then what you said to it. Flash, or
whoever the chat is with, is then told that the spark was called, what
it used, and what it answered, as a note in its history rather than as
a reply of its own, so the next message without a mention can pick up
from what the spark said. Tell it how to do its job
differently and it keeps that as a lesson; give it a new goal or
schedule and it changes them. Ask it for real work ("actually, can you
check the login page?") and it takes the job on with its `take_on`
tool: a shift starts at once, does that job first, and when it is done
the report comes back into the chat you asked in, marked as from its
shift (or to its reports, from the terminal). Sparks live in `~/.flash/sparks`,
one JSON file each. Like sub-agents, they can only run commands or
change files in autonomous mode.

#### Approvals, stopping, and teams

A shift never runs a command or changes a file on its own (unless
autonomous mode is on). It stops at that step and asks: "Repo Watch
wants to: Run a command, `git status`". Approve or deny it in the
spark's window, or with `/sparks approve scout` and `/sparks deny scout
[why]`, and the shift carries on from exactly where it stopped, told
why if you said no. Nothing else starts for that spark while it waits.
A shift that is running can be stopped at its next step: Stop in its
window, or `/sparks stop scout`. Or say so to the spark, in a chat or in
its team's chat: "@scout stop, Bo already fixed it" has it call off its
own shift, and "@scout skip the tests" changes its course, the shift
taking that from its next step. While it works, a spark says what it
is doing in a few words beside its name ("fixed it, now running lint
and tests").

Sparks work as a team: each knows the others, and can hand one a
finding or a job with its `hand_off` tool. The other starts a shift
soon, told what it was handed and by whom.

#### Triggers, templates and sharing

Besides its schedule, a spark can watch a folder: when files in it
change, a shift starts, told which files (the form's "Watch a folder",
or `/sparks watch scout ~/Downloads`).

A spark can also start the moment something comes in: a new email, a
new iMessage (on a Mac), or a change in a connected MCP server, like a
new note in a notes app. Checking for those on a schedule costs a shift,
and its tokens, on every look, mostly to find nothing. Instead, Flash
looks itself, without the model:

- for email, it asks the mail server what uid the next email will get;
- for iMessages, it reads the newest message's number from Messages;
- for an MCP server, it calls one read-only tool that needs no input
  (a `search` or `list` tool, say) and compares the answer.

Only when something is new does a shift start, told exactly what came
in, to handle it as the spark's goal says. Turn it on in Settings,
General, Sparks ("Start sparks when something comes in"), or with
`/sparks events on`; it is off until you do. Then tick what starts each
spark under "Start when" in its form, as many as you like, or:

```text
/sparks events                       on or off, and which sparks start on what
/sparks events on                    turn it on (off turns it off)
/sparks events scout mcp:notes       start Scout on a change in "notes"
/sparks events inbox email imessage  start Inbox on new email or iMessages
/sparks events inbox none            stop
```

With the schedule set to "Only when called on", a spark spends nothing
until something comes in. The **Email Handler** template is made that
way: it handles each new email the way its lessons say (sort it, say
what needs you, draft replies you approve). `mcp:<server>/<tool>`
watches a server by a tool you name. iMessages need "Read iMessages" on
under Your computer. The first look at each only learns where things
stand, so what was already there starts nothing. A server that allows
one connection at a time is best watched from the Flash you have open,
not a keeper of its own beside it.

A spark can also freeze itself until something comes in, without any of
this set up: in a shift, `wait_for_events` pauses its schedule until a
new email, iMessage or change in an MCP server arrives, and its next
shift starts with what came. That's how a spark waits for an answer, or
handles messages as they come, calling it again at the end of each
shift. Calling it again while frozen replaces what it waits for, rather
than adding to it. Its window shows "frozen until a new email comes in", and Run now
thaws it.

The new-spark form starts from a template if you like, among them
Morning Brief, Repo Watch, Test Runner, Disk Guard, Dependency Check,
Page Watch, Inbox and Messages (`/sparks templates`, `/sparks add "Repo
Watch"`). Messages, on a Mac, sorts your new iMessages, says which need
you, and drafts replies that wait for your yes, every one. Any
spark can be shared as a code (Share in its window, or `/sparks share
scout`): whoever adds it with Add shared, or `/sparks add <code>`, sees
its goal and boundaries first and gets a copy of their own, with its
lessons but none of its reports or notes.

#### Always on

Out of the box, sparks work while a Flash is open. `/sparks always on`
(or the switch under Sparks in the web UI's Settings) keeps them working with the
terminal and browser both closed: Flash registers `flash --sparks`, a
Flash with no window that only runs sparks, to start at login, and
starts it right away. No administrator rights are needed.

| System  | How it starts at login |
| ------- | ---------------------- |
| Linux   | a systemd user service (`flash-sparks`), or an autostart entry where there is no systemd |
| macOS   | a launchd agent in `~/Library/LaunchAgents` |
| Windows | a Task Scheduler task that starts at logon, with no console window |

With Flash closed, a spark with news sends a desktop notification. The
background keeper and an open Flash never run shifts at the same time:
whichever holds `~/.flash/sparks/.keeper.lock` does the work, and the
other takes over when it quits. It reads `~/.flash.env` before every
shift, so a model you switch to in Flash is the one the next shift uses.
Ollama has to be reachable for a shift to run; one that cannot reach it
says so in its report.

`/sparks always off` removes what `on` added, and the background keeper
stops within a minute. On Linux a systemd user service only runs while
you are logged in; `loginctl enable-linger` keeps it going after you log
out. You can also run `flash --sparks` yourself, in a terminal you leave
open or under your own service manager.

#### Teams, team chat and rules

Sparks can work as a company. Put them on a **team** and give each a
lead to report to: what a spark finds rolls up to its lead, and the
lead tells you what matters. The Sparks page draws each team as a live
org chart (`/sparks teams` in the terminal), and Dev Team and Personal
Desk come ready made (`/sparks teams add "Dev Team"`), paused until you
have looked them over.

- **Team chat.** Every team has a group chat, the user and its sparks in
  one room (the chat button on the team, or `/sparks teamchat <team>
  [message]`). The leads answer what is said to no one; `@scout` calls
  on one, `@everyone` on all of them, and replying to a message calls on
  whoever wrote it. Sparks can bring a teammate in, send a few messages
  in a row, react with an emoji, or stay quiet when they have nothing to
  add. A spark can edit a message of its own to fix it or bring it up to
  date; it is marked Edited, and its arrows step back through every
  version it has had. A spark hears of a reaction to what it said, a message or a
  report, by you or a teammate: in its chats, and on its next shift,
  without being called to one. Taken back, it is never told.
  Under a spark's message, the play button reads it aloud, and Retry
  has the spark answer again in its place, the old message gone from
  the chat and from what every spark reads of it.
  Voice mode and `:emoji` work there too.
  Under each of your messages, one tick means it is in the chat and two
  mean a spark has read it, answering or on a shift; hover the ticks to
  see which sparks read it, and when. The terminal says the same.
- **Team rules.** Lines every spark on a team keeps to, on top of its own
  boundaries, on shifts and in chats alike (the team's edit form, or
  `/sparks teams rules <team> <rules|none>`).
- **Team projects.** Put a whole team on one project and every spark on
  it works there, and so does any spark that joins it later (Project in
  the team's edit form, or `/sparks teams project <team> <project|none>`).
  Taking the team off its project takes its sparks off too, unless one
  has been moved to a project of its own since.
- **Shared workers.** A spark can report to more than one lead, as
  equals: pick each under Reports to in its form (or
  `/sparks lead test-runner engine manager, flash manager`), and the
  org chart draws a line up to each. What it finds on its own goes up
  to all of them. Work one of them hands it (with `hand_off`) is done in
  that lead's project, and the report on it goes back to that one: two
  managers, one per project, can share the same Test Runner, and each
  hears back about its own work. Any spark can hand work to any other,
  across leads and projects, the same way.
- **Mentions in reports.** A spark that writes `@fixer` in its report
  sends it the report: Fixer reads it on its next shift, or starts one
  now if it is on call, and does what it was called on for. A lead the
  report rolls up to anyway gets it once, marked as mentioning it. The
  page draws each mention as a chip in that spark's colour, and the
  terminal in its colour.
- **Hiring.** A spark can propose hiring another, or letting one go. It
  waits for your yes (`/sparks hires`, `/sparks hire <id> yes|no`). A
  hire says all it will be: its instructions, title, schedule and
  boundaries, who on the team it reports to (the proposer unless it
  says), its colour, model, project, a folder to watch, a budget, and
  whether it starts paused. Anything it leaves out takes the usual.
- **Budgets.** Off by default. Give a spark tokens per hour, day, week
  or month and it pauses itself once they are spent
  (`/sparks budget scout 50k day`).
- **Audit log.** Every shift, tool call, approval and change is kept in
  an append-only log, each entry carrying the hash of the one before, so
  an edit by hand shows (`/sparks audit scout`).
- **On call.** A spark can skip the schedule and work only when you or
  another spark calls on it (`/sparks every scout on call`).
- **Pause all, Stop all.** Pause all lets running shifts finish and
  starts nothing new; Stop all stops them too. Teams pause as a whole.
- **No silent fallbacks.** If a spark's model fails, it is marked
  unavailable rather than switched to another model behind your back.
- **Documents and colours.** A spark can write a document, which opens
  in the side panel; and each wears a colour you pick
  (`/sparks colour scout lagoon`, or any `#rrggbb`).

### Progress emails

Flash can email you the milestones of what it is working on, so you can
follow a long request from your phone, a watch, or another computer:

- **▶ Flash started** when it starts on something,
- **☐ Plan, 4 steps** and **☑ Step 2/4** as it plans and ticks steps off,
- **❓ Flash needs you** when it is waiting on your yes or no,
- **✓ Flash finished** (the first line of its answer, and how long it
  took), or **✗ Flash stopped** (and why).

Subjects are short, since a small screen shows little more, and every
email of one request is in one thread. They are sent through the
account `/email connect` set up, to your own address unless you give
another, and only for the request you are waiting on: sub-agents and
sparks working on their own don't send any.

```text
/email progress on            turn them on
/email progress to x@y.com    send them somewhere else; "to me" goes back
/email progress only ask,end  just these: start, plan, ask, end
/email progress all           every one
/email progress test          send one now, to see it arrive
/email progress off
```

The web UI has the same under Settings, Email, Progress emails. Sent to
an iCloud address, they reach an Apple Watch's Mail on its own, over
Wi-Fi or cellular. `only ask,end` is the quiet setting: a message only
when Flash needs you and when it is done. One that can't be sent (a
wrong app password, no network) is noted in
`~/.flash/progress-email.log`, and `/email progress` shows the last
problem.

### Image Recognition

`/image <path> [prompt]` attaches a local image (`.png`, `.jpg`, `.jpeg`,
`.webp`, `.gif`, `.bmp`) to your next message and sends both to the model.
If you leave off the prompt, Flash asks it to describe the image. This
requires a vision-capable model  text-only models will ignore the image
or error. Pull one and switch to it first, e.g.:

```bash
ollama pull my-vision-model
```

Flash Onyx 2+ is vision capable.

```console
/model my-vision-model
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

To listen or speak with a different model, `/voice models` lists the ones
Flash offers, with their sizes, which are downloaded, and which are in use.
`/voice pull <name>` downloads one and switches to it (part of the name is
enough, as in `/voice pull ryan`, and Ctrl+C stops the download), and
`/voice remove <name>` deletes one you no longer use. The web UI does the
same under Settings > Voice models.

```FLASH
/voice models
/voice pull lgraph
/voice pull kristin
```

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
Linux `sounddevice` also needs PortAudio from the system (`apt install
libportaudio2`); the macOS and Windows wheels bundle it. The voice and the
listening model can be swapped with `VOICE_PIPER_VOICE` and
`VOICE_VOSK_MODEL`, and the microphone's sensitivity tuned with
`VOICE_SILENCE_THRESHOLD`; see [docs/CONFIGURATION.md](CONFIGURATION.md).

For a voice that sounds far more natural, pick a Kokoro voice in
Settings, Voice models, or `/voice pull heart`: Heart, Bella, Michael,
Emma or George. They share one download of about 120 MB and run on the
CPU, a little slower than Piper but well ahead of the reading. They need
one more package, which an update keeps once you have it:

```bash
pipx inject flash kokoro-onnx      # or: pip install "flash[kokoro]"
```

For listening that hears far more accurately, pick a Moonshine model in
Settings, Voice models, or `/voice pull moonshine-small`: Tiny, Small or
Medium. They stream, so the words on screen while you talk are already
the right ones, and they need one more package, which an update keeps
too. "Hey Flash" still listens with the small Vosk model, which downloads
alongside.

```bash
pipx inject flash moonshine-voice  # or: pip install "flash[moonshine]"
```

In the web UI none of these commands need typing: wherever Flash finds a
package missing (starting voice mode, a Kokoro voice, a Moonshine model,
a tool that failed for want of one), it shows an Install button that
runs the right command for your install. Logs shows what it prints as it
goes, and opens by itself if the install fails.

### Waiting for something

In a web UI chat, Flash can set a task aside until something happens and
then carry on in the same chat, knowing everything it did before. Ask in
your own words:

- "in two hours, check the download finished and unzip it"
- "when CI on #42 goes green, deploy it"
- "if Sam hasn't replied by Friday morning, draft a follow-up"
- "wait for new texts and answer them"

It waits for one of four things: a time; a command that succeeds, run
every so often in the chat's folder (`gh pr checks 42`, `curl -f` on a
URL, `test -f` on a file), which you say yes to once when the wait is
set, unless autonomous mode is on; or something it looks for with its
tools, in words ("a reply from Sam is in my inbox"), checked now and
then without changing anything; or something coming in: a new email, a
new iMessage (on a Mac), or a change in a connected MCP server, looked
for without the model, so nothing is spent until it comes. That last
one can keep going: the chat wakes with each new one, answers it, and
goes on waiting until you press Cancel or it gives up. A time and a
condition together wait for the time, then start looking. A wait that keeps looking gives up
after three days unless told otherwise, and Flash tells you it did.

Flash can change a wait it set without starting over ("make that four
hours", "also watch for email", "check every five minutes"): the same
wait and card, now waiting for what it says. A new command is put to
you again, as when the wait was set.

Each wait shows in its chat as a card with what it is waiting for, what
it will do then, how its last look went, and Cancel. Waits are kept in
`~/.flash/waits.json`, so they outlast Flash restarting, an update, or
the laptop sleeping. A time that passed while Flash was not running is
over the moment it is back. Deleting a chat cancels its waits.

### Recording a meeting

In the web UI, + > Record a meeting (or `/meeting`) listens through the
microphone and writes down what is said as it is said, with the
listening model on this computer: nothing leaves it. The bar over the
message box shows the transcript as it grows, with Pause, Discard, and
Stop and write up.

Stopping keeps the transcript in `~/.flash/meetings`, and opens a new
chat with it attached, where the model writes up what the meeting was
about, what was decided, the action items with who and by when where
they were said, and the questions left open. A short meeting goes to the
model whole; a long one it reads from the file a part at a time. A
Moonshine listening model (see [Voice mode](#voice-mode)) hears a
meeting far better than the small Vosk one. It hears one microphone, so
for a call, leave the sound on the speakers rather than in headphones,
and it does not say who said what.

### Connectors

A connector is an MCP server: a program that offers tools (GitHub's
issues and pull requests, a Postgres database, a Notion workspace, the
files in a folder) in the Model Context Protocol, the one shape every
assistant can call. Add one and its tools join Flash's own, named
`<connector>__<tool>`, in the terminal and the web UI alike.

```text
/mcp add files npx -y @modelcontextprotocol/server-filesystem ~/Documents
/mcp add github npx -y @modelcontextprotocol/server-github
/mcp env github GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
/mcp add docs https://example.com/mcp
/mcp env docs Authorization=Bearer ...
```

A command is a program Flash starts on this computer and talks to over
its standard input and output; a URL is a server elsewhere, spoken to
over HTTP. `/mcp env` gives a command its environment and a URL its
headers. In the web UI, Settings > Connectors does the same, shows each
one's tools, and turns them on and off.

They are kept in `~/.flash/mcp.json`, readable only by you, in the
`mcpServers` shape other MCP clients use, so a config written for one of
them works pasted in as it is. Servers start in the background when
Flash starts (the terminal, the web UI, or the sparks' keeper), so their
tools are there for the first message and the first shift, and stop
when it exits.

A tool its server marks read-only runs straight away. Any other asks
first, as a shell command does, unless autonomous mode is on, where
[System One](#system-one) looks it over instead.

Sparks have the connectors' tools too, in their shifts and in a chat
with them, asked about the same way: a shift that reaches for one that
changes things waits for your yes, out of autonomous mode. If a tool
seems missing, because a server was added or crashed since, the model
can call `refresh_tools`: it starts any server that isn't running, has
each list its tools again, and the new ones are there from its next
step.

### Your computer

With `/computer on`, or Settings > General > Your computer in the web
UI, Flash can do simple things on the computer it runs on when you ask:

- play, pause and skip music or video, and say what is playing
- set the volume, turn it up or down, mute it
- open or quit an app by its name (a quit asks to save, as usual)
- open a link, a file or a folder
- lock the screen or turn the display off
- copy text to the clipboard
- switch dark mode on or off
- on a Mac, read your recent iMessages ("what did Sam say?") and send
  one ("text Sam I'm running late")

It is off until you turn it on, and while it is off the model is not
offered any of it. Turned on, each of those gets a switch of its own
under it in Settings, all on, so you can turn off just what you'd
rather it didn't do, like reading or sending iMessages. In the terminal,
`/computer` lists them, and `/computer off imessage` or `/computer on
music` switches one; the model isn't offered what's off. Each action is one fixed step Flash takes itself: it
never clicks, types, or runs a command the model wrote, and it will not
open a script or installer, only apps by name.

On a Mac, Spotify and Music are asked directly; anything else playing,
in a browser or any other app, gets the keyboard's media keys, which go
to whatever played last. Flash reads what macOS shows as Now Playing,
before and after, so it knows what is playing in any app, never presses
play on something already playing, and can tell you whether the key
worked. The first time, macOS asks to let the app Flash runs in (Terminal,
iTerm and the like) control Spotify and Music, and, for the media keys,
to turn it on under Privacy & Security > Accessibility. Dark mode asks
to control System Events.

Reading iMessages goes through what Messages keeps on your Mac, newest
first, with who said it by their name in Contacts and group chats named;
nothing is marked read. It needs Full Disk Access for the app Flash runs
in (System Settings > Privacy & Security > Full Disk Access), which is
how macOS guards your messages.

An iMessage goes from your account in the Messages app. Flash shows who
it's to and exactly what it says, and asks every time before sending,
autonomous mode or not; a no means it isn't sent. A name or nickname
("Mom") is looked up in Contacts, and the message goes to that person's
mobile number, or their other number or email address when there is
none. If more than one contact has that name, Flash asks you which one.
The first time, macOS asks to let the app Flash runs in see Contacts
and control Messages.

Windows uses the keyboard's media keys,
which every player answers, though it cannot tell what is playing. Linux
uses `playerctl` for music and PipeWire, PulseAudio or ALSA for the
volume.

### Computer Use (beta)

For the things no tool can do, a setting deep in an app or a button in
a program with no other way in, Flash can look at your screen and work
the mouse and keyboard itself. Turn it on with `/computer use on`, or
Settings > General > Computer Use in the web UI. It is separate from
[Your computer](#your-computer), and off until you turn it on; while it
is off the model is not offered it.

Flash takes a screenshot, says where to click, what to type or which
keys to press, and gets a fresh screenshot after each step. The model
is told to use every other tool first, as this is slow and can click
the wrong thing, and never to type passwords or card numbers.

It asks before it takes your computer, every reply that wants it, even
in autonomous mode; only UltraAuto skips the question. A yes lasts that
one reply. While Flash has the computer:

- press **Ctrl+Alt+Esc** (**Control+Option+Esc** on a Mac) to take it
  back: the reply stops then and there. Flash never presses those keys
  itself. In the web UI the same keys work too, wherever you are.
- or just move the mouse: the next step sees the pointer isn't where
  Flash left it, and stops.
- the Stop button and Esc still stop the reply as usual.

Only one chat has the computer at a time. It needs a model that can see
images. On a Mac, the app Flash runs in needs Accessibility (for the
mouse and keyboard) and Screen Recording (for the screenshots), under
System Settings > Privacy & Security; screenshots are of the main
display. Windows uses user32 and PowerShell. Linux needs `xdotool`, an
X session, and a screenshot program (`gnome-screenshot`, `grim`,
`scrot` or ImageMagick's `import`).

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
rest. See [docs/EXTENSIONS.md](EXTENSIONS.md) to write your own.

### Learning

Flash learns from its own work.

- **Skills** are procedures for tasks that come up again: the steps, the
  commands that worked, and the pitfalls to avoid. Each one lives in
  `~/.flash/skills/<name>/SKILL.md`. The model sees each skill's name and
  one-line description on every turn, and reads the full skill when a
  task matches it. Ask it to remember how to do something, or correct
  how it did a task, and it saves or fixes the skill.
- **Memory** is saved with the `remember` tool, and it is now in the
  system prompt, so the model uses it without having to search for it.
  The newest entries that fit in 2200 characters are sent. `recall`
  still searches older ones.
- **Reviews** happen in the background. After 10 tool calls, and every
  10 messages, Flash rereads the recent conversation once the reply has
  gone out and saves what is worth keeping: a new skill, a fix to one it
  wrote before, or a fact about you. It says what it saved under your
  next reply, and the status bar shows `learning` while it runs. A review
  can only change skills Flash wrote itself, never ones you wrote. It
  stops the moment you send a message, so it never holds up your turn,
  and it runs again after the next turn.

What is learned reaches the system prompt in your next session, or
after `/clear`. The prompt stays the same for the rest of a session,
so Ollama can reuse the work it did on it instead of rereading it all.

`/skills` lists what Flash has learned, `/skills show <name>` shows one,
and `/skills remove <name>` deletes one. `SKILL_REVIEW_AFTER` and
`MEMORY_REVIEW_EVERY` change how often reviews run, and `0` turns them
off (see [docs/CONFIGURATION.md](CONFIGURATION.md)).

### Web UI

```bash
flash --web                 # opens Flash in your browser
flash --web --lan           # also reachable from your phone, with a QR code to scan
flash --web --port 9000     # on another port (the default is 7433)
flash --web --no-open       # print the link instead of opening it
```

Inside a terminal session, `/web` starts the same UI in the background,
`/web lan` opens it to your network, and `/web stop` shuts it down.

The browser UI is the same agent as the terminal, with the same model,
tools, permission prompts, skills, and extensions. Replies stream in as
the model writes them. Tool calls show as compact rows that expand, and
file changes show as colored diffs. A permission prompt takes the
keyboard focus, so a single `y` or `n` answers it.

**Think harder**, in the + menu, has the model think harder about your
next message: a model that can reason apart from its answer reasons
longer, and every model is told to take its time and check its work. A
Think pill in the box shows while it is set (a click on it skips it),
and it lets go once the message is sent.

**Custom instructions**, in Settings, are how you want every reply, in
your own words ("use metric", "no bullet points"), in the browser and
the terminal alike; `/instructions <text>` sets them from the terminal
and `/instructions clear` removes them. They are kept in
`~/.flash/instructions.md`.

Flash can retitle the chat it is in, when the title it has is wrong: a
chat named after a message it misread is renamed once it understands
(a title you gave it yourself stays). And in voice mode it can end the
conversation itself when you are done, after saying goodbye. Ctrl+C in
the terminal running `flash --web` stops it.

In voice mode in the browser, the camera button shows Flash what you
point it at: while it is on, what it sees at the moment you finish
speaking goes with what you said, as a photo (it needs a model that can
see images). It starts on the back camera; on a phone or tablet with
more than one, the flip button beside it switches to the front and back,
the front one shown as a mirror would. And Settings, Voice, **Voice mode look** swaps the orb for a
companion, a face that blinks while it listens, perks up while you talk,
looks up while it thinks, and moves its mouth as it speaks.

Every button has a shortcut, shown when you hover over it, and `?`
lists them all:

- `Ctrl K` searches every action, chat, and model.
- `Alt N` starts a new chat, `Alt ↑` and `Alt ↓` step between chats,
  and `Alt 1`–`9` jumps to one.
- `Alt F` searches every chat, titles and messages alike. Opening a
  result scrolls to the message that matched and highlights the words.
- `Alt M` switches model, `Alt R` retries, `Alt E` edits your last
  message, and `Alt Z` undoes the last turn's file changes.
- `Shift Tab` toggles autonomous mode, `Ctrl O` expands every tool
  row, `Esc` stops a reply, and `Alt T` switches between light and dark.
- Outside the message box, `j` and `k` move between blocks, `c` copies
  one, and `g` and `G` jump to the top and bottom.

**Editing a message** happens where it is. Hover over one you sent and
click the pencil, or press `Alt E` for the last one. Change it, then
press `Enter` or Save, or `Esc` to cancel. The chat goes back to that
message and carries on from what it says now. The earlier version is
kept: `‹ 1 / 2 ›` under the message steps between versions, each with
the replies that followed it. Retry on a reply answers your message
again the same way, and a reply that failed with an error has a Retry
button of its own.

**Files** the agent makes come to you. It sends a picture with
`send_image` and a PDF with `send_pdf`. In the browser, the file slides
out in a panel on the right, with its name and size across the top and
buttons to download it or open it in a tab. A 3D model from
`make_3d_model` or `send_3d_model` opens there in a viewer: drag to turn
it, scroll to zoom, right-drag to pan, with buttons to reset the view,
stop it spinning, and see its wireframe. A card stays in the chat to
open it again, and `Alt V` shows or hides the panel. Each file is kept
as a copy in `~/.flash/web/files`, so a chat still shows its files
after the originals are gone. In the terminal, a PDF opens in your PDF
viewer.

**Projects** group chats about one codebase. A project is a folder plus
instructions: every chat in it runs its tools in that folder, and the
instructions go into its system prompt. Open them from Projects in the
sidebar (`Alt P`). Chats are saved as they change, so a project keeps its
conversations across restarts. Hosts, projects, and chats live in
`~/.flash/web`, and removing a project never touches its folder or its
chats.

**Making it yours.** Settings › General changes how the page looks, in
this browser:

- **Colors**: a palette for the whole page in light and dark: Flash,
  Ocean, Forest, Grape, Rose, Sunset, Midnight, Graphite, High contrast,
  and any an extension adds.
- **Accent**: the colour of buttons, links and the loader. Pick a swatch
  or any colour.
- **Text and layout**: the chat font (default, rounded, serif, mono or
  system), text size, how wide the conversation runs, the spacing
  between messages, and how round the corners are.
- **Loading animation**: Dots, Bloom, Orbit, Pulse, Wave, Bounce, Grid,
  Snake, Arc, Cursor, Comet, or an extension's. Turn on **Morph between
  loaders** and every 10 seconds it shrinks away into another, at
  random, for as long as Flash works. Both are the same settings as the
  terminal's `/loader` and `/loader morph`.

**Hosts** are the Ollama servers Flash can talk to: this computer, and
any other machine you add, like one with a bigger GPU. The model menu
(`Alt M`, or `Alt H` to start on the hosts) lists them with a dot showing
which ones answer. Picking one switches Flash to it and lists the models
there. The choice is saved as `OLLAMA_HOST`, the same setting the
terminal uses, and the terminal's `/host` lists the same hosts.

Ollama itself has no API keys, but a server can sit behind a proxy that
asks for one. Give the key in the API key box when you add the host, and
Flash sends it with every request to that host (chat, model lookups, and
System One alike) as `Authorization: Bearer <key>`. It is kept in
`~/.flash/web/hosts.json`, readable only by you, and never shown again;
a lock marks the hosts that have one, and a yellow dot one that wants a
key or turned its key down. Adding the host again replaces its key. The menu's three sections (Host, Model, System One) each
fold away with a click on their name, and stay as you left them.

The model button shows a model's own name, so
`Natuworkguy/flash-onyx-2.5:31b-cloudbase` reads `flash-onyx-2.5`; the
menu shows the whole name. On a new chat the button sits in the message
box; once a chat is going, it fades from the box to the top left of the
chat, out of the way of what you are writing.

By default the server only listens on this machine. `--lan` listens on
your network too, prints a QR code in the terminal, and shows one under
Open on phone in the sidebar. Every request needs the random token in
that link, so other websites and other people on the network cannot
drive Flash without it. The link travels unencrypted, though, so use
`--lan` only on a network you trust. If your phone cannot connect, your
firewall is probably blocking Python from accepting connections. On a
Mac, allow it in System Settings, Network, Firewall.

#### Open from anywhere, through a tunnel

Below "Open on your network" in Settings, General, Network, a tunnel
puts the web UI on a public https address, so you can open it away from
home. Pick the service, then Open tunnel:

| Service | Needs | Address |
| --- | --- | --- |
| Cloudflare quick tunnel | `cloudflared`, no account | random, new each time |
| localhost.run | `ssh`, nothing else | random, new each time |
| Tailscale Funnel | Tailscale signed in, Funnel allowed | your tailnet's, the same each time |
| ngrok | `ngrok` with its authtoken added | ngrok's |

Settings then shows the link, token and all, with a QR code, and every
page shows a red bar with Turn off while it lasts. In the terminal,
`/web tunnel` lists the services, `/web tunnel cloudflare` opens one
(after a y/n), and `/web tunnel off` closes it. It also closes when
Flash stops, and nothing ever opens one but you.

**Think before you use it.** A tunnel puts Flash on the internet.
Anyone who gets the link can use Flash as you, from anywhere: read
every chat, run commands on your computer, change and delete files,
and send email and messages in your name. The link is as good as your
password, so never post it or send it where you wouldn't send a
password. The service carries every request and can see what goes
through it. Keep autonomous mode off while it's on, so every command
still waits for your yes, and turn it off when you're done. If the link
may have got out, Security, "Sign out all other browsers" makes a new
one. Flash answers only to the tunnel's own address, not to any other
under the same service.

A browser that opens the link is signed in by a cookie until Flash
stops, so each new `flash --web` takes its new link. To skip that, turn
on Remember signed-in browsers under Settings, Security (off by
default). From then on a browser that has opened the link once opens
Flash at its plain address, `http://127.0.0.1:7433/` or whatever port it
runs on, across restarts, until you sign it out there or it goes 180
days without opening Flash. Flash keeps only a fingerprint of each
browser's cookie, in `~/.flash/web-browsers.json`, which cannot be used
to sign in.

The suggestions use the [Orbit](https://github.com/JAMO-TYPEFACE/Orbit)
typeface, bundled under the SIL Open Font License
(`flash/web/OFL-orbit.txt`).

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
