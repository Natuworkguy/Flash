"""Main App"""

import json
import os
import re
import shlex
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import ollama
from dotenv import load_dotenv
from ollama import ResponseError
from rich.box import ROUNDED
from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from . import agent as subagents
from . import background, checkpoint, context, extensions, plan, terminal
from .cli import parse_args
from .envfile import set_env_var, unset_env_var
from .images import resolve_image_path
from .latex import render_latex
from .memory import forget_memory, list_memory
from .models import fetch_if_missing, pick_model
from .notify import notify_reply_ready
from .paths import ENV_PATH
from .repl_input import (
    EXPAND,
    HEALTH_DOWN,
    HEALTH_HEX,
    HEALTH_OK,
    HEALTH_UNKNOWN,
    MAX_MENU_ROWS,
    RESERVED_COMMANDS,
    RESIZE,
    TOGGLE_AUTO,
    WAKE,
    all_commands,
    read_line,
    screen_redrawn,
    screen_size,
    status_segments,
)
from .stats import Turn, elapsed, window
from .stats import summary as stats_summary
from .sysprompt import (
    get_context_ceiling,
    get_context_limit,
    get_model_system_prompt,
    is_remote,
    model_sees_images,
)
from .theme import (
    ACCENT,
    ACCENT_ANSI,
    BULLET,
    CHEVRON,
    CURSOR,
    DIM,
    DIM_ANSI,
    ELLIPSIS,
    MIDDOT,
    RESET_ANSI,
    WARN,
    ScreenConsole,
    clear_collapsed,
    confirm,
    console,
    expand_collapsed,
    glimmer,
    tool_line,
    tool_result,
    typed,
    warn,
)
from .theme import error as show_error
from .tools import (
    FUNCTIONS,
    MAX_SHELL_TIMEOUT,
    SCRATCH_DIR,
    build_system_prompt,
    clear_user_runs,
    init,
    reason,
    run_extension_command,
    run_tool,
    shell_tool,
    take_pending_images,
    trim_tool_output,
    turn_tools,
    user_runs,
)
from .updater import (
    check_for_update,
    fetch_latest_version,
    is_newer,
    perform_update,
)
from .urlscheme import SchemeError, parse_flash_url, register, unregister
from .version import __version__
from .voice import (
    INSTALL_HINT as VOICE_INSTALL_HINT,
)
from .voice import (
    ensure_models,
    for_speech,
    is_exit_phrase,
    listen,
    missing_packages,
    models_present,
    speak,
)

OLLAMA_HOST_DEFAULT = "http://localhost:11434"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_IMAGE_PROMPT = "Describe this image in detail."

# Sent with the file view_image opened. The image rides on a user
# message because that is where every vision model expects to find
# one; a tool result carries only text.
TOOL_IMAGE_NOTE = (
    "Here is the image you opened with view_image. Answer from what "
    "you can see in it."
)

IMAGE_BACKEND_HINT = (
    "That request carried an image, which makes it much larger and needs "
    "a vision-capable model. If it keeps failing, check the model with "
    "`ollama show <model>` and that the backend is healthy."
)

load_dotenv(dotenv_path=ENV_PATH)


VOICE_PROMPT = """

=== Voice Mode ===
The user is speaking to you, and your reply is read back to them out loud.
Keep it short and plain: whole sentences, no code blocks, tables, or long
lists unless they ask for one, because only the prose is spoken and the
rest is silently dropped. What they said reached you through speech
recognition, so expect missing punctuation and the occasional misheard
word; ask when a name, path, or command sounds wrong rather than acting on
a guess.""".rstrip()


class FlashError(Exception):
    """General error for uncaught exceptions in the main loop"""


