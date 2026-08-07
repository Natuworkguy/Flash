"""AI Tool System"""

import os
import platform
import subprocess  # nosec B404
from datetime import datetime
from tempfile import mkdtemp

from colorama import Fore, Style
from ddgs import DDGS
from rich.console import Console
from rich.markdown import Markdown

from .sysprompt import get_system_prompt

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

SYSTEM_PROMPT = f"""
{get_system_prompt()}
{TOOL_SYSTEM_PROMPT}
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

    if not is_user and not NO_COMMAND_CONFIRMATION:
        Console().print(Markdown(
            Fore.YELLOW +
            "Flash is trying to execute "
            f"`{command}`. " +
            Fore.YELLOW +
            "Do you approve it to run? [Y/N, default n] " +
            Style.RESET_ALL
        ), end="")

        print(Fore.YELLOW + ">>> " + Fore.GREEN + Style.BRIGHT, end="")

        user_input = input().strip().lower()
        print(Style.RESET_ALL)

        if user_input != "y":
            return "Command blocked by user"

    seconds = _shell_timeout(timeout)

    suffix = "" \
        if seconds == DEFAULT_SHELL_TIMEOUT \
        else f" (timeout {seconds}s)"

    print(
        Fore.BLUE
        + f"Executing shell command: {command}{suffix}"
        + Style.RESET_ALL
    )

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
        return (
            f"Error: Command timed out after {seconds} seconds. "
            "Please note that shell commands are run non-interactively. "
            "If the command was simply slow rather than stuck, retry it with "
            "a larger timeout."
        )
    except KeyboardInterrupt:
        return "Error: Command execution interrupted by user."

    parts = [result.stdout.strip(), result.stderr.strip()]
    output = "\n".join(part for part in parts if part)

    if result.returncode and output:
        return f"(exit {result.returncode})\n{output}"

    return output or "(no output)"


def web_search(query: str, max_results: int) -> str:
    """Search the web and return the top DuckDuckGo results."""

    print(
        Fore.BLUE +
        f"Searching the web for: {query} " +
        f"(Got first {max_results} results)" +
        Style.RESET_ALL
    )

    results = ""

    for result in DDGS().text(query, max_results=max_results):
        results += f"""
- {result['title'] or 'No title'}
  "{result['body'] or 'No description'}"
  URL: {result['href'] or 'No URL'}
""".strip()

    return results or "No results found."


def get_os() -> str:
    """Return a brief description of the user's operating system."""

    print(
        Fore.BLUE + "Retrieving operating system information" + Style.RESET_ALL
    )

    return (
        f"OS: {platform.system()} {platform.release()}\n"
        f"Platform: {platform.platform()}\n"
        f"Architecture: {platform.machine()}"
    )


def reason(thought: str) -> str:
    """Show the user a line of reasoning without ending the turn."""

    print(Fore.MAGENTA + f"Thinking: {thought}" + Style.RESET_ALL)
    return "(noted)"


def get_date() -> str:
    """Return the current date using the local timezone."""

    print(Fore.BLUE + "Retrieving current date" + Style.RESET_ALL)

    return datetime.now().date().isoformat()  # noqa: DTZ005


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
