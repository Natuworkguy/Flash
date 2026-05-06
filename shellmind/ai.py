from .tools import tools, SYSTEM_PROMPT, run_tool

import os
import sys

from colorama import Fore, Style
from dotenv import load_dotenv
from google import generativeai as genai
from google.api_core.exceptions import ResourceExhausted \
    as ResourceExhaustedError
from google.generativeai import protos
from pyfiglet import Figlet
from rich.console import Console
from rich.markdown import Markdown
from pathlib import Path

ENV_PATH = str(Path.home() / "shellmind.env")

load_dotenv(dotenv_path=ENV_PATH)


class ShellMindError(Exception):
    pass


def _int_env(name: str, default: int, *, minimum: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return max(int(value), minimum)
    except ValueError:
        return default


class config:
    api_key = os.getenv("API_KEY")
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
    prompt = Fore.BLUE + "[SM]> " + Style.RESET_ALL


def banner(c: Console) -> None:
    f = Figlet(font="slant")
    c.print(f.renderText("ShellMind CLI"), style="bold cyan")


def _message(role: str, text: str) -> dict:
    return {"role": role, "parts": [{"text": text}]}


def _message_text(message: dict) -> str:
    parts = message.get("parts", [])
    text_parts = []

    for part in parts:
        text = part.get("text") if isinstance(part, dict) else None
        if text:
            text_parts.append(text)

    return "\n".join(text_parts)


def _trim_history(messages: list[dict]) -> None:
    if len(messages) > config.max_history_messages:
        del messages[:-config.max_history_messages]

    while (
        len(messages) > 1
        and sum(len(_message_text(message)) for message in messages)
        > config.max_history_chars
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

    if len(text) <= config.max_tool_output_chars:
        return text

    head_len = config.max_tool_output_chars // 2
    tail_len = config.max_tool_output_chars - head_len
    omitted = len(text) - config.max_tool_output_chars

    return (
        text[:head_len]
        + f"\n\n... truncated {omitted} characters ...\n\n"
        + text[-tail_len:]
    )


def _response_parts(response) -> tuple[str, list]:
    final = ""
    tool_calls: list = []
    candidates = getattr(response, "candidates", None) or []

    if not candidates:
        return final, tool_calls

    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []

    for part in parts:
        if getattr(part, "text", None):
            final += part.text

        if getattr(part, "function_call", None):
            tool_calls.append(part.function_call)

    return final, tool_calls


def _response_content(response):
    candidates = getattr(response, "candidates", None) or []

    if not candidates:
        return None

    return getattr(candidates[0], "content", None)


def _tool_response_part(name: str, result: str):
    return protos.Part(
        function_response=protos.FunctionResponse(
            name=name,
            response={"result": result}
        )
    )


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
    def _not_set_error(name: str) -> None:
        print(
            Fore.RED +
            f"{name} is not set. Please set it to use ShellMind CLI.\n" +
            f"Set {name} in environment variable or in {ENV_PATH} file." +
            Style.RESET_ALL
        )
        sys.exit(1)

    if not config.api_key or config.model is None:
        _not_set_error("API_KEY")

    if not config.model or config.model is None:
        _not_set_error("MODEL")

    if hasattr(genai, "configure"):
        genai.configure(api_key=config.api_key)  # type: ignore

    console = Console()
    messages: list = []

    model = None
    if hasattr(genai, "GenerativeModel"):
        if config.model is None:
            return

        try:
            model = genai.GenerativeModel(  # type: ignore
                config.model,  # pyright: ignore[reportArgumentType]
                generation_config={
                    "max_output_tokens": config.max_output_tokens,
                },
                system_instruction=SYSTEM_PROMPT
            )
        except ResourceExhaustedError:
            print(
                Fore.RED +
                "Model is currently overloaded.",
                "Please try again later."
                + Style.RESET_ALL
            )
            return

    if model is None:
        print(
            Fore.RED +
            "Failed to initialize the generative model."
            + Style.RESET_ALL
        )
        return

    banner(console)

    while True:
        try:
            try:
                uin = input(config.prompt)
            except EOFError:
                print()
                return

            if uin in ("/bye", "/exit"):
                return

            if uin == "/model":
                if not config.model or config.model is None:
                    print(
                        Fore.RED +
                        "Error retrieving model information."
                        + Style.RESET_ALL
                    )

                print(
                    Fore.LIGHTBLACK_EX +
                    config.model
                    if config.model
                    else ""
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
Type {Fore.BLUE}/model{Fore.YELLOW} to show active model.
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

            try:
                res = model.generate_content(messages, tools=tools)
            except ResourceExhaustedError:
                print(
                    Fore.RED +
                    "Model is currently overloaded.",
                    "Please try again later."
                    + Style.RESET_ALL
                )
                continue

            final, tool_calls = _response_parts(res)

            if not tool_calls:
                if final:
                    _render_markdown(console, final)
                    messages.append(_message("model", final))
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

            tool_messages = messages.copy()
            tool_outputs = []
            followup = ""

            try:
                for _ in range(config.max_tool_rounds):
                    content = _response_content(res)
                    if content is not None:
                        tool_messages.append(content)

                    tool_response_parts = []

                    for call in tool_calls:
                        tool_result = run_tool((call.name, dict(call.args)))
                        trimmed = _trim_tool_output(tool_result)
                        tool_outputs.append(f"{call.name}:\n{trimmed}")
                        tool_response_parts.append(
                            _tool_response_part(call.name, trimmed)
                        )

                    tool_messages.append(
                        protos.Content(role="user", parts=tool_response_parts)
                    )

                    res = model.generate_content(tool_messages, tools=tools)
                    followup, tool_calls = _response_parts(res)

                    if not tool_calls:
                        break
            except ResourceExhaustedError:
                print(
                    Fore.RED +
                    "While generating follow-up response:",
                    "Model is currently overloaded.",
                    "Please try again later."
                    + Style.RESET_ALL
                )
                continue

            if not followup.strip():
                tool_messages.append(
                    _message(
                        "user",
                        "Using the tool results above, answer the original "
                        "request now. Do not call more tools."
                    )
                )

                try:
                    followup, _ = _response_parts(
                        model.generate_content(tool_messages)
                    )
                except ResourceExhaustedError:
                    print(
                        Fore.RED +
                        "While generating final response:",
                        "Model is currently overloaded.",
                        "Please try again later."
                        + Style.RESET_ALL
                    )
                    continue

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
            messages.append(_message("model", followup))
            _trim_history(messages)

            print()

        except KeyboardInterrupt:
            print()
            continue


if __name__ == "__main__":
    try:
        print(Fore.GREEN + "Loading..." + Style.RESET_ALL)
        main()
    except ShellMindError as e:
        print(Fore.RED + str(e) + Style.RESET_ALL)
        sys.exit(1)
