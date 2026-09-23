"""REPL input with a dropdown menu of slash-command suggestions."""

import asyncio
import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app, get_app_or_none
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import (
    ANSI,
    StyleAndTextTuples,
    fragment_list_width,
    to_formatted_text,
)
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.renderer import Renderer
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from . import background
from .emojis import EMOJIS
from .images import IMAGE_EXTENSIONS
from .memory import MEMORY_PATH
from .paths import ENV_PATH
from .theme import (
    BAR_EMPTY,
    CURSOR,
    DIFF_ADD,
    DIM_HEX,
    ERROR,
    RESET_ANSI,
    SPARKLE,
    ansi,
    ptk_sweep_reveal,
)

# Single source of truth for both the completion dropdown and /help.
COMMANDS = [
    ("/model", "pick from the models here, or /model <name> to switch"),
    ("/auto", "toggle autonomous command mode (/auto on|off)"),
    ("/voice", "talk to Flash and hear its replies (/voice on|off)"),
    ("/background", "pixel-art scene behind the prompt (/background <name>)"),
    ("/set", f"set an env var, saved to {ENV_PATH} (/set NAME VALUE)"),
    ("/unset", "remove an env var (/unset NAME)"),
    ("/refresh", "reload config from the env file"),
    ("/memory", f"show saved memory, numbered ({MEMORY_PATH})"),
    ("/forget", "delete one memory by its 1-based index (/forget N)"),
    ("/plan", "show the checklist the model is working through"),
    ("/agents", "watch sub-agents work live (/agents <id> for one)"),
    ("/hook", "let Flash see what you run in VS Code's terminal"),
    ("/clear", "clear saved context"),
    ("/undo", "take back the file changes from the last turn"),
    ("/compact", "summarize the conversation to free up room"),
    ("/context", "show how much of the window is in use"),
    ("/image", "send an image to the model (/image <path> [prompt])"),
    ("/version", "show the current version and check for updates"),
    ("/update", "update Flash to the latest version (pipx installs)"),
    ("/help", "show this help (alias: /?)"),
    ("/bye", "exit Flash (alias: /exit)"),
]


def _is_image_path(path: str) -> bool:
    """Passed to PathCompleter: always show directories (to navigate into),
    and files whose extension is a supported image type."""

    if os.path.isdir(path):
        return True
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


_image_path_completer = PathCompleter(
    expanduser=True, file_filter=_is_image_path
)


def _mention_completer(typed: str) -> PathCompleter:
    """A completer over every file, for @ mentions.

    Dot-entries stay out of the way until one is asked for by name, so a
    bare @ offers the working directory rather than .git and __pycache__.
    """

    show_hidden = os.path.basename(typed).startswith(".")

    return PathCompleter(
        expanduser=True,
        file_filter=lambda path: (
            show_hidden or not os.path.basename(path).startswith(".")
        ),
    )


def _mention_before(text: str) -> Optional[str]:
    """The @ mention being typed at the end of TEXT, if there is one.

    Returns whatever follows the '@', which is "" the moment it is typed,
    so the dropdown opens on the working directory right away. A mention
    only starts at an '@' that opens the line or follows a space, so an
    email address or a decorator halfway through a word does not open it.
    """

    at = text.rfind("@")

    if at == -1 or (at > 0 and not text[at - 1].isspace()):
        return None

    return text[at + 1:]


def _parse_path_arg(
    remainder: str,
) -> Optional[tuple[str, bool]]:
    """Track quoting while scanning the path argument typed so far.

    Returns `(literal_path, in_quote)`: `literal_path` is the path with any
    quote marks stripped out (what's actually on disk), and `in_quote` is
    True if the text currently ends inside a quote the user opened
    themselves. Returns None once an unquoted space ends the path
    argument, which is where /image's optional prompt starts, and where
    an @ mention stops being one.
    """

    literal_chars = []
    quote: Optional[str] = None
    for ch in remainder:
        if quote:
            if ch == quote:
                quote = None
            else:
                literal_chars.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == " ":
            return None
        else:
            literal_chars.append(ch)
    return "".join(literal_chars), quote is not None


