"""Main App"""

import json
import os
import re
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Union

import ollama
from colorama.ansi import clear_screen
from dotenv import load_dotenv
from ollama import ResponseError
from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .cli import parse_args
from .envfile import set_env_var, unset_env_var
from .images import resolve_image_path
from .memory import forget_memory, list_memory
from .notify import notify_reply_ready
from .paths import ENV_PATH
from .repl_input import COMMANDS, read_line
from .sysprompt import get_model_system_prompt, model_sees_images
from .theme import (
    ACCENT,
    ACCENT_ANSI,
    CHEVRON,
    CURSOR,
    DIM,
    DIM_ANSI,
    ELLIPSIS,
    RESET_ANSI,
    console,
    glimmer,
    tool_line,
    tool_result,
    warn,
)
from .theme import error as show_error
from .tools import (
    MAX_SHELL_TIMEOUT,
    SCRATCH_DIR,
    build_system_prompt,
    init,
    reason,
    run_tool,
    shell_tool,
    take_pending_images,
    tools,
)
from .updater import (
    check_for_update,
    fetch_latest_version,
    is_newer,
    perform_update,
)
from .urlscheme import SchemeError, parse_flash_url, register, unregister
from .version import __version__

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


class Config:
    """App configuration, re-derived from the environment on demand."""

    host: str
    model: Union[str, None]  # noqa: UP007, RUF100
    max_history_messages: int
    max_history_chars: int
    max_tool_rounds: int
    max_tool_output_chars: int
    max_output_tokens: int
    no_command_confirmation: bool
    prompt: str

    @classmethod
    def refresh(cls) -> None:
        cls.host = os.getenv("OLLAMA_HOST", OLLAMA_HOST_DEFAULT)
        cls.model = os.getenv("MODEL")
        cls.max_history_messages = _int_env(
            "MAX_HISTORY_MESSAGES", 6, minimum=2
        )
        cls.max_history_chars = _int_env(
            "MAX_HISTORY_CHARS", 3000, minimum=1000
        )
        cls.max_tool_rounds = _int_env("MAX_TOOL_ROUNDS", 10, minimum=1)
        cls.max_tool_output_chars = _int_env(
            "MAX_TOOL_OUTPUT_CHARS", 1200, minimum=500
        )
        cls.max_output_tokens = _int_env(
            "MAX_OUTPUT_TOKENS", 1024, minimum=128
        )
        cls.no_command_confirmation = bool(
            _int_env("NO_COMMAND_CONFIRMATION", 0, minimum=0)
        )
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


def banner(
    c: Console,
    update_version: Union[str, None] = None,  # noqa: UP007, RUF100
) -> None:
    """Print the app banner"""

    title = Text()
    title.append(f"Flash CLI v{__version__}", style="bold")

    info = Text()
    info.append("/help", style=ACCENT)
    info.append(" for commands   model: ", style=DIM)
    info.append(str(Config.model or "(unset)"))
    info.append("   host: ", style=DIM)
    info.append(Config.host)

    lines = [title, Text(""), info]
    if Config.no_command_confirmation:
        lines.append(
            Text("Autonomous mode: commands run without confirmation",
                 style=f"bold {ACCENT}")
        )
    if update_version:
        lines.append(
            Text(
                f"\nUpdate available: v{update_version} "
                "(run /update to upgrade)",
                style=f"bold {ACCENT}"
            )
        )

    c.print(
        Panel(Group(*lines), border_style=DIM, padding=(1, 2), expand=False)
    )
    print()


def _message(
    role: str,
    text: str,
    images: Union[list, None] = None,  # noqa: UP007, RUF100
) -> dict:
    message: dict = {"role": role, "content": text}
    if images:
        message["images"] = images
    return message


def _message_text(message: dict) -> str:
    return message.get("content", "") or ""


def _trim_history(messages: list[dict]) -> None:
    if len(messages) > Config.max_history_messages:
        del messages[:-Config.max_history_messages]

    while (
        len(messages) > 1
        and sum(len(_message_text(message)) for message in messages)
        > Config.max_history_chars
    ):
        del messages[0]


def _direct_shell_command(
    text: str,
) -> Union[str, None]:  # noqa: UP007, RUF100
    if text.startswith("!"):
        cmd = text[1:].strip()
        for prefix in ["shell ", "run "]:
            if cmd.startswith(prefix):
                return cmd[len(prefix):].strip()
        return cmd

    return None