def _int_env(name: str, default: int, *, minimum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return max(int(value), minimum)
    except ValueError:
        return default


def _opt_int_env(
    name: str, *, minimum: int
) -> Optional[int]:
    """An override the user set, or None when they left it alone.

    Distinguishing "unset" from a default matters for the history caps:
    unset means the token budget decides, which is almost always the
    better answer, while a number means the user asked for that number
    and gets it.
    """

    value = os.getenv(name)

    if value is None or not value.strip():
        return None

    try:
        return max(int(value), minimum)
    except ValueError:
        return None


class Config:
    """App configuration, re-derived from the environment on demand."""

    host: str
    model: Optional[str]
    # Unset by default: the token budget in context.py decides how
    # much history survives, and these only cap it further when the
    # user has explicitly asked for a smaller one.
    max_history_messages: Optional[int]
    max_history_chars: Optional[int]
    auto_compact: bool
    max_tool_rounds: int
    max_tool_output_chars: int
    max_output_tokens: int
    num_ctx: str
    no_command_confirmation: bool
    show_stats: bool
    voice: bool
    background: str
    prompt: str

    @classmethod
    def refresh(cls) -> None:
        cls.host = os.getenv("OLLAMA_HOST", OLLAMA_HOST_DEFAULT)
        cls.model = os.getenv("MODEL")
        cls.max_history_messages = _opt_int_env(
            "MAX_HISTORY_MESSAGES", minimum=2
        )
        cls.max_history_chars = _opt_int_env(
            "MAX_HISTORY_CHARS", minimum=1000
        )
        cls.auto_compact = bool(_int_env("AUTO_COMPACT", 1, minimum=0))
        cls.max_tool_rounds = _int_env("MAX_TOOL_ROUNDS", 10, minimum=1)
        cls.max_tool_output_chars = _int_env(
            "MAX_TOOL_OUTPUT_CHARS", 1200, minimum=500
        )
        cls.max_output_tokens = _int_env(
            "MAX_OUTPUT_TOKENS", 1024, minimum=128
        )
        cls.num_ctx = (os.getenv("NUM_CTX") or "").strip().lower()
        cls.no_command_confirmation = bool(
            _int_env("NO_COMMAND_CONFIRMATION", 0, minimum=0)
        )
        cls.show_stats = bool(_int_env("SHOW_STATS", 1, minimum=0))
        cls.voice = bool(_int_env("VOICE", 0, minimum=0))
        cls.background = (os.getenv("BACKGROUND") or "").strip()
        cls.prompt = \
            (ACCENT_ANSI + CHEVRON + " " + RESET_ANSI) \
            if cls.host == OLLAMA_HOST_DEFAULT \
            else (
                DIM_ANSI
                + cls.host.removeprefix("http://").removeprefix("https://")
                .partition(":")[0]
                + RESET_ANSI
                + " "
                + ACCENT_ANSI + CHEVRON + " " + RESET_ANSI
            )
        init(cls)


Config.refresh()


def set_config_var(name: str, value: str) -> None:
    """Persist NAME=VALUE to the env file and apply it immediately."""

    os.environ[name] = value
    set_env_var(ENV_PATH, name, value)
    Config.refresh()


def unset_config_var(name: str) -> bool:
    """Remove NAME from the env file and the live environment."""

    removed_from_file = unset_env_var(ENV_PATH, name)
    removed_from_env = os.environ.pop(name, None) is not None
    Config.refresh()
    return removed_from_file or removed_from_env


def refresh_config() -> None:
    """Reload the env file from disk and re-derive Config from it."""

    load_dotenv(dotenv_path=ENV_PATH, override=True)
    Config.refresh()


def _short_path(path: Path) -> str:
    """A path the way a shell prompt writes it, with $HOME as ~."""

    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _short_host(host: str) -> str:
    """The host without its scheme, which is noise in a status line."""

    return host.removeprefix("http://").removeprefix("https://")


# The opening prompt is three rows: the rule above, the line you type
# on, and the rule below. The status line joins above them from the
# second prompt onward, once there is a turn behind it.
FIRST_PROMPT_ROWS = 3

# Rows of the opening screen handed to the prompt to draw rather than
# printed. The completion menu can only open over rows the prompt
# owns, so owning the foot of the picture lets it open over the scene
# instead of growing the prompt and shoving the scene up the screen.
HELD_ROWS = MAX_MENU_ROWS


def _banner_lines(update_version: Optional[str] = None) -> list:
    """What goes inside the welcome box.

    Laid out the way a terminal agent's welcome reads best: the name on
    its own line, then one fact per line underneath. Running the fields
    together wrapped the host mid-URL on a narrow terminal, which is
    the one line someone actually needs when they are pointed at the
    wrong backend.
    """

    title = Text()
    title.append(f"Flash CLI v{__version__}", style="bold")

    fields = Text()
    fields.append("  /help", style=ACCENT)
    fields.append(" for commands\n", style=DIM)
    for label, value in (
        ("model", str(Config.model or "(unset)")),
        ("host", _short_host(Config.host)),
        ("cwd", _short_path(Path.cwd())),
    ):
        fields.append(f"  {label + ':':<7}", style=DIM)
        fields.append(f"{value}\n")
    fields.rstrip()

    lines = [title, Text(""), fields]

    notices = []
    if Config.no_command_confirmation:
        notices.append("autonomous, commands run without confirmation")
    if Config.voice:
        notices.append("voice on, Enter on an empty line speaks")
    if update_version:
        notices.append(f"v{update_version} available, run /update")

    if notices:
        block = Text()
        for index, notice in enumerate(notices):
            if index:
                block.append("\n")
            block.append(f"  {BULLET} {notice}", style=f"bold {ACCENT}")
        lines.extend([Text(""), block])

    return lines


def banner_panel(update_version: Optional[str] = None) -> Panel:
    """The welcome box, built but not drawn."""

    return Panel(
        Group(*_banner_lines(update_version)),
        border_style=DIM,
        box=ROUNDED,
        padding=(0, 1),
        expand=False,
    )


def banner(
    c: Console,
    update_version: Optional[str] = None,
) -> int:
    """Print the welcome box, and answer with the rows it used."""

    panel = banner_panel(update_version)

    c.print(panel)
    # c.print, not print: the blank row has to land in the same stream
    # the panel did, or the height reported below counts a row that
    # went somewhere else.
    c.print()

    # The blank line above counts as one of the rows it used.
    return len(c.render_lines(panel, c.options, pad=False)) + 1


def _overlay_cells(c: Console, renderable, width: int, height: int) -> list:
    """A rich renderable as rows of (character, style) cells.

    Padded out to the full area so the rows a short banner does not
    reach come back as blank cells, which is what lets the scene show
    through underneath them.
    """

    # Width only. Handing rich a height as well stretches the banner
    # to fill it, and a welcome box with its sides running the length
    # of the terminal is not a welcome box.
    options = c.options.update(width=width)
    rows = []

    for line in c.render_lines(renderable, options, pad=True):
        cells = []
        for segment in line:
            cells.extend((char, segment.style) for char in segment.text)
        rows.append(cells)

    while len(rows) < height:
        rows.append([])

    return rows[:height]


def open_screen(
    c: Console, update_version: Optional[str] = None
) -> list[str]:
    """Draw the opening screen, and answer with the rows held back.

    Those rows are the prompt's to draw, see HELD_ROWS.
    """

    held: list[str] = []

    if not paint_launch(c, update_version, held):
        pad_to_bottom(c, banner(c, update_version), held)

    return held


def repaint(c: Console, update_version: Optional[str] = None) -> list[str]:
    """Draw the opening screen again, after the terminal changed shape.

    The picture is ordinary output, so a resize reflows it at the
    width it was drawn for. Nothing can rescue those rows in place;
    they have to be wiped and drawn again at the size the terminal is
    now.
    """

    _clear_screen()

    return open_screen(c, update_version)


def paint_launch(
    c: Console,
    update_version: Optional[str] = None,
    held: Optional[list[str]] = None,
) -> bool:
    """Fill the screen with the scene, banner set on top of it.

    Returns whether it drew anything. Painting only the gap under the
    banner left the picture in a band with black above and below it,
    so the scene now runs from the top of the terminal down to the
    frame around the prompt, and the banner sits on the picture rather
    than punching a hole in it.
    """

    scene = current_scene()

    if scene is None or not c.is_terminal:
        return False

    rows = c.size.height - FIRST_PROMPT_ROWS
    width = c.size.width

    overlay = _overlay_cells(c, banner_panel(update_version), width, rows)
    drawn = background.render(scene, width, rows, overlay=overlay)

    if not drawn:
        return False

    keep = min(HELD_ROWS, len(drawn) - 1) if held is not None else 0
    cut = len(drawn) - max(keep, 0)

    for line in drawn[:cut]:
        c.print(line)

    for line in drawn[cut:]:
        with c.capture() as capture:
            c.print(line)
        held.append(capture.get().rstrip("\n"))

    return True


def _clear_screen(bottom: bool = False) -> None:
    """Wipe the terminal, scrollback included.

    With `bottom`, the cursor is parked on the last row afterwards.
    What prints next then fills the screen from the bottom upward, and
    the prompt stays glued to the foot of the terminal from that point
    on, the way it does once a long session has filled the screen on
    its own. Without it, the conversation runs down from the top and
    the prompt keeps its own frame on the last rows.

    The console's transcript goes with it: there is nothing left on
    screen to draw again after a resize.
    """

    if not console.is_terminal:
        return

    console.forget()
    print("\x1b[H\x1b[2J\x1b[3J", end="", flush=True)

    if bottom:
        print(f"\x1b[{console.size.height};1H", end="", flush=True)


# How long the terminal has to hold one size before it is redrawn. A
# drag resizes it many times a second, and each redraw prints the whole
# conversation again, so only the size it comes to rest at is drawn.
RESIZE_SETTLE_SECONDS = 0.25
RESIZE_SETTLE_LIMIT_SECONDS = 2.0


def _settle_size() -> None:
    """Wait for the terminal to stop changing shape, up to a limit."""

    size = shutil.get_terminal_size()
    until = time.monotonic() + RESIZE_SETTLE_LIMIT_SECONDS

    while time.monotonic() < until:
        time.sleep(RESIZE_SETTLE_SECONDS)
        now = shutil.get_terminal_size()

        if now == size:
            return

        size = now


def redraw_conversation(c: ScreenConsole) -> None:
    """Wipe the screen and print the conversation on it again.

    The terminal rewraps what it shows when it changes shape, at the
    width it was printed for, and the prompt's frame on the last rows
    goes with it. Printing it all again lets rich wrap it for the width
    the terminal is now, and gives the prompt a clean screen to pin its
    frame to the foot of.
    """

    if not c.is_terminal:
        return

    # The wipe and everything after it go out as one write, inside a
    # synchronized update where the terminal has them, so it goes from
    # the old screen to the new one without showing the steps between.
    c.file.write(
        SYNC_BEGIN + "\x1b[H\x1b[2J\x1b[3J" + c.rendered() + SYNC_END
    )
    c.file.flush()


# DEC mode 2026. Terminals that know it hold the screen still between
# the two and show the result at once; the rest ignore both.
SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"


def _sync(begin: bool) -> None:
    """Open or close a synchronized update, see SYNC_BEGIN."""

    if console.is_terminal:
        print(SYNC_BEGIN if begin else SYNC_END, end="", flush=True)


_background_notices: set = set()


def _note_background(message: str) -> None:
    """Say once why the chosen background is not showing.

    A scene that quietly fails to draw looks like the feature is
    broken rather than like the file is.
    """

    if Config.background in _background_notices:
        return

    _background_notices.add(Config.background)
    warn(f"  {message}")


def current_scene() -> Optional[background.Scene]:
    """The scene the user picked, if it can be drawn at all."""

    if not Config.background:
        return None

    if not background.drawable():
        _note_background(
            "This terminal cannot draw the half block a background is "
            "made of, so BACKGROUND was ignored."
        )
        return None

    path = background.find(Config.background)

    if path is None:
        _note_background(
            f"No background called {Config.background!r}. "
            "Run /background to see what there is."
        )
        return None

    try:
        return background.load(path)
    except background.SceneError as exc:
        _note_background(str(exc))
        return None


# Tall enough to read as a picture, short enough not to shove the
# conversation off the screen when someone is just browsing scenes.
PREVIEW_ROWS = 12


def _preview_scene(scene: background.Scene) -> None:
    """Draw a scene at once, so switching shows what you switched to."""

    def paint() -> None:
        for line in background.render(
            scene, console.size.width, PREVIEW_ROWS
        ):
            console.print(line)

    # Cut to the terminal's width, so a redraw has to cut it again.
    console.draw(paint)


def _list_backgrounds(scenes: list) -> None:
    """Every scene there is, with the current one marked."""

    body = Text()
    body.append("\nBackgrounds\n\n", style="bold")

    for name in scenes:
        current = name == Config.background
        body.append(
            f"  {BULLET} " if current else "    ",
            style=ACCENT,
        )
        body.append(f"{name:<12}", style="" if current else DIM)

        path = background.find(name)
        try:
            title = background.load(path).name if path else ""
        except background.SceneError as exc:
            body.append(f"unreadable: {exc}\n", style=WARN)
            continue

        body.append(f"{title}\n", style=DIM)

    body.append(
        "\n  /background <name> to switch, /background off to stop\n",
        style=DIM,
    )
    console.print(body)


def _background_command(arg: str) -> None:
    """/background on its own, with a name, or with off."""

    if arg.lower() in ("off", "none", "no", "0"):
        unset_config_var("BACKGROUND")
        _background_notices.clear()
        console.print(Text("Background off.", style=DIM))
        return

    scenes = background.names()

    if not scenes:
        warn(
            "No scenes found. Drop .scene files in "
            f"{background.user_dir()}."
        )
        return

    if not arg:
        _list_backgrounds(scenes)
        return

    path = background.find(arg)

    if path is None:
        warn(
            f"No background called {arg!r}. "
            f"There is: {', '.join(scenes)}."
        )
        return

    try:
        scene = background.load(path)
    except background.SceneError as exc:
        show_error(str(exc))
        return

    set_config_var("BACKGROUND", path.stem)
    _background_notices.clear()

    if not background.drawable():
        warn("Saved, but this terminal cannot draw it.")
        return

    console.print(Text(f"Background set to {scene.name}.", style=DIM))
    _preview_scene(scene)


def pad_to_bottom(
    c: Console, used: int, held: Optional[list[str]] = None
) -> int:
    """Push the opening prompt down to the foot of the screen.

    A prompt drawn straight under the banner leaves the rest of the
    terminal empty below it, which reads as a window that has not
    finished loading. Dropping the blank rows above instead puts the
    input where every other agent TUI keeps it, at the bottom, with
    the banner above it.

    Only the first screen needs this. After a turn or two the
    conversation has filled the terminal and the prompt sits at the
    bottom on its own.
    """

    if not c.is_terminal:
        return 0

    room = c.size.height - used - FIRST_PROMPT_ROWS

    if room <= 0:
        return 0

    keep = min(HELD_ROWS, room) if held is not None else 0

    print("\n" * (room - keep), end="")

    if held is not None:
        held.extend([""] * keep)

    return room


def _message(
    role: str,
    text: str,
    images: Optional[list] = None,
) -> dict:
    message: dict = {"role": role, "content": text}
    if images:
        message["images"] = images
    return message


def _message_text(message: dict) -> str:
    return message.get("content", "") or ""


def _worth_keeping(messages: list[dict], start: int) -> list[dict]:
    """This turn's tool traffic, as the next turn should remember it.

    Without this the history kept only the model's closing prose, so
    the next turn could read a file, be told what the file said, and
    have no record of either.

    Two things do not survive. The mid-loop nudge to wrap up is Flash
    talking to the model, not part of the conversation. And the message
    carrying an image a tool opened is dropped whole: the bytes cost
    more than any later turn gets back from them, and the tool result
    just above it already records that the image was opened, so the
    exchange still reads correctly without it.
    """

    kept = []

    for message in messages[start:]:
        if message.get("role") == "system" or message.get("images"):
            continue

        kept.append(message)

    return kept


_tool_schema_tokens: dict[int, int] = {}


def _tools_overhead(tools_arg) -> int:
    """What the tool schemas cost, measured once per tool set.

    Serialising two dozen JSON schemas is nothing once a turn and
    wasteful a dozen times a second, which is how often the waiting
    view redraws now that it carries the status line.
    """

    if not tools_arg:
        return 0

    key = len(tools_arg)

    if key not in _tool_schema_tokens:
        try:
            _tool_schema_tokens[key] = context.estimate_tokens(
                json.dumps(tools_arg)
            )
        except (TypeError, ValueError):
            _tool_schema_tokens[key] = 0

    return _tool_schema_tokens[key]


def _history_budget() -> int:
    """How many tokens of conversation this model can afford to keep.

    Measured against the window the turn will actually run in, minus
    what the request carries besides the history: the system prompt,
    the tool schemas, and room for the reply.
    """

    overhead = context.estimate_tokens(_session_system_prompt())
    overhead += _tools_overhead(turn_tools())

    return context.history_budget(
        _context_limit(),
        system_tokens=overhead,
        output_tokens=Config.max_output_tokens,
    )


def _apply_legacy_caps(messages: list[dict]) -> list[dict]:
    """Honour MAX_HISTORY_MESSAGES and MAX_HISTORY_CHARS if they are set.

    Both used to have defaults that governed every session. They are
    overrides now, so a user who pinned one still gets it and everyone
    else gets the token budget instead.
    """

    kept = messages

    if Config.max_history_messages is not None:
        kept = kept[-Config.max_history_messages:]

    if Config.max_history_chars is not None:
        while (
            len(kept) > 1
            and sum(len(_message_text(m)) for m in kept)
            > Config.max_history_chars
        ):
            kept = kept[1:]

    return kept


def _trim_history(messages: list[dict]) -> list[dict]:
    """Fit MESSAGES into the budget in place; return what fell off.

    Whole blocks go at a time, so a tool result is never left behind
    without the call that produced it. What comes back is the material
    a compaction pass would summarize.
    """

    result = context.trim(messages, _history_budget())
    kept = _apply_legacy_caps(result.kept)

    # The caps only ever take messages off the front, so what they cut
    # is the prefix of result.kept that is no longer there. Counted by
    # length rather than by value: two identical messages compare equal,
    # and `not in` would report neither of them as dropped.
    capped = result.kept[:len(result.kept) - len(kept)]

    messages[:] = kept

    return result.dropped + capped


def _summarize(
    console: Console, client: "ollama.Client", messages: list[dict]
) -> Optional[str]:
    """Have the model condense MESSAGES into a few lines, or None."""

    if not messages:
        return None

    summary, _, _, err = _chat_retry_until_response(
        console, client, context.summary_request(messages), None
    )

    if err or not summary.strip():
        return None

    return summary.strip()


def _compact(
    console: Console,
    client: "ollama.Client",
    messages: list[dict],
    dropped: list[dict],
) -> bool:
    """Replace DROPPED with a summary at the head of MESSAGES.

    Without this, running out of room simply loses the start of the
    session: what the user originally asked for, and every decision
    made before the window filled. A few lines of summary cost far less
    than the turns they stand in for and keep the thread intact.
    """

    if not dropped:
        return False

    tool_line(f"Compact({len(dropped)} earlier messages)")

    carried = [m for m in dropped if not context.is_summary(m)]
    summary = _summarize(console, client, carried)

    if summary is None:
        tool_result(
            "Could not summarize; the earlier turns were dropped.",
            style=WARN,
        )
        return False

    messages[:] = context.merge_summary(messages, summary)
    tool_result(
        f"Summarized into {context.estimate_tokens(summary)} tokens"
    )

    return True


def _fit_and_compact(
    console: Console, client: "ollama.Client", messages: list[dict]
) -> None:
    """Trim to the budget, summarizing whatever that costs."""

    dropped = _trim_history(messages)

    if dropped and Config.auto_compact:
        _compact(console, client, messages, dropped)
        # The summary takes up room of its own, so make sure the
        # result still fits rather than trusting that it does.
        _trim_history(messages)


def _direct_shell_command(
    text: str,
) -> Optional[str]:
    if text.startswith("!"):
        cmd = text[1:].strip()
        for prefix in ["shell ", "run "]:
            if cmd.startswith(prefix):
                return cmd[len(prefix):].strip()
        return cmd

    return None


def _tool_limit_message() -> dict:
    return {
        "role": "system",
        "content": (
            "The tool-calling loop has reached its limit and the assistant "
            "has run out of tokens. Answer the original request now using "
            "the tool results above. Do not call any more tools."
        ),
    }


def _response_parts(response) -> tuple[str, str, list]:
    message = getattr(response, "message", None)

    if message is None:
        return "", "", []

    text = getattr(message, "content", "") or ""
    thinking = getattr(message, "thinking", "") or ""
    tool_calls = list(getattr(message, "tool_calls", None) or [])

    return text, thinking, tool_calls


def _render_thinking(text: str) -> None:
    """Show the reasoning a thinking model returns alongside its reply.

    Ollama sends it in `message.thinking`, separate from the content, so
    it only appears if something asks for it. The `reason` tool already
    draws a thought, so hand it over rather than drawing it twice.
    """

    body = text.strip()

    if not body:
        return

    reason(body)


def _tool_call_name_args(call) -> tuple[str, dict]:
    function = getattr(call, "function", None)
    name = getattr(function, "name", "") or ""
    args = getattr(function, "arguments", None) or {}

    return name, dict(args)


def _clear_scratch_dir() -> None:
    if not SCRATCH_DIR:
        return

    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)


