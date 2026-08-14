"""REPL input with a dropdown menu of slash-command suggestions."""

from typing import Union

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI

from .memory import MEMORY_PATH
from .paths import ENV_PATH

# Single source of truth for both the completion dropdown and /help.
COMMANDS = [
    ("/model", "show the active model, or /model <name> to switch"),
    ("/auto", "toggle autonomous command mode (/auto on|off)"),
    ("/set", f"set an env var, saved to {ENV_PATH} (/set NAME VALUE)"),
    ("/unset", "remove an env var (/unset NAME)"),
    ("/refresh", "reload config from the env file"),
    ("/memory", f"show saved memory, numbered ({MEMORY_PATH})"),
    ("/forget", "delete one memory by its 1-based index (/forget N)"),
    ("/clear", "clear saved context"),
    ("/help", "show this help (alias: /?)"),
    ("/bye", "exit Flash (alias: /exit)"),
]


class SlashCommandCompleter(Completer):
    """Suggests / commands as the line is typed."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
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


_session: Union[PromptSession, None] = None # noqa: UP007


def read_line(prompt_ansi: str) -> str:
    """Read one line; suggests / commands in a dropdown while typing one."""

    global _session
    if _session is None:
        _session = PromptSession(
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
        )
    return _session.prompt(ANSI(prompt_ansi))
