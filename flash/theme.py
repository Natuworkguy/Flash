"""Shared terminal theme for Flash CLI, styled after Claude Code's CLI.

Centralizes the color palette and the "tool call" line format (a bulleted
header line followed by an indented result) so ai.py and tools.py render
consistently through one Console instance.
"""

import json
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Optional

from prompt_toolkit.formatted_text import StyleAndTextTuples
from rich.console import Console
from rich.control import Control
from rich.markdown import Markdown
from rich.text import Text

ACCENT = "#d97757"
DIM = "grey62"
ERROR = "#e5484d"
WARN = "#d9a63f"
DIFF_ADD = "#3fb950"
DIFF_DEL = "#e5484d"


class ScreenConsole(Console):
    """The console, keeping a copy of what it has left on screen.

    A terminal that changes shape rewraps what it shows at the width it
    was printed for, the prompt's frame included, and nothing can put
    that right in place. So after a resize the screen is wiped and
    everything since it was last cleared is printed again, at the width
    the terminal is now. This is where that copy comes from.

    Only what stays counts: a Live's frames are drawn through render
    hooks and wiped again, and the newline it writes on the way out
    goes with them when it was transient.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.transcript: list = []
        self._not_keeping = 0

    @contextmanager
    def unkept(self) -> Iterator[None]:
        """Output in here is not kept, whatever path it takes."""

        self._not_keeping += 1
        try:
            yield
        finally:
            self._not_keeping -= 1

    def print(self, *objects, **kwargs) -> None:
        if not (objects and all(isinstance(o, Control) for o in objects)):
            self.keep(("print", objects, kwargs))
        super().print(*objects, **kwargs)

    def line(self, count: int = 1) -> None:
        # Only Live calls this, to step past its frame, and a transient
        # one wipes the step along with the frame.
        with self.unkept():
            super().line(count)

    def begin_capture(self) -> None:
        self._not_keeping += 1
        super().begin_capture()

    def end_capture(self) -> str:
        self._not_keeping -= 1
        return super().end_capture()

    def keep(self, item) -> None:
        """Note down something that reached the screen.

        A string is raw text, as a subprocess or the terminal's own echo
        of typed input left it. A callable is run again, see draw().
        Anything else is a renderable, or a ("print", objects, kwargs)
        call to make again.
        """

        if not self._not_keeping:
            self.transcript.append(item)

    def draw(self, paint: Callable[[], None]) -> None:
        """Run `paint` now, and again in place of what it printed on a redraw.

        For output measured against the terminal, which kept as printed
        would come back at the width it was printed for: `paint` gets
        to measure again.
        """

        self.keep(paint)
        with self.unkept():
            paint()

    def echo(self, text: str) -> None:
        """Write raw text, keeping it."""

        self.keep(text)
        with self.unkept():
            self.file.write(text)
            self.file.flush()

    def forget(self) -> None:
        """The screen was cleared, so there is nothing on it to redraw."""

        self.transcript = []

    def rendered(self) -> str:
        """Everything kept, as it prints at the terminal's current size."""

        parts = []

        for item in self.transcript:
            if isinstance(item, str):
                parts.append(item)
                continue

            with self.capture() as capture:
                if isinstance(item, tuple) and item[:1] == ("print",):
                    super().print(*item[1], **item[2])
                elif callable(item):
                    item()
                else:
                    super().print(item)

            parts.append(capture.get())

        return "".join(parts)

    def replay(self) -> None:
        """Print everything kept again, at the terminal's current size.

        In one write, so the terminal takes it in as one change rather
        than scrolling through the conversation a print at a time.
        """

        self.file.write(self.rendered())
        self.file.flush()


console = ScreenConsole()


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

    # Nested, as a spark asked something mid-shift is, the outer one
    # gets its sink back when the inner one is done.
    before = getattr(_capture, "sink", None)
    _capture.sink = sink
    try:
        yield
    finally:
        _capture.sink = before


def _sink() -> Optional[ToolSink]:
    return getattr(_capture, "sink", None)


Answerer = Callable[[str], str]


@contextmanager
def answer_from(answerer: Answerer) -> Iterator[None]:
    """Send this thread's y/n questions to ANSWERER instead of stdin.

    ANSWERER gets the question and returns the answer typed, lowered.
    The web UI runs a turn on a thread of its own and asks the browser;
    reading the terminal from there would hang on a keypress nobody is
    going to make.
    """

    before = getattr(_capture, "answerer", None)
    _capture.answerer = answerer
    try:
        yield
    finally:
        _capture.answerer = before


def remote_answer(question: str) -> Optional[str]:
    """The answer from this thread's answerer, or None to ask here."""

    answerer = getattr(_capture, "answerer", None)

    if answerer is None:
        return None

    return (answerer(question) or "").strip().lower()


# Past this many lines a tool's output is cut down on screen, since a
# test run or a directory listing would otherwise push the reply that
# follows off the top. The model always gets the whole of it.
COLLAPSE_AFTER = 12
COLLAPSED_LINES = 5

