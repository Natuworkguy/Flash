"""AI Tool System"""

import os
import platform
import subprocess  # nosec B404
from datetime import datetime
from tempfile import mkdtemp

from ddgs import DDGS
from rich.text import Text

from .notify import notify_needs_input
from .sysprompt import get_system_prompt
from .theme import ACCENT, DIM, ERROR, WARN, console, tool_line, tool_result

SCRATCH_DIR = mkdtemp(prefix="flash-scratch-", suffix="-temp")

TOOL_SYSTEM_PROMPT = f"""
=== Tool System Prompt ===
Answer concisely. Use shell only when command output is needed.
When using shell, call the tool without extra text first.
When searching for recent information, use the web_search tool.
When you need to know the user's operating system, use the get_os tool.
To think or plan mid-task without ending your turn, use the reason tool.
When you need the current date, use the get_date tool.

Your temporary scratch directory is: {SCRATCH_DIR}
It will be deleted when the program exits. Use it for temporary files, but do
  not assume it will persist across runs.
Always use the scratch directory for temporary files, and never write to
  the user's home directory, other directories, or the current working
  directory.
""".strip()

now = datetime.now()  # noqa: DTZ005

CURRENT_DATE_PROMPT = f"""
=== Current Date ===
Today's real-world date is {now.date().isoformat()}.
Treat the year above as the present year in every reply. When a search is
time-sensitive, put THIS year into the query (for example "best Nvidia GPU
{now.year}"); never a year recalled from training data. This date is
authoritative, so you do not need to call get_date to confirm the current year,
only to get a more precise day if a task needs one.
""".strip()

SYSTEM_PROMPT = f"""
{get_system_prompt()}
{TOOL_SYSTEM_PROMPT}
{CURRENT_DATE_PROMPT}
=== END OF SYSTEM PROMPT ===

You are now being transferred to a user.
""".strip()


DEFAULT_SHELL_TIMEOUT = 15
MAX_SHELL_TIMEOUT = 600
NO_COMMAND_CONFIRMATION = False


def init(config, ):
    global NO_COMMAND_CONFIRMATION
    NO_COMMAND_CONFIRMATION = config.no_command_confirmation


def _shell_timeout(timeout) -> int:
    if timeout is None:
        return DEFAULT_SHELL_TIMEOUT

    try:
        seconds = int(float(timeout))
    except (TypeError, ValueError):
        return DEFAULT_SHELL_TIMEOUT

    return max(1, min(seconds, MAX_SHELL_TIMEOUT))


def shell_tool(command: str, timeout=None, is_user=False) -> str:
    """Tool to execute a shell command"""

    seconds = _shell_timeout(timeout)

    if not is_user:
        suffix = "" \
            if seconds == DEFAULT_SHELL_TIMEOUT \
            else f" (timeout {seconds}s)"
        tool_line(f"Bash({command}){suffix}")

        if not NO_COMMAND_CONFIRMATION:
            notify_needs_input()

            prompt = Text("  ⎿  ", style=DIM)
            prompt.append("Run this command? ", style=DIM)
            prompt.append("y", style=f"bold {ACCENT}")
            prompt.append("/n ", style=DIM)
            console.print(prompt, end="")

            user_input = input().strip().lower()

            if user_input != "y":
                tool_result("Command blocked by user", style=WARN)
                return "Command blocked by user"

    try:
        if os.name == "nt":
            args = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
            result = subprocess.run(  # nosec B603
                args,
                capture_output=True,
                text=True,
                timeout=seconds,
                check=False,
                stdin=subprocess.DEVNULL if not is_user else None,
            )
        else:
            result = subprocess.run(
                command,
                shell=True,  # nosec B602
                capture_output=True,
                text=True,
                timeout=seconds,
                check=False,
                stdin=subprocess.DEVNULL if not is_user else None,
            )
    except subprocess.TimeoutExpired:
        message = (
            f"Error: Command timed out after {seconds} seconds. "
            "Please note that shell commands are run non-interactively. "
            "If the command was simply slow rather than stuck, retry it with "
            "a larger timeout."
        )
        if not is_user:
            tool_result(message, style=ERROR)
        return message
    except KeyboardInterrupt:
        return "Error: Command execution interrupted by user."

    parts = [result.stdout.strip(), result.stderr.strip()]
    output = "\n".join(part for part in parts if part)

    if result.returncode and output:
        final = f"(exit {result.returncode})\n{output}"
    else:
        final = output or "(no output)"

    if not is_user:
        tool_result(final, style=ERROR if result.returncode else DIM)

    return final


def web_search(query: str, max_results: int) -> str:
    """Search the web and return the top DuckDuckGo results."""

    tool_line(f"Search({query})")

    results = ""

    for result in DDGS().text(query, max_results=max_results):
        block = f"""
- {result['title'] or 'No title'}
  "{result['body'] or 'No description'}"
  URL: {result['href'] or 'No URL'}
""".strip()
        results += ("\n\n" if results else "") + block

    count = results.count("\n\n") + 1 if results else 0
    tool_result(
        f"{count} result{'s' if count != 1 else ''}"
        if count else "No results found."
    )

    return results or "No results found."


def get_os() -> str:
    """Return a brief description of the user's operating system."""

    tool_line("GetOS()")

    info = (
        f"OS: {platform.system()} {platform.release()}\n"
        f"Platform: {platform.platform()}\n"
        f"Architecture: {platform.machine()}"
    )
    tool_result(info)

    return info


def reason(thought: str) -> str:
    """Show the user a line of reasoning without ending the turn."""

    console.print(Text(f"\n{thought}\n", style=f"italic {DIM}"))
    return "(noted)"


def get_date() -> str:
    """Return the current date using the local timezone."""

    tool_line("GetDate()")

    today = datetime.now().date().isoformat()  # noqa: DTZ005
    tool_result(today)

    return today


# Tool schema expected by Ollama function calling (OpenAI-style).
tools = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Optional seconds to wait before killing the "
                            f"command. Defaults to {DEFAULT_SHELL_TIMEOUT}. "
                            "Omit it unless you expect the command to be "
                            "slow, such as an install, build, or test run. "
                            f"Maximum {MAX_SHELL_TIMEOUT}."
                        ),
                        "minimum": 1,
                        "maximum": MAX_SHELL_TIMEOUT,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web and return summarized results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "Maximum number of results to return. "
                        ),
                        "minimum": 1,
                    },
                },
                "required": ["query", "max_results"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_os",
            "description": "Return the operating system and platform info.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reason",
            "description": (
                "Share a short line of reasoning or a plan with the user "
                "without ending your turn. Produces no command output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "The reasoning to show.",
                    }
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": (
                "Return the current date in YYYY-MM-DD format using the "
                "local timezone."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


FUNCTIONS = {
    "shell": shell_tool,
    "web_search": web_search,
    "get_os": get_os,
    "reason": reason,
    "get_date": get_date,
}


def run_tool(call):
    """Run a tool with arguments"""

    name, args = call

    func = FUNCTIONS.get(name)
    if func is None:
        return f"Unknown tool: {name}"

    try:
        return func(**args)
    except Exception as e:  # noqa: BLE001
        return f"{e.__class__.__name__}: {e}"
