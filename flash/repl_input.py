"""REPL input with a dropdown menu of slash-command suggestions."""

import json
import os
import time
from pathlib import Path
from typing import Union

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples

from .memory import MEMORY_PATH
from .paths import ENV_PATH
from .theme import SPARKLE, ptk_sweep_reveal

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

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


class SlashCommandCompleter(Completer):
    """Suggests / commands as the line is typed, and image file paths as
    the argument to /image."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if text.startswith("/image "):
            remainder = text[len("/image "):]
            if " " in remainder:
                return  # past the path, now typing the optional prompt
            sub_document = Document(remainder, cursor_position=len(remainder))
            yield from _image_path_completer.get_completions(
                sub_document, complete_event
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


_session: Union[PromptSession, None] = None  # noqa: UP007


def read_line(prompt_ansi: str) -> str:
    """Read one line; suggests / commands in a dropdown while typing one,
    and animates a rotating hint at the cursor while the line is empty."""

    global _session
    if _session is None:
        _session = PromptSession(
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            placeholder=_suggestion_placeholder,
            refresh_interval=PLACEHOLDER_REFRESH_SECONDS,
        )
    return _session.prompt(ANSI(prompt_ansi))