EXPAND_HINT = "ctrl+o to expand"

# The outputs that were cut this turn, as (tool line, whole output),
# for Ctrl+O to print in full.
_collapsed: list[tuple[str, str]] = []
_last_label = ""


def collapsed() -> list[tuple[str, str]]:
    return list(_collapsed)


def clear_collapsed() -> None:
    """Forget the cut outputs, once a new turn starts making its own."""

    _collapsed.clear()


def tool_line(label: str) -> None:
    """Print a tool-invocation header, e.g. '⏺ Bash(ls -la)'."""

    global _last_label

    sink = _sink()
    if sink:
        sink("line", label, "")
        return

    _last_label = label

    line = Text()
    line.append(f"{BULLET} ", style=ACCENT)
    line.append(label)
    console.print(line)


def capturing() -> bool:
    """Whether this thread's output goes somewhere other than the
    terminal: a sub-agent's log, or the web UI."""

    return _sink() is not None


def tool_file(path: str) -> bool:
    """Hand a file the agent is showing the user to whoever is drawing.

    True when something took it: the web UI, which shows the file
    itself. False in the terminal, where the tool shows it its own way.
    """

    sink = _sink()
    if sink is None:
        return False
    sink("file", path, "")
    return True


def tool_browser(
    path: str, *, run: str, url: str, title: str = "", note: str = "",
) -> bool:
    """Hand whoever is drawing a picture of the agent's browser as it
    stands: one frame of RUN, a page opened and acted on in turn, taken
    after NOTE, the action that got it there.

    True when something took it: the web UI, which shows the browser
    live beside the chat. False in the terminal, which has no picture.
    """

    sink = _sink()
    if sink is None:
        return False
    sink("browser", json.dumps({
        "path": path, "run": run, "url": url, "title": title, "note": note,
    }), "")
    return True


def tool_document(path: str, comments: list[dict]) -> bool:
    """Hand a document to whoever is drawing, with the agent's comments
    on it: each a quote from the document and a note about it.

    True when something took it, as with `tool_file`.
    """

    sink = _sink()
    if sink is None:
        return False
    sink("document", json.dumps({"path": path, "comments": comments}), "")
    return True


def tool_plan(steps: list[dict]) -> bool:
    """Hand the plan's checklist to whoever is drawing, as its steps.

    True when something took it. The web UI draws the boxes itself, and
    a sub-agent keeps no checklist on screen, so neither wants the
    terminal's lines. False in the terminal, where the plan prints.
    """

    sink = _sink()
    if sink is None:
        return False
    sink("plan", json.dumps(steps), "")
    return True


def tool_result(text: str, *, style: str = DIM) -> None:
    """Print an indented result block under the most recent tool_line()."""

    sink = _sink()
    if sink:
        sink("result", text or "", style)
        return

    lines = (text or "").splitlines() or [""]

    if len(lines) > COLLAPSE_AFTER:
        _collapsed.append((_last_label, text))
        hidden = len(lines) - COLLAPSED_LINES

        # A failure says what went wrong at the end, a traceback's last
        # line or a test run's summary, so that is the end kept.
        if style == ERROR:
            lines = [
                f"{ELLIPSIS} {hidden} lines above ({EXPAND_HINT})",
                *lines[-COLLAPSED_LINES:],
            ]
        else:
            lines = [
                *lines[:COLLAPSED_LINES],
                f"{ELLIPSIS} +{hidden} lines ({EXPAND_HINT})",
            ]

    first = Text(f"  {BRANCH}  ", style=style)
    first.append(lines[0], style=style)
    console.print(first)

    for line in lines[1:]:
        console.print(Text(f"     {line}", style=style))


def expand_collapsed() -> None:
    """Print, whole, every tool output this turn cut short."""

    if not _collapsed:
        dim("No tool output was cut short this turn.")
        return

    for label, text in _collapsed:
        head = Text()
        head.append(f"{BULLET} ", style=ACCENT)
        head.append(label or "Tool output")
        console.print(head)

        for index, line in enumerate(text.splitlines()):
            lead = f"  {BRANCH}  " if index == 0 else "     "
            console.print(Text(lead + line, style=DIM))


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


def typed() -> str:
    """input(), lowered and stripped, keeping the line the terminal echoed.

    The terminal draws what is typed at input(), not the console, so
    without this a redraw would bring back the question and not the
    answer.
    """

    answer = input()
    console.keep(answer + "\n")
    return answer.strip().lower()


def confirm(question: str) -> bool:
    """Ask QUESTION on one y/n line. True only for a plain yes."""

    ask = Text(f"  {question} ", style=DIM)
    ask.append("y", style=f"bold {ACCENT}")
    ask.append("/n ", style=DIM)
    console.print(ask, end="")

    try:
        answer = typed()
    except EOFError:
        console.print()
        return False

    console.print()
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
