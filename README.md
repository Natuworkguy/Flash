# FLASH CLI

FLASH (**F**ast **L**ocal **A**gent **SH**ell) CLI is an AI-powered command-line interface that allows you to interact with local (or remote) [Ollama](https://ollama.com) models while having the ability to execute shell commands directly or through the AI.

## Features

- **Interactive AI Chat**: Chat with local or self-hosted models served by Ollama, directly from your terminal.
- **Switchable Backend**: Point Flash at `localhost` or any remote Ollama server via a single config option.
- **Shell Command Execution**:
  - AI can use a `shell` tool to execute commands and see their output.
  - Manually execute shell commands using the `!` prefix.
- **Context Management**: Automatic history trimming to stay within token limits.
- **Markdown Support**: Rich formatting for AI responses in the terminal.

## Installation

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

For a full reference of every option, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Environment Variables

| Variable | Description | Default | Minimum |
| -------- | ----------- | ------- | ------- |
| `MODEL` | **Required**. The Ollama model name (e.g., `llama3.1`). Must be pulled on the target server. | None | N/A |
| `OLLAMA_HOST` | Base URL of the Ollama server. Set this to point at a remote machine instead of localhost. | `http://localhost:11434` | N/A |
| `MAX_HISTORY_MESSAGES` | Maximum number of messages kept in history. | 6 | 2 |
| `MAX_HISTORY_CHARS` | Maximum characters kept in history. | 3000 | 1000 |
| `MAX_TOOL_ROUNDS` | Maximum tool-calling rounds per request. | 4 | 1 |
| `MAX_TOOL_OUTPUT_CHARS` | Maximum characters for tool output before truncation. | 1200 | 500 |
| `MAX_OUTPUT_TOKENS` | Maximum tokens for AI response (`num_predict`). | 512 | 128 |

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
- `/bye`: Exit the application.

### Direct Shell Execution

You can run shell commands directly without AI intervention:

- `!ls -la`
- `!git status`
- `!echo "Hello"`

### AI Interaction

Simply type your request. If the AI needs to see the contents of a file or run a command to answer your question, it can invoke the shell tool automatically. It can also search the web via Duck Duck Go and show it's reasoning.
