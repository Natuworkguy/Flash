"""AI Tool System"""

import os
import platform
import subprocess  # nosec B404
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from colorama import Fore, Style

from .sysprompt import get_system_prompt


TOOL_SYSTEM_PROMPT = """
=== Tool System Prompt ===
Answer concisely. Use shell only when command output is needed.
When using shell, call the tool without extra text first.
When searching for recent information, use the web_search tool.
When you need to know the user's operating system, use the get_os tool.
To think or plan mid-task without ending your turn, use the reason tool.
""".strip()

SYSTEM_PROMPT = f"""
{get_system_prompt()}
{TOOL_SYSTEM_PROMPT}
=== END OF SYSTEM PROMPT ===
""".strip()


DEFAULT_SHELL_TIMEOUT = 15
MAX_SHELL_TIMEOUT = 600


def _shell_timeout(timeout) -> int:
    if timeout is None:
        return DEFAULT_SHELL_TIMEOUT

    try:
        seconds = int(float(timeout))
    except (TypeError, ValueError):
        return DEFAULT_SHELL_TIMEOUT

    return max(1, min(seconds, MAX_SHELL_TIMEOUT))


def shell_tool(command: str, timeout=None) -> str:
    """Tool to execute a shell command"""

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
            )
        else:
            result = subprocess.run(
                command,
                shell=True,  # nosec B602
                capture_output=True,
                text=True,
                timeout=seconds,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return (
            f"Error: Command timed out after {seconds} seconds. "
            "Please note that shell commands are run non-interactively. "
            "If the command was simply slow rather than stuck, retry it with "
            "a larger timeout."
        )

    parts = [result.stdout.strip(), result.stderr.strip()]
    output = "\n".join(part for part in parts if part)

    if result.returncode and output:
        return f"(exit {result.returncode})\n{output}"

    return output or "(no output)"


class DuckDuckGoSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list = []
        self._current = None
        self._capture_title = False
        self._capture_snippet = False

    def _finish_current(self) -> None:
        if self._current is None:
            return

        if self._current["title"].strip() and self._current["href"]:
            self.results.append(self._current)

        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        class_name = attrs.get("class") or ""

        if tag == "a" and "result__a" in class_name:
            # A new result starts here, so bank the previous one first.
            self._finish_current()
            self._current = {
                "title": "",
                "href": attrs.get("href", ""),
                "snippet": ""
            }
            self._capture_title = True
        elif self._current and "result__snippet" in class_name:
            # Snippets come back as <a>, but have been <div>/<span> before.
            self._capture_snippet = True

    def handle_data(self, data):
        if self._capture_title and self._current is not None:
            self._current["title"] += data
        elif self._capture_snippet and self._current is not None:
            self._current["snippet"] += data

    def handle_endtag(self, tag):
        if self._capture_title and tag == "a":
            self._capture_title = False
        if self._capture_snippet and tag in {"a", "div", "span"}:
            self._capture_snippet = False

    def close(self) -> None:
        super().close()
        self._finish_current()


def web_search(query: str) -> str:
    """Search the web and return the top DuckDuckGo results."""

    print(Fore.BLUE + f"Searching the web for: {query}" + Style.RESET_ALL)

    # DuckDuckGo answers GET with an anti-bot challenge (HTTP 202) and no
    # results, so the query has to be POSTed as form data instead.
    request = Request(
        "https://html.duckduckgo.com/html/",
        data=urlencode({"q": query}).encode(),
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urlopen(request, timeout=15) as response:  # nosec B310
            status = response.status
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        return f"Web search failed: {exc}"

    if status != 200:
        return (
            f"Web search failed: DuckDuckGo returned HTTP {status} instead of "
            "results. The search backend is unavailable, so nothing was "
            "searched. This does not mean the topic has no results."
        )

    parser = DuckDuckGoSearchParser()
    parser.feed(html)
    parser.close()

    if not parser.results:
        return "No web search results found."

    output_lines = []
    for index, result in enumerate(parser.results[:5], start=1):
        title = result["title"].strip().replace("\n", " ")
        snippet = result["snippet"].strip().replace("\n", " ")
        href = result["href"].strip()
        output_lines.append(f"{index}. {title}\n{href}\n{snippet}")

    return "\n\n".join(output_lines)


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
                    }
                },
                "required": ["query"],
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
]


FUNCTIONS = {
    "shell": shell_tool,
    "web_search": web_search,
    "get_os": get_os,
    "reason": reason,
}


def run_tool(call):
    """Run a tool with arguments"""

    name, args = call

    func = FUNCTIONS.get(name)
    if func is None:
        return f"Unknown tool: {name}"

    try:
        return func(**args)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"{e.__class__.__name__}: {e}"