def _chat_options() -> dict:
    """The per-call options Flash sends, on top of the model's own."""

    options: dict = {"num_predict": Config.max_output_tokens}
    num_ctx = _num_ctx()

    if num_ctx:
        options["num_ctx"] = num_ctx

    return options


def _chat(client: "ollama.Client", messages: list, tools_arg=None):
    if Config.model is None:
        raise FlashError(
            "MODEL is not set. Please set it in environment variable or "
            f"in {ENV_PATH} file."
        )

    return client.chat(
        model=Config.model,  # pyright: ignore[reportArgumentType]
        messages=messages,
        tools=tools_arg,
        options=_chat_options(),
    )


# Sub-agents send the same options: Ollama reloads a model whenever the
# requested num_ctx changes, so mismatched requests would thrash it.
subagents.chat_options = _chat_options


_model_system_prompts: dict[str, str] = {}
_context_limits: dict[str, Optional[int]] = {}
_context_ceilings: dict[str, Optional[int]] = {}
_context_notices: set[str] = set()
_num_ctx_notices: set[str] = set()
NUM_CTX_MAX = "max"


def _session_system_prompt(heard: bool = False) -> str:
    """Flash's system prompt, with the current model's own prepended.

    Cached per model name, since /api/show costs a round trip and the
    answer only changes when the model does.
    """

    model = Config.model or ""

    if model not in _model_system_prompts:
        _model_system_prompts[model] = get_model_system_prompt(
            Config.host, model
        )

    prompt = build_system_prompt(_model_system_prompts[model])

    return prompt + VOICE_PROMPT if heard else prompt


def _state_pair(entry: Any) -> dict:
    """One state as {"now", "then"}, whatever shape the file used.

    A bare string still works and reads back as its own past tense,
    which is wrong but harmless, and better than dropping the state.
    """

    if isinstance(entry, dict):
        now = str(entry.get("now", "")).strip()
        then = str(entry.get("then", "")).strip()
    else:
        now = str(entry).strip()
        then = ""

    if not now:
        return {}

    return {"now": now, "then": then or now}


def _load_states(key: str, fallback: list[dict]) -> list[dict]:
    try:
        p = Path(__file__).parent / "thinking_states.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        states = [_state_pair(entry) for entry in data.get(key, [])]
        states = [state for state in states if state]
        if not states:
            raise ValueError("no states")
        return states
    except (ValueError, OSError):
        return fallback


def _load_thinking_states() -> list[dict]:
    return _load_states("states", [
        {"now": "Thinking", "then": "Thought"},
        {"now": "Pondering", "then": "Pondered"},
        {"now": "Analyzing", "then": "Analyzed"},
    ])


def _load_image_thinking_states() -> list[dict]:
    return _load_states("image_states", [
        {"now": "Examining the image", "then": "Examined the image"},
        {"now": "Looking closely", "then": "Looked closely"},
    ])


_thinking_state_index = 0


def _next_thinking_state(states: list[dict]) -> dict:
    global _thinking_state_index
    state = states[_thinking_state_index % len(states)]
    _thinking_state_index += 1
    return state


GLIMMER_SPEED = 10.0  # characters per second
GLIMMER_SPREAD = 2.5
GLIMMER_FRAME_SECONDS = 0.08

# How long a wait runs before the spinner says how to end it.
STOP_HINT_SECONDS = 4

MAX_CHAT_RETRIES = 2
RETRY_DELAY_SECONDS = 2.0
FINAL_RESPONSE_RETRIES = 2


