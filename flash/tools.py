"""AI Tool System"""

import base64
import difflib
import fnmatch
import io
import json
import os
import platform
import queue
import re
import struct
import subprocess  # nosec B404
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Union

from ddgs import DDGS
from rich.live import Live
from rich.text import Text

from . import agent as subagents
from . import checkpoint, editor, plan
from .browser import (
    ACTIONS,
    MAX_ELEMENTS,
    NO_PAGE,
    capture,
    close_session,
    resolve_target,
)
from .browser import drain_problems as page_problems
from .browser import elements as page_elements
from .browser import interact as browser_interact
from .browser import is_open as page_is_open
from .browser import open_page as browser_open
from .browser import snapshot as page_snapshot
from .browser import where as page_where
from .documents import extract_document_text, is_document_path
from .edit import Edit, apply_edits
from .images import resolve_image_path
from .memory import add_memory, forget_memory, search_memory
from .notify import notify_needs_input
from .sysprompt import get_system_prompt, model_sees_images
from .theme import (
    ACCENT,
    BRANCH,
    DIM,
    ELLIPSIS,
    ERROR,
    WARN,
    console,
    glimmer,
    plural,
    tool_diff,
    tool_line,
    tool_result,
)

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
To look at a file's contents, use the read tool instead of shell
  cat/sed/head/type. It numbers the lines and pages through long files with
  its offset argument. It also extracts text from .pdf and .docx files, so
  read them the same way; a legacy .doc file needs converting to .docx
  first.
A path the user writes after an @, such as @flash/models.py, is a file they
  are pointing you at. Read it before answering, unless what they asked
  plainly does not depend on what is in it.
To change a file that already exists, use the edit tool. It swaps one exact
  block of text for another and leaves the rest of the file untouched, so
  it costs you only the lines that actually change. Copy old_string out of
  a read of the file, without the line numbers read puts in front, and take
  in enough surrounding lines that it appears exactly once. Set
  replace_all=true only when you mean every occurrence. Use multi_edit to
  make several changes to one file in a single call; they are applied in
  order and either all land or none do. If an edit comes back not found,
  the error quotes the closest text in the file: fix old_string from it and
  call edit again rather than falling back to write or to a sed command.
To create a file, use the write tool instead of shell redirection,
  heredocs, or Set-Content. It needs no quoting or escaping and works the
  same on every platform, so shell quoting can never corrupt the content.
  It replaces the whole file, so point it at an existing one only when you
  mean to rewrite all of it. Your reply has a token limit, so a long file
  does not fit in one call: write the first part, then call write again
  with append=true for each following part, about 80 lines at a time,
  until the file is finished.
When searching for recent information, use the web_search tool.
When you need to know the user's operating system, use the get_os tool.
To think or plan mid-task without ending your turn, use the reason tool.
When you need the current date, use the get_date tool.
To look at an image file on disk, use the view_image tool with its path;
  it is the only way to see an image the user did not send with /image.
  Reading image bytes with shell or grep shows you nothing.
To hand a finished picture to the user, use the send_image tool with its
  path. It draws the image in their terminal where the terminal can draw
  one and opens it in their image viewer where it cannot, naming the path
  either way. It shows the image to them and not to you, so look at your
  own render with view_image first and send it once it is right.
To see how a web page actually renders, use the screenshot tool on the
  .html file you wrote or on a URL. It runs a headless browser and
  attaches the picture, so it is the only way to check a page you built;
  reading the source back shows you what you asked for, never what you
  got. Call it after writing a page, after every visual edit, and again
  after each fix, before you report the work done. width and height set
  the viewport (default 1280x800; use width=375 for the phone layout),
  full_page captures the whole scrollable page, and wait_ms gives a slow
  or animated page longer to settle. It also reports the JavaScript
  errors the page threw, which is what usually explains a blank section,
  so read those before changing any CSS. Serve the page over HTTP with
  shell first if it needs fetch or ES modules, which file:// blocks.
To click a button, fill in a form, or work out why a page misbehaves,
  open it with the open_page tool and then drive it with the interact
  tool, one action per call: click, fill, press, hover, select, scroll,
  wait, eval, back, reload, close. The browser stays open between calls,
  so the page keeps whatever state your last action put it in. Each call
  answers with the page's address, up to {MAX_ELEMENTS} numbered elements
  you can act on, and the JavaScript errors the page threw, and attaches
  a fresh screenshot, so you see the result of every action instead of
  guessing it. Act on an element by the number beside it; a CSS selector
  or the visible text works too. Those numbers are handed out again after
  every call, so use the newest list, never one from earlier in the
  conversation. The eval action runs JavaScript on the live page and
  returns the result, which is the quickest way to check state a picture
  cannot show, such as what a handler stored or what a value really is.
  Close the browser with the close action once the page is working.
When a request takes several steps, call the plan tool first with those
  steps, shortest useful list you can write. They appear to the user as a
  checklist of empty boxes. Then work the list in order, and call
  check_step with a step's number the moment that step is actually
  finished, so its box ticks in front of them. Tick each step as you go,
  never all of them at the end, and never before the work is done. Call
  plan again to replace the list if the task turns out to need different
  steps. Skip the plan entirely for anything you can finish in one or two
  tool calls; a checklist for a one-line answer is noise.
To work on independent pieces of a task at the same time, use the agent
  tool to start a sub-agent per piece, e.g. one for each of two unrelated
  research questions. It returns an ID at once and runs in the background.
  Prefer ending your turn over waiting for it: tell the user what you
  started, and when a sub-agent finishes you are woken with its answer in
  a sub-agent update, so you can report back then.
  Call agent_result only when this turn cannot go on without the answer;
  it waits for the sub-agent to finish.
  A sub-agent cannot talk to the user or start sub-agents of its own, and
  has the file, search, and web tools (plus shell and write in autonomous
  mode), so give it one clear, self-contained task rather than something
  needing back and forth. Skip it for anything you can just do yourself
  in a tool call or two.
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
=== System Prompt ===

{get_system_prompt()}
{TOOL_SYSTEM_PROMPT}
{CURRENT_DATE_PROMPT}
=== END OF SYSTEM PROMPT ===

