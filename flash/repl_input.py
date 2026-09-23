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
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
from prompt_toolkit.styles import Style

from .images import IMAGE_EXTENSIONS
from .memory import MEMORY_PATH
from .paths import ENV_PATH
from .theme import (
    CURSOR,
    DIFF_ADD,
    DIM_HEX,
    ERROR,
    SPARKLE,
    ptk_sweep_reveal,
)

# Single source of truth for both the completion dropdown and /help.
COMMANDS = [
    ("/model", "pick from the models here, or /model <name> to switch"),
    ("/auto", "toggle autonomous command mode (/auto on|off)"),
    ("/voice", "talk to Flash and hear its replies (/voice on|off)"),
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

# Overrides prompt_toolkit's default bottom bar, which is a reversed
# block of colour. This is meant to read as a footnote, not a widget.
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


_STATUS_STYLE = Style.from_dict({
    "bottom-toolbar": f"noreverse {DIM_HEX} bg:default",
    "bottom-toolbar.text": f"noreverse {DIM_HEX} bg:default",
    f"bottom-toolbar.health.{HEALTH_OK}":
        f"noreverse {HEALTH_HEX[HEALTH_OK]} bg:default",
    f"bottom-toolbar.health.{HEALTH_DOWN}":
        f"noreverse {HEALTH_HEX[HEALTH_DOWN]} bg:default",
    f"bottom-toolbar.health.{HEALTH_UNKNOWN}":
        f"noreverse {HEALTH_HEX[HEALTH_UNKNOWN]} bg:default",
})


# prompt_toolkit keeps eight rows free for the completion dropdown, and
# once a bottom toolbar anchors the layout it draws them whether or not
# a menu is open. Ten rows of banner plus a prompt, those eight and the
# bar comes to twenty, which fits a terminal window and does not fit a
# VS Code panel, so the reservation scales with the room there is.
MAX_MENU_ROWS = 8
MIN_MENU_ROWS = 2


def menu_rows() -> int:
    """How many rows to keep free for the completion dropdown."""

    rows = shutil.get_terminal_size().lines

    return max(MIN_MENU_ROWS, min(MAX_MENU_ROWS, rows // 4))


def status_line(status: str, prefix: int = 0) -> str:
    """The bar as plain text, with the hints at the right margin.

    `prefix` is how many columns something else has already drawn on
    this line, so the right margin still lands at the right margin.

    Kept separate from `status_bar` so the same line can be drawn by
    rich while the model is answering, where prompt_toolkit is not
    running and its formatted-text tuples mean nothing.
    """

    width = shutil.get_terminal_size().columns - prefix

    if width < MIN_STATUS_WIDTH:
        return f" {status}"

    gap = width - len(status) - len(HINTS) - 2

    if gap < 2:
        return f" {status}"

    return f" {status}{' ' * gap}{HINTS} "


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


def status_bar(
    status: str, health: str = HEALTH_UNKNOWN
) -> StyleAndTextTuples:
    """The status line, as prompt_toolkit's bottom toolbar wants it."""

    return [
        (
            f"class:bottom-toolbar.{name}" if name
            else "class:bottom-toolbar",
            text,
        )
        for name, text in status_segments(status, health)
    ]


_session: Optional[PromptSession] = None

# What read_line returns when `wake` fired instead of the user submitting.
# A NUL can't be typed at the prompt, so no real line can collide with it.
WAKE = "\x00wake"
WAKE_POLL_SECONDS = 0.25


def read_line(
    prompt_ansi: str,
    wake: Optional[Callable[[], bool]] = None,
    status: Optional[str] = None,
    health: str = HEALTH_UNKNOWN,
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
            style=_STATUS_STYLE,
        )

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

    return _session.prompt(
        ANSI(prompt_ansi),
        pre_run=watch_for_wake,
        reserve_space_for_menu=menu_rows(),
        bottom_toolbar=(
            (lambda: status_bar(status, health)) if status else None
        ),
    )
