# FLASH CLI

FLASH (**F**ast **L**ocal **A**gent **SH**ell) CLI is an AI-powered command-line interface that allows you to interact with local (or remote) [Ollama](https://ollama.com) models while having the ability to execute shell commands directly or through the AI.

[Watch the video on YouTube](https://www.youtube.com/watch?v=padyQR3tPUs)

## Features

- **Interactive AI Chat**: Chat with local or self-hosted models served by Ollama, directly from your terminal.
- **Switchable Backend**: Point Flash at `localhost` or any remote Ollama server via a single config option.
- **Shell Command Execution**:
  - AI can use a `shell` tool to execute commands and see their output.
  - Manually execute shell commands using the `!` prefix.
- **`flash://` Links**: Open Flash from a browser or another app with a prompt ready to go (`flash://?prompt=What+is+Python`).
- **Image Recognition**: Send a local image to a vision-capable model with `/image <path> [prompt]`, or let the AI open one itself with its `view_image` tool.
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

**Flash Onyx** is a series of custom Ollama models built for Flash. The current
release, [**Flash Onyx 1**](https://ollama.com/Natuworkguy/flash-onyx-1), is `llama3.1` with Flash's persona and tuned
parameters baked in.

Pull it straight from the registry:

```bash
ollama pull Natuworkguy/flash-onyx-1
```

Or build it from the repo:

```bash
ollama create flash-onyx-1 -f models/flash-onyx-1.Modelfile
```

Then set `MODEL` to whichever you used (`Natuworkguy/flash-onyx-1` or
`flash-onyx-1`) in `~/.flash.env` or your environment.

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
- `/model`: Show the currently active model and Ollama host.
- `/clear`: Clear the conversation history.
- `/image <path> [prompt]`: Send a local image to the model.
- `/version`: Show the current version and check GitHub for updates.
- `/update`: Update Flash to the latest version (pipx installs only).
- `/bye`: Exit the application.

### Image Recognition

`/image <path> [prompt]` attaches a local image (`.png`, `.jpg`, `.jpeg`,
`.webp`, `.gif`, `.bmp`) to your next message and sends both to the model.
If you leave off the prompt, Flash asks it to describe the image. This
requires a vision-capable model — text-only models will ignore the image
or error. Pull one and switch to it first, e.g.:

```bash
ollama pull llama3.2-vision
```

```
/model llama3.2-vision
/image ~/Pictures/screenshot.png What's going on in this UI?
```

The model can also open an image on its own with the `view_image` tool, so
you can just name the file in a normal message and let it look:

```
Why does the legend in ~/Desktop/plot.png overlap the bars?
```

It accepts the same file types (up to 20 MB) and sees the image for that
turn only, calling `view_image` again later if it needs another look.

### Updates

Flash checks `main` on GitHub for a newer version on startup and shows it
in the banner if one is available. Run `/version` anytime to check on
demand, or `/update` to install it. Flash re-runs the same pipx-based
steps `install.sh` uses, so it only works for installs done via the
quick-install script. If you cloned the repo manually, update with
`git pull` instead.

You can also check and update from outside the REPL:

```bash
flash --update          # check for a newer version and, if found, confirm and install it
flash --update --force  # reinstall from `main` unconditionally, no confirmation
```

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
the prompt and asks before sending it to the model — any web page can open a
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