You are now being transferred to a user.
""".strip()


def build_system_prompt(model_prompt: str = "") -> str:
    """Prepend the model's own system prompt to Flash's, when it has one."""

    model_prompt = model_prompt.strip()

    if not model_prompt:
        return SYSTEM_PROMPT

    return (
        "=== Model System Prompt ===\n\n"
        f"{model_prompt}\n\n"
        f"{SYSTEM_PROMPT}"
    )


DEFAULT_SHELL_TIMEOUT = 15
MAX_SHELL_TIMEOUT = 600
NO_COMMAND_CONFIRMATION = False
OLLAMA_HOST = ""
MODEL_NAME = ""
MAX_TOOL_OUTPUT_CHARS = 1200


def init(config, ):
    global NO_COMMAND_CONFIRMATION, OLLAMA_HOST, MODEL_NAME
    global MAX_TOOL_OUTPUT_CHARS
    NO_COMMAND_CONFIRMATION = config.no_command_confirmation
    OLLAMA_HOST = config.host
    MODEL_NAME = config.model or ""
    MAX_TOOL_OUTPUT_CHARS = config.max_tool_output_chars


# read already caps its own output by whole lines and tells the model how
# to page on; the middle-out trim below would silently gut a file read.
# The page tools cap themselves too, and their element list is only useful
# whole: a trim through the middle of it takes away the very numbers the
# next click has to name.
_SELF_LIMITING_TOOLS = {"read", "open_page", "interact"}


def trim_tool_output(text: str, name: str = "") -> str:
    """Cut a tool result down to MAX_TOOL_OUTPUT_CHARS, keeping both ends."""

    text = text.strip() or "(no output)"
    limit = MAX_TOOL_OUTPUT_CHARS

    if name in _SELF_LIMITING_TOOLS or len(text) <= limit:
        return text

    head_len = limit // 2
    tail_len = limit - head_len
    omitted = len(text) - limit

    return (
        text[:head_len]
        + f"\n\n... truncated {omitted} characters ...\n\n"
        + text[-tail_len:]
    )


def _run_shell_streaming(
    args, *, shell: bool, seconds: int
) -> tuple[str, int]:
    """Run a command, printing its output live as it's produced.

    Uses a background reader thread so the timeout can still be enforced
    while blocked on a line read (subprocess has no streaming timeout).
    """
    proc = subprocess.Popen(  # nosec B602 B603
        args,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    stdout = proc.stdout
    assert stdout is not None  # nosec B101 -- guaranteed by stdout=PIPE above

    # Read one character at a time rather than by line: a prompt like
    # "Proceed (Y/n)? " has no trailing newline, so readline() would block
    # on it -- holding it (and anything typed in response) out of order
    # until later output finally supplies a newline.
    output_queue: queue.Queue = queue.Queue()

    def reader():
        while True:
            chunk = stdout.read(1)
            if chunk == "":
                break
            output_queue.put(chunk)
        output_queue.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    start = time.monotonic()
    chunks = []
    try:
        while True:
            remaining = seconds - (time.monotonic() - start)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, seconds)
            try:
                chunk = output_queue.get(timeout=remaining)
            except queue.Empty:
                raise subprocess.TimeoutExpired(args, seconds)
            if chunk is None:
                break
            print(chunk, end="", flush=True)
            chunks.append(chunk)
    except BaseException:
        proc.kill()
        proc.wait()
        raise

    proc.wait()
    return "".join(chunks), proc.returncode


def _shell_timeout(timeout) -> int:
    if timeout is None:
        return DEFAULT_SHELL_TIMEOUT

    try:
        seconds = int(float(timeout))
    except (TypeError, ValueError):
        return DEFAULT_SHELL_TIMEOUT

    return max(1, min(seconds, MAX_SHELL_TIMEOUT))


MAX_USER_RUNS = 3
USER_RUNS_HEADER = (
    "=== Commands the user ran in Flash with ! since their last message ==="
)
USER_RUNS_FOOTER = "=== End of ! commands ==="

# What the user ran with `!` since their last message. Those runs stream
# straight to the terminal and never enter the conversation, so without
# this "why did that fail?" would reach a model that saw nothing.
_user_runs: list[str] = []


def _note_user_run(command: str, outcome: str, output: str) -> None:
    lines = [f"$ {command}", f"{outcome}:" if output.strip() else outcome]
    if output.strip():
        lines.append(trim_tool_output(output))
    _user_runs.append("\n".join(lines))
    del _user_runs[:-MAX_USER_RUNS]


def user_runs() -> str:
    """The `!` commands since the last message, as the block put before
    the next one, or "" when there were none."""

    if not _user_runs:
        return ""
    return "\n\n".join([USER_RUNS_HEADER, *_user_runs, USER_RUNS_FOOTER])


def clear_user_runs() -> None:
    """Forget the `!` commands once a message carrying them went through."""

    _user_runs.clear()


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

    args: list[str] | str
    if os.name == "nt":
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        shell = False
    else:
        args = command
        shell = True

    try:
        if is_user:
            # Commands typed directly by the user (via `!`) stream their
            # output live as it's produced, instead of waiting for the
            # whole command to finish before showing anything.
            output, returncode = _run_shell_streaming(  # nosec B604
                args, shell=shell, seconds=seconds
            )
            _note_user_run(command, f"exit {returncode}", output)
            if not output.strip():
                return "(no output)"
            if returncode:
                return f"(exit {returncode})"
            return ""
        if os.name == "nt":
            result = subprocess.run(  # nosec B603
                args,
                capture_output=True,
                text=True,
                timeout=seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        else:
            result = subprocess.run(
                args,
                shell=True,  # nosec B602
                capture_output=True,
                text=True,
                timeout=seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        message = (
            f"Error: Command timed out after {seconds} seconds. "
            "Please note that shell commands are run non-interactively. "
            "If the command was simply slow rather than stuck, retry it with "
            "a larger timeout."
        )
        if is_user:
            _note_user_run(command, f"timed out after {seconds}s", "")
        else:
            tool_result(message, style=ERROR)
        return message
    except KeyboardInterrupt:
        if is_user:
            _note_user_run(command, "interrupted with Ctrl+C", "")
        return "Error: Command execution interrupted by user."

    parts = [result.stdout.strip(), result.stderr.strip()]
    output = "\n".join(part for part in parts if part)

    if result.returncode and output:
        final = f"(exit {result.returncode})\n{output}"
    else:
        final = output or "(no output)"

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


DEFAULT_READ_LINES = 200
MAX_READ_LINES = 2000
MAX_READ_LINE_LENGTH = 2000
MAX_READ_OUTPUT_CHARS = 20000
MAX_DIFF_PREVIEW_LINES = 40


def _read_lines(file_path: Path) -> Union[list[str], str]:  # noqa: UP007
    """Split a text file into lines, or return an error string."""

    if is_document_path(file_path):
        text, reason = extract_document_text(file_path)
        if text is None:
            return f"Error: {reason}"
        return text.splitlines()

    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (
            f"Error: {file_path} is not a UTF-8 text file. Use view_image "
            "for images."
        )
    except OSError as exc:
        return f"Error: could not read {file_path}: {exc}"

    return text.splitlines()


def read_tool(
    path: str,
    offset: Union[int, None] = None,  # noqa: UP007, RUF100
    limit: Union[int, None] = None,  # noqa: UP007, RUF100
) -> str:
    """Tool to read a text file, numbered by line."""

    start = max(1, offset or 1)
    count = min(max(1, limit or DEFAULT_READ_LINES), MAX_READ_LINES)

    label = f"Read({path})"
    if offset or limit:
        label += f" lines {start}-{start + count - 1}"
    tool_line(label)

    file_path = Path(path).expanduser()
    if not file_path.exists():
        result = f"Error: file not found: {file_path}"
        tool_result(result, style=ERROR)
        return result
    if file_path.is_dir():
        result = f"Error: {file_path} is a directory, not a file."
        tool_result(result, style=ERROR)
        return result

    lines = _read_lines(file_path)
    if isinstance(lines, str):
        tool_result(lines, style=ERROR)
        return lines

    total = len(lines)
    if total == 0:
        tool_result("Empty file.")
        return "(empty file)"
    if start > total:
        result = (
            f"Error: offset {start} is past the end of {file_path} "
            f"({total} lines)."
        )
        tool_result(result, style=ERROR)
        return result

    selected = lines[start - 1:start - 1 + count]

    numbered = []
    chars = 0
    for offset_index, line in enumerate(selected):
        if len(line) > MAX_READ_LINE_LENGTH:
            line = line[:MAX_READ_LINE_LENGTH] + "..."
        entry = f"{start + offset_index:>6}\t{line}"
        chars += len(entry) + 1
        if chars > MAX_READ_OUTPUT_CHARS:
            break
        numbered.append(entry)

    last = start + len(numbered) - 1
    result = "\n".join(numbered)
    if last < total:
        result += (
            f"\n\n... {total - last} more line{plural(total - last)}; "
            f"read again with offset={last + 1} to continue."
        )

    tool_result(
        f"{len(numbered)} line{plural(len(numbered))} "
        f"({start}-{last} of {total})"
    )
    return result


def _diff_preview(old_text: str, new_text: str, name: str) -> tuple[
    list[str], int, int, int
]:
    """Unified diff of a pending write, capped for display.

    Returns (preview_lines, omitted_line_count, additions, removals).
    """

    diff = list(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=name,
        tofile=name,
        lineterm="",
        n=2,
    ))
    # Drop the ---/+++ header; the tool line already names the file.
    body = diff[2:] if len(diff) > 2 else diff

    # The ---/+++ header is already gone, so every +/- line is a real one.
    additions = sum(1 for line in body if line.startswith("+"))
    removals = sum(1 for line in body if line.startswith("-"))
    omitted = max(0, len(body) - MAX_DIFF_PREVIEW_LINES)

    return body[:MAX_DIFF_PREVIEW_LINES], omitted, additions, removals


def _read_exact(file_path: Path) -> Union[str, None]:  # noqa: UP007
    """The file's text exactly as it sits on disk, or None if unreadable."""

    # newline="" keeps the line endings exactly as they are on disk,
    # which is the whole point of reading it again here. Path.read_text
    # only learned that argument in 3.13, and Flash supports 3.10.
    try:
        with open(file_path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_tool(path: str, content: str, append: Any = False) -> str:
    """Tool to write a text file, showing a diff and asking to confirm."""

    adding = bool(append)

    tool_line(f"Write({path}, append)" if adding else f"Write({path})")

    file_path = Path(path).expanduser()
    if file_path.is_dir():
        result = f"Error: {file_path} is a directory, not a file."
        tool_result(result, style=ERROR)
        return result

    existed = file_path.exists()
    if existed:
        old_lines = _read_lines(file_path)
        if isinstance(old_lines, str):
            tool_result(old_lines, style=ERROR)
            return old_lines
        old_text = "\n".join(old_lines)
    else:
        old_text = ""

    # An append is confirmed as the whole file it will produce, so the
    # user sees the new lines in place rather than a fragment out of
    # context. The exact text matters: whether the file already ends in
    # a newline decides whether the first added line joins the last one.
    if adding and existed:
        exact = _read_exact(file_path)
        new_text = (old_text if exact is None else exact) + content
    else:
        new_text = content

    preview, omitted, additions, removals = _diff_preview(
        old_text, new_text, file_path.name
    )

    if not existed:
        new_lines = len(content.splitlines())
        summary = f"New file, {new_lines} line{plural(new_lines)}"
    elif not preview:
        summary = "No changes"
    else:
        summary = (
            f"{additions} addition{plural(additions)}, "
            f"{removals} removal{plural(removals)}"
        )
    tool_result(summary)
    tool_diff(preview, more=omitted)

    if not NO_COMMAND_CONFIRMATION:
        if (preview or not existed) and editor.show_diff(
            old_text, new_text, file_path.name, SCRATCH_DIR
        ):
            tool_result("Opened side by side in VS Code")

        notify_needs_input()

        prompt = Text(f"  {BRANCH}  ", style=DIM)
        prompt.append(
            "Append to this file? " if adding else "Write this file? ",
            style=DIM,
        )
        prompt.append("y", style=f"bold {ACCENT}")
        prompt.append("/n ", style=DIM)
        console.print(prompt, end="")

        if input().strip().lower() != "y":
            tool_result("Write blocked by user", style=WARN)
            return "Write blocked by user"

    checkpoint.record(file_path)

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the model's content lands byte-for-byte, instead of
        # every \n becoming \r\n on Windows.
        mode = "a" if adding else "w"
        with open(file_path, mode, encoding="utf-8", newline="") as handle:
            handle.write(content)
    except OSError as exc:
        result = f"Error: could not write {file_path}: {exc}"
        tool_result(result, style=ERROR)
        return result

    written = len(content.splitlines())

    if adding:
        total = len(new_text.splitlines())
        tool_result(f"Appended {written} line{plural(written)}")

        return (
            f"Appended {written} line{plural(written)} to {file_path}, "
            f"which now has {total} line{plural(total)}. Append the next "
            "piece the same way, or stop here if the file is finished."
        )

    verb = "Wrote" if existed else "Created"
    tool_result(f"{verb} {written} line{plural(written)} to {file_path}")
    return f"{verb} {written} line{plural(written)} to {file_path}"


def _confirm_change(
    file_path: Path,
    old_text: str,
    new_text: str,
    question: str,
) -> Union[str, None]:  # noqa: UP007, RUF100
    """Show the pending change and ask. None means go ahead.

    The same diff, editor window, and y/n the write tool uses, so an
    edit and a write look identical to the user however the model chose
    to make the change.
    """

    preview, omitted, additions, removals = _diff_preview(
        old_text, new_text, file_path.name
    )
    tool_result(
        f"{additions} addition{plural(additions)}, "
        f"{removals} removal{plural(removals)}"
    )
    tool_diff(preview, more=omitted)

    if NO_COMMAND_CONFIRMATION:
        return None

    if editor.show_diff(old_text, new_text, file_path.name, SCRATCH_DIR):
        tool_result("Opened side by side in VS Code")

    notify_needs_input()

    prompt = Text(f"  {BRANCH}  ", style=DIM)
    prompt.append(question + " ", style=DIM)
    prompt.append("y", style=f"bold {ACCENT}")
    prompt.append("/n ", style=DIM)
    console.print(prompt, end="")

    if input().strip().lower() != "y":
        tool_result("Edit blocked by user", style=WARN)
        return "Edit blocked by user"

    return None


def _editable_text(file_path: Path) -> str:
    """The file's exact text, or an "Error: ..." string explaining why not.

    Callers tell the two apart by the "Error: " prefix, the same way the
    rest of the tools in this module report a failure to the model.
    """

    if not file_path.exists():
        return (
            f"Error: file not found: {file_path}. Use the write tool to "
            "create a file; edit only changes one that already exists."
        )

    if file_path.is_dir():
        return f"Error: {file_path} is a directory, not a file."

    if is_document_path(file_path):
        return (
            f"Error: {file_path} is a document, not a text file. Its "
            "text can be read but not edited in place; rebuild it with "
            "the write tool or a script instead."
        )

    text = _read_exact(file_path)
    if text is None:
        return (
            f"Error: could not read {file_path} as UTF-8 text. Binary "
            "files cannot be edited."
        )

    return text


def _write_exact(
    file_path: Path, text: str
) -> Union[str, None]:  # noqa: UP007, RUF100
    """Replace the file's contents byte for byte. None on success."""

    try:
        # newline="" so the text lands exactly as the edit produced it,
        # which is what keeps a CRLF file from being rewritten as LF.
        with open(file_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except OSError as exc:
        return f"Error: could not write {file_path}: {exc}"

    return None


def _edit_file(path: str, edits: list[Edit], question: str) -> str:
    """Apply edits to one file, confirming the result as a diff."""

    file_path = Path(path).expanduser()

    old_text = _editable_text(file_path)
    if old_text.startswith("Error: "):
        tool_result(old_text, style=ERROR)
        return old_text

    result = apply_edits(old_text, edits)

    if not result.ok:
        tool_result(result.error, style=ERROR)
        return result.error

    new_text = result.text

    if new_text == old_text:
        message = (
            "No change: the edit produced text identical to what is "
            "already in the file."
        )
        tool_result(message)
        return message

    blocked = _confirm_change(file_path, old_text, new_text, question)
    if blocked is not None:
        return blocked

    # Snapshotted only once the user has said yes, so a declined edit
    # never lands in the undo history.
    checkpoint.record(file_path)

    failed = _write_exact(file_path, new_text)
    if failed is not None:
        tool_result(failed, style=ERROR)
        return failed

    made = result.replacements
    summary = (
        f"Made {made} replacement{plural(made)} in {file_path}"
    )
    tool_result(summary)

    return "\n".join([summary + ".", *result.notes])


def edit_tool(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: Any = False,
) -> str:
    """Tool to replace one exact block of text in a file."""

    tool_line(f"Edit({path})")

    return _edit_file(
        path,
        [Edit(str(old_string), str(new_string), bool(replace_all))],
        "Apply this edit?",
    )


def _parse_edits(edits: Any) -> Union[list[Edit], str]:  # noqa: UP007
    """Turn the model's edit list into Edit objects, or explain why not."""

    if isinstance(edits, str):
        # Some models hand back a JSON string instead of a real array.
        try:
            edits = json.loads(edits)
        except ValueError:
            return (
                "Error: edits could not be read as a list. Pass an array "
                'of {"old_string": ..., "new_string": ...} objects.'
            )

    if not isinstance(edits, list):
        return (
            "Error: edits must be a list of "
            '{"old_string": ..., "new_string": ...} objects.'
        )

    parsed = []
    for index, entry in enumerate(edits, start=1):
        if not isinstance(entry, dict):
            return (
                f"Error: edit {index} is not an object. Each edit needs "
                "an old_string and a new_string."
            )
        if "old_string" not in entry or "new_string" not in entry:
            return (
                f"Error: edit {index} is missing old_string or "
                "new_string."
            )
        parsed.append(Edit(
            str(entry["old_string"]),
            str(entry["new_string"]),
            bool(entry.get("replace_all", False)),
        ))

    return parsed


def multi_edit_tool(path: str, edits: Any) -> str:
    """Tool to make several exact edits to one file, all or nothing."""

    parsed = _parse_edits(edits)

    if isinstance(parsed, str):
        tool_line(f"MultiEdit({path})")
        tool_result(parsed, style=ERROR)
        return parsed

    count = len(parsed)
    tool_line(f"MultiEdit({path}, {count} edit{plural(count)})")

    return _edit_file(path, parsed, f"Apply these {count} edits?")


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
        f"{count} result{plural(count)}"
        if count else "No results found."
    )

    return results or "No results found."


FETCH_TIMEOUT_SECONDS = 20
FETCH_MAX_BYTES = 5_000_000
FETCH_MAX_CHARS = 20000
FETCH_USER_AGENT = "Mozilla/5.0 (compatible; FlashCLI)"
FETCH_SCHEMES = ("http://", "https://")

# Everything inside these is markup machinery, never page text.
_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template"})
# Tags whose content is a block, so it needs a line break around it.
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "tr", "li", "section", "article", "header",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
})
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_SPACES_RE = re.compile(r"[ \t]{2,}")


