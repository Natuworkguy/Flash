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


def shell_tool(command: str) -> str:
    """Tool to execute a shell command"""

    print(Fore.BLUE + f"Executing shell command: {command}" + Style.RESET_ALL)

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
                timeout=15,
                check=False,
            )
        else:
            result = subprocess.run(
                command,
                shell=True,  # nosec B602
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return (
            "Error: Command timed out after 15 seconds. "
            "Please note that shell commands are run non-interactively."
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

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        class_name = attrs.get("class", "")

        if tag == "a" and class_name is not None and "result__a" in class_name:
            self._current = {
                "title": "",
                "href": attrs.get("href", ""),
                "snippet": ""
            }
            self._capture_title = True
        elif \
                self._current \
                and tag in {"div", "span"} \
                and class_name is not None \
                and "result__snippet" in class_name:
            self._capture_snippet = True

    def handle_data(self, data):
        if self._capture_title and self._current is not None:
            self._current["title"] += data
        elif self._capture_snippet and self._current is not None:
            self._current["snippet"] += data

    def handle_endtag(self, tag):
        if self._capture_title and tag == "a" and self._current is not None:
            self._capture_title = False
            if self._current["title"].strip() and self._current["href"]:
                self.results.append(self._current)
            self._current = None
        if self._capture_snippet and tag in {"div", "span"}:
            self._capture_snippet = False


def web_search(query: str) -> str:
    """Search the web and return the top DuckDuckGo results."""

    print(Fore.BLUE + f"Searching the web for: {query}" + Style.RESET_ALL)
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        },
    )

    try:
        with urlopen(request, timeout=15) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        return f"Web search failed: {exc}"

    parser = DuckDuckGoSearchParser()
    parser.feed(html)

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
                    }
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
