# ShellMind CLI

> **Bridging the gap between Natural Language and the Command Line.**

ShellMind CLI is a sophisticated, AI-enhanced terminal interface designed to streamline your workflow. It integrates powerful generative AI models directly into your shell environment, allowing you to converse with an intelligent assistant that doesn't just talk, but *acts*.

Whether you need to explain a complex error message, automate repetitive tasks, or explore your file system using natural language, ShellMind CLI provides the tools to do it efficiently.

## ✨ Key Features

- **🤖 Intelligent AI Interaction**: Seamlessly chat with state-of-the-art generative models (like Gemini) optimized for technical tasks and terminal environments.
- **🛠️ Integrated Shell Execution**:
    - **Autonomous Tooling**: The AI can autonomously decide to run shell commands to gather information, inspect files, or perform actions to better answer your queries.
    - **Direct Command Passthrough**: Execute shell commands manually using `!`, `/run`, or `/shell` prefixes without leaving the interactive session.
- **🧠 Smart Context Management**: Intelligently manages conversation history and token limits, ensuring long-running sessions remain responsive and relevant.
- **🎨 Rich Terminal Experience**:
    - Full **Markdown** support for beautifully formatted AI responses.
    - Vibrant syntax highlighting for code blocks.
    - Intuitive, color-coded interface for clear distinction between user input, AI responses, and tool outputs.
- **⚙️ Flexible Configuration**: Easily customize model parameters, history limits, and API settings via environment variables or an `.env` file.

## 🚀 Getting Started

### Installation

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd shellmind
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

### Configuration

ShellMind CLI uses environment variables for configuration. You can set these in your shell or create a `shellmind.env` file in your home directory (`~/shellmind.env`):

```env
API_KEY=your_google_ai_api_key
MODEL=gemini-1.5-flash
```

#### Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `API_KEY` | **Required**. Your Google Generative AI API key. | None |
| `MODEL` | **Required**. The model name (e.g., `gemini-1.5-flash`). | None |
| `MAX_HISTORY_MESSAGES` | Maximum number of messages kept in history. | 6 |
| `MAX_HISTORY_CHARS` | Maximum characters kept in history. | 3000 |
| `MAX_TOOL_ROUNDS` | Maximum tool-calling rounds per request. | 4 |
| `MAX_TOOL_OUTPUT_CHARS` | Maximum characters for tool output before truncation. | 1200 |
| `MAX_OUTPUT_TOKENS` | Maximum tokens for AI response. | 512 |

## 💡 Usage

Launch the interface:

```bash
python run.py
```

### Internal Commands

-   `/help` or `/?`: Display the interactive help menu.
-   `/model`: Display the currently active AI model.
-   `/clear`: Clear the conversation history to start fresh.
-   `/bye`: Exit ShellMind CLI.

### Direct Shell Execution

Run shell commands directly from the prompt:
-   `!ls -la`
-   `/run git status`
-   `/shell echo "Hello World"`

### AI Interaction

Simply type your request. ShellMind will leverage its capabilities to assist you. If a task requires external data, it will automatically use the shell tool to get the job done.

---
*Built with ❤️ by Natuworkguy*