class _TextExtractor(HTMLParser):
    """Pulls the readable text out of a page, with its title and summary.

    Deliberately not a renderer. It keeps block boundaries so lists and
    paragraphs do not run together, drops script and style content, and
    leaves everything else to the reader. The head metadata is kept
    because a page that draws its body with JavaScript still says what
    it is up there, and that is worth more than an empty answer.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def _note_meta(self, attrs) -> None:
        """Keep the page summary, preferring the plain description over
        the social-card copy written for a preview box."""

        pairs = {name: (value or "") for name, value in attrs}
        kind = (pairs.get("name") or pairs.get("property") or "").lower()
        content = pairs.get("content", "").strip()

        if not content:
            return

        if kind not in ("description", "og:description"):
            return

        if kind == "description" or not self.description:
            self.description = content

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "meta":
            self._note_meta(attrs)
        elif tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data) -> None:
        if self._skip_depth:
            return

        if self._in_title:
            self.title += data.strip()
            return

        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [_SPACES_RE.sub(" ", line.strip()) for line in
                 joined.splitlines()]
        return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _readable(body: bytes, content_type: str) -> tuple[str, str, str]:
    """Return (title, description, text) for a body of CONTENT_TYPE."""

    charset = "utf-8"

    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip()

    try:
        decoded = body.decode(charset, "replace")
    except LookupError:
        decoded = body.decode("utf-8", "replace")

    if "html" not in content_type:
        # JSON, plain text, CSV and friends are already readable, and
        # running them through an HTML parser would eat the angle
        # brackets they use as data.
        return "", "", decoded.strip()

    parser = _TextExtractor()

    try:
        parser.feed(decoded)
        parser.close()
    except (AssertionError, ValueError):
        # A malformed page is still worth something, so keep whatever
        # was parsed before it broke rather than failing the call.
        pass

    return parser.title, parser.description, parser.text()


EMPTY_BODY_NOTE = (
    "This page builds its body with JavaScript, so the HTML carries "
    "nothing to read. Use screenshot to see it, or open_page to work "
    "with it."
)


def _head_only(
    final_url: str, title: str, description: str, content_type: str
) -> str:
    """What to say about a page whose body came back empty.

    The head still names and summarises the page, so hand that back with
    the reason the rest is missing, rather than reporting nothing and
    sending the reader away empty.
    """

    if not (title or description):
        result = (
            f"Error: {final_url} returned no readable text "
            f"(content type {content_type}). {EMPTY_BODY_NOTE}"
        )
        tool_result(result, style=ERROR)
        return result

    lines = [f"URL: {final_url}"]

    if title:
        lines.append(f"Title: {title}")

    if description:
        lines.append(f"Description: {description}")

    tool_result(f"head only, no body text: {title or final_url}", style=WARN)

    return "\n".join(lines) + f"\n\n{EMPTY_BODY_NOTE}"


def fetch(url: str) -> str:
    """Fetch a URL and return its readable text."""

    tool_line(f"Fetch({url})")

    address = url.strip()

    if not address.lower().startswith(FETCH_SCHEMES):
        result = (
            f"Error: fetch only handles http:// and https:// URLs, "
            f"got {address!r}."
        )
        tool_result(result, style=ERROR)
        return result

    request = urllib.request.Request(
        address,
        headers={"User-Agent": FETCH_USER_AGENT},
    )

    try:
        with urllib.request.urlopen(  # nosec B310 -- scheme checked above
            request, timeout=FETCH_TIMEOUT_SECONDS
        ) as response:
            content_type = response.headers.get_content_type()
            charset_header = response.headers.get("Content-Type", "")
            body = response.read(FETCH_MAX_BYTES)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        result = f"Error: {address} returned HTTP {exc.code} {exc.reason}."
        tool_result(result, style=ERROR)
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result = f"Error: could not fetch {address}: {exc}"
        tool_result(result, style=ERROR)
        return result

    title, description, text = _readable(
        body, charset_header or content_type
    )

    if not text:
        return _head_only(final_url, title, description, content_type)

    clipped = len(text) > FETCH_MAX_CHARS
    text = text[:FETCH_MAX_CHARS]

    header = f"URL: {final_url}"

    if title:
        header += f"\nTitle: {title}"

    if description:
        header += f"\nDescription: {description}"

    if clipped:
        header += (
            f"\nNote: truncated to the first {FETCH_MAX_CHARS} characters."
        )

    summary = f"{len(text)} char{plural(len(text))}"

    if title:
        summary += f" from {title}"

    tool_result(summary + (" (truncated)" if clipped else ""))

    return f"{header}\n\n{text}"


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


def _plan_steps(steps: Any) -> list[str]:
    """Coerce whatever the model sent into a list of step descriptions.

    Small models often hand back one newline-separated string, or a list
    of {"step": ...} objects, instead of the list of strings asked for.
    """

    if isinstance(steps, str):
        steps = steps.replace("\\n", "\n").splitlines()
    if not isinstance(steps, (list, tuple)):
        return []

    items = []
    for entry in steps:
        if isinstance(entry, dict):
            entry = entry.get("step") or entry.get("text") or ""
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(entry)).strip()
        if text:
            items.append(text)
    return items


def plan_tool(steps: Any) -> str:
    """Post a checklist of the steps about to be taken."""

    items = _plan_steps(steps)
    if not items:
        return "A plan needs at least one step."

    plan.set_steps(items)
    tool_line(plan.headline())
    plan.render()
    return plan.as_text()


def check_step(index: Any) -> str:
    """Tick one step of the current plan, by its 1-based number."""

    try:
        number = int(str(index).strip())
    except (TypeError, ValueError):
        return f"Step number must be a whole number, not {index!r}."

    problem = plan.mark_done(number)
    if problem:
        return problem

    tool_line(plan.headline())
    plan.render()
    return plan.as_text()


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


def open_in_editor(path: str, line: Any = None) -> str:
    """Open a file in the user's VS Code, at a line when given."""

    try:
        number = max(int(line), 0) if line not in (None, "") else 0
    except (TypeError, ValueError):
        number = 0

    tool_line(f"OpenInEditor({path}{f':{number}' if number else ''})")

    if not Path(path).expanduser().is_file():
        result = f"Error: {path} is not a file."
        tool_result(result, style=ERROR)
        return result

    if not editor.open_at(path, number):
        result = "VS Code is not available here, so nothing was opened."
        tool_result(result, style=WARN)
        return result

    result = f"Opened {path}{f' at line {number}' if number else ''}."
    tool_result(result)
    return result