# read already caps its own output by whole lines and tells the model how
# to page on; the middle-out trim below would silently gut a file read.
# The page tools cap themselves too, and their element list is only useful
# whole: a trim through the middle of it takes away the very numbers the
# next click has to name.
_SELF_LIMITING_TOOLS = {"read", "open_page", "interact"}


def _trim_tool_output(text: str, name: str = "") -> str:
    text = text.strip() or "(no output)"

    if name in _SELF_LIMITING_TOOLS:
        return text

    if len(text) <= Config.max_tool_output_chars:
        return text

    head_len = Config.max_tool_output_chars // 2
    tail_len = Config.max_tool_output_chars - head_len
    omitted = len(text) - Config.max_tool_output_chars

    return (
        text[:head_len]
        + f"\n\n... truncated {omitted} characters ...\n\n"
        + text[-tail_len:]
    )


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
        options={"num_predict": Config.max_output_tokens},
    )


_model_system_prompts: dict[str, str] = {}


def _session_system_prompt() -> str:
    """Flash's system prompt, with the current model's own prepended.

    Cached per model name, since /api/show costs a round trip and the
    answer only changes when the model does.
    """

    model = Config.model or ""

    if model not in _model_system_prompts:
        _model_system_prompts[model] = get_model_system_prompt(
            Config.host, model
        )

    return build_system_prompt(_model_system_prompts[model])


def _load_states(key: str, fallback: list[str]) -> list[str]:
    try:
        p = Path(__file__).parent / "thinking_states.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        states = list(data.get(key, []))
        if not states:
            raise ValueError("no states")
        return states
    except ValueError:
        return fallback


def _load_thinking_states() -> list[str]:
    return _load_states(
        "states",
        ["Thinking", "Pondering", "Analyzing", "Considering", "Reflecting"],
    )


def _load_image_thinking_states() -> list[str]:
    return _load_states(
        "image_states",
        ["Examining the image", "Analyzing the image", "Looking closely"],
    )


_thinking_state_index = 0


def _next_thinking_state(states: list[str]) -> str:
    global _thinking_state_index
    state = states[_thinking_state_index % len(states)]
    _thinking_state_index += 1
    return state


GLIMMER_SPEED = 10.0  # characters per second
GLIMMER_SPREAD = 2.5
GLIMMER_FRAME_SECONDS = 0.08

MAX_CHAT_RETRIES = 2
RETRY_DELAY_SECONDS = 2.0
FINAL_RESPONSE_RETRIES = 2


def _chat_with_retries(
    client: "ollama.Client", messages: list, tools_arg=None
) -> tuple[Union[object, None], Union[str, None]]:  # noqa: UP007, RUF100
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
    status,
    tools_arg=None,
    *,
    is_image: bool = False,
) -> tuple[Union[object, None], Union[str, None]]:  # noqa: UP007, RUF100
    states = _load_image_thinking_states() if is_image \
        else _load_thinking_states()
    state = _next_thinking_state(states)
    word = f"{state}{ELLIPSIS}"
    period = len(word) + 2 * GLIMMER_SPREAD
    stop_event = threading.Event()
    start = time.monotonic()

    def _label(elapsed: float) -> str:
        offset = (elapsed * GLIMMER_SPEED) % period - GLIMMER_SPREAD
        shine = glimmer(word, offset, GLIMMER_SPREAD)
        return f"[bold]{shine}[/bold] [{DIM}]({int(elapsed)}s)[/{DIM}]"

    def _rotate():
        while True:
            try:
                status.update(_label(time.monotonic() - start))
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


def _chat_with_status(
    console: Console,
    client: "ollama.Client",
    messages: list,
    tools_arg=None,
    *,
    is_image: bool = False,
) -> tuple[Union[object, None], Union[str, None]]:  # noqa: UP007, RUF100
    with console.status(
        f"[bold {ACCENT}]Thinking{ELLIPSIS}", spinner="point",
        spinner_style=ACCENT,
        speed=5
    ) as status:
        return _try_chat(
            client, messages, status, tools_arg, is_image=is_image
        )


def _chat_retry_until_response(
    console: Console,
    client: "ollama.Client",
    messages: list,
    tools_arg=None,
    *,
    is_image: bool = False,
) -> tuple[str, str, list, Union[str, None]]:  # noqa: UP007, RUF100
    """Call the model, retrying up to FINAL_RESPONSE_RETRIES times if it
    comes back with neither reply text nor a tool call to make."""

    final = ""
    thinking = ""
    tool_calls: list = []
    for attempt in range(1, FINAL_RESPONSE_RETRIES + 2):
        res, err = _chat_with_status(
            console, client, messages, tools_arg, is_image=is_image
        )
        if err:
            return "", "", [], err

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


