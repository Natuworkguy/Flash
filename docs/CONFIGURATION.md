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
an API key. Instead, it connects to an Ollama server over HTTP, either on your
own machine (the default) or on another host.

Requirements:

- An Ollama server must be running and reachable at `OLLAMA_HOST`.
- The model named in `MODEL` must already be pulled on that server
  (`ollama pull <model>`).
- For tool calling (shell / web search / OS info) to work, choose a model that
  supports tools, such as `llama3.1`.
- For `/image` and the `view_image` tool to work, the model must also be
  vision-capable, such as `llama3.2-vision`.

## Options

| Variable | Required | Default | Minimum | Description |
| -------- | -------- | ------- | ------- | ----------- |
| `MODEL` | Yes | - | - | Name of the Ollama model to use, e.g. `llama3.1`, `qwen2.5`, `mistral`. Must be pulled on the target server. |
| `OLLAMA_HOST` | No | `http://localhost:11434` | - | Base URL of the Ollama server. Change this to switch from a local server to a remote one. |
| `MAX_HISTORY_MESSAGES` | No | `6` | `2` | Maximum number of chat messages kept in memory before the oldest are dropped. |
| `MAX_HISTORY_CHARS` | No | `3000` | `1000` | Maximum total characters of history kept. Older messages are dropped once this is exceeded. |
| `MAX_TOOL_ROUNDS` | No | `10` | `1` | Maximum number of tool-calling rounds allowed per request. |
| `MAX_TOOL_OUTPUT_CHARS` | No | `1200` | `500` | Tool output longer than this is truncated (middle removed) before being sent back to the model. |
| `MAX_OUTPUT_TOKENS` | No | `1024` | `128` | Maximum tokens the model may generate per response. Maps to Ollama's `num_predict` option. |
| `VOICE` | No | `0` | - | `1` turns voice mode on at startup: press Enter on an empty prompt to speak, and replies are read aloud. Usually set with `/voice on` rather than by hand. |
| `VOICE_VOSK_MODEL` | No | `vosk-model-small-en-us-0.15` | - | Name of the [Vosk model](https://alphacephei.com/vosk/models) used for listening. Downloaded to `~/.flash/models` on first use. |
| `VOICE_PIPER_VOICE` | No | `en_US-amy-medium` | - | Name of the [Piper voice](https://huggingface.co/rhasspy/piper-voices) used for speaking, as `locale-speaker-quality`. |
| `VOICE_SILENCE_SECONDS` | No | `1.2` | `0.2` | How long you have to stop talking before Flash decides your turn is over. |
| `VOICE_INTERRUPT_WORD` | No | `interrupt` | - | Word that stops a reply being read aloud when you say it over the top. Matched as a whole word; "stop talking" and "be quiet" always work as well. |
| `VOICE_NO_SPEECH_SECONDS` | No | `8` | `1` | How long a listening turn waits for you to start speaking before handing the prompt back. This is what ends a hands-free conversation. |
| `VOICE_SILENCE_THRESHOLD` | No | `500` | `0` | Loudness (0-32768) above which audio counts as speech. Raise it in a noisy room; lower it if a quiet voice is missed. |
| `VOICE_MAX_CHARS` | No | `700` | `80` | Longest reply spoken aloud. Past this the voice stops at a sentence and says the rest is on screen. |

### Notes on the voice options

- Every `VOICE_*` option is read when it is used, so `/set` followed by
  `/refresh` changes voice mode without restarting Flash, including
  swapping the voice or the listening model.
- An unreadable value (a typo, a unit like `8 seconds`) falls back to the
  default rather than failing.

- Voice mode needs the `vosk`, `piper-tts`, and `sounddevice` packages.
  The installers add them for every install; `/voice on` prints the right
  command if they are somehow missing, and downloads the two models the
  first time it runs.
- Voice mode listens again after each reply, so a conversation continues
  without a keypress. It stops when you stay quiet for
  `VOICE_NO_SPEECH_SECONDS`, press Ctrl+C, or leave voice mode.
- While a reply is being read aloud, Flash listens for
  `VOICE_INTERRUPT_WORD`. Saying it cuts the reply off and starts
  listening for your next message.
- Saying "voice off" (or "exit voice mode", "stop listening") ends the
  conversation and hands the prompt back, leaving `VOICE` set so Enter
  starts talking again. Typing `/voice off` is what turns the feature off,
  since a dictated slash command cannot reach Flash.
- The models live in `~/.flash/models`. Deleting that directory frees the
  space; the next `/voice on` downloads them again.

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

```FLASH
/model
```

This prints the active model and the Ollama host FLASH is connected to.