EDITOR_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_in_editor",
            "description": (
                "Open a file in the user's VS Code, at a line if given, so "
                "they see the spot you are talking about in their editor. "
                "Call it whenever they ask you to show them where "
                "something is."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-based line to put the cursor on.",
                        "minimum": 1,
                    },
                },
                "required": ["path"],
            },
        },
    },
]


def turn_tools() -> list[dict[str, Any]]:
    """The tools offered on this turn: the editor's only inside VS Code."""

    return tools + (EDITOR_TOOLS if editor.available() else [])


def agent_tool(task: str) -> str:
    """Start an async sub-agent for TASK; return immediately with its ID."""

    task = str(task).strip()
    tool_line(f"Agent({task})")

    if not task:
        result = "Error: task must not be empty."
        tool_result(result, style=ERROR)
        return result

    agent_id = subagents.start(task)
    result = f"Started sub-agent. ID: {agent_id}"
    tool_result(result)
    return result


def agent_result(agent_id: str, wait_seconds: Any = None) -> str:
    """Wait for a sub-agent to finish and return its result."""

    agent_id = str(agent_id).strip()
    tool_line(f"AgentResult({agent_id})")

    try:
        timeout = (
            subagents.DEFAULT_WAIT_SECONDS
            if wait_seconds is None
            else float(wait_seconds)
        )
    except (TypeError, ValueError):
        timeout = subagents.DEFAULT_WAIT_SECONDS
    timeout = max(1.0, min(timeout, subagents.MAX_WAIT_SECONDS))

    entry = subagents.follow(agent_id, timeout)

    if entry is None:
        result = f"Error: no sub-agent with ID {agent_id!r}."
        tool_result(result, style=ERROR)
        return result

    if entry.status == subagents.RUNNING:
        result = (
            f"Sub-agent {agent_id} is still running after {timeout:.0f}s. "
            "Call agent_result again to keep waiting."
        )
        tool_result(result, style=WARN)
        return result

    subagents.mark_delivered([agent_id])

    if entry.status == subagents.FAILED:
        return f"Sub-agent {agent_id} failed: {entry.result}"

    return entry.result


def get_date() -> str:
    """Return the current date using the local timezone."""

    tool_line("GetDate()")

    today = datetime.now().date().isoformat()  # noqa: DTZ005
    tool_result(today)

    return today


_pending_images: list[bytes] = []


def take_pending_images() -> list[bytes]:
    """Return the image data queued by view_image, clearing the queue.

    view_image can only queue the bytes; the caller attaches them to the
    conversation, because an image reaches the model as message content
    rather than as tool output text.
    """

    images = list(_pending_images)
    _pending_images.clear()
    return images


