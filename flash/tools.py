"""AI Tool System"""

import fnmatch
import os
import platform
import re
import subprocess  # nosec B404
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Union

from ddgs import DDGS
from rich.text import Text

from .memory import add_memory, forget_memory, search_memory
from .notify import notify_needs_input
from .sysprompt import get_system_prompt
from .theme import ACCENT, DIM, ERROR, WARN, console, tool_line, tool_result

SCRATCH_DIR = mkdtemp(prefix="flash-scratch-", suffix="-temp")

TOOL_SYSTEM_PROMPT = f"""
=== Tool System Prompt ===
Answer concisely. Use shell only when command output is needed.
When using shell, call the tool without extra text first.
When searching a codebase or directory for files by name or pattern, use
  the glob tool instead of shell find/ls. When searching file contents for
  a pattern, use the grep tool instead of shell grep/rg. Both are read-only,
  faster, and work the same on every platform, so prefer them over shell
  for search whenever they cover the need.
When searching for recent information, use the web_search tool.
When you need to know the user's operating system, use the get_os tool.
To think or plan mid-task without ending your turn, use the reason tool.
When you need the current date, use the get_date tool.
To save a durable fact or preference for future sessions, use the remember
  tool. To check saved memory, use the recall tool with a specific phrase;
  it does not return everything for a blank search. To delete one saved
  memory by its 1-based index, use the forget tool.

Your temporary scratch directory is: {SCRATCH_DIR}
It will be deleted when the program exits. Use it for temporary files, but do
  not assume it will persist across runs.
Always use the scratch directory for temporary files, and never write to
  the user's home directory, other directories, or the current working
  directory unless explicitly asked.
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


# Directories that are rarely what a codebase search is looking for and
# can be huge (dependency trees, VCS internals, caches) -- pruned while
# walking so grep/glob stay fast and relevant.
_SEARCH_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode",
}
MAX_SEARCH_FILES = 5000
MAX_GREP_MATCHES = 200
MAX_GLOB_RESULTS = 500
MAX_MATCH_LINE_LENGTH = 300


def _should_skip_dir(name: str) -> bool:
    return name in _SEARCH_EXCLUDE_DIRS or name.endswith(".egg-info")


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return

    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for filename in filenames:
            count += 1
            if count > MAX_SEARCH_FILES:
                return
            yield Path(dirpath) / filename


def _relative_path(file_path: Path, root: Path) -> str:
    base = root if root.is_dir() else root.parent
    try:
        return file_path.relative_to(base).as_posix()
    except ValueError:
        return file_path.as_posix()


def glob_tool(pattern: str, path: str = ".") -> str:
    """Tool to find files by name pattern."""

    label = f"Glob({pattern})" + (f" in {path}" if path != "." else "")
    tool_line(label)

    root = Path(path).expanduser()
    if not root.exists():
        result = f"Error: path not found: {root}"
        tool_result(result, style=ERROR)
        return result

    matches = []
    for file_path in _iter_files(root):
        rel = _relative_path(file_path, root)
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
            file_path.name, pattern
        ):
            matches.append(rel)
            if len(matches) >= MAX_GLOB_RESULTS:
                break

    matches.sort()
    result = "\n".join(matches) if matches else "No files matched."
    if len(matches) >= MAX_GLOB_RESULTS:
        result += f"\n... truncated at {MAX_GLOB_RESULTS} matches"

    tool_result(
        f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
        if matches else "No matches."
    )
    return result


def grep_tool(
    pattern: str,
    path: str = ".",
    glob_filter: Union[str, None] = None,  # noqa: UP007, RUF100
    case_insensitive: bool = False,
) -> str:
    """Tool to search file contents by regex."""

    label = f"Grep({pattern})" + (f" in {path}" if path != "." else "")
    tool_line(label)

    root = Path(path).expanduser()
    if not root.exists():
        result = f"Error: path not found: {root}"
        tool_result(result, style=ERROR)
        return result

    try:
        regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as exc:
        result = f"Error: invalid regex: {exc}"
        tool_result(result, style=ERROR)
        return result

    matches = []
    files_matched = set()
    for file_path in _iter_files(root):
        rel = _relative_path(file_path, root)
        if glob_filter and not (
            fnmatch.fnmatch(rel, glob_filter)
            or fnmatch.fnmatch(file_path.name, glob_filter)
        ):
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            snippet = line.strip()
            if len(snippet) > MAX_MATCH_LINE_LENGTH:
                snippet = snippet[:MAX_MATCH_LINE_LENGTH] + "..."
            matches.append(f"{rel}:{lineno}: {snippet}")
            files_matched.add(rel)
            if len(matches) >= MAX_GREP_MATCHES:
                break
        if len(matches) >= MAX_GREP_MATCHES:
            break

    result = "\n".join(matches) if matches else "No matches."
    if len(matches) >= MAX_GREP_MATCHES:
        result += f"\n... truncated at {MAX_GREP_MATCHES} matches"

    tool_result(
        f"{len(matches)} match{'es' if len(matches) != 1 else ''} in "
        f"{len(files_matched)} file{'s' if len(files_matched) != 1 else ''}"
        if matches else "No matches."
    )
    return result


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


def remember(entry: str) -> str:
    """Save a fact or preference to persistent memory for future sessions."""

    tool_line(f"Remember({entry})")
    result = add_memory(entry)
    tool_result(result)
    return result


def recall(phrase: str) -> str:
    """Search saved memory for every entry containing a phrase."""

    tool_line(f"Recall({phrase})")
    matches = search_memory(phrase)
    result = (
        "\n".join(f"{i}. {entry}" for i, entry in matches)
        if matches
        else "No matching memories."
    )
    tool_result(result)
    return result


def forget(index: int) -> str:
    """Delete one saved memory entry by its 1-based index."""

    tool_line(f"Forget({index})")
    try:
        result = forget_memory(index)
    except IndexError as exc:
        result = str(exc)
    tool_result(result)
    return result


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
            "name": "glob",
            "description": (
                "Find files by name pattern (e.g. '*.py', '**/test_*.py'). "
                "Read-only and fast; prefer this over shell find/ls when "
                "searching a directory for files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern to match against each file's "
                            "path, e.g. '*.py' or 'flash/**/*.py'."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory to search. Defaults to the current "
                            "directory."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents for a regex pattern, returning each "
                "match as 'path:line: text'. Read-only and fast; prefer "
                "this over shell grep/rg when searching file contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "File or directory to search. Defaults to the "
                            "current directory."
                        ),
                    },
                    "glob_filter": {
                        "type": "string",
                        "description": (
                            "Optional glob pattern to only search matching "
                            "files, e.g. '*.py'."
                        ),
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Match case-insensitively.",
                    },
                },
                "required": ["pattern"],
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
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a fact or user preference to persistent memory so it "
                "is available in future sessions. Use it when the user "
                "tells you something worth remembering long-term, not for "
                "details only relevant to the current conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry": {
                        "type": "string",
                        "description": "The fact to remember, one sentence.",
                    }
                },
                "required": ["entry"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search saved memory for every entry containing a phrase. "
                "Use this before assuming something hasn't been "
                "remembered, or to check details saved in past sessions. "
                "Each result is returned as 'N. entry', where N is that "
                "entry's 1-based index; pass N to the forget tool to "
                "delete it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phrase": {
                        "type": "string",
                        "description": "Case-insensitive phrase to search.",
                    }
                },
                "required": ["phrase"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": (
                "Delete one saved memory entry by its 1-based index (the "
                "number shown next to it in recall's results; the first "
                "saved entry is index 1, not 0). Deletes exactly that one "
                "entry, never all of memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "1-based index of the entry to delete.",
                        "minimum": 1,
                    }
                },
                "required": ["index"],
            },
        },
    },
]


FUNCTIONS = {
    "shell": shell_tool,
    "glob": glob_tool,
    "grep": grep_tool,
    "web_search": web_search,
    "get_os": get_os,
    "reason": reason,
    "get_date": get_date,
    "remember": remember,
    "recall": recall,
    "forget": forget,
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
