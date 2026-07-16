"""Main App"""

from .tools import tools, SYSTEM_PROMPT, run_tool

import os
import sys

from colorama import Fore, Style
from dotenv import load_dotenv
import ollama
from ollama import ResponseError
from pyfiglet import Figlet
from rich.console import Console
from rich.markdown import Markdown
from pathlib import Path

ENV_PATH = str(Path.home() / "flash.env")
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
    max_tool_rounds = _int_env("MAX_TOOL_ROUNDS", 4, minimum=1)
    max_tool_output_chars = _int_env(
        "MAX_TOOL_OUTPUT_CHARS",
        1200,
        minimum=500
    )
    max_output_tokens = _int_env("MAX_OUTPUT_TOKENS", 512, minimum=128)
    prompt = \
        Fore.BLUE + \
        (
            "[Flash]> "
            if host == OLLAMA_HOST_DEFAULT
            else f"[Flash @ {host.lstrip('http://').lstrip('https://')}]> "
        ) + \
        Style.RESET_ALL


def banner(c: Console) -> None:
    """Print the app banner"""

    f = Figlet(font="slant")
    c.print(f.renderText("FLASH CLI"), style="bold cyan")


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


def _try_chat(
    client: "ollama.Client", messages: list, tools_arg=None
) -> tuple[object | None, str | None]:
    try:
        return _chat(client, messages, tools_arg), None
    except ResponseError as exc:
        return None, str(exc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return None, f"Could not reach Ollama at {Config.host}. {exc}"


def _print_backend_error(detail: str) -> None:
    print(Fore.RED + f"Ollama backend error: {detail}" + Style.RESET_ALL)


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
        print(
            Fore.RED +
            f"{name} is not set. Please set it to use Flash CLI.\n" +
            f"Set {name} in environment variable or in {ENV_PATH} file." +
            Style.RESET_ALL
        )
        sys.exit(1)

    if not Config.model:
        _not_set_error("MODEL")

    client = ollama.Client(host=Config.host)

    console = Console()
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

            if uin in ("/bye", "/exit"):
                return

            if uin == "/model":
                print(
                    Fore.LIGHTBLACK_EX +
                    f"Model: {Config.model or '(unset)'}\n"
                    f"Host: {Config.host}"
                    + Style.RESET_ALL
                )
                continue

            if uin == "/clear":
                messages.clear()
                print(
                    Fore.LIGHTBLACK_EX +
                    "Context cleared."
                    + Style.RESET_ALL
                )
                continue

            direct_command = _direct_shell_command(uin)
            if direct_command:
                print(run_tool(("shell", {"command": direct_command})))
                print()
                continue

            if uin in {"/help", "/?"}:
                print(
                    f"""
{Fore.CYAN}Console{Fore.YELLOW}
Type {Fore.BLUE}/model{Fore.YELLOW} to show active model and host.
Type {Fore.BLUE}/help{Fore.YELLOW} or {Fore.BLUE}/?{Fore.YELLOW} to see this.
Type {Fore.BLUE}/bye{Fore.YELLOW} or {Fore.BLUE}/exit{Fore.YELLOW} to exit.
Type {Fore.BLUE}/clear{Fore.YELLOW} to clear saved context.
Type {Fore.BLUE}!<command>{Fore.YELLOW} to run shell directly.
Type anything else to get a response from the AI.
""" + Style.RESET_ALL
                )
                continue

            messages.append(_message("user", uin))
            _trim_history(messages)

            res, err = _try_chat(
                client, [system_message] + messages, tools
            )
            if err:
                _print_backend_error(err)
                messages.pop()
                continue

            final, tool_calls = _response_parts(res)

            if not tool_calls:
                if final:
                    _render_markdown(console, final)
                    messages.append(_message("assistant", final))
                    _trim_history(messages)
                else:
                    print(
                        Fore.YELLOW +
                        "The model returned no response."
                        + Style.RESET_ALL
                    )
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

                res, err = _try_chat(client, tool_messages, tools)
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
                tool_messages.append(
                    _message(
                        "user",
                        "Using the tool results above, answer the original "
                        "request now. Do not call more tools."
                    )
                )

                res, err = _try_chat(client, tool_messages, None)
                if err:
                    _print_backend_error(err)
                    continue

                followup, _ = _response_parts(res)

            if not followup.strip():
                print(
                    Fore.YELLOW +
                    "The model did not provide a final response after tools."
                    + Style.RESET_ALL
                )
                followup = (
                    "Tool output:\n\n```text\n"
                    + "\n\n".join(tool_outputs)
                )
                followup += "\n```"

            _render_markdown(console, followup)
            messages.append(_message("assistant", followup))
            _trim_history(messages)

            print()

        except KeyboardInterrupt:
            print()
            continue


if __name__ == "__main__":
    try:
        print(Fore.GREEN + "Loading..." + Style.RESET_ALL)
        main()
    except FlashError as e:
        print(Fore.RED + str(e) + Style.RESET_ALL)
        sys.exit(1)