def view_image(path: str) -> str:
    """Attach a local image file so the model can see it."""

    tool_line(f"ViewImage({path})")

    image_path, reason = resolve_image_path(path)
    if image_path is None:
        result = f"Error: {reason}"
        tool_result(result, style=ERROR)
        return result

    if not model_sees_images(OLLAMA_HOST, MODEL_NAME):
        result = (
            f"Error: the active model ({MODEL_NAME}) has no vision "
            "support, so it cannot be sent an image. Tell the user to "
            "switch to a vision-capable model with /model."
        )
        tool_result(result, style=ERROR)
        return result

    # Read the bytes now rather than handing the path onward: the file is
    # only known to exist at this moment, and reading it here puts any
    # failure in the tool result, where the model can react to it.
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        result = f"Error: could not read {image_path}: {exc}"
        tool_result(result, style=ERROR)
        return result

    _pending_images.append(data)

    kilobytes = max(1, round(len(data) / 1024))
    tool_result(f"{image_path.name} ({kilobytes} KB)")

    return (
        f"Attached {image_path.name} ({kilobytes} KB). The image is "
        "included with this tool result, so answer from what you can "
        "actually see in it."
    )


# Terminals that can draw a picture between two lines of output. Writing
# a graphics escape to one that cannot read it dumps a screenful of
# base64 into the session, so anything unrecognised falls back to the
# OS image viewer instead.
_KITTY_TERMINALS = {"ghostty", "kitty"}
_ITERM_TERMINALS = {"iterm.app", "wezterm", "hyper", "tabby"}
_INLINE_CHUNK = 4096

# Width of the '  L  ' gutter tool_result() prints, so the picture and
# the path line up under the name instead of starting at column zero.
RESULT_INDENT = 5


def _graphics_protocol() -> str:
    """Name the inline-image protocol this terminal speaks, or ""."""

    if not sys.stdout.isatty():
        return ""

    # Inside tmux or screen the escape has to be wrapped to pass through
    # and an unwrapped one corrupts the pane, so do not try.
    if os.environ.get("TMUX") or os.environ.get("STY"):
        return ""

    term = os.environ.get("TERM", "").lower()
    program = os.environ.get("TERM_PROGRAM", "").lower()

    if os.environ.get("KITTY_WINDOW_ID") or "kitty" in term:
        return "kitty"

    if program in _KITTY_TERMINALS:
        return "kitty"

    if os.environ.get("LC_TERMINAL", "").lower() == "iterm2":
        return "iterm"

    if program in _ITERM_TERMINALS:
        return "iterm"

    return ""


def _inline_payload(protocol: str, data: bytes, name: str) -> bytes:
    """Build the escape sequence that draws DATA in the terminal."""

    encoded = base64.standard_b64encode(data)

    if protocol == "kitty":
        # Base64 goes out in chunks of at most 4096, each flagged m=1
        # while more follow and m=0 on the last one.
        chunks = [
            encoded[at:at + _INLINE_CHUNK]
            for at in range(0, len(encoded), _INLINE_CHUNK)
        ] or [b""]

        out = bytearray()
        for index, chunk in enumerate(chunks):
            more = 0 if index == len(chunks) - 1 else 1
            if index == 0:
                out += b"\033_Ga=T,f=100,m=%d;" % more
            else:
                out += b"\033_Gm=%d;" % more
            out += chunk + b"\033\\"

        return bytes(out) + b"\n"

    return (
        b"\033]1337;File=inline=1;preserveAspectRatio=1;size="
        + str(len(data)).encode()
        + b";name="
        + base64.standard_b64encode(name.encode())
        + b":"
        + encoded
        + b"\a\n"
    )


def _draw_inline(
    protocol: str, data: bytes, name: str, indent: int = 0
) -> bool:
    """Write the image to the terminal. True when the bytes went out."""

    try:
        sys.stdout.buffer.write(
            b" " * indent + _inline_payload(protocol, data, name)
        )
        sys.stdout.buffer.flush()
    except (OSError, ValueError, AttributeError):
        return False

    return True


def _open_in_viewer(path: Path) -> str:
    """Hand PATH to whatever the OS shows pictures with.

    Returns "" on success, or a short reason it could not be opened.
    """

    try:
        if platform.system() == "Darwin":
            subprocess.run(  # nosec B603 B607
                ["open", str(path)], check=True, timeout=10
            )
        elif os.name == "nt":
            start = getattr(os, "startfile", None)
            if start is None:
                return "no image viewer on this system"
            start(str(path))
        else:
            subprocess.run(  # nosec B603 B607
                ["xdg-open", str(path)], check=True, timeout=10
            )
    except FileNotFoundError:
        return "no image viewer on this system"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"the viewer failed ({exc.__class__.__name__})"

    return ""


# The result block sweeps in the way ai.py streams a reply: a transient
# Live carries the coral glimmer across the line, then the settled Text
# is printed so scrollback keeps the real styling.
SWEEP_FRAME_SECONDS = 0.02
SWEEP_SPREAD = 3.0
SWEEP_CPS = 180.0
SWATCH_COUNT = 6
SWATCH_WIDTH = 4
# Quantizing straight to six on a dark image returns six near-identical
# blacks, which render as one smudge. Take a wider pool and keep only
# the colors far enough apart in RGB to actually read as different.
SWATCH_POOL = 24
SWATCH_MIN_DISTANCE = 32


def _animating() -> bool:
    """False when motion would be wasted or unwanted.

    Nothing animates into a pipe or a log, and FLASH_NO_ANIMATION turns
    it off for a slow link, a recording, or anyone who just wants the
    line to appear.
    """

    return console.is_terminal and not os.environ.get("FLASH_NO_ANIMATION")


def _sweep_in(plain: str, settled: Text) -> None:
    """Glimmer across PLAIN, then leave SETTLED on the screen."""

    if not _animating() or not plain.strip():
        console.print(settled)
        return

    period = len(plain) + 2 * SWEEP_SPREAD
    frames = max(1, round(period / (SWEEP_CPS * SWEEP_FRAME_SECONDS)))

    with Live(
        Text(),
        console=console,
        transient=True,
        refresh_per_second=round(1 / SWEEP_FRAME_SECONDS),
    ) as live:
        for step in range(frames + 1):
            offset = -SWEEP_SPREAD + period * step / frames
            live.update(
                Text.from_markup(glimmer(plain, offset, SWEEP_SPREAD))
            )
            time.sleep(SWEEP_FRAME_SECONDS)

    console.print(settled)


def _palette(data: bytes) -> list:
    """Up to SWATCH_COUNT dominant colors as hex, or [].

    Pillow is not a dependency of the CLI, so the swatches appear for
    anyone who has it and are simply absent for anyone who does not.
    """

    try:
        from PIL import Image
    except ImportError:
        return []

    try:
        with Image.open(io.BytesIO(data)) as opened:
            small = opened.convert("RGB").resize((64, 64))
            reduced = small.quantize(colors=SWATCH_POOL)
            table = reduced.getpalette() or []
            counts = sorted(reduced.getcolors() or [], reverse=True)
    except (OSError, ValueError, TypeError):
        return []

    kept = []
    for _count, index in counts:
        rgb = tuple(table[index * 3:index * 3 + 3])
        if len(rgb) < 3:
            continue
        if all(_apart(rgb, seen) for seen in kept):
            kept.append(rgb)
        if len(kept) == SWATCH_COUNT:
            break

    return [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in kept]


def _apart(one: tuple, other: tuple) -> bool:
    """True when two colors differ enough to read as different."""

    gap = sum((a - b) ** 2 for a, b in zip(one, other))

    return gap >= SWATCH_MIN_DISTANCE ** 2


def _swatch_row(colors: list) -> Text:
    """A row of solid blocks, one per dominant color."""

    row = Text(" " * RESULT_INDENT)
    for color in colors:
        row.append(" " * SWATCH_WIDTH, style=f"on {color}")
        row.append(" ")

    return row


def _open_with_spinner(image_path: Path) -> str:
    """Open PATH in the OS viewer, glimmering while it starts.

    Returns "" on success or the reason it could not be opened, the
    same as _open_in_viewer, which it runs on a worker thread so the
    cold start of an image viewer is not dead air.
    """

    if not _animating():
        return _open_in_viewer(image_path)

    outcome = {}

    def work():
        outcome["why"] = _open_in_viewer(image_path)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()

    word = f"opening{ELLIPSIS}"
    period = len(word) + 2 * SWEEP_SPREAD
    start = time.monotonic()

    with Live(
        Text(),
        console=console,
        transient=True,
        refresh_per_second=round(1 / SWEEP_FRAME_SECONDS),
    ) as live:
        while worker.is_alive():
            elapsed = time.monotonic() - start
            offset = (elapsed * SWEEP_CPS / 6) % period - SWEEP_SPREAD
            live.update(
                Text.from_markup(
                    " " * RESULT_INDENT
                    + glimmer(word, offset, SWEEP_SPREAD)
                )
            )
            time.sleep(SWEEP_FRAME_SECONDS)

    worker.join()

    return outcome.get("why", "")