class SlashCommandCompleter(Completer):
    """Suggests / commands as the line is typed, and image file paths as
    the argument to /image (auto-quoting suggestions that contain spaces)."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if text.startswith("/background "):
            for name in background.names():
                yield Completion(
                    name,
                    start_position=0,
                    display=f"scene: {name}",
                )
            return

        if text.startswith("/image "):
            remainder = text[len("/image "):]
            parsed = _parse_path_arg(remainder)
            if parsed is None:
                return  # past the path, now typing the optional prompt
            literal_path, in_quote = parsed

            sub_document = Document(
                literal_path, cursor_position=len(literal_path)
            )
            for completion in _image_path_completer.get_completions(
                sub_document, complete_event
            ):
                suffix = completion.text
                if not in_quote and " " in suffix:
                    suffix = f'"{suffix}"'
                yield Completion(
                    suffix, start_position=0, display=completion.display
                )
            return

        # Emoji names, as :smile. Checked after the path branches so a
        # colon inside a path does not swallow the completion.
        words = text.split()

        if words and words[-1].startswith(":"):
            query = words[-1][1:].lower()
            start = -len(words[-1])

            for name, emoji in EMOJIS.items():
                if name.startswith(query):
                    yield Completion(
                        emoji,
                        start_position=start,
                        display=f"{name}: {emoji}",
                    )
            return

        mention = _mention_before(text)
        if mention is not None:
            parsed = _parse_path_arg(mention)
            if parsed is None:
                return  # a space ended the mention
            literal_path, in_quote = parsed

            sub_document = Document(
                literal_path, cursor_position=len(literal_path)
            )
            for completion in _mention_completer(
                literal_path
            ).get_completions(sub_document, complete_event):
                whole = literal_path + completion.text

                # A path with a space in it has to be quoted whole, so
                # the mention is replaced rather than appended to.
                if " " in whole and not in_quote:
                    yield Completion(
                        f'"{whole}"',
                        start_position=-len(mention),
                        display=completion.display,
                    )
                else:
                    yield Completion(
                        completion.text,
                        start_position=0,
                        display=completion.display,
                    )
            return

        if not text.startswith("/") or " " in text:
            return

        for cmd, desc in COMMANDS:
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


def _load_suggestions() -> list[str]:
    try:
        p = Path(__file__).parent / "suggestions.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        items = list(data.get("suggestions", []))
        if items:
            return items
    except (OSError, ValueError):
        pass
    return ["Try /help to see every command"]


_SUGGESTIONS = _load_suggestions()

# Timing for one suggestion's reveal-sweep -> hold -> conceal-sweep -> gap.
_SWEEP_BAND = 1.6
_REVEAL = 0.9
_HOLD = 5.0
_CONCEAL = 0.9
_GAP = 1.2
_CYCLE = _REVEAL + _HOLD + _CONCEAL + _GAP
PLACEHOLDER_REFRESH_SECONDS = 0.08


def _sweep_edge(progress: float, label_len: int) -> float:
    """Map 0..1 sweep progress to an `edge` spanning the full label."""

    span = label_len + 2 * _SWEEP_BAND
    return -_SWEEP_BAND + progress * span


def _suggestion_placeholder() -> StyleAndTextTuples:
    """Current animation frame: one suggestion's letters materializing in a
    coral sweep, holding, then erased by another sweep, cycling through
    `_SUGGESTIONS` over time."""

    # The app does one final render (in its "done" state) right as it's
    # exiting, e.g. on Ctrl+C or Ctrl+D. Without this, that last frame
    # would freeze whatever sweep frame was mid-animation and leave it
    # printed on screen permanently once the session tears down.
    if get_app().is_done:
        return []

    total = _CYCLE * len(_SUGGESTIONS)
    pos = time.monotonic() % total
    idx = int(pos // _CYCLE)
    t = pos - idx * _CYCLE

    label = f"{SPARKLE} {_SUGGESTIONS[idx]}"

    if t < _REVEAL:
        edge = _sweep_edge(t / _REVEAL, len(label))
        revealing = True
    elif t < _REVEAL + _HOLD:
        edge = len(label) + _SWEEP_BAND
        revealing = True
    elif t < _REVEAL + _HOLD + _CONCEAL:
        edge = _sweep_edge((t - _REVEAL - _HOLD) / _CONCEAL, len(label))
        revealing = False
    else:
        return []

    return ptk_sweep_reveal(
        label, edge, revealing=revealing, band=_SWEEP_BAND
    )


# The line pinned under the input, the way a terminal agent carries
# its state: what it is pointed at on the left, what you can type on
# the right.
HINTS = "/ commands   @ files   ! shell"

# Below this the two halves collide, so the hints go and the state stays.
MIN_STATUS_WIDTH = 60


# Whether the backend answered last time it was asked. Green once it
# has, red once it has not, and grey before anything has been sent,
# because "untested" and "broken" are different things to look at.
HEALTH_OK = "ok"
HEALTH_DOWN = "down"
HEALTH_UNKNOWN = "unknown"

HEALTH_DOT = CURSOR

HEALTH_HEX = {
    HEALTH_OK: DIFF_ADD,
    HEALTH_DOWN: ERROR,
    HEALTH_UNKNOWN: DIM_HEX,
}


def input_rule() -> str:
    """The horizontal rule that frames the input area.

    A rule above the prompt and another below it, which is how the
    current crop of agent TUIs mark out where you type: Hermes names
    them input_rule_top and input_rule_bot, Pi calls the result a
    rounded editor. Plain rules rather than a box with sides, because
    the completion dropdown renders between the two and a box would
    have to leave its walls off those rows.

    Drawn from the opening prompt onward. The status line under it
    waits until a turn has happened; the frame itself does not.
    """

    return BAR_EMPTY * shutil.get_terminal_size().columns


def status_segments(
    status: str, health: str = HEALTH_UNKNOWN
) -> list[tuple[str, str]]:
    """The bar as (style name, text) pairs, health dot first.

    The dot is the one part of the line that is not dim, because it is
    the one part that is worth looking at when something is wrong.
    """

    return [
        (f"health.{health}", f" {HEALTH_DOT}"),
        ("", status_line(status, prefix=2)),
    ]


_RULE_STYLE = Style.from_dict({
    "bottom-toolbar": f"noreverse {DIM_HEX} bg:default",
    "bottom-toolbar.text": f"noreverse {DIM_HEX} bg:default",
})


def closing_rule() -> StyleAndTextTuples:
    """The rule under the input, as prompt_toolkit's toolbar wants it.

    The toolbar is the only thing that can draw below the input, so
    the rule that closes the frame has to ride on it. It carries the
    rule and nothing else: the status line sits above the input
    instead, which leaves the completion menu somewhere to open.
    """

    return [("class:bottom-toolbar", input_rule())]


# How far the completion menu may open.
#
# prompt_toolkit draws the menu over the rows above the cursor and no
# further: containers.py caps it at min(height, cursor_position.y),
# which for an inline prompt is however tall the prompt itself is. A
# three row prompt therefore gets a two row menu, which is the single
# visible completion. The prompt fills the gap above its frame to
# keep the frame on the foot of the screen, and those are the rows
# the menu opens over.
MAX_MENU_ROWS = 12

# The fewest rows the menu gets once the conversation fills the screen
# and there is no gap left above the frame. Any rows the prompt grows
# by then scroll the messages up, so it gets a short scrolling list
# rather than the whole thing.
MIN_MENU_ROWS = 4


def menu_headroom() -> int:
    """Blank rows to open above the input for the menu to draw into."""

    app = get_app_or_none()

    if app is None:
        return 0

    state = app.current_buffer.complete_state

    if state is None or not state.completions:
        return 0

    return min(MAX_MENU_ROWS, len(state.completions))


def status_prefix(
    status: Optional[str] = None, health: str = HEALTH_UNKNOWN
) -> str:
    """The status line and the rule, as rows drawn above the input.

    They used to hang off prompt_toolkit's bottom toolbar, which pins
    the layout to the foot of the screen. Nothing can then be drawn
    under the input except the rows reserved for the completion
    dropdown, so the dropdown and a frame that hugs the input were
    competing for the same space and the dropdown lost, down to a
    single visible row.

    Above the input, neither has to give: the input is the last thing
    on screen, so the menu opens into the whole terminal below it, and
    the frame stays three rows whatever the menu is doing.
    """

    rule = ansi(DIM_HEX) + input_rule() + RESET_ANSI + "\n"

    if not status:
        return rule

    dot, rest = status_segments(status, health)

    return (
        ansi(HEALTH_HEX[health]) + dot[1] + RESET_ANSI
        + ansi(DIM_HEX) + rest[1] + RESET_ANSI + "\n"
        + rule
    )


# Nothing is reserved for the completion dropdown. prompt_toolkit
# draws that reservation between the input and the bottom toolbar, so
# any of it stretches the frame into a tall empty box with the closing
# rule stranded at the bottom. With none, the rule hugs the line you
# are typing and the menu simply scrolls the screen when it opens,
# which is what it would do on a full terminal anyway.
def status_line(status: str, prefix: int = 0) -> str:
    """The bar as plain text, with the hints at the right margin.

    `prefix` is how many columns something else has already drawn on
    this line, so the right margin still lands at the right margin.

    Kept separate from `status_prefix` so the same line can be drawn
    by rich while the model is answering, where the prompt is not
    running and its escape codes would land in the wrong place.
    """

    width = shutil.get_terminal_size().columns - prefix

    if width < MIN_STATUS_WIDTH:
        return f" {status}"

    gap = width - len(status) - len(HINTS) - 2

    if gap < 2:
        return f" {status}"

    return f" {status}{' ' * gap}{HINTS} "


_session: Optional[PromptSession] = None

# What read_line returns when `wake` fired instead of the user submitting.
# A NUL can't be typed at the prompt, so no real line can collide with it.
WAKE = "\x00wake"
WAKE_POLL_SECONDS = 0.25

# What read_line returns when the terminal was resized while it waited.
# The picture behind the prompt is ordinary scrolling output, so a
# resize reflows it at the width it was drawn for and leaves it
# mangled. Nothing here can redraw it from inside the prompt, so the
# prompt stands down and lets the caller paint the screen again.
RESIZE = "\x00resize"
RESIZE_POLL_SECONDS = 0.2

# Whatever was half typed when that happened, handed back on the next
# call so a resize does not cost someone their sentence.
_carried = ""


def _take_carried() -> str:
    """The half-typed line a resize interrupted, once."""

    global _carried

    text, _carried = _carried, ""
    return text


def reflowed_rows(screen: Screen, rows: int, columns: int) -> int:
    """How many rows `rows` lines of `screen` take up at `columns` wide.

    A terminal that narrows rewraps whatever it already shows, so a
    full width rule drawn at 120 columns becomes two rows at 80. The
    renderer still thinks the prompt starts where it did, erases from
    there, and every rewrapped row above that point is left behind as
    a stale copy of the frame.
    """

    total = 0

    for y in range(rows):
        row = screen.data_buffer[y]
        used = max(
            (x + 1 for x, cell in row.items()
             if cell.char != " " or cell.style),
            default=0,
        )
        total += max(1, -(-used // columns))

    return total


class SnugRenderer(Renderer):
    """The renderer, minus the two things a resize broke.

    It never stretches the prompt to the foot of the screen. Stock
    prompt_toolkit asks how many rows are left below the cursor and
    fills them all, which is harmless when the prompt already sits at
    the bottom. After a resize it asks again from the top of the
    prompt, on a terminal that may have grown, and the frame opens
    into a tall empty box with the closing rule stranded at the
    bottom until something else forces a redraw.

    And it deals with a size change itself, on whichever render first
    sees it, rather than in a resize handler that races the placeholder
    refresh and the terminal's own rewrapping.
    """

    on_resize: Optional[Callable[[], None]] = None

    # The size the last prompt's last frame went out at. A resize in
    # between prompts, while a reply streams or the caller repaints,
    # happens while no renderer is watching, and this is how the next
    # prompt finds out about it.
    settled = None

    @property
    def _min_available_height(self) -> int:
        # One row is enough for height_is_known to say yes, which is
        # all the bottom toolbar waits on. Anything more is padding.
        return min(self._rows_below, 1)

    @_min_available_height.setter
    def _min_available_height(self, value: int) -> None:
        self._rows_below = value

    @property
    def rows_below(self) -> int:
        """Rows from the top of the prompt to the foot of the screen.

        Zero until the terminal has said where the cursor is.
        """

        return self._rows_below

    def render(self, app, layout, is_done: bool = False) -> None:
        size = self.output.get_size()
        was = self._last_size
        screen = self._last_screen

        if was is not None and screen is not None and size != was:
            if size.columns < was.columns:
                x, y = self._cursor_pos
                up = reflowed_rows(screen, y, size.columns)
                self._cursor_pos = Point(x=x, y=up + x // size.columns)

            self.erase(leave_alternate_screen=False)
            self.request_absolute_cursor_position()

            if self.on_resize is not None:
                self.on_resize()

            # The prompt is standing down for a redraw of the whole
            # screen. A frame drawn now would land on a screen the
            # terminal is still rewrapping and stay there until then.
            if app.future is not None and app.future.done():
                return
        elif (
            not is_done
            and screen is not None
            and layout.container.preferred_height(
                size.columns, size.rows
            ).preferred < screen.height
        ):
            # prompt_toolkit never draws a frame shorter than the last
            # one, so rows the menu gave back stayed on as padding
            # between the input and the closing rule. Wiping the frame
            # and drawing it fresh is the only way it gets shorter.
            rows_below = self._rows_below
            self.erase(leave_alternate_screen=False)
            self._rows_below = rows_below
        elif was is None and SnugRenderer.settled not in (None, size):
            if self.on_resize is not None:
                self.on_resize()

        super().render(app, layout, is_done)
        SnugRenderer.settled = size

        # A frame taller than the rows that were under it scrolled the
        # screen, which leaves its top that much higher up.
        if self._rows_below and self._last_screen is not None:
            self._rows_below = max(
                self._rows_below, self._last_screen.height
            )


def screen_redrawn() -> None:
    """Say the whole screen was just drawn again at its current size.

    The next prompt checks the size against the last frame it drew, to
    catch a resize while no prompt was up. After a redraw that frame is
    stale, and the check would only ask for the same redraw again.

    Measured the way the renderer measures, through prompt_toolkit's
    output. On Windows that is a column narrower than shutil's figure,
    and a size taken from shutil never matched, so every prompt after
    a resize asked for another redraw, forever.
    """

    SnugRenderer.settled = (
        None if _session is None else _session.app.output.get_size()
    )


def _snug(session: PromptSession) -> None:
    """Swap the session's renderer for a SnugRenderer, in place."""

    app = session.app
    renderer = app.renderer
    rows_below = renderer.__dict__.pop("_min_available_height", 0)
    renderer.__class__ = SnugRenderer
    renderer._min_available_height = rows_below

    # The renderer notices a new size on its next frame, and the
    # placeholder redraws several times a second, so the stock handler
    # has nothing left to do but erase from the wrong row first.
    app._on_resize = app.invalidate


