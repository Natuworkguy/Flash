"""Shared terminal theme for Flash CLI, styled after Claude Code's CLI.

Centralizes the color palette and the "tool call" line format (a bulleted
header line followed by an indented result) so ai.py and tools.py render
consistently through one Console instance.
"""

import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Optional

from prompt_toolkit.formatted_text import StyleAndTextTuples
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

ACCENT = "#d97757"
DIM = "grey62"
ERROR = "#e5484d"
WARN = "#d9a63f"
DIFF_ADD = "#3fb950"
DIFF_DEL = "#e5484d"

console = Console()


def can_encode(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


# Some Windows terminals (older cmd.exe/PowerShell hosts, mintty/git-bash)
# report a non-UTF8 stdout encoding and raise UnicodeEncodeError on these
# glyphs instead of substituting a fallback, crashing the whole process.
# Fall back to plain ASCII there rather than risk that.
_UNICODE_OK = can_encode("✻⏺⎿❯…●━─")

SPARKLE = "✻" if _UNICODE_OK else "*"      # ✻
BULLET = "⏺" if _UNICODE_OK else "*"        # ⏺
BRANCH = "⎿" if _UNICODE_OK else "L"        # ⎿
CHEVRON = "❯" if _UNICODE_OK else ">"       # ❯
ELLIPSIS = "…" if _UNICODE_OK else "..."    # …
CURSOR = "●" if _UNICODE_OK else "."        # ●
BAR_FULL = "━" if _UNICODE_OK else "#"      # ━
BAR_EMPTY = "─" if _UNICODE_OK else "-"     # ─

# Its own check: a console can carry every glyph above and still
# choke on this one.
MIDDOT = "·" if can_encode("·") else "-"   # ·

# Plan checkboxes, kept on their own encoding check: a terminal can carry
# the glyphs above and still choke on these.
_BOXES_OK = can_encode("☒☐")
CHECK_DONE = "☒" if _BOXES_OK else "[x]"    # ☒
CHECK_TODO = "☐" if _BOXES_OK else "[ ]"    # ☐

_MARKS_OK = can_encode("✓✗⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
TICK = "✓" if _MARKS_OK else "+"
CROSS = "✗" if _MARKS_OK else "x"
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if _MARKS_OK else "|/-\\"

# Raw ANSI escapes for text fed straight into input()/print(), where rich
# markup can't reach (e.g. the interactive prompt string).
_ACCENT_RGB = (217, 119, 87)
ACCENT_ANSI = f"\033[38;2;{_ACCENT_RGB[0]};{_ACCENT_RGB[1]};{_ACCENT_RGB[2]}m"
DIM_ANSI = "\033[38;5;244m"
# DIM as a hex literal (see _REST_RGB), for prompt_toolkit style
# strings, which take neither rich's style names nor raw escapes.
DIM_HEX = "#949494"
RESET_ANSI = "\033[0m"


def ansi(colour: str) -> str:
    """A truecolour escape for a "#rrggbb" string.

    For text fed straight into a prompt string, where rich's styles
    and prompt_toolkit's style classes both fall outside.
    """

    value = colour.lstrip("#")
    red, green, blue = (int(value[at:at + 2], 16) for at in (0, 2, 4))

    return f"\033[38;2;{red};{green};{blue}m"


ToolSink = Callable[[str, str, str], None]

_capture = threading.local()


@contextmanager
def capture_tool_output(sink: ToolSink) -> Iterator[None]:
    """Send this thread's tool_line/tool_result/tool_diff output to SINK.

    SINK gets (kind, text, style) with kind "line", "result" or "diff".
    A sub-agent's tools run on a background thread; printing there would
    land in the middle of whatever the main loop is drawing.
    """

    _capture.sink = sink
    try:
        yield
    finally:
        _capture.sink = None


def _sink() -> Optional[ToolSink]:
    return getattr(_capture, "sink", None)


def tool_line(label: str) -> None:
    """Print a tool-invocation header, e.g. '⏺ Bash(ls -la)'."""

    sink = _sink()
    if sink:
        sink("line", label, "")
        return

    line = Text()
    line.append(f"{BULLET} ", style=ACCENT)
    line.append(label)
    console.print(line)


def tool_result(text: str, *, style: str = DIM) -> None:
    """Print an indented result block under the most recent tool_line()."""

    sink = _sink()
    if sink:
        sink("result", text or "", style)
        return

    lines = (text or "").splitlines() or [""]

    first = Text(f"  {BRANCH}  ", style=style)
    first.append(lines[0], style=style)
    console.print(first)

    for line in lines[1:]:
        console.print(Text(f"     {line}", style=style))


def tool_diff(diff_lines: list[str], *, more: int = 0) -> None:
    """Print a colored unified diff, indented under a tool_result() line.

    `more` is the number of diff lines omitted from the tail, shown as a
    trailing note so a large rewrite does not flood the terminal.
    """

    sink = _sink()
    if sink:
        sink("diff", "\n".join(diff_lines), DIM)
        return

    for line in diff_lines:
        if line.startswith("+"):
            style = DIFF_ADD
        elif line.startswith("-"):
            style = DIFF_DEL
        elif line.startswith("@@"):
            style = ACCENT
        else:
            style = DIM
        console.print(Text(f"     {line}", style=style))

    if more > 0:
        console.print(
            Text(f"     ... {more} more diff line{plural(more)}", style=DIM)
        )


def confirm(question: str) -> bool:
    """Ask QUESTION on one y/n line. True only for a plain yes."""

    ask = Text(f"  {question} ", style=DIM)
    ask.append("y", style=f"bold {ACCENT}")
    ask.append("/n ", style=DIM)
    console.print(ask, end="")

    try:
        answer = input().strip().lower()
    except EOFError:
        print()
        return False

    print()
    return answer == "y"


def plural(count: int, suffix: str = "s") -> str:
    """'' for one, `suffix` otherwise -- for '1 line' / '2 lines'."""

    return "" if count == 1 else suffix


def dim(text: str) -> None:
    console.print(Text(text, style=DIM))


def error(text: str) -> None:
    console.print(Markdown(text, style=ERROR))


def warn(text: str) -> None:
    console.print(Text(text, style=WARN))


_GLIMMER_BASE_RGB = (120, 120, 120)


def glimmer(text: str, offset: float, spread: float = 2.5) -> str:
    """Rich markup for `text` with a coral highlight sweeping across it."""

    parts = []
    for i, ch in enumerate(text):
        if ch.isspace():
            parts.append(ch)
            continue

        t = max(0.0, 1.0 - (abs(i - offset) / spread) ** 2)
        rgb = (
            round(base + (accent - base) * t)
            for base, accent in zip(_GLIMMER_BASE_RGB, _ACCENT_RGB)
        )
        parts.append(f"[#{''.join(f'{c:02x}' for c in rgb)}]{ch}[/]")

    return "".join(parts)


_REST_RGB = (148, 148, 148)  # ~ grey62, matches DIM


def ptk_sweep_reveal(
    text: str,
    edge: float,
    *,
    revealing: bool,
    band: float = 1.6,
) -> StyleAndTextTuples:
    """prompt_toolkit style fragments for `text`, where a moving `edge`
    sweeps letters into or out of existence with a coral glow riding the
    boundary between them.

    `revealing=True` materializes characters left of `edge`, leaving
    everything to its right blank (not yet appeared). `revealing=False`
    erases characters left of `edge`, leaving everything to its right
    intact (not yet erased). Sweep `edge` from `-band` to
    `len(text) + band` for a full pass in either mode.
    """

    fragments: StyleAndTextTuples = []
    for i, ch in enumerate(text):
        d = (edge - i) if revealing else (i - edge)
        shown = max(0.0, min(1.0, (d + band) / (2 * band)))
        if shown <= 0.0:
            fragments.append(("", " "))
            continue

        glow = max(0.0, 1.0 - (d / band) ** 2) if abs(d) < band else 0.0
        rgb = tuple(
            round(base + (accent - base) * glow)
            for base, accent in zip(_REST_RGB, _ACCENT_RGB)
        )
        fragments.append((f"fg:#{''.join(f'{c:02x}' for c in rgb)}", ch))

    return fragments