def _chat_with_retries(
    client: "ollama.Client", messages: list, tools_arg=None
) -> tuple[Optional[object], Optional[str]]:
    """Call _chat, retrying transient backend errors before giving up."""

    detail = ""
    for attempt in range(1, MAX_CHAT_RETRIES + 2):
        try:
            return _chat(client, messages, tools_arg), None
        except ResponseError as exc:
            detail = str(exc)
        except Exception as exc:  # noqa: BLE001
            detail = f"Could not reach Ollama at {Config.host}. {exc}"

        if attempt > MAX_CHAT_RETRIES:
            break

        tool_line(f"Retry({attempt}/{MAX_CHAT_RETRIES})")
        tool_result(f"{detail}\nRetrying in {RETRY_DELAY_SECONDS:g}s...")
        time.sleep(RETRY_DELAY_SECONDS)

    return None, detail


def _try_chat(
    client: "ollama.Client",
    messages: list,
    live,
    tools_arg=None,
    *,
    is_image: bool = False,
    bar: Optional[Callable[[], str]] = None,
    turn: Optional[Turn] = None,
) -> tuple[Optional[object], Optional[str]]:
    states = _load_image_thinking_states() if is_image \
        else _load_thinking_states()
    state = _next_thinking_state(states)
    word = f"{state['now']}{ELLIPSIS}"
    period = len(word) + 2 * GLIMMER_SPREAD
    stop_event = threading.Event()
    start = time.monotonic()

    def _label(elapsed: float) -> str:
        offset = (elapsed * GLIMMER_SPEED) % period - GLIMMER_SPREAD
        shine = glimmer(word, offset, GLIMMER_SPREAD)
        # The way out only appears once the wait is long enough to want
        # one, so a quick answer is not decorated with an escape hatch.
        stop = "   ctrl+c to stop" if elapsed >= STOP_HINT_SECONDS else ""
        return (
            f"[bold]{shine}[/bold] "
            f"[{DIM}]({int(elapsed)}s{stop})[/{DIM}]"
        )

    def _frame(elapsed: float):
        spinner = Spinner(
            "point",
            text=Text.from_markup(_label(elapsed)),
            style=ACCENT,
            speed=5,
        )

        # Rebuilt each frame rather than captured once: sub-agents
        # finish while the model is writing, which is exactly when a
        # count frozen at the start of the turn would be wrong.
        line = bar() if bar is not None else None

        if line is None or not line.plain.strip():
            return spinner

        # A top-level line rather than more text beside the spinner, so
        # it sits at column zero and reads as the same bar the prompt
        # carries rather than a continuation of the label.
        return Group(spinner, line)

    def _rotate():
        while True:
            try:
                live.update(_frame(time.monotonic() - start))
            except ValueError:
                pass
            if stop_event.wait(GLIMMER_FRAME_SECONDS):
                break

    t = threading.Thread(target=_rotate, daemon=True)
    t.start()

    try:
        return _chat_with_retries(client, messages, tools_arg)
    finally:
        stop_event.set()
        t.join(timeout=0.1)

        if turn is not None:
            turn.note_wait(state["then"], time.monotonic() - start)


# console.status() draws one line and nothing else, so the waiting view
# is a Live of its own: the spinner on top, the status bar under it.
GLIMMER_REFRESH_PER_SECOND = max(1, round(1 / GLIMMER_FRAME_SECONDS))


def _chat_with_status(
    console: Console,
    client: "ollama.Client",
    messages: list,
    tools_arg=None,
    *,
    is_image: bool = False,
    bar: Optional[Callable[[], str]] = None,
    turn: Optional[Turn] = None,
) -> tuple[Optional[object], Optional[str]]:
    with Live(
        Spinner(
            "point",
            text=Text.from_markup(f"[bold]Thinking{ELLIPSIS}[/bold]"),
            style=ACCENT,
            speed=5,
        ),
        console=console,
        refresh_per_second=GLIMMER_REFRESH_PER_SECOND,
        transient=True,
    ) as live:
        return _try_chat(
            client, messages, live, tools_arg,
            is_image=is_image, bar=bar, turn=turn,
        )


def _chat_retry_until_response(
    console: Console,
    client: "ollama.Client",
    messages: list,
    tools_arg=None,
    *,
    is_image: bool = False,
    turn: Optional[Turn] = None,
    bar: Optional[Callable[[], str]] = None,
) -> tuple[str, str, list, Optional[str]]:
    """Call the model, retrying up to FINAL_RESPONSE_RETRIES times if it
    comes back with neither reply text nor a tool call to make."""

    final = ""
    thinking = ""
    tool_calls: list = []
    for attempt in range(1, FINAL_RESPONSE_RETRIES + 2):
        res, err = _chat_with_status(
            console, client, messages, tools_arg,
            is_image=is_image, bar=bar, turn=turn,
        )
        _note_backend(err is None)

        if err:
            return "", "", [], err

        if turn is not None:
            turn.add(res)

        final, thinking, tool_calls = _response_parts(res)
        if final.strip() or tool_calls or attempt > FINAL_RESPONSE_RETRIES:
            break

        tool_line(
            f"Retry({attempt}/{FINAL_RESPONSE_RETRIES}) no response yet"
        )
        messages = messages + [{
            "role": "system",
            "content": "Please provide a final response to the user.",
        }]

    return final, thinking, tool_calls, None


def _context_ceiling() -> Optional[int]:
    """The longest window the active model could do, asked once."""

    model = Config.model or ""

    if model not in _context_ceilings:
        _context_ceilings[model] = get_context_ceiling(Config.host, model)

    return _context_ceilings[model]


def _num_ctx() -> int:
    """The window Flash asks Ollama for, or 0 to leave it alone.

    NUM_CTX takes a token count or "max", where max is the model's own
    ceiling. Max is opt-in on purpose: Ollama allocates the cache at
    load whether the session fills it or not, so nothing here picks it
    for someone who did not ask for it.
    """

    setting = Config.num_ctx

    if not setting:
        return 0

    if setting == NUM_CTX_MAX:
        ceiling = _context_ceiling()

        if ceiling:
            return ceiling

        model = Config.model or ""
        why = (
            "runs on Ollama's cloud, so its architecture is not readable "
            "from here"
            if is_remote(Config.host, model)
            else "does not report one"
        )
        _note_num_ctx(
            f"NUM_CTX=max changed nothing: {model} {why}. "
            "Set NUM_CTX to a token count instead."
        )

        return 0

    if setting.isdigit():
        return int(setting)

    _note_num_ctx(
        f"NUM_CTX is set to {setting!r}, which is neither a token count "
        f"nor {NUM_CTX_MAX!r}, so it was ignored."
    )

    return 0


def _note_num_ctx(message: str) -> None:
    """Say once why NUM_CTX did nothing.

    A setting that is quietly ignored is worse than one never set: the
    user believes the window changed and reads every number after it in
    that belief.
    """

    key = f"{Config.model}:{Config.num_ctx}"

    if key in _num_ctx_notices:
        return

    _num_ctx_notices.add(key)
    warn(f"  {message}")


def _context_limit() -> Optional[int]:
    """The window this turn ran in, or None if nobody set one.

    What Flash asks for wins, since that is what Ollama allocates, then
    whatever the Modelfile pins. A model pinning nothing, with NUM_CTX
    unset, has no window worth quoting.
    """

    asked = _num_ctx()

    if asked:
        return asked

    model = Config.model or ""

    if model not in _context_limits:
        _context_limits[model] = get_context_limit(Config.host, model)

    return _context_limits[model]


def _note_unpinned_context() -> None:
    """Say once per model that it runs in Ollama's default window.

    A model pinning no num_ctx gets whatever Ollama defaults to, small
    enough to quietly drop the top of a long session. Fixing that costs
    memory, so the choice stays the user's; this is the line that lets
    them know there is one to make.
    """

    model = Config.model or ""

    if model in _context_notices:
        return

    _context_notices.add(model)

    note = Text(
        "  no context window pinned, so Ollama's default applies",
        style=DIM,
    )
    ceiling = _context_ceiling()

    if ceiling:
        note.append(
            f"\n  {model} goes up to {window(ceiling)}: "
            "set NUM_CTX to a size, or to max"
        )

    console.print(note)


TURN_LABEL_MAX = 48


def _turn_label(text: str) -> str:
    """A short name for the turn, so /undo can say what it would revert."""

    line = " ".join(text.split())

    if len(line) <= TURN_LABEL_MAX:
        return line

    return line[:TURN_LABEL_MAX - 1].rstrip() + ELLIPSIS


# Whether the backend answered the last time it was asked. Nothing
# here polls it: a request either came back or it did not, and that is
# the only evidence worth showing.
_backend_health = HEALTH_UNKNOWN


def _note_backend(ok: bool) -> None:
    """Record how the last request to the backend went."""

    global _backend_health
    _backend_health = HEALTH_OK if ok else HEALTH_DOWN


def _bar_text(messages: list[dict]) -> Text:
    """The status bar as rich draws it, for the waiting view."""

    dot, rest = status_segments(_status_text(messages), _backend_health)

    line = Text()
    line.append(dot[1], style=HEALTH_HEX[_backend_health])
    line.append(rest[1], style=DIM)

    return line