def _render_sent_message(
    console: Console,
    prompt_ansi: str,
    text: str
) -> None:
    """Echo a just-submitted line back as rendered Markdown, in place of
    the plain text prompt_toolkit erased on submit -- so things like
    `code` show up highlighted rather than as raw backticks."""

    prompt = Text.from_ansi(prompt_ansi)
    console.print(prompt, end="")
    console.print(
        Markdown(text, code_theme="monokai", hyperlinks=True),
        width=console.width - cell_len(prompt.plain),
    )


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
            answer = input().strip().lower()
        except EOFError:
            print()
            return True
        print()
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
        answer = input().strip().lower()
    except EOFError:
        print()
        return False

    print()
    return answer == "y"


def main() -> None:
    """Main app loop"""

    args = parse_args()

    if args.register_url_scheme or args.unregister_url_scheme:
        _handle_scheme_flags(args)
        return

    if args.update:
        sys.exit(0 if _run_update(force=args.force) else 1)

    pending: list[str] = []
    if args.url:
        try:
            pending.append(parse_flash_url(args.url))
        except SchemeError as exc:
            show_error(str(exc))
            input("\n\nPress any key to continue ")
            sys.exit(2)

    client = ollama.Client(host=Config.host)

    print(clear_screen(), end="")

    messages: list = []

    banner(console, check_for_update())

    while True:
        try:
            pending_images: Union[  # noqa: UP007, RUF100
                list[str], None
            ] = None

            if pending:
                uin = pending.pop(0)
                if not _confirm_url_prompt(uin):
                    console.print(Text("Prompt discarded.", style=DIM))
                    break
            else:
                try:
                    uin = read_line(Config.prompt)
                except EOFError:
                    print()
                    return
                if uin.strip():
                    _render_sent_message(console, Config.prompt, uin)

            if uin.strip() == "":
                continue

            if uin in ("/bye", "/exit"):
                _clear_scratch_dir()
                return

            if uin == "/model" or uin.startswith("/model "):
                arg = uin[len("/model"):].strip()
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
                    new_value = not Config.no_command_confirmation
                elif arg in ("on", "enable", "true", "1"):
                    new_value = True
                elif arg in ("off", "disable", "false", "0"):
                    new_value = False
                else:
                    warn("Usage: /auto [on|off|toggle]")
                    continue
                set_config_var(
                    "NO_COMMAND_CONFIRMATION", "1" if new_value else "0"
                )
                state = "enabled" if new_value else "disabled"
                console.print(
                    Text(f"Autonomous mode {state}.", style=f"bold {ACCENT}")
                )
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
                _model_system_prompts.clear()
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
                console.print(Text("Context cleared.", style=DIM))
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
                print(
                    shell_tool(
                        direct_command,
                        is_user=True,
                        timeout=MAX_SHELL_TIMEOUT
                    )
                )
                print()
                continue

            if uin in {"/help", "/?"}:
                help_text = Text()
                help_text.append("\nCommands\n\n", style="bold")
                for cmd, desc in [
                    *COMMANDS,
                    ("!<command>", " run a shell command directly"),
                ]:
                    help_text.append(f"  {cmd:<10}", style=ACCENT)
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

            messages.append(_message("user", uin, pending_images))
            _trim_history(messages)

            system_message = _message("system", _session_system_prompt())

            final, thinking, tool_calls, err = _chat_retry_until_response(
                console, client, [system_message] + messages, tools,
                is_image=bool(pending_images),
            )
            if err:
                _print_backend_error(err)
                messages.pop()
                continue

            _render_thinking(thinking)

            if not tool_calls:
                if not final.strip():
                    warn("The model returned no response.")
                    final = (
                        "I wasn't able to come up with a response to that. "
                        "Could you rephrase or try again?"
                    )
                _render_markdown(console, final)
                notify_reply_ready()
                messages.append(_message("assistant", final))
                _trim_history(messages)
                print()
                continue

            tool_messages = [system_message] + messages.copy()
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
                    tool_result = run_tool((name, call_args))
                    trimmed = _trim_tool_output(tool_result, name)
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
                    console, client, tool_messages, tools,
                    is_image=bool(tool_images),
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
                    console, client, tool_messages, None
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
            notify_reply_ready()
            messages.append(_message("assistant", followup))
            _trim_history(messages)

            print()

        except KeyboardInterrupt:
            print()
            continue


if __name__ == "__main__":
    try:
        console.print(Text(f"Loading{ELLIPSIS}", style=DIM))
        main()
    except FlashError as e:
        show_error(str(e))
        sys.exit(1)
