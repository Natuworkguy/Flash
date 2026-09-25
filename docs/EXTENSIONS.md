# Extensions

An extension adds to Flash without changing Flash. It can bring:

- **Slash commands** that send a prompt to the model, or run a program.
- **Tools** the model can call, answered by a program.
- **System prompt text** sent on every turn.
- **Backgrounds** for `/background`.

## Installing

From the shell:

```bash
flash --extension-install github@owner/repo
flash --extension-list
flash --extension-remove <name>
```

Or from inside a session:

```text
/extension install github@owner/repo
/extension                      # list what is installed
/extension remove <name>
```

`path@/some/folder` installs from a folder on disk instead of GitHub,
which is how you try out an extension while you are writing it.

Before anything is installed, Flash shows what the extension adds and
asks you to confirm. Installed extensions live in
`~/.flash/extensions/<name>`. To update one, install it again: Flash
replaces the old copy. If an extension's commands or tools clash with a
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
  "backgrounds": "scenes"
}
```

Only `name` is required, plus at least one command, tool, prompt, or
backgrounds folder. Every path is relative to the extension's folder
and cannot point outside it.

| Field | Meaning |
| --- | --- |
| `name` | Lowercase letters, digits, `-` and `_`. It is also the install folder's name. |
| `description`, `version` | Shown when installing and in `/extension`. |
| `prompt` | A text file added to the system prompt on every turn, up to 8000 characters. Keep it short: local models have small context windows. |
| `commands` | Slash commands. See below. |
| `tools` | Tools the model can call. See below. |
| `backgrounds` | A folder of `.scene` files, added to `/background`. |

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
  `FLASH_EXTENSION_DIR` set to the extension's folder.
- `timeout` sets a limit in seconds (default 30, maximum 600).

A tool in Python:

```python
import json
import sys

args = json.load(sys.stdin)
print(f"It is sunny in {args['city']}.")
```
