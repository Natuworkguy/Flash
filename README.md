# ShellMind CLI

ShellMind CLI is an AI-powered command-line interface that allows you to interact with generative AI models while having the ability to execute shell commands directly or through the AI.

## Features

- **Interactive AI Chat**: Chat with advanced generative models directly from your terminal.
- **Shell Command Execution**:
  - AI can use a `shell` tool to execute commands and see their output.
  - Manually execute shell commands using prefixes like `!`, `/run`, or `/shell`.
- **Context Management**: Automatic history trimming to stay within token limits.
- **Markdown Support**: Rich formatting for AI responses in the terminal.

## Installation

1. **Clone the repository**:

   ```bash
   git clone <repository-url>
   cd shellmind
   ```

2. **Install dependencies**:

   ```bash
   pip install -r requirements.txt
   ```

### Quick Install & Run (Windows)

For a quick build, install, and run cycle, you can use the following one-liner (replace `[VERSION]` with the actual version number, e.g., `0.1.0`):

```powershell
py.exe -m build; pip install .\dist\shellmind-[VERSION]-py3-none-any.whl --force-reinstall; py.exe -m shellmind
```

**Explanation:**

- `py.exe -m build`: Builds the distribution packages (like the `.whl` file) from ShellMind's source code.
- `pip install .\dist\shellmind-[VERSION]-py3-none-any.whl --force-reinstall`: Installs the generated wheel file, ensuring any previous version is replaced. Replace `[VERSION]` with the version found in `pyproject.toml` or the `dist` folder.
- `py.exe -m shellmind`: Launches the ShellMind application.

## Configuration

ShellMind CLI requires configuration through environment variables. You can create a `shellmind.env` file in your home directory:

```env
API_KEY=your_google_ai_api_key
MODEL=gemini-1.5-flash
```

### Environment Variables

| Variable | Description | Default |
|---------- |-------------|--------|
| `API_KEY` | **Required**. Your Google Generative AI API key. | None |
| `MODEL` | **Required**. The model name (e.g., `gemini-1.5-flash`). | None |
| `MAX_HISTORY_MESSAGES` | Maximum number of messages kept in history. | 6 |
| `MAX_HISTORY_CHARS` | Maximum characters kept in history. | 3000 |
| `MAX_TOOL_ROUNDS` | Maximum tool-calling rounds per request. | 4 |
| `MAX_TOOL_OUTPUT_CHARS` | Maximum characters for tool output before truncation. | 1200 |
| `MAX_OUTPUT_TOKENS` | Maximum tokens for AI response. | 512 |

## Usage

Start the CLI by running:

```bash
python run.py
```

### Internal Commands

- `/help` or `/?`: Display the help message.
- `/model`: Show the currently active model.
- `/clear`: Clear the conversation history.
- `/bye`: Exit the application.

### Direct Shell Execution

You can run shell commands directly without AI intervention:

- `!ls -la`
- `/run git status`
- `/shell echo "Hello"`

### AI Interaction

Simply type your request. If the AI needs to see the contents of a file or run a command to answer your question, it can invoke the shell tool automatically.
