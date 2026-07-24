# Configuration

FLASH CLI is configured entirely through environment variables. Values are
read once at startup from your shell environment and from an optional
`.flash.env` file in your home directory.

## Where configuration is loaded from

1. Real environment variables (highest priority).
2. A `.flash.env` file in your home directory
   (`~/.flash.env`, e.g. `C:\Users\<you>\.flash.env` on Windows).

If a variable is set in both places, the real environment variable wins.

Example `~/.flash.env`:

```env
MODEL=llama3.1
OLLAMA_HOST=http://localhost:11434
```

## Backend

FLASH uses [Ollama](https://ollama.com) as its backend. It does not require
an API key. Instead, it connects to an Ollama server over HTTP — either on your
own machine (the default) or on another host.

Requirements:

- An Ollama server must be running and reachable at `OLLAMA_HOST`.
- The model named in `MODEL` must already be pulled on that server
  (`ollama pull <model>`).
- For tool calling (shell / web search / OS info) to work, choose a model that
  supports tools, such as `llama3.1`.

## Options

| Variable | Required | Default | Minimum | Description |
| -------- | -------- | ------- | ------- | ----------- |
| `MODEL` | Yes | — | — | Name of the Ollama model to use, e.g. `llama3.1`, `qwen2.5`, `mistral`. Must be pulled on the target server. |
| `OLLAMA_HOST` | No | `http://localhost:11434` | — | Base URL of the Ollama server. Change this to switch from a local server to a remote one. |
| `MAX_HISTORY_MESSAGES` | No | `6` | `2` | Maximum number of chat messages kept in memory before the oldest are dropped. |
| `MAX_HISTORY_CHARS` | No | `3000` | `1000` | Maximum total characters of history kept. Older messages are dropped once this is exceeded. |
| `MAX_TOOL_ROUNDS` | No | `10` | `1` | Maximum number of tool-calling rounds allowed per request. |
| `MAX_TOOL_OUTPUT_CHARS` | No | `1200` | `500` | Tool output longer than this is truncated (middle removed) before being sent back to the model. |
| `MAX_OUTPUT_TOKENS` | No | `1024` | `128` | Maximum tokens the model may generate per response. Maps to Ollama's `num_predict` option. |

### Notes on the numeric options

- All numeric options are clamped to their listed **Minimum**. For example,
  setting `MAX_OUTPUT_TOKENS=10` is treated as `128`.
- Non-integer or empty values fall back to the listed **Default**.

## Switching between local and remote servers

The backend is switched purely with `OLLAMA_HOST`.

### Local (default)

Leave `OLLAMA_HOST` unset, or set it explicitly:

```env
MODEL=llama3.1
OLLAMA_HOST=http://localhost:11434
```

### Remote server

Point FLASH at another machine running Ollama:

```env
MODEL=llama3.1
OLLAMA_HOST=http://192.168.1.50:11434
```

Or a server behind a hostname / reverse proxy:

```env
MODEL=llama3.1
OLLAMA_HOST=https://ollama.example.com
```

When using a remote server:

- Ensure the Ollama server is started with network access enabled
  (for example by setting `OLLAMA_HOST=0.0.0.0:11434` on the **server** so it
  listens on all interfaces).
- Ensure any firewall allows access to the Ollama port (default `11434`).
- Ensure the model in `MODEL` has been pulled on that server.

## Verifying the active configuration

Inside FLASH, run:

```shellmind
/model
```

This prints the active model and the Ollama host FLASH is connected to.
