"""AI Tool System"""

import difflib
import fnmatch
import os
import platform
import queue
import re
import subprocess  # nosec B404
import threading
import time
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Union

from ddgs import DDGS
from rich.text import Text

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
from .images import resolve_image_path
from .memory import add_memory, forget_memory, search_memory
from .notify import notify_needs_input
from .sysprompt import get_system_prompt, model_sees_images
from .theme import (
    ACCENT,
    BRANCH,
    DIM,
    ERROR,
    WARN,
    console,
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
To create or change a file, use the write tool instead of shell redirection,
  heredocs, or Set-Content. It needs no quoting or escaping and works the
  same on every platform, so shell quoting can never corrupt the content.
  It replaces the whole file, so read the file first when editing one, and
  pass back the complete new contents.
When searching for recent information, use the web_search tool.
When you need to know the user's operating system, use the get_os tool.
To think or plan mid-task without ending your turn, use the reason tool.
When you need the current date, use the get_date tool.
To look at an image file on disk, use the view_image tool with its path;
  it is the only way to see an image the user did not send with /image.
  Reading image bytes with shell or grep shows you nothing.
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


def init(config, ):
    global NO_COMMAND_CONFIRMATION, OLLAMA_HOST, MODEL_NAME
    NO_COMMAND_CONFIRMATION = config.no_command_confirmation
    OLLAMA_HOST = config.host
    MODEL_NAME = config.model or ""


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


def write_tool(path: str, content: str) -> str:
    """Tool to write a text file, showing a diff and asking to confirm."""

    tool_line(f"Write({path})")

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

    preview, omitted, additions, removals = _diff_preview(
        old_text, content, file_path.name
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
        notify_needs_input()

        prompt = Text(f"  {BRANCH}  ", style=DIM)
        prompt.append("Write this file? ", style=DIM)
        prompt.append("y", style=f"bold {ACCENT}")
        prompt.append("/n ", style=DIM)
        console.print(prompt, end="")

        if input().strip().lower() != "y":
            tool_result("Write blocked by user", style=WARN)
            return "Write blocked by user"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the model's content lands byte-for-byte, instead of
        # every \n becoming \r\n on Windows.
        with open(file_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    except OSError as exc:
        result = f"Error: could not write {file_path}: {exc}"
        tool_result(result, style=ERROR)
        return result

    written = len(content.splitlines())
    verb = "Wrote" if existed else "Created"
    tool_result(f"{verb} {written} line{plural(written)}")
    return f"{verb} {written} line{plural(written)} to {file_path}"


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
                "Write a text file, replacing it if it exists. The user "
                "sees a diff and confirms before anything is written. "
                "Cross-platform and needs no quoting or escaping; prefer "
                "this over shell redirection or heredocs for every file "
                "you create or change. Read the file first when editing "
                "one, since this replaces the whole file."
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
                            "should land on disk."
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
    "read": read_tool,
    "write": write_tool,
    "view_image": view_image,
    "screenshot": screenshot,
    "open_page": open_page,
    "interact": interact,
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