def _image_size(data: bytes) -> tuple:
    """Read (width, height) out of an image header, or (0, 0).

    Pillow would do this in one line, but it is not a dependency of the
    CLI and the pixel count is only here to label the result line, so
    the five formats resolve_image_path accepts are parsed by hand.
    """

    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])

        if data[:3] == b"GIF":
            return struct.unpack("<HH", data[6:10])

        if data[:2] == b"BM":
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)

        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp_size(data)

        if data[:2] == b"\xff\xd8":
            return _jpeg_size(data)
    except (struct.error, IndexError, ValueError):
        return 0, 0

    return 0, 0


def _webp_size(data: bytes) -> tuple:
    """(width, height) for the three WebP chunk layouts."""

    kind = data[12:16]

    if kind == b"VP8X":
        wide = int.from_bytes(data[24:27], "little") + 1
        high = int.from_bytes(data[27:30], "little") + 1
        return wide, high

    if kind == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

    if kind == b"VP8 ":
        wide, high = struct.unpack("<HH", data[26:30])
        return wide & 0x3FFF, high & 0x3FFF

    return 0, 0


def _jpeg_size(data: bytes) -> tuple:
    """(width, height) from the first JPEG start-of-frame marker."""

    at = 2
    while at + 9 < len(data):
        if data[at] != 0xFF:
            at += 1
            continue

        marker = data[at + 1]

        # Every SOF carries the dimensions except DHT, DAC and the
        # restart markers, which share the range.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            high, wide = struct.unpack(">HH", data[at + 5:at + 9])
            return wide, high

        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue

        at += 2 + int.from_bytes(data[at + 2:at + 4], "big")

    return 0, 0


def _display_path(path: Path) -> str:
    """PATH made absolute, with the home directory shortened to ~."""

    try:
        full = path.expanduser().resolve()
    except OSError:
        return str(path)

    try:
        return str(Path("~") / full.relative_to(Path.home()))
    except (ValueError, RuntimeError):
        return str(full)


def _image_block(
    image_path: Path, data: bytes, note: str, status: str
) -> None:
    """Render the result block: name, shape, size, status, caption."""

    wide, high = _image_size(data)
    kilobytes = max(1, round(len(data) / 1024))

    shape = f"  {wide}x{high}" if wide and high else ""
    tail = f"{shape}  {kilobytes} KB" + (f"  {status}" if status else "")

    head = Text(f"  {BRANCH}  ", style=DIM)
    head.append(image_path.name, style=ACCENT)
    head.append(tail, style=DIM)

    _sweep_in(f"  {BRANCH}  {image_path.name}{tail}", head)

    colors = _palette(data)
    if colors:
        console.print(_swatch_row(colors))

    if note:
        console.print(
            Text(f"{' ' * RESULT_INDENT}{note}", style=f"italic {DIM}")
        )


def send_image(path: str, caption: str = "") -> str:
    """Put an image file in front of the user."""

    tool_line(f"SendImage({path})")

    image_path, reason = resolve_image_path(path)
    if image_path is None:
        result = f"Error: {reason}"
        tool_result(result, style=ERROR)
        return result

    try:
        data = image_path.read_bytes()
    except OSError as exc:
        result = f"Error: could not read {image_path}: {exc}"
        tool_result(result, style=ERROR)
        return result

    kilobytes = max(1, round(len(data) / 1024))
    note = caption.strip()
    protocol = _graphics_protocol()

    if protocol:
        _image_block(image_path, data, note, "")
        if _draw_inline(protocol, data, image_path.name, RESULT_INDENT):
            return (
                f"Sent {image_path.name} ({kilobytes} KB), drawn in the "
                "user's terminal. You cannot see it from here; "
                "view_image is what shows it to you."
            )

    problem = _open_with_spinner(image_path)
    status = problem or "opened in your image viewer"
    _image_block(image_path, data, note, status)
    console.print(
        Text(f"{' ' * RESULT_INDENT}{_display_path(image_path)}", style=DIM)
    )

    if problem:
        return (
            f"Wrote {image_path.name} ({kilobytes} KB) and put its path "
            f"on screen, but could not display it: {problem}. Tell the "
            "user where the file is."
        )

    return (
        f"Sent {image_path.name} ({kilobytes} KB). This terminal cannot "
        "draw images, so it opened in the user's image viewer with the "
        "path on screen. You cannot see it from here."
    )


DEFAULT_SCREENSHOT_WIDTH = 1280
DEFAULT_SCREENSHOT_HEIGHT = 800
MIN_SCREENSHOT_SIDE = 200
MAX_SCREENSHOT_SIDE = 4000
DEFAULT_SCREENSHOT_WAIT_MS = 2000
MAX_SCREENSHOT_WAIT_MS = 20000
MAX_PAGE_PROBLEMS = 5

_screenshot_count = 0


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    """Coerce a model-supplied number into `low..high`.

    Arguments arrive as whatever the model put in its JSON, so `value`
    is deliberately untyped: a string, a float, or nothing at all all
    fall back to the default rather than raising mid-call.
    """

    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(low, min(high, number))


def screenshot(
    target: str,
    width: Any = DEFAULT_SCREENSHOT_WIDTH,
    height: Any = DEFAULT_SCREENSHOT_HEIGHT,
    full_page: Any = False,
    wait_ms: Any = DEFAULT_SCREENSHOT_WAIT_MS,
) -> str:
    """Render a page in a headless browser and attach the picture."""

    global _screenshot_count

    view_width = _clamp(width, MIN_SCREENSHOT_SIDE, MAX_SCREENSHOT_SIDE,
                        DEFAULT_SCREENSHOT_WIDTH)
    view_height = _clamp(height, MIN_SCREENSHOT_SIDE,
                         MAX_SCREENSHOT_SIDE, DEFAULT_SCREENSHOT_HEIGHT)
    settle_ms = _clamp(wait_ms, 0, MAX_SCREENSHOT_WAIT_MS,
                       DEFAULT_SCREENSHOT_WAIT_MS)
    whole_page = bool(full_page)

    shape = f"{view_width}x{view_height}"
    if whole_page:
        shape += " full page"
    tool_line(f"Screenshot({target}, {shape})")

    url, why = resolve_target(target)
    if url is None:
        result = f"Error: {why}"
        tool_result(result, style=ERROR)
        return result

    if not model_sees_images(OLLAMA_HOST, MODEL_NAME):
        result = (
            f"Error: the active model ({MODEL_NAME}) has no vision "
            "support, so it cannot be shown a screenshot. Tell the user to "
            "switch to a vision-capable model with /model."
        )
        tool_result(result, style=ERROR)
        return result

    _screenshot_count += 1
    out = Path(SCRATCH_DIR) / f"screenshot-{_screenshot_count}.png"

    problems, why = capture(
        url,
        out,
        width=view_width,
        height=view_height,
        full_page=whole_page,
        wait_ms=settle_ms,
    )

    if why:
        result = f"Error: {why}"
        tool_result(result, style=ERROR)
        return result

    data = out.read_bytes()
    _pending_images.append(data)

    kilobytes = max(1, round(len(data) / 1024))
    tool_result(f"{shape} ({kilobytes} KB) {out.name}")

    if problems:
        for problem in problems[:MAX_PAGE_PROBLEMS]:
            tool_result(problem, style=WARN)

    result = (
        f"Rendered {url} at {shape}. The screenshot is attached to this "
        f"tool result and saved at {out}, so judge the page from what you "
        "can actually see in it, not from the source you wrote."
    )

    if problems:
        shown = problems[:MAX_PAGE_PROBLEMS]
        extra = len(problems) - len(shown)
        result += (
            f"\n\nThe page reported {len(problems)} "
            f"error{plural(len(problems))} while rendering, which may be why "
            "it does not look right:\n"
            + "\n".join(f"- {problem}" for problem in shown)
        )

        if extra:
            result += f"\n- and {extra} more"

    return result


def _page_report(headline: str, *, full_page: bool = False) -> str:
    """Show the model the page it just acted on.

    Every open_page and interact call ends here, because an action the
    model cannot see the result of is an action it has to guess about: a
    picture when the model has eyes, the elements it can act on next, and
    whatever the page complained about while doing it.
    """

    global _screenshot_count

    lines = [headline]

    url, title = page_where()
    if url:
        lines.append(f"Page: {title or 'untitled'} - {url}")

    if model_sees_images(OLLAMA_HOST, MODEL_NAME):
        _screenshot_count += 1
        out = Path(SCRATCH_DIR) / f"page-{_screenshot_count}.png"
        why = page_snapshot(out, full_page=bool(full_page))

        if why:
            lines.append(f"No screenshot of the page: {why}")
            tool_result(why, style=WARN)
        else:
            data = out.read_bytes()
            _pending_images.append(data)
            kilobytes = max(1, round(len(data) / 1024))
            tool_result(f"{out.name} ({kilobytes} KB)")
            lines.append(
                "A screenshot of the page as it stands is attached to this "
                "tool result, so judge it from what you can see there."
            )
    else:
        lines.append(
            f"The active model ({MODEL_NAME}) has no vision, so there is no "
            "screenshot. Work from the element list and from eval."
        )

    found, why = page_elements()
    if why:
        lines.append(f"Could not list the page's elements: {why}")
    elif found:
        lines.append(
            "Things you can act on now (pass the number as the selector):"
        )
        lines.extend(found)
    else:
        lines.append("Nothing on this page can be clicked or typed into.")

    problems = page_problems()
    if problems:
        for problem in problems[:MAX_PAGE_PROBLEMS]:
            tool_result(problem, style=WARN)

        shown = problems[:MAX_PAGE_PROBLEMS]
        extra = len(problems) - len(shown)
        lines.append(
            f"The page reported {len(problems)} error{plural(len(problems))}, "
            "which is usually what explains anything that looks wrong:"
        )
        lines.extend(f"- {problem}" for problem in shown)

        if extra:
            lines.append(f"- and {extra} more")

    return "\n".join(lines)


