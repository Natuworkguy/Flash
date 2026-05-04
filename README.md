# ShellMind

ShellMind is a lightweight AI shell copilot for the terminal, powered by Gemini with tool use for command execution.

This repository is currently a small CLI-focused project, not a packaged product.

## Features

- Interactive CLI chat in the terminal
- Model-selected tool calling through a built-in shell tool
- Explicit direct shell commands via `!<command>`, `/run <command>`, and
  `/shell <command>`
- Bounded conversation history and tool-output truncation to limit prompt size
- Windows PowerShell handling for shell execution

## Requirements

- Python 3.13+
- A Gemini API key
- A Gemini model name

## Setup

1. Install dependencies:

   ```bash
   pip install -r reqirements.txt
   ```

2. Create a `.env` file in the project root:

   ```env
   API_KEY=your_api_key_here
   MODEL=your_model_name_here
   ```

## Usage

Start the CLI:

```bash
python run.py
```

Built-in commands:

- `/help` or `/?` shows command help
- `/model` prints the active model name
- `/clear` clears saved conversation context
- `/bye` exits the CLI
- `!<command>` runs a shell command directly
- `/run <command>` runs a shell command directly
- `/shell <command>` runs a shell command directly

Example conversation:

```text
[SM]> What files are in my current working dir?
Executing shell command: ls
The files and directories in your current working directory are:

- app/
- reqirements.txt
- run.py
```

Example direct shell command:

```text
[SM]> !ls
```

## How It Works

1. Your prompt is sent to Gemini.
2. The model can choose to call the built-in shell tool.
3. Tool results are sent back to the model so it can produce a final answer.
4. Direct shell commands such as `!ls` bypass the model and run immediately.

## Limitations

- The project currently uses the deprecated `google-generativeai` SDK.
- The tool surface is small and centered on shell execution.
- This repo does not currently include a packaged install flow, contributor
  guide, or test suite.

## Project Layout

- `run.py` - CLI entrypoint
- `app/ai.py` - chat loop, model interaction, and tool-follow-up handling
- `app/tools.py` - shell tool implementation and tool declarations
- `reqirements.txt` - current dependency list

## Next Steps

- Add automated tests
- Migrate from `google-generativeai` to a supported SDK
- Further modularize the CLI and expand the tool layer
