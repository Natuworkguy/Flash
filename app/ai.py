from .tools import tools, SYSTEM_PROMPT, run_tool

import os
import sys

from colorama import Fore, Style
from dotenv import load_dotenv
from google import generativeai as genai
from pyfiglet import Figlet
from rich.console import Console
from rich.markdown import Markdown

load_dotenv()


class ShellMindError(Exception):
    pass


class config:
    api_key = os.getenv("API_KEY")
    model = os.getenv("MODEL")
    prompt = Fore.BLUE + "[SM]> " + Style.RESET_ALL


def banner(c: Console) -> None:
    f = Figlet(font="slant")
    c.print(f.renderText("ShellMind CLI"), style="bold cyan")


def _render_markdown(console: Console, text: str, *, end: str = "\n") -> None:
    console.print(Markdown(text, code_theme="monokai", hyperlinks=True), end=end)


def main() -> None:
    if not config.api_key:
        raise ShellMindError("API_KEY is not set.")

    if not config.model:
        raise ShellMindError("MODEL is not set.")

    genai.configure(api_key=config.api_key)

    console = Console()
    messages = [{"role": "user", "parts": [{"text": SYSTEM_PROMPT}]}]

    banner(console)

    try:
        while True:
            uin = input(config.prompt)

            if uin == "/bye":
                return

            if uin == "/model":
                print(Fore.LIGHTBLACK_EX + config.model + Style.RESET_ALL)
                continue

            if uin in {"/help", "/?"}:
                print(
                    f"""
{Fore.CYAN}Console{Fore.YELLOW}
Type {Fore.BLUE}/model{Fore.YELLOW} to show active model.
Type {Fore.BLUE}/help{Fore.YELLOW} or {Fore.BLUE}/?{Fore.YELLOW} to see this.
Type {Fore.BLUE}/bye{Fore.YELLOW} to exit.
Type anything else to get a response from the AI.
""" + Style.RESET_ALL
                )
                continue

            messages.append({"role": "user", "parts": [{"text": uin}]})

            model = genai.GenerativeModel(config.model, system_instruction=SYSTEM_PROMPT)
            res = model.generate_content(messages, tools=tools)

            final = ""
            tool_calls = []

            for part in res.candidates[0].content.parts:
                if getattr(part, "text", None):
                    final += part.text
                    _render_markdown(console, part.text, end="")

                if getattr(part, "function_call", None):
                    tool_calls.append(part.function_call)

            if final:
                messages.append({"role": "model", "parts": [{"text": final}]})

            for call in tool_calls:
                tool_result = run_tool((call.name, dict(call.args)))
                messages.append({"role": "user", "parts": [{"text": f"Tool result ({call.name}):\n{tool_result}"}]})

                followup = model.generate_content(messages, tools=tools).text
                _render_markdown(console, followup)
                messages.append({"role": "model", "parts": [{"text": followup}]})

            print()

    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    try:
        print(Fore.GREEN + "Loading..." + Style.RESET_ALL)
        main()
    except ShellMindError as e:
        print(Fore.RED + str(e) + Style.RESET_ALL)
        sys.exit(1)