def open_page(
    target: str,
    width: Any = DEFAULT_SCREENSHOT_WIDTH,
    height: Any = DEFAULT_SCREENSHOT_HEIGHT,
    wait_ms: Any = DEFAULT_SCREENSHOT_WAIT_MS,
) -> str:
    """Open a page in a browser that stays open to be clicked through."""

    view_width = _clamp(width, MIN_SCREENSHOT_SIDE, MAX_SCREENSHOT_SIDE,
                        DEFAULT_SCREENSHOT_WIDTH)
    view_height = _clamp(height, MIN_SCREENSHOT_SIDE,
                         MAX_SCREENSHOT_SIDE, DEFAULT_SCREENSHOT_HEIGHT)
    settle_ms = _clamp(wait_ms, 0, MAX_SCREENSHOT_WAIT_MS,
                       DEFAULT_SCREENSHOT_WAIT_MS)

    shape = f"{view_width}x{view_height}"
    tool_line(f"OpenPage({target}, {shape})")

    url, why = resolve_target(target)
    if url is None:
        result = f"Error: {why}"
        tool_result(result, style=ERROR)
        return result

    why = browser_open(
        url,
        width=view_width,
        height=view_height,
        wait_ms=settle_ms,
    )

    if why:
        result = f"Error: {why}"
        tool_result(result, style=ERROR)
        return result

    return _page_report(
        f"Opened {url} at {shape}. The browser stays open, so use the "
        "interact tool to click, type, or run JavaScript on this page, and "
        "close it when you are done."
    )


def interact(
    action: str,
    selector: str = "",
    value: str = "",
    wait_ms: Any = 0,
    full_page: Any = False,
) -> str:
    """Act on the page the browser already has open."""

    action = str(action).strip().lower()
    selector = str(selector or "")
    value = "" if value is None else str(value)

    label = f"{action} {selector}".strip()
    tool_line(f"Interact({label})")

    if action == "close":
        result = (
            "Closed the browser."
            if close_session()
            else "There was no browser open."
        )
        tool_result(result)
        return result

    if not page_is_open():
        result = f"Error: {NO_PAGE}"
        tool_result(result, style=ERROR)
        return result

    note, why = browser_interact(
        action,
        selector=selector,
        value=value,
        wait_ms=_clamp(wait_ms, 0, MAX_SCREENSHOT_WAIT_MS, 0),
    )

    if why:
        # A failed action leaves the page as it was, so the model still
        # needs to see it to work out what went wrong.
        result = _page_report(f"That did not work: {why}")
        tool_result(why, style=ERROR)
        return result

    return _page_report(note, full_page=bool(full_page))


