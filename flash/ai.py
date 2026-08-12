"""Main App"""

import json
import os
import shutil
import sys
import threading
from pathlib import Path

import ollama
from dotenv import load_dotenv
from ollama import ResponseError
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .cli import parse_args
from .notify import notify_reply_ready
from .theme import (
    ACCENT,
    ACCENT_ANSI,
    CHEVRON,
    DIM,
    DIM_ANSI,
    ELLIPSIS,
    RESET_ANSI,
    console,
    warn,
)
from .theme import error as show_error
from .tools import (
    MAX_SHELL_TIMEOUT,
    SCRATCH_DIR,
    SYSTEM_PROMPT,
    init,
    run_tool,
    shell_tool,
    tools,
)

ENV_PATH = str(Path.home() / ".flash.env")
OLLAMA_HOST_DEFAULT = "http://localhost:11434"

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
    """App configuration"""
    host = os.getenv("OLLAMA_HOST", OLLAMA_HOST_DEFAULT)
    model = os.getenv("MODEL")
    max_history_messages = _int_env("MAX_HISTORY_MESSAGES", 6, minimum=2)
    max_history_chars = _int_env("MAX_HISTORY_CHARS", 3000, minimum=1000)
    max_tool_rounds = _int_env("MAX_TOOL_ROUNDS", 10, minimum=1)
    max_tool_output_chars = _int_env(
        "MAX_TOOL_OUTPUT_CHARS",
        1200,
        minimum=500
    )
    max_output_tokens = _int_env("MAX_OUTPUT_TOKENS", 1024, minimum=128)
    no_command_confirmation = bool(
        _int_env("NO_COMMAND_CONFIRMATION", 0, minimum=0)
    )
    prompt = \
        (ACCENT_ANSI + CHEVRON + " " + RESET_ANSI) \
        if host == OLLAMA_HOST_DEFAULT \
        else (
            DIM_ANSI
            + host.removeprefix("http://").removeprefix("https://")
            .partition(":")[0]
            + RESET_ANSI
            + " "
            + ACCENT_ANSI + CHEVRON + " " + RESET_ANSI
        )


init(Config)


def banner(c: Console) -> None:
    """Print the app banner"""

    title = Text()
    title.append("Flash CLI", style="bold")

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

    c.print(
        Panel(Group(*lines), border_style=DIM, padding=(1, 2), expand=False)
    )
    print()


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": text}


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


def _direct_shell_command(text: str) -> str | None:
    if text.startswith("!"):
        cmd = text[1:].strip()
        for prefix in ["shell ", "run "]:
            if cmd.startswith(prefix):
                return cmd[len(prefix):].strip()
        return cmd

    return None


def _trim_tool_output(text: str) -> str:
    text = text.strip() or "(no output)"

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


def _response_parts(response) -> tuple[str, list]:
    message = getattr(response, "message", None)

    if message is None:
        return "", []

    text = getattr(message, "content", "") or ""
    tool_calls = list(getattr(message, "tool_calls", None) or [])

    return text, tool_calls


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


def _load_thinking_states() -> tuple[list[str], float]:
    try:
        p = Path(__file__).parent / "thinking_states.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        states = list(data.get("states", []))
        interval = float(data.get("interval", 2))
        if not states:
            raise ValueError("no states")
        return states, interval
    except ValueError:
        return [
            "Thinking",
            "Pondering",
            "Analyzing",
            "Considering",
            "Reflecting",
        ], 2.0


def _try_chat(
    client: "ollama.Client", messages: list, status, tools_arg=None
) -> tuple[object | None, str | None]:
    states, interval = _load_thinking_states()
    stop_event = threading.Event()

    def _rotate():
        i = 0
        try:
            status.update(f"[bold {ACCENT}]{states[0]}{ELLIPSIS}")
        except ValueError:
            pass
        while not stop_event.wait(interval):
            try:
                state = states[i % len(states)]
                status.update(f"[bold {ACCENT}]{state}{ELLIPSIS}")
            except ValueError:
                pass
            i += 1

    t = threading.Thread(target=_rotate, daemon=True)
    t.start()

    try:
        return _chat(client, messages, tools_arg), None
    except ResponseError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not reach Ollama at {Config.host}. {exc}"
    finally:
        stop_event.set()
        t.join(timeout=0.1)