def _status_text(messages: list[dict]) -> str:
    """The dim line under the prompt: what Flash is currently pointed at.

    Only what changes the next answer earns a place here. The model is
    always worth saying, the host only when it is not this machine, the
    sub-agents only while some are still working, and the two modes
    that change what happens without being asked again.
    """

    parts = [str(Config.model or "no model")]

    if Config.host != OLLAMA_HOST_DEFAULT:
        parts.append(_short_host(Config.host))

    budget = _history_budget()

    if budget and messages:
        used = context.total_tokens(messages)
        # Capped: history is trimmed on the way into a turn, not out of
        # one, so a reading above the budget is real but says "full"
        # rather than anything the reader can act on.
        share = min(100, round(100 * used / budget))
        parts.append(f"context {share}%")

    agents = subagents.running_count()

    if agents:
        parts.append(f"{agents} agent{'' if agents == 1 else 's'}")

    if Config.no_command_confirmation:
        parts.append("auto")

    if Config.voice:
        parts.append("voice")

    return "   ".join(parts)


def _render_context(messages: list[dict]) -> None:
    """Show how much room the conversation is using, and what /undo holds."""

    budget = _history_budget()
    used = context.total_tokens(messages)
    limit = _context_limit()
    share = round(100 * used / budget) if budget else 0

    body = Text()
    body.append("history   ", style=DIM)
    body.append(f"{used} of {budget} tokens ({share}%)\n")
    body.append("messages  ", style=DIM)
    body.append(f"{len(messages)}\n")
    body.append("window    ", style=DIM)
    body.append(f"{window(limit)}\n" if limit else "not pinned\n")
    body.append("compact   ", style=DIM)
    body.append(
        "automatic when full\n" if Config.auto_compact
        else "off; run /compact by hand\n"
    )

    if any(context.is_summary(message) for message in messages):
        body.append("summary   ", style=DIM)
        body.append("earlier turns have been summarized\n")

    body.append("undo      ", style=DIM)
    body.append(checkpoint.describe())

    console.print(body)


def _clock() -> str:
    """The wall clock the way a person reads it: 7:32 PM."""

    return time.strftime("%I:%M %p").lstrip("0")


def _render_done(turn: Turn) -> None:
    """Close the turn out: what the wait was, how long, and when it ended.

    The state is the one the spinner opened with, in the past tense,
    so the line reads as that same thought finishing rather than as a
    new one starting.
    """

    if not turn.state:
        return

    console.print(Text(
        f"  {turn.state} for {elapsed(turn.waited)} "
        f"{MIDDOT} done {_clock()}",
        style=DIM,
    ))


def _render_stats(turn: Turn) -> None:
    """Print what the finished turn cost, unless SHOW_STATS turns it off."""

    if not Config.show_stats:
        return

    limit = _context_limit()
    line = stats_summary(turn, limit)

    if line is not None:
        console.print(line)

    if limit is None:
        _note_unpinned_context()


# A woken turn can start another sub-agent, and a failing backend would
# wake straight back up, so wakes stop after this many without the user
# writing; answers after that ride along with their next message.
MAX_WAKES_IN_A_ROW = 3

WAKE_NOTE = (
    "(The user has not written anything new. You were woken because a "
    "sub-agent finished; tell them what it found.)"
)


def _announce_wake() -> None:
    """Show why the model is taking a turn nobody asked for."""

    for entry in subagents.unseen():
        verb = "finished" if entry.status == subagents.DONE else "failed"
        tool_line(f"Sub-agent {entry.id} {verb}")


def _note_running_agents() -> None:
    """Point at /agents when sub-agents outlive the turn that started them."""

    count = subagents.running_count()

    if count:
        console.print(Text(
            f"  {count} sub-agent{'' if count == 1 else 's'} still "
            "running · /agents to watch",
            style=DIM,
        ))


def _hook_command(arg: str) -> None:
    """/hook, /hook install, /hook remove: the VS Code terminal hook."""

    shell = terminal.current_shell()
    if not shell:
        warn(
            "The terminal hook supports zsh and bash; your shell is "
            f"{os.environ.get('SHELL') or 'unknown'}."
        )
        return

    rc = terminal.rc_path(shell)

    if arg == "install":
        if terminal.installed(shell):
            terminal.write_hook(shell)
            console.print(Text(f"Already set up in {rc}.", style=DIM))
            return
        console.print(Text(
            f"This adds three lines to {rc} that load "
            f"{terminal.hook_path(shell)} in VS Code's terminal only:\n"
            f"{terminal.rc_block(shell)}",
            style=DIM,
        ))
        if confirm("Add them?"):
            console.print(Text(terminal.install(shell), style=DIM))
        return

    if arg == "remove":
        console.print(Text(terminal.remove(shell), style=DIM))
        return

    if arg:
        warn("Usage: /hook [install|remove]")
        return

    if terminal.installed(shell):
        console.print(Text(
            f"Set up in {rc}: Flash sees the commands you run in VS "
            "Code's terminal and their exit codes. /hook remove turns it "
            "off.",
            style=DIM,
        ))
    else:
        console.print(Text(
            "Not set up. /hook install lets Flash see the commands you run "
            "in VS Code's terminal, so it knows what just broke.",
            style=DIM,
        ))


EXTENSION_USAGE = (
    "Usage: /extension [list] | install github@owner/repo | "
    "remove <name>"
)


def _extensions_changed() -> None:
    """Drop what was worked out from the old set of extensions."""

    _tool_schema_tokens.clear()
    _background_notices.clear()


def _describe_extension(
    ext: extensions.Extension, source: str = ""
) -> Text:
    """One extension: its name, what it is, and what it adds."""

    body = Text()
    body.append(f"  {BULLET} ", style=ACCENT)
    body.append(ext.name, style="bold")

    if ext.version:
        body.append(f" v{ext.version}", style=DIM)
    if ext.description:
        body.append(f"  {ext.description}", style=DIM)

    body.append("\n")

    source = source or ext.source
    lines = [f"from {source}"] if source else []

    for line in lines + ext.contents():
        body.append(f"    {line}\n", style=DIM)

    return body


def _list_extensions() -> None:
    installed = extensions.installed()
    broken = extensions.problems()

    if not installed and not broken:
        console.print(Text(
            "No extensions installed. /extension install "
            "github@owner/repo adds one.",
            style=DIM,
        ))
        return

    body = Text()
    body.append("\nExtensions\n\n", style="bold")

    for ext in installed:
        body.append_text(_describe_extension(ext))

    for problem in broken:
        body.append(f"  {problem}\n", style=WARN)

    body.append(
        f"\n  Installed in {_short_path(extensions.extensions_dir())}. "
        "/extension remove <name> takes one out.\n",
        style=DIM,
    )
    console.print(body)


def _install_extension(spec: str) -> bool:
    """Fetch, show, confirm, install. False only on an actual failure."""

    try:
        source = extensions.canonical(spec)
    except extensions.ExtensionError as exc:
        show_error(str(exc))
        return False

    try:
        with console.status(
            f"[bold {ACCENT}]Fetching {source.partition('@')[2]}"
            f"{ELLIPSIS}",
            spinner="bouncingBall", spinner_style=ACCENT,
        ):
            checkout = extensions.fetch(spec)
    except (extensions.ExtensionError, OSError) as exc:
        show_error(str(exc))
        return False

    try:
        try:
            ext = extensions.load(checkout)
        except extensions.ExtensionError as exc:
            show_error(f"{source} is not a Flash extension: {exc}")
            return False

        clashes = extensions.clashes(
            ext, RESERVED_COMMANDS, frozenset(FUNCTIONS)
        )
        if clashes:
            show_error(
                f"Cannot install {ext.name}: " + "; ".join(clashes) + "."
            )
            return False

        existing = extensions.find(ext.name)

        console.print(_describe_extension(ext, source))

        if existing and existing.source and existing.source != source:
            warn(
                f"  This replaces the {ext.name} installed from "
                f"{existing.source}."
            )
        if ext.commands or ext.tools:
            warn(
                "  Extensions run programs on this machine as you. "
                "Only install ones you trust."
            )

        verb = "Update" if existing else "Install"
        if not confirm(f"{verb} {ext.name}?"):
            console.print(Text("Nothing installed.", style=DIM))
            return True

        try:
            ext = extensions.install(checkout, spec)
        except (extensions.ExtensionError, OSError) as exc:
            show_error(f"Could not install {ext.name}: {exc}")
            return False
    finally:
        extensions.discard(checkout)

    _extensions_changed()

    done = "updated" if existing else "installed"
    console.print(
        Text(f"{ext.name} {done}.", style=f"bold {ACCENT}")
    )

    if ext.commands:
        console.print(Text(
            "Try " + ", ".join(f"/{c.name}" for c in ext.commands) + ".",
            style=DIM,
        ))

    return True


def _remove_extension(name: str) -> bool:
    if not name:
        warn("Usage: /extension remove <name>")
        return False

    if not extensions.remove(name):
        warn(f"No extension called {name!r} is installed.")
        return False

    _extensions_changed()
    console.print(Text(f"{name.strip().lower()} removed.", style=DIM))
    return True


def _extension_command(arg: str) -> None:
    """/extension, /extension install <source>, /extension remove <name>."""

    action, _, rest = arg.partition(" ")
    action = action.lower()
    rest = rest.strip()

    if action in ("", "list", "ls"):
        _list_extensions()
    elif action in ("install", "add", "update") and rest:
        _install_extension(rest)
    elif action in ("remove", "uninstall", "rm"):
        _remove_extension(rest)
    else:
        warn(EXTENSION_USAGE)


def _handle_extension_flags(args) -> bool:
    """Run --extension-install/-remove/-list. False on a failure."""

    if args.extension_install:
        return _install_extension(args.extension_install)

    if args.extension_remove:
        return _remove_extension(args.extension_remove)

    _list_extensions()
    return True


def _print_backend_error(detail: str) -> None:
    show_error(f"Ollama backend error: {detail}")