def read_line(
    prompt_ansi: str,
    wake: Optional[Callable[[], bool]] = None,
    status: Optional[str] = None,
    health: str = HEALTH_UNKNOWN,
    backdrop: Optional[list[str]] = None,
) -> str:
    """Read one line; suggests / commands in a dropdown while typing one,
    and animates a rotating hint at the cursor while the line is empty.

    If `wake` turns true while the line is still empty, the prompt gives
    way and returns WAKE. It never does while the user has typed
    something, so a half-written message is not snatched away.

    A `status` pins a state line under the input. Passing None leaves it
    off, which is what the opening prompt does: a bottom bar anchors
    prompt_toolkit's layout to the foot of the screen, and the rows it
    reserves for the completion menu would then scroll the banner away
    before it has been read.
    """

    global _session
    if _session is None:
        _session = PromptSession(
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            placeholder=_suggestion_placeholder,
            refresh_interval=PLACEHOLDER_REFRESH_SECONDS,
            erase_when_done=True,
            style=_RULE_STYLE,
        )
        _snug(_session)

    def watch_for_wake() -> None:
        if wake is None:
            return

        app = get_app()

        async def poll() -> None:
            while True:
                await asyncio.sleep(WAKE_POLL_SECONDS)
                if not app.current_buffer.text and wake():
                    app.exit(result=WAKE)
                    return

        app.create_background_task(poll())

    def watch_for_resize() -> None:
        """Stand down when the terminal changes shape.

        The renderer has already wiped the old frame by the time this
        runs, reflowed rows included, so all that is left is handing
        the caller the chance to repaint what sits above the prompt.
        """

        app = get_app()

        def stand_down() -> None:
            global _carried

            if app.is_done:
                return

            _carried = app.current_buffer.text
            app.exit(result=RESIZE)

        renderer = app.renderer
        if isinstance(renderer, SnugRenderer):
            renderer.on_resize = stand_down

    def pre_run() -> None:
        watch_for_wake()
        watch_for_resize()

    def message() -> ANSI:
        """The prompt, rebuilt on every redraw.

        Built once, it was measured for the terminal it was built in,
        so a resize left both rules at the old width and the status
        line padded to a margin that had moved.
        """

        return ANSI(above() + status_prefix(status, health) + prompt_ansi)

    def above() -> str:
        """Everything between the last message and the status line.

        The rows of the picture the caller held back, then blank rows
        down to wherever the frame has to start for it to sit on the
        foot of the screen. Messages run from the top, so until they
        fill the screen there is a gap between them and the frame, and
        the prompt owns it.

        The menu opens up over those rows. They are blank or the
        prompt's own to redraw, so it covers nothing it cannot give
        back and nothing has to move to make room for it. Only once
        the conversation fills the screen, with no gap left, does the
        prompt grow for the menu, by as little as it can.
        """

        held = backdrop or []
        rows = "".join(row + RESET_ANSI + "\n" for row in held)
        app = get_app_or_none()

        if app is None:
            return rows

        renderer = app.renderer
        below = (
            renderer.rows_below
            if isinstance(renderer, SnugRenderer) else 0
        )
        head = (status_prefix(status, health) + prompt_ansi).count("\n")
        gap = max(0, below - len(held) - head - input_rows(app) - 1)

        # The menu can cover every row above the cursor.
        cover = len(held) + gap + head
        wanted = menu_headroom()

        if wanted > cover:
            gap += max(0, min(wanted, MIN_MENU_ROWS) - cover)

        return rows + "\n" * gap

    def input_rows(app) -> int:
        """Rows the line being typed takes up, wrapping included."""

        columns = max(1, app.output.get_size().columns)
        indent = fragment_list_width(
            to_formatted_text(ANSI(prompt_ansi.split("\n")[-1]))
        )

        return sum(
            # One more column for the cursor sitting past the end.
            max(1, -(-(indent + get_cwidth(line) + 1) // columns))
            for line in app.current_buffer.document.lines
        )

    # Nothing reserved under the input. Those rows are drawn whether or
    # not a menu is open, so they show up as dead space below the
    # prompt and lift the frame off the foot of the screen.
    return _session.prompt(
        message,
        default=_take_carried(),
        pre_run=pre_run,
        reserve_space_for_menu=0,
        bottom_toolbar=closing_rule,
    )