def _chat_with_status(
    console: Console,
    client: "ollama.Client",
    messages: list,
    tools_arg=None,
) -> tuple[object | None, str | None]:
    with console.status(
        f"[bold {ACCENT}]Thinking{ELLIPSIS}", spinner="dots",
        spinner_style=ACCENT
    ) as status:
        return _try_chat(client, messages, status, tools_arg)


def _print_backend_error(detail: str) -> None:
    show_error(f"Ollama backend error: {detail}")


def _render_markdown(console: Console, text: str, *, end: str = "\n") -> None:
    console.print(
        Markdown(
            text,
            code_theme="monokai",
            hyperlinks=True
        ),
        end=end
    )


def main() -> None:
    """Main app loop"""

    def _not_set_error(name: str) -> None:
        show_error(
            f"{name} is not set. Please set it to use Flash CLI.\n"
            f"Set {name} in environment variable or in {ENV_PATH} file."
        )
        sys.exit(1)

    parse_args()

    if not Config.model:
        _not_set_error("MODEL")

    client = ollama.Client(host=Config.host)

    messages: list = []
    system_message = _message("system", SYSTEM_PROMPT)

    banner(console)

    while True:
        try:
            try:
                uin = input(Config.prompt)
            except EOFError:
                print()
                return

            if uin.strip() == "":
                continue

            if uin in ("/bye", "/exit"):
                _clear_scratch_dir()
                return

            if uin == "/model":
                info = Text()
                info.append("model: ", style=DIM)
                info.append(str(Config.model or "(unset)"))
                info.append("\nhost:  ", style=DIM)
                info.append(Config.host)
                console.print(info)
                continue

            if uin == "/clear":
                messages.clear()
                console.print(Text("Context cleared.", style=DIM))
                continue

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
                    ("/model", "show the active model and host"),
                    ("/help, /?", "show this help"),
                    ("/bye, /exit", "exit Flash"),
                    ("/clear", "clear saved context"),
                    ("!<command>", "run a shell command directly"),
                ]:
                    help_text.append(f"  {cmd:<14}", style=ACCENT)
                    help_text.append(f"{desc}\n", style=DIM)
                help_text.append(
                    "\nAnything else is sent to the model.\n", style=DIM
                )
                console.print(help_text)
                continue

            messages.append(_message("user", uin))
            _trim_history(messages)

            res, err = _chat_with_status(
                console, client, [system_message] + messages, tools
            )
            if err:
                _print_backend_error(err)
                messages.pop()
                continue

            final, tool_calls = _response_parts(res)

            if not tool_calls:
                if final:
                    _render_markdown(console, final)
                    notify_reply_ready()
                    messages.append(_message("assistant", final))
                    _trim_history(messages)
                else:
                    warn("The model returned no response.")
                    messages.pop()
                print()
                continue

            tool_messages = [system_message] + messages.copy()
            tool_outputs = []
            followup = ""
            tool_error = None

            for _ in range(Config.max_tool_rounds):
                assistant_tool_calls = []
                for call in tool_calls:
                    name, args = _tool_call_name_args(call)
                    assistant_tool_calls.append(
                        {"function": {"name": name, "arguments": args}}
                    )

                tool_messages.append({
                    "role": "assistant",
                    "content": final,
                    "tool_calls": assistant_tool_calls,
                })

                for call in tool_calls:
                    name, args = _tool_call_name_args(call)
                    tool_result = run_tool((name, args))
                    trimmed = _trim_tool_output(tool_result)
                    tool_outputs.append(f"{name}:\n{trimmed}")
                    tool_messages.append({
                        "role": "tool",
                        "content": trimmed,
                        "tool_name": name,
                    })

                res, err = _chat_with_status(
                    console, client, tool_messages, tools
                )
                if err:
                    tool_error = err
                    break

                final, tool_calls = _response_parts(res)
                followup = final

                if not tool_calls:
                    break

            if tool_error:
                _print_backend_error(tool_error)
                continue

            if not followup.strip():
                tool_messages.append(_tool_limit_message())

                res, err = _chat_with_status(
                    console, client, tool_messages, None
                )
                if err:
                    _print_backend_error(err)
                    continue

                followup, _ = _response_parts(res)

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