STREAM_CPS = 200.0  # simulated characters-per-second reveal rate
STREAM_MIN_DURATION = 0.25
STREAM_MAX_DURATION = 2.0
STREAM_FRAME_SECONDS = 0.04


def _render_markdown(console: Console, text: str, *, end: str = "\n") -> None:
    """Render `text` as Markdown, revealing it progressively with a
    trailing cursor dot -- the full reply already arrived in one shot, so
    this is a paced typewriter effect rather than real token streaming."""

    text = render_latex(text)

    def render(body: str) -> Markdown:
        return Markdown(body, code_theme="monokai", hyperlinks=True)

    if not text.strip() or not console.is_terminal:
        console.print(render(text), end=end)
        return

    duration = max(
        STREAM_MIN_DURATION, min(STREAM_MAX_DURATION, len(text) / STREAM_CPS)
    )
    steps = max(1, int(duration / STREAM_FRAME_SECONDS))
    chunk = max(1, (len(text) + steps - 1) // steps)

    with Live(
        render(CURSOR), console=console,
        refresh_per_second=int(1 / STREAM_FRAME_SECONDS), transient=True,
    ) as live:
        cut = 0
        while cut < len(text):
            cut = min(len(text), cut + chunk)
            partial = text[:cut] + (f" {CURSOR}" if cut < len(text) else "")
            live.update(render(partial))
            time.sleep(STREAM_FRAME_SECONDS)

    console.print(render(text), end=end)


# Listed under /help. The bindings themselves are in repl_input.
KEYS = [
    ("Alt+Enter", "new line (or end the line with \\ and press Enter)"),
    ("Up / Down", "earlier messages, kept across sessions"),
    ("Ctrl+R", "search earlier messages"),
    ("Shift+Tab", "toggle autonomous mode"),
    ("Ctrl+O", "show tool output that was cut short this turn"),
    ("Ctrl+C", "stop the model mid-answer"),
]


def _set_auto(on: bool) -> None:
    """Autonomous mode on or off, from /auto, saying which."""

    set_config_var("NO_COMMAND_CONFIRMATION", "1" if on else "0")
    state = "enabled" if on else "disabled"
    console.print(
        Text(f"Autonomous mode {state}.", style=f"bold {ACCENT}")
    )


def _hard_breaks(text: str) -> str:
    """TEXT with every line break kept when rendered as Markdown.

    Markdown joins single line breaks into one paragraph, which is
    right for a model's prose and wrong for a message typed over
    several lines on purpose. Code fences are left alone: their breaks
    already survive, and the marker would show up inside them.
    """

    lines = text.split("\n")
    fenced = False

    for index, line in enumerate(lines[:-1]):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and line.strip():
            lines[index] = line + "  "

    return "\n".join(lines)


def _render_sent_message(
    console: Console,
    prompt_ansi: str,
    text: str
) -> None:
    """Echo a just-submitted line back as rendered Markdown, in place of
    the plain text prompt_toolkit erased on submit -- so things like
    `code` show up highlighted rather than as raw backticks."""

    prompt = Text.from_ansi(prompt_ansi)
    body = Markdown(
        _hard_breaks(render_latex(text)),
        code_theme="monokai",
        hyperlinks=True,
    )

    def paint() -> None:
        console.print(prompt, end="")
        console.print(body, width=console.width - cell_len(prompt.plain))

    # Measured against the terminal, so a redraw after a resize runs it
    # again rather than bringing it back at the old width.
    console.draw(paint)


VOICE_STATES = {
    "listening": 'Listening, speak now (say "voice off" to leave)',
    "transcribing": "Working out what you said",
}


def _voice_progress_line(label: str, percent: int) -> Text:
    line = Text()
    line.append(f"Downloading the {label} ", style=DIM)
    line.append(f"{percent}%", style=ACCENT)
    return line


def _download_voice_models() -> bool:
    """Fetch whatever voice mode is missing, showing how far along it is."""

    console.print(Text(
        "Voice mode needs a listening model and a voice. "
        "Downloading them once now.",
        style=DIM,
    ))

    last: list = [None]

    with Live(Text(), console=console, transient=True,
              refresh_per_second=10) as live:
        def on_progress(label: str, percent: int) -> None:
            if last[0] != (label, percent):
                last[0] = (label, percent)
                live.update(_voice_progress_line(label, percent))

        why = ensure_models(on_progress)

    if why:
        show_error(f"Voice mode is not ready: {why}")
        return False

    console.print(Text("Speech models ready.", style=DIM))
    return True


def _set_voice(on: bool) -> None:
    """Turn voice mode on (downloading what it needs) or off."""

    if on:
        missing = missing_packages()
        if missing:
            show_error(VOICE_INSTALL_HINT)
            return

        if not models_present() and not _download_voice_models():
            return

    set_config_var("VOICE", "1" if on else "0")

    if not on:
        console.print(Text("Voice mode off.", style=f"bold {ACCENT}"))
        return

    told = Text()
    told.append("Voice mode on. ", style=f"bold {ACCENT}")
    told.append(
        "Press Enter on an empty line to start talking."
        "Talk over a reply with \"interrupt\" to cut it "
        "short, and say \"voice off\" to stop the conversation; Enter "
        "starts it again.\nType /voice off to disable voice mode "
        "altogether.",
        style=DIM,
    )
    console.print(told)


def _voice_input() -> Optional[str]:
    """Record one spoken turn and return it, or None if nothing was said."""

    # VOICE can be set by hand, and a model directory can be deleted, so
    # the models are checked here rather than only when /voice turns on.
    if not models_present() and not _download_voice_models():
        return None

    try:
        with Live(Text(), console=console, transient=True,
                  refresh_per_second=10) as live:
            def on_state(state: str) -> None:
                live.update(Text(
                    f"{VOICE_STATES.get(state, state)}{ELLIPSIS}",
                    style=ACCENT,
                ))

            heard, why = listen(on_state)
    except KeyboardInterrupt:
        # Ctrl+C ends the spoken turn and hands the prompt back, rather
        # than tearing down whatever else the main loop was doing.
        console.print(Text("Stopped listening.", style=DIM))
        return None

    if why:
        show_error(why)
        return None

    if not heard:
        console.print(Text("Nothing heard.", style=DIM))
        return None

    return heard


def _speak_reply(text: str, heard: bool = True) -> bool:
    """Read a finished reply aloud when the turn was spoken to us.

    Returns whether to listen for the answer straight away, so a spoken
    conversation carries on without a keypress between turns. A typed
    turn is answered in writing and hands the prompt back, because voice
    mode being armed is not the same as the user talking.
    """

    if not (Config.voice and heard):
        return False

    spoken = for_speech(text)
    if not spoken:
        return True

    with Live(
        Text(f'Speaking (say "interrupt" to stop){ELLIPSIS}', style=DIM),
        console=console,
        transient=True,
        refresh_per_second=4,
    ):
        why, interrupted = speak(spoken)

    if why:
        warn(why)
        return False

    if interrupted:
        console.print(Text("Interrupted.", style=DIM))

    return True


def _handle_scheme_flags(args) -> None:
    """Run --register-url-scheme / --unregister-url-scheme and exit."""

    try:
        if args.register_url_scheme:
            where = register()
            console.print(Text("flash:// handler registered.", style=DIM))
            console.print(Text(where, style=DIM))
        else:
            if unregister():
                console.print(
                    Text("flash:// handler removed.", style=DIM)
                )
            else:
                console.print(
                    Text("No flash:// handler was registered.", style=DIM)
                )
    except SchemeError as exc:
        show_error(str(exc))
        sys.exit(1)
    except OSError as exc:
        show_error(f"Could not update the flash:// handler: {exc}")
        sys.exit(1)


def _run_update(*, force: bool = False) -> bool:
    """Check for a newer Flash version and, if one exists (or FORCE),
    install it. Returns False only on an actual failure."""

    with console.status(
        f"[bold {ACCENT}]Checking for updates{ELLIPSIS}",
        spinner="bouncingBall", spinner_style=ACCENT
    ):
        latest = fetch_latest_version()

    if latest is None and not force:
        warn(
            "Could not check for updates "
            "(no network or GitHub unreachable)."
        )
        return False

    if latest is not None and not is_newer(latest) and not force:
        console.print(Text("Already up to date.", style=DIM))
        return True

    if not force:
        target = f"v{latest}" if latest else "the latest version"
        ask = Text(f"  Update Flash {__version__} to {target}? ", style=DIM)
        ask.append("y", style=f"bold {ACCENT}")
        ask.append("/n ", style=DIM)
        console.print(ask, end="")
        try:
            answer = typed()
        except EOFError:
            console.print()
            return True
        console.print()
        if answer != "y":
            console.print(Text("Update cancelled.", style=DIM))
            return True

    with console.status(
        f"[bold {ACCENT}]Updating{ELLIPSIS} ",
        spinner="arrow3", spinner_style=ACCENT
    ) as status:
        # git and pipx print their progress straight to the terminal,
        # which collides with the spinner and comes out shredded. Their
        # output arrives here a line at a time instead, and printing it
        # through the console puts each line cleanly above the spinner.
        ok, message = perform_update(
            on_step=lambda label: status.update(
                f"[bold {ACCENT}]{label}{ELLIPSIS} "
            ),
            on_output=lambda line: console.print(Text(line, style=DIM)),
        )

    if ok:
        console.print(Text(message, style=f"bold {ACCENT}"))
    else:
        show_error(message)

    return ok


def _confirm_url_prompt(prompt: str) -> bool:
    """Confirm a prompt that arrived over a flash:// URL.

    Any web page can open one of these links, so the prompt is never sent
    to the model without the user seeing it first.
    """

    console.print(
        Panel(
            Text(prompt),
            title="This prompt was sent by a site or app",
            border_style=ACCENT,
            padding=(0, 1),
            expand=False,
        )
    )

    ask = Text("  Send this prompt to the model? ", style=DIM)
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


def main() -> None:
    """Main app loop"""

    args = parse_args()

    if args.register_url_scheme or args.unregister_url_scheme:
        _handle_scheme_flags(args)
        return

    if args.update:
        sys.exit(0 if _run_update(force=args.force) else 1)

    if (
        args.extension_install
        or args.extension_remove
        or args.extension_list
    ):
        sys.exit(0 if _handle_extension_flags(args) else 1)

    pending: list[str] = []

    if args.url:
        try:
            pending.append(parse_flash_url(args.url))
        except SchemeError as exc:
            show_error(str(exc))
            input("\n\nPress any key to continue ")
            sys.exit(2)

    client = ollama.Client(host=Config.host)

    _clear_screen()

    messages: list = []

    # Voice mode holds the floor: after a reply is spoken, the next turn
    # starts listening on its own instead of waiting for a keypress.
    listening_on = False

    wakes_in_a_row = 0

    # Commands from the user's VS Code terminal newer than this are
    # attached to their next message.
    terminal_seen = terminal.start_time()
    hook_shell = terminal.current_shell()
    if hook_shell and terminal.installed(hook_shell):
        terminal.write_hook(hook_shell)

    def wake_ready() -> bool:
        return (
            wakes_in_a_row < MAX_WAKES_IN_A_ROW
            and bool(subagents.unseen())
        )

    update = check_for_update()

    backdrop = open_screen(console, update)

    banner_showing = True

    def redraw_banner() -> None:
        """Paint the opening screen again, for a resize or a toggle."""

        nonlocal backdrop

        # Measured before the paint, not after it. A drag that moves
        # again while the paint goes out would otherwise be written
        # down as the size the screen was drawn for, and the prompt
        # would never find out that it was not.
        painted = screen_size()
        _sync(True)
        backdrop = repaint(console, update)
        _sync(False)
        screen_redrawn(painted)

    while True:
        try:
            pending_images: Optional[list[str]] = None
            heard = False

            woken = False

            if pending:
                uin = pending.pop(0)
                if not _confirm_url_prompt(uin):
                    console.print(Text("Prompt discarded.", style=DIM))
                    break
            elif listening_on:
                # An empty line is what the voice branch below listens on.
                listening_on = False
                uin = ""
            elif wake_ready():
                # Finished while the last turn was still running.
                woken = True
            else:
                try:
                    uin = read_line(
                        Config.prompt,
                        wake=wake_ready,
                        # Off while the opening screen is up. That screen
                        # fills the terminal to the row, so a status line
                        # is one row more than there is and scrolls the
                        # top of the banner away. An empty Enter or a
                        # resize leaves the screen up, and has to leave
                        # the bar off with it.
                        status=(
                            None if banner_showing
                            else _status_text(messages)
                        ),
                        health=_backend_health,
                        backdrop=backdrop if banner_showing else None,
                    )
                except EOFError:
                    print()
                    return

                if uin == RESIZE:
                    _settle_size()
                    if banner_showing:
                        redraw_banner()
                    else:
                        # Measured before the paint, as redraw_banner
                        # does, and for the same reason.
                        painted = screen_size()
                        redraw_conversation(console)
                        screen_redrawn(painted)
                    continue

                if banner_showing and (
                    uin in (WAKE, EXPAND)
                    or (not uin.strip() and Config.voice)
                ):
                    # Something is about to print under the opening
                    # screen. The foot of the picture went with the
                    # prompt, so put it back first, and from here on
                    # the screen is a conversation, not ours to redraw.
                    for row in backdrop:
                        print(row)
                    banner_showing = False

                if uin == TOGGLE_AUTO:
                    # Silent: a line per press would pile up in the
                    # conversation for someone flicking it back and
                    # forth. The feedback is the status bar's "auto",
                    # or on the opening screen, which has no status
                    # bar, the banner's autonomous notice.
                    set_config_var(
                        "NO_COMMAND_CONFIRMATION",
                        "0" if Config.no_command_confirmation else "1",
                    )
                    if banner_showing:
                        redraw_banner()
                    continue

                if uin == EXPAND:
                    expand_collapsed()
                    continue

                if uin == WAKE:
                    woken = True
                elif uin.strip():
                    if banner_showing:
                        # The banner has been read by now, and the
                        # padding under it was only ever there to put
                        # the opening prompt at the foot of the screen.
                        # Both are in the way of the conversation, which
                        # starts from the top: the prompt then follows
                        # the last message down with free rows under it,
                        # and the completion menu opens into those
                        # instead of shoving the messages up the screen.
                        _clear_screen()
                        banner_showing = False
                    _render_sent_message(console, Config.prompt, uin)

            if woken:
                _announce_wake()
                uin = WAKE_NOTE
                wakes_in_a_row += 1
            else:
                wakes_in_a_row = 0

            if uin.strip() == "":
                if not Config.voice:
                    continue

                spoken = _voice_input()
                if spoken is None:
                    continue

                # Saying "voice off" ends the conversation, it does not
                # disarm voice mode: Enter picks it straight back up,
                # and only a typed /voice off turns the feature off.
                if is_exit_phrase(spoken):
                    console.print(Text(
                        "Stopped listening. Press Enter to speak again, "
                        "or type /voice off to disable voice mode.",
                        style=DIM,
                    ))
                    continue

                uin = spoken
                heard = True
                _render_sent_message(console, Config.prompt, uin)

            if uin in ("/bye", "/exit"):
                _clear_scratch_dir()
                return

            if uin == "/model" or uin.startswith("/model "):
                arg = uin[len("/model"):].strip()

                if arg:
                    # Setting a model by hand stays allowed whatever the
                    # backend says, so a declined or failed download is
                    # not a reason to leave MODEL where it was.
                    fetch_if_missing(client, arg)
                else:
                    # Bare /model picks from what this machine holds.
                    # Picking nothing falls through to the summary below
                    # rather than leaving the screen bare.
                    arg = pick_model(client, Config.model or "") or ""

                if arg:
                    set_config_var("MODEL", arg)
                    client = ollama.Client(host=Config.host)
                    console.print(
                        Text(f"Model set to {arg}.", style=DIM)
                    )
                else:
                    info = Text()
                    info.append("model: ", style=DIM)
                    info.append(str(Config.model or "(unset)"))
                    info.append("\nhost:  ", style=DIM)
                    info.append(Config.host)
                    console.print(info)
                continue

            if uin == "/auto" or uin.startswith("/auto "):
                arg = uin[len("/auto"):].strip().lower()
                if arg in ("", "toggle"):
                    _set_auto(not Config.no_command_confirmation)
                elif arg in ("on", "enable", "true", "1"):
                    _set_auto(True)
                elif arg in ("off", "disable", "false", "0"):
                    _set_auto(False)
                else:
                    warn("Usage: /auto [on|off|toggle]")
                continue

            if uin == "/background" or uin.startswith("/background "):
                _background_command(uin[len("/background"):].strip())
                continue

            if uin == "/voice" or uin.startswith("/voice "):
                arg = uin[len("/voice"):].strip().lower()
                if arg in ("", "toggle"):
                    _set_voice(not Config.voice)
                elif arg in ("on", "enable", "true", "1"):
                    _set_voice(True)
                elif arg in ("off", "disable", "false", "0"):
                    _set_voice(False)
                else:
                    warn("Usage: /voice [on|off|toggle]")
                continue

            if uin == "/set" or uin.startswith("/set "):
                rest = uin[len("/set"):].strip()
                parts = rest.split(maxsplit=1)
                if len(parts) != 2 or not ENV_NAME_RE.match(parts[0]):
                    warn("Usage: /set NAME VALUE")
                    continue
                name, value = parts
                set_config_var(name, value)
                client = ollama.Client(host=Config.host)
                console.print(
                    Text(f"{name} set in {ENV_PATH}.", style=DIM)
                )
                continue

            if uin == "/unset" or uin.startswith("/unset "):
                name = uin[len("/unset"):].strip()
                if not name or not ENV_NAME_RE.match(name):
                    warn("Usage: /unset NAME")
                    continue
                if unset_config_var(name):
                    client = ollama.Client(host=Config.host)
                    console.print(Text(f"{name} unset.", style=DIM))
                else:
                    console.print(Text(f"{name} was not set.", style=DIM))
                continue

            if uin == "/refresh":
                refresh_config()
                extensions.reload()
                _extensions_changed()
                _model_system_prompts.clear()
                _context_limits.clear()
                _context_ceilings.clear()
                _context_notices.clear()
                _num_ctx_notices.clear()
                client = ollama.Client(host=Config.host)
                console.print(Text("Config refreshed.", style=DIM))
                continue

            if uin == "/memory":
                entries = list_memory()
                if entries:
                    listing = "\n".join(
                        f"{i}. {e}" for i, e in enumerate(entries, start=1)
                    )
                    console.print(Text(listing, style=DIM))
                else:
                    console.print(Text("No memories saved yet.", style=DIM))
                continue

            if uin == "/forget" or uin.startswith("/forget "):
                arg = uin[len("/forget"):].strip()
                if not arg.isdigit():
                    warn("Usage: /forget <index> (see /memory, 1-based)")
                    continue
                try:
                    console.print(
                        Text(forget_memory(int(arg)), style=DIM)
                    )
                except IndexError as exc:
                    warn(str(exc))
                continue

            if uin == "/clear":
                messages.clear()
                clear_collapsed()
                plan.clear()
                checkpoint.clear()
                console.print(Text("Context cleared.", style=DIM))
                continue

            if uin == "/undo":
                console.print(Text(checkpoint.undo(), style=DIM))
                continue

            if uin == "/context":
                _render_context(messages)
                continue

            if uin == "/compact":
                if not messages:
                    console.print(Text("Nothing to compact.", style=DIM))
                    continue
                if not Config.model:
                    show_error(
                        "Model is not set. Use `/model <model>` to set it."
                    )
                    continue

                tool_line(f"Compact({len(messages)} messages)")
                summary = _summarize(console, client, messages)

                if summary is None:
                    tool_result(
                        "The model returned no summary; history kept as is.",
                        style=WARN,
                    )
                else:
                    was = context.total_tokens(messages)
                    messages[:] = [context.summary_message(summary)]
                    now = context.total_tokens(messages)
                    tool_result(
                        f"{was} tokens of history down to {now}"
                    )
                continue

            if uin == "/plan":
                console.print(Text(plan.headline(), style=DIM))
                plan.render()
                continue

            if uin == "/hook" or uin.startswith("/hook "):
                _hook_command(uin[len("/hook"):].strip().lower())
                continue

            if uin == "/agents" or uin.startswith("/agents "):
                subagents.watch(uin[len("/agents"):].strip())
                console.print()
                continue

            if uin == "/version":
                console.print(Text(f"Flash CLI v{__version__}", style=DIM))
                with console.status(
                    f"[bold {ACCENT}]Checking for updates{ELLIPSIS}",
                    spinner="bouncingBall", spinner_style=ACCENT
                ):
                    latest = fetch_latest_version()
                if latest is None:
                    warn(
                        "Could not check for updates "
                        "(no network or GitHub unreachable)."
                    )
                elif is_newer(latest):
                    console.print(
                        Text(
                            f"Update available: v{latest}. "
                            "Run /update to upgrade.",
                            style=f"bold {ACCENT}"
                        )
                    )
                else:
                    console.print(
                        Text("You're on the latest version.", style=DIM)
                    )
                continue

            if uin == "/update":
                _run_update()
                continue

            if uin in ("/extension", "/extensions") or uin.startswith(
                "/extension "
            ):
                _extension_command(uin[len("/extension"):].strip())
                continue

            called = (
                extensions.find_command(uin)
                if uin.split(" ", 1)[0] not in RESERVED_COMMANDS
                else None
            )
            if called:
                ext, command, rest = called

                if command.run is None:
                    # A prompt command: its text goes to the model as
                    # though it had been typed.
                    uin = extensions.expand_prompt(command, rest)
                else:
                    try:
                        arguments = shlex.split(rest)
                    except ValueError as exc:
                        warn(f"Could not parse arguments: {exc}")
                        continue
                    console.echo(
                        run_extension_command(ext, command, arguments)
                        + "\n"
                    )
                    console.print()
                    continue

            if uin == "/image" or uin.startswith("/image "):
                arg = uin[len("/image"):].strip()
                if not arg:
                    warn("Usage: /image <path> [prompt]")
                    continue
                try:
                    parts = shlex.split(arg)
                except ValueError as exc:
                    warn(f"Could not parse path: {exc}")
                    continue
                if not parts:
                    warn("Usage: /image <path> [prompt]")
                    continue

                image_path, reason = resolve_image_path(parts[0])
                if image_path is None:
                    show_error(reason)
                    continue
                if not model_sees_images(Config.host, Config.model or ""):
                    warn(
                        f"{Config.model} reports no vision support; "
                        "sending it anyway, but expect an error."
                    )

                # Fall through to the normal send path below with UIN
                # replaced by the prompt and PENDING_IMAGES attached.
                uin = " ".join(parts[1:]).strip() or DEFAULT_IMAGE_PROMPT
                pending_images = [str(image_path)]

            direct_command = _direct_shell_command(uin)
            if direct_command:
                console.echo(
                    shell_tool(
                        direct_command,
                        is_user=True,
                        timeout=MAX_SHELL_TIMEOUT
                    )
                    + "\n"
                )
                console.print()
                continue

            if uin in {"/help", "/?"}:
                help_text = Text()
                help_text.append("\nCommands\n\n", style="bold")
                rows = [
                    *all_commands(),
                    ("@<path>", "point the model at a file"),
                    ("!<command>", "run a shell command directly"),
                ]
                width = max(len(cmd) for cmd, _desc in rows) + 2
                for cmd, desc in rows:
                    help_text.append(f"  {cmd:<{width}}", style=ACCENT)
                    help_text.append(f"{desc}\n", style=DIM)
                help_text.append("\nKeys\n\n", style="bold")
                keys_width = max(len(key) for key, _desc in KEYS) + 2
                for key, desc in KEYS:
                    help_text.append(f"  {key:<{keys_width}}", style=ACCENT)
                    help_text.append(f"{desc}\n", style=DIM)
                help_text.append(
                    "\nAnything else is sent to the model.\n", style=DIM
                )
                console.print(help_text)
                continue

            if not Config.model:
                show_error(
                    "Model is not set. Use `/model <model>` to set it."
                )
                continue

            # Riding inside the user's own message, not beside it, keeps
            # roles alternating for templates that require it, and lets
            # history trimming keep or drop the two together.
            agent_news, delivered_ids = subagents.notices()
            looked_at = time.time()
            ran = "" if woken else terminal.since(terminal_seen)
            content = "\n\n".join(
                part for part in (user_runs(), ran, agent_news, uin) if part
            )

            messages.append(_message("user", content, pending_images))
            _fit_and_compact(console, client, messages)

            system_message = _message(
                "system", _session_system_prompt(heard)
            )

            checkpoint.start_turn(_turn_label(uin))
            clear_collapsed()

            turn = Turn()
            offered = turn_tools()

            def bar() -> Text:
                return _bar_text(messages)

            final, thinking, tool_calls, err = _chat_retry_until_response(
                console, client, [system_message] + messages, offered,
                is_image=bool(pending_images), turn=turn, bar=bar,
            )
            if err:
                _print_backend_error(err)
                messages.pop()
                continue

            subagents.mark_delivered(delivered_ids)
            if not woken:
                terminal_seen = looked_at
            clear_user_runs()

            _render_thinking(thinking)

            if not tool_calls:
                if not final.strip():
                    warn("The model returned no response.")
                    final = (
                        "I wasn't able to come up with a response to that. "
                        "Could you rephrase or try again?"
                    )
                _render_markdown(console, final)
                _render_done(turn)
                _render_stats(turn)
                _note_running_agents()
                notify_reply_ready()
                listening_on = _speak_reply(final, heard)
                messages.append(_message("assistant", final))
                _fit_and_compact(console, client, messages)
                console.print()
                continue

            tool_messages = [system_message] + messages.copy()
            # Everything tool_messages grows past this point is this
            # turn's tool traffic, which is kept so the next turn
            # remembers what was read, run, and changed.
            keep_from = len(tool_messages)
            tool_outputs = []
            followup = ""
            tool_error = None
            sent_tool_images = False

            for _ in range(Config.max_tool_rounds):
                assistant_tool_calls = []
                for call in tool_calls:
                    name, call_args = _tool_call_name_args(call)
                    assistant_tool_calls.append(
                        {"function": {"name": name, "arguments": call_args}}
                    )

                tool_messages.append({
                    "role": "assistant",
                    "content": final,
                    "tool_calls": assistant_tool_calls,
                })

                for call in tool_calls:
                    name, call_args = _tool_call_name_args(call)
                    # Not `tool_result`: that is the imported renderer,
                    # and a local of that name shadows it for the whole
                    # of main(), including code that runs before this.
                    output = run_tool((name, call_args))
                    trimmed = trim_tool_output(output, name)
                    tool_outputs.append(f"{name}:\n{trimmed}")
                    tool_messages.append({
                        "role": "tool",
                        "content": trimmed,
                        "tool_name": name,
                    })

                tool_images = take_pending_images()
                if tool_images:
                    sent_tool_images = True
                    tool_messages.append(
                        _message("user", TOOL_IMAGE_NOTE, tool_images)
                    )

                final, thinking, tool_calls, err = _chat_retry_until_response(
                    console, client, tool_messages, offered, turn=turn,
                    is_image=bool(tool_images), bar=bar,
                )
                if err:
                    tool_error = err
                    break

                _render_thinking(thinking)

                followup = final

                if not tool_calls:
                    break

            if tool_error:
                _print_backend_error(tool_error)
                if sent_tool_images:
                    warn(IMAGE_BACKEND_HINT)
                continue

            if not followup.strip():
                tool_messages.append(_tool_limit_message())
                followup, thinking, _, err = _chat_retry_until_response(
                    console, client, tool_messages, None, turn=turn,
                    bar=bar,
                )
                if err:
                    _print_backend_error(err)
                    continue

                _render_thinking(thinking)

            if not followup.strip():
                warn("The model did not provide a final response after tools.")
                followup = (
                    "Tool output:\n\n```text\n"
                    + "\n\n".join(tool_outputs)
                )
                followup += "\n```"

            _render_markdown(console, followup)
            _render_done(turn)
            _render_stats(turn)
            _note_running_agents()
            notify_reply_ready()
            listening_on = _speak_reply(followup, heard)
            messages.extend(_worth_keeping(tool_messages, keep_from))
            messages.append(_message("assistant", followup))
            _fit_and_compact(console, client, messages)

            console.print()

        except KeyboardInterrupt:
            console.print()
            continue


if __name__ == "__main__":
    try:
        console.print(Text(f"Loading{ELLIPSIS}", style=DIM))
        main()
    except FlashError as e:
        show_error(str(e))
        sys.exit(1)
