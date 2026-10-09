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

Only `name` is required, plus at least one command, tool, prompt,
backgrounds folder, or provider. Every path is relative to the extension's folder
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

### Providers

A provider brings models that Flash does not get from Ollama. Its
models are named `@provider/model`, such as `@openai/gpt-5`, and are used
like any other: `/model @openai/gpt-5`, the web UI's model menu, a
spark's model, or `MODEL` in `~/.flash.env`. They are listed beside
Ollama's, and are never downloaded.

```json
{
  "name": "openai",
  "providers": [
    {
      "name": "openai",
      "description": "OpenAI's hosted models",
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
{"models": [{"name": "gpt-5", "capabilities": ["completion", "tools", "vision"], "context": 400000}]}
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
with `/set OPENAI_API_KEY ...`, which saves it to `~/.flash.env`, and
the program sees it on its next run. Provider models are never counted
as running on your own machine in `/stats`, since Flash cannot see where
a provider sends them.

A provider for an API that speaks OpenAI's chat format, using only the
standard library:

```python
import json
import os
import sys
import urllib.request

BASE = "https://api.openai.com/v1"
KEY = os.environ.get("OPENAI_API_KEY", "")


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
    print(json.dumps({"error": "set OPENAI_API_KEY with /set"}))
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