# Tool schema expected by Ollama function calling (OpenAI-style).
tools: list[dict[str, Any]] = [
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
            "name": "read",
            "description": (
                "Read a text file, returned with line numbers. Read-only "
                "and cross-platform; prefer this over shell cat/sed/head "
                "when you need a file's contents. Also reads .pdf and "
                ".docx files, extracting their text the same way; a "
                "legacy .doc file needs converting to .docx first. "
                "Returns at most "
                f"{DEFAULT_READ_LINES} lines per call, so use offset to "
                "page through a longer file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file, e.g. 'flash/renderer.py'."
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "1-based line number to start at. Defaults to "
                            "the first line."
                        ),
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "How many lines to read. Defaults to "
                            f"{DEFAULT_READ_LINES}, maximum "
                            f"{MAX_READ_LINES}."
                        ),
                        "minimum": 1,
                        "maximum": MAX_READ_LINES,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": (
                "Create a new text file, or replace an existing one "
                "outright, or add to the end of one with append. To "
                "change part of a file that already exists, use edit "
                "instead: this tool makes you write out every line in "
                "the file, which is slow and truncates on a long one. "
                "The user sees a diff and confirms before anything is "
                "written. Cross-platform and needs no quoting or "
                "escaping; prefer it over shell redirection or heredocs "
                "for every file you create. A file too long for one call "
                "is written in pieces: the first part with no append, "
                "then the rest with append=true, in order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file, e.g. 'flash/renderer.py'. "
                            "Missing parent directories are created."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The file's full new contents, exactly as it "
                            "should land on disk, or the piece to add to "
                            "the end of it when append is true."
                        ),
                    },
                    "append": {
                        "type": "boolean",
                        "description": (
                            "Add content to the end of the file instead of "
                            "replacing it. Use it to build a file that is "
                            "too long for one call, one piece per call, "
                            "and to continue an unfinished one. It is "
                            "written exactly as given, so start the piece "
                            "with a newline if the last one did not end "
                            "with one."
                        ),
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "Change part of a text file by replacing one exact block "
                "of text with another. This is how you change a file "
                "that already exists: it costs you only the lines that "
                "actually change, where write costs you every line in "
                "the file. old_string must match the file exactly, "
                "character for character, and must appear only once, so "
                "include the lines above and below it until it is "
                "unique. Read the file first and copy the text out of "
                "the result rather than typing it from memory, leaving "
                "off the line numbers read adds. The user sees a diff "
                "and confirms before anything is written."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file, e.g. 'flash/theme.py'."
                        ),
                    },
                    "old_string": {
                        "type": "string",
                        "description": (
                            "The exact text to replace, copied from the "
                            "file. Include enough surrounding lines that "
                            "it appears only once."
                        ),
                    },
                    "new_string": {
                        "type": "string",
                        "description": (
                            "The text to put in its place. Pass an empty "
                            "string to delete old_string outright."
                        ),
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": (
                            "Replace every occurrence instead of failing "
                            "when old_string appears more than once. Use "
                            "it for a rename across a file."
                        ),
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_edit",
            "description": (
                "Make several exact edits to one file in a single call. "
                "They are applied in order, and each one sees the text "
                "the one before it produced. Either all of them land or "
                "none do, so a failed edit never leaves the file half "
                "changed. Prefer this over several edit calls whenever "
                "you have more than one change to make to the same file: "
                "it costs one confirmation and one round trip instead of "
                "one of each per edit. Every old_string follows the same "
                "rules as the edit tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file every edit applies to."
                        ),
                    },
                    "edits": {
                        "type": "array",
                        "description": (
                            "The edits to apply, in the order they "
                            "should be made."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {
                                    "type": "string",
                                    "description": (
                                        "Exact text to replace, unique "
                                        "in the file as it stands when "
                                        "this edit's turn comes."
                                    ),
                                },
                                "new_string": {
                                    "type": "string",
                                    "description": (
                                        "The text to put in its place."
                                    ),
                                },
                                "replace_all": {
                                    "type": "boolean",
                                    "description": (
                                        "Replace every occurrence of "
                                        "old_string."
                                    ),
                                },
                            },
                            "required": ["old_string", "new_string"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": (
                "Show an image file (.png, .jpg, .jpeg, .webp, .gif, "
                ".bmp) to the user. It is drawn in their terminal where "
                "the terminal can draw one, and opened in their image "
                "viewer where it cannot, with the path printed either "
                "way. Use it to hand over a picture you generated or "
                "edited, once it is finished. This shows the image to "
                "the user and not to you, so check your own work with "
                "view_image before sending it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the image file, e.g. './poster.png'."
                        ),
                    },
                    "caption": {
                        "type": "string",
                        "description": (
                            "Optional single line shown with the image."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": (
                "Look at an image file on disk (.png, .jpg, .jpeg, .webp, "
                ".gif, .bmp). The image is attached to the conversation so "
                "you can see it. This is the only way to see an image the "
                "user did not send with /image; no shell command can show "
                "you one. It stays visible for the current turn, so call "
                "this again later if you need another look."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the image file, e.g. "
                            "'~/Pictures/screenshot.png'."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": (
                "Render a web page in a headless browser and look at the "
                "result. Takes a local .html file or a URL, and the picture "
                "is attached to the conversation so you can see how the page "
                "actually renders. Use it on every page you build or change, "
                "and again at a narrow width to check it on a phone. "
                "Reading the HTML source does not tell you what it looks "
                "like."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Path to a local page, e.g. './index.html', or "
                            "a URL, e.g. 'http://localhost:8000'."
                        ),
                    },
                    "width": {
                        "type": "integer",
                        "description": (
                            "Viewport width in pixels. Defaults to "
                            f"{DEFAULT_SCREENSHOT_WIDTH}. Use 375 to check "
                            "the page on a phone."
                        ),
                        "minimum": MIN_SCREENSHOT_SIDE,
                        "maximum": MAX_SCREENSHOT_SIDE,
                    },
                    "height": {
                        "type": "integer",
                        "description": (
                            "Viewport height in pixels. Defaults to "
                            f"{DEFAULT_SCREENSHOT_HEIGHT}. Only what fits in "
                            "the viewport is captured, so raise it to see "
                            "further down a long page."
                        ),
                        "minimum": MIN_SCREENSHOT_SIDE,
                        "maximum": MAX_SCREENSHOT_SIDE,
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": (
                            "Capture the whole scrollable page instead of "
                            "just the viewport. Use it to check a long page "
                            "end to end; leave it off to see the fold the "
                            "way a visitor first does."
                        ),
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": (
                            "Milliseconds to let the page load and animate "
                            "before capturing. Defaults to "
                            f"{DEFAULT_SCREENSHOT_WAIT_MS}. Raise it for a "
                            "page that fetches data or plays an intro."
                        ),
                        "minimum": 0,
                        "maximum": MAX_SCREENSHOT_WAIT_MS,
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_page",
            "description": (
                "Open a local .html file or a URL in a real browser that "
                "stays open, so you can then click, type, and debug your "
                "way through the page with the interact tool. The result "
                "shows the page's address, a numbered list of everything "
                "that can be clicked or typed into, and any JavaScript "
                "errors it threw, with a screenshot attached. Use this "
                "instead of screenshot whenever the page has buttons, a "
                "form, or behaviour to check; screenshot is only a still "
                "picture."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Path to a local page, e.g. './index.html', or "
                            "a URL, e.g. 'http://localhost:8000'."
                        ),
                    },
                    "width": {
                        "type": "integer",
                        "description": (
                            "Viewport width in pixels. Defaults to "
                            f"{DEFAULT_SCREENSHOT_WIDTH}. Use 375 to work "
                            "through the page as a phone would show it."
                        ),
                        "minimum": MIN_SCREENSHOT_SIDE,
                        "maximum": MAX_SCREENSHOT_SIDE,
                    },
                    "height": {
                        "type": "integer",
                        "description": (
                            "Viewport height in pixels. Defaults to "
                            f"{DEFAULT_SCREENSHOT_HEIGHT}."
                        ),
                        "minimum": MIN_SCREENSHOT_SIDE,
                        "maximum": MAX_SCREENSHOT_SIDE,
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": (
                            "Milliseconds to let the page load before "
                            "looking at it. Defaults to "
                            f"{DEFAULT_SCREENSHOT_WAIT_MS}."
                        ),
                        "minimum": 0,
                        "maximum": MAX_SCREENSHOT_WAIT_MS,
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interact",
            "description": (
                "Do one thing to the page open_page opened, then look at "
                "the result: click a button, fill a field, press a key, "
                "choose an option, scroll, wait for something to appear, "
                "or run JavaScript against the live page. The page keeps "
                "its state between calls, so work through a flow one call "
                "at a time. Every call reports where the page is now, its "
                "numbered elements, and the errors it threw, with a "
                "screenshot attached, so this is how you debug what a page "
                "actually does rather than what its source says."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "What to do: 'click', 'fill' (type value into "
                            "a field), 'press' (send a key such as Enter or "
                            "Tab), 'hover', 'select' (choose value in a "
                            "dropdown), 'scroll', 'wait', 'eval' (run the "
                            "JavaScript in value and return its result), "
                            "'back', 'reload', or 'close' (shut the "
                            "browser when you are done)."
                        ),
                        "enum": list(ACTIONS),
                    },
                    "selector": {
                        "type": "string",
                        "description": (
                            "Which element to act on: the number shown "
                            "next to it in the last element list (simplest "
                            "and safest), a CSS selector, or the visible "
                            "text on it. The numbers are handed out again "
                            "after every call, so always use the newest "
                            "list. Leave it out for eval, back, reload, "
                            "close, and for a scroll of the whole page."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "The text to type for fill, the key for press, "
                            "the option for select, the JavaScript for "
                            "eval, or 'top', 'bottom', or a number of "
                            "pixels for scroll."
                        ),
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": (
                            "Extra milliseconds to wait after the action "
                            "before looking, for a page that animates or "
                            "fetches in response to it."
                        ),
                        "minimum": 0,
                        "maximum": MAX_SCREENSHOT_WAIT_MS,
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": (
                            "Photograph the whole scrollable page instead "
                            "of just the viewport."
                        ),
                    },
                },
                "required": ["action"],
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
            "name": "fetch",
            "description": (
                "Fetch a URL and return its readable text, with no "
                "browser and no screenshot."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Absolute http:// or https:// URL to fetch."
                        ),
                    },
                },
                "required": ["url"],
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
            "name": "plan",
            "description": (
                "Post the steps you are about to take as a checklist the "
                "user can watch. Replaces any earlier plan. Use it for a "
                "task with several distinct steps, not for something you "
                "can finish in one or two tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The steps, in the order you will do them. One "
                            "short line each, phrased as the work itself "
                            "(e.g. 'Read the renderer'), not as a promise. "
                            f"At most {plan.MAX_STEPS}."
                        ),
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_step",
            "description": (
                "Tick one step of the current plan, by its 1-based number, "
                "the moment that step is finished. Ticking a step redraws "
                "the checklist for the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "1-based number of the finished step.",
                        "minimum": 1,
                    },
                },
                "required": ["index"],
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
            "name": "agent",
            "description": (
                "Start a sub-agent on a background thread to do one "
                "focused piece of work, and return immediately with its "
                "ID rather than waiting for it. Use this to run "
                "independent pieces of a task (e.g. researching two "
                "separate topics) at the same time: call agent once per "
                "piece of work, then end your turn; you are woken with "
                "each answer when its sub-agent finishes. Call "
                "agent_result instead only if this turn cannot go on "
                "without the answer. The "
                "sub-agent cannot talk to the user or spawn further "
                "sub-agents, so give it a self-contained task it can "
                "finish without asking anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The full task for the sub-agent to carry "
                            "out on its own, written so it needs no "
                            "further context or follow-up questions."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_result",
            "description": (
                "Wait for a sub-agent started with agent to finish, and "
                "return its final answer. Returns right away if it has "
                "already finished. If it is still running when the wait "
                "runs out, call agent_result again with the same ID to "
                "keep waiting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "The ID returned by the agent tool call to "
                            "wait for."
                        ),
                    },
                    "wait_seconds": {
                        "type": "number",
                        "description": (
                            f"Maximum seconds to wait. Defaults to "
                            f"{subagents.DEFAULT_WAIT_SECONDS:.0f}, "
                            f"maximum {subagents.MAX_WAIT_SECONDS:.0f}."
                        ),
                        "minimum": 1,
                    },
                },
                "required": ["agent_id"],
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
    "read": read_tool,
    "write": write_tool,
    "edit": edit_tool,
    "multi_edit": multi_edit_tool,
    "view_image": view_image,
    "send_image": send_image,
    "screenshot": screenshot,
    "open_page": open_page,
    "interact": interact,
    "web_search": web_search,
    "fetch": fetch,
    "get_os": get_os,
    "reason": reason,
    "get_date": get_date,
    "plan": plan_tool,
    "check_step": check_step,
    "remember": remember,
    "recall": recall,
    "forget": forget,
    "agent": agent_tool,
    "agent_result": agent_result,
    "open_in_editor": open_in_editor,
}

# Tools a sub-agent (flash/agent.py) is allowed to call: read/search/shell
# only, since plan, check_step, remember/recall/forget, the browser tools,
# and agent itself all touch single-user global state on the main loop.
# Kept here, next to FUNCTIONS, as the one place that names a tool, so
# adding, renaming, or removing one only means updating this file.
SUBAGENT_TOOL_NAMES = (
    "shell", "glob", "grep", "read", "write", "edit", "multi_edit",
    "web_search", "fetch", "get_os", "get_date", "reason",
)

# Tools that stop for a y/n unless autonomous mode is on. A sub-agent has
# no terminal to ask from, so it only gets these in autonomous mode.
CONFIRMED_TOOL_NAMES = ("shell", "write", "edit", "multi_edit")


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
