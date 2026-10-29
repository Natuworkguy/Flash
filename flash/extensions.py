"""Extensions: slash commands, tools, prompts, and scenes from outside.

An extension is a folder with a `flash-extension.json` at its root,
usually a GitHub repository, installed with

    flash install github@owner/repo

or `/extension install github@owner/repo` from inside a session. It is
copied into ~/.flash/extensions/<name>, and from then on every session
picks up whatever the manifest declares:

    {
      "name": "standup",
      "description": "Daily standup helpers",
      "version": "1.0.0",
      "prompt": "prompt.md",
      "commands": [
        {"name": "standup", "description": "draft today's standup",
         "prompt": "commands/standup.md"},
        {"name": "deploy", "description": "ship the current branch",
         "run": ["./scripts/deploy.sh"], "timeout": 300}
      ],
      "tools": [
        {"name": "jira_issue", "description": "Look up a Jira issue.",
         "parameters": {"type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"]},
         "run": ["python", "./tools/jira.py"]}
      ],
      "backgrounds": "scenes",
      "themes": "themes.json",
      "loaders": [
        {"name": "rocket", "label": "Rocket", "interval": 0.15,
         "frames": [">   ", " >  ", "  > ", "   >"]}
      ]
    }

"knowledge" is a folder of guides for Flash's knowledge base, each a
<name>.md with a description of when to read it:

    ---
    name: deploy
    description: Ship a branch to staging and production with Acme
    ---
    1. Run /deploy staging first ...

The model sees each guide's name and description on every turn, named
<extension>-<name> (here "standup-deploy"), and reads one in full when
a task matches it, as it does the user's own skills.

"settings" are choices the user makes for the extension, each a
switch, a line of text, a number or a pick from a list; the web UI
shows them in the extension's own settings panel, and /extension set
changes one in the terminal:

      "settings": [
        {"id": "banners", "label": "Banners", "type": "switch",
         "default": true, "help": "A banner when it finishes."},
        {"id": "color", "label": "Colour", "type": "text",
         "default": "d97757"},
        {"id": "every", "label": "Check every", "type": "number",
         "default": 5, "min": 1, "max": 60},
        {"id": "size", "label": "Size", "type": "select",
         "options": ["small", "large"], "default": "small"}
      ]

Every program the extension runs is handed them: all of them as JSON
in FLASH_SETTINGS, and each in FLASH_SETTING_<ID> ("1" or "0" for a
switch). They are kept outside the extension's folder, so an update
keeps them.

"themes" are colour themes for the web UI (see flash/themes.py) and
"loaders" are loading animations for both the terminal and the page
(see flash/loaders.py). Either can be the list itself or a JSON file
holding it.

Everything an extension runs is a program, not Python loaded into
Flash, so an extension can be written in anything, cannot take the
session down with it, and needs nothing installed into Flash's own
environment. A tool gets its arguments as JSON on stdin and answers
on stdout; a command gets whatever was typed after it as arguments.
"""

import contextlib
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .paths import FLASH_DIR

MANIFEST = "flash-extension.json"

# Where an installed extension remembers what it was installed from, so
# installing it again from the same place is how it is updated.
SOURCE_FILE = ".flash-source"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COMMAND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# Ollama and the models behind it take tool names in this shape; a name
# outside it is refused by some backends and mangled by others.
TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")

# GitHub's own rules: an owner is letters, digits and single hyphens, a
# repository may also use dots and underscores.
GITHUB_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100})$"
)

DEFAULT_TIMEOUT = 30

SETTING_TYPES = ("switch", "text", "number", "select")
SETTING_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_SETTINGS = 24
MAX_OPTIONS = 24
SETTING_CHARS = 500
MAX_TIMEOUT = 600
# A provider answers a whole turn, which a big model can take minutes on.
DEFAULT_PROVIDER_TIMEOUT = 300

# Every extension's prompt rides along on every turn, and Flash runs
# on local models with windows measured in thousands of tokens. A
# prompt past this is a document, and belongs behind a tool instead.
MAX_PROMPT_CHARS = 8000

CLONE_TIMEOUT_SECONDS = 120

# Stands for whatever was typed after a prompt command's name.
ARGUMENTS = "$ARGUMENTS"

PYTHON_NAMES = ("python", "python3")


class ExtensionError(ValueError):
    """An extension that could not be fetched, read, or installed."""


@dataclass
class Command:
    """A slash command: a prompt for the model, or a program to run."""

    name: str
    description: str
    prompt: Optional[str] = None
    run: Optional[list[str]] = None
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class Tool:
    """A tool the model can call, answered by a program."""

    name: str
    description: str
    parameters: dict
    run: list[str]
    timeout: int = DEFAULT_TIMEOUT
    confirm: bool = False


@dataclass
class Events:
    """A program told of the turn's milestones (progress.py has them),
    one JSON event on stdin at a time."""

    run: list[str]
    on: tuple[str, ...]
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class Voice:
    """A program in the middle of a voice conversation: told of each
    moment, and for some asked what to do (voicehooks.py has them), one
    JSON event on stdin at a time."""

    run: list[str]
    on: tuple[str, ...]
    timeout: int = 5


@dataclass
class Setting:
    """One choice in an extension's settings panel."""

    id: str
    label: str
    type: str
    default: Any
    help: str = ""
    options: list[str] = field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # A text setting that is a secret, such as an API key: shown as
    # bullets, and its value never sent to a page or printed.
    secret: bool = False

    def check(self, value: Any) -> Any:
        """VALUE as this setting keeps it, or ExtensionError."""

        if self.type == "switch":
            if isinstance(value, str) and value.lower() in (
                "1", "on", "yes", "true", "0", "off", "no", "false",
            ):
                return value.lower() in ("1", "on", "yes", "true")
            if not isinstance(value, bool):
                raise ExtensionError(f"{self.label} is on or off")
            return value
        if self.type == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ExtensionError(f"{self.label} is a number") from None
            if number != number or number in (float("inf"), float("-inf")):
                raise ExtensionError(f"{self.label} is a number")
            if self.minimum is not None and number < self.minimum:
                raise ExtensionError(
                    f"{self.label} is at least {_plain(self.minimum)}")
            if self.maximum is not None and number > self.maximum:
                raise ExtensionError(
                    f"{self.label} is at most {_plain(self.maximum)}")
            return int(number) if number.is_integer() else number
        text = " ".join(str(value if value is not None else "").split())
        if self.type == "select":
            if text not in self.options:
                raise ExtensionError(
                    f"{self.label} is one of: {', '.join(self.options)}")
            return text
        return text[:SETTING_CHARS]

    def summary(self) -> dict:
        return {
            "id": self.id, "label": self.label, "type": self.type,
            "default": self.default, "help": self.help,
            "options": self.options, "min": self.minimum,
            "max": self.maximum, "secret": self.secret,
            **({"default": ""} if self.secret else {}),
        }


def _plain(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


@dataclass
class Provider:
    """Models from somewhere other than Ollama, answered by a program:
    asked for its models, and to chat, with JSON on stdin (providers.py
    has the protocol)."""

    name: str
    description: str
    run: list[str]
    timeout: int = DEFAULT_PROVIDER_TIMEOUT


@dataclass
class Speech:
    """A voice to speak with, or a model to listen with, run by a
    program. A voice is given {"text", "pace"} as JSON on stdin and
    writes the speech to stdout: a WAV file, or raw 16-bit mono audio at
    RATE. A listening model is given 16 kHz mono 16-bit audio on stdin
    and writes what was said."""

    name: str
    kind: str
    label: str
    run: list[str]
    rate: int = 22050
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class Briefing:
    """A program that adds cards to the briefing: run when it is read,
    it writes a JSON list of {"title", "body"}, and maybe "id", "at" (a
    Unix time), "url" and "label"."""

    run: list[str]
    timeout: int = 10


@dataclass
class Extension:
    """One installed (or about to be installed) extension."""

    name: str
    path: Path
    description: str = ""
    version: str = ""
    source: str = ""
    prompt: str = ""
    commands: list[Command] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    backgrounds: Optional[Path] = None
    providers: list[Provider] = field(default_factory=list)
    themes: list = field(default_factory=list)
    loaders: list = field(default_factory=list)
    events: Optional[Events] = None
    voice: Optional[Voice] = None
    settings: list[Setting] = field(default_factory=list)
    # What it changes of the web page and the terminal (ext_ui.UI).
    ui: Any = None
    speech: list[Speech] = field(default_factory=list)
    briefing: Optional[Briefing] = None
    # A folder of guides for the knowledge base (skills.knowledge).
    knowledge: Optional[Path] = None

    def contents(self) -> list[str]:
        """What installing this adds, one line per kind of thing."""

        lines = []

        if self.commands:
            lines.append(
                "commands: "
                + ", ".join(f"/{c.name}" for c in self.commands)
            )
        if self.tools:
            lines.append(
                "tools: " + ", ".join(t.name for t in self.tools)
            )
        if self.prompt:
            lines.append(
                f"system prompt: {len(self.prompt)} characters added to "
                "every turn"
            )
        if self.backgrounds is not None:
            lines.append(f"backgrounds: {self.backgrounds.name}/")
        if self.knowledge is not None:
            guides = sorted(p.stem for p in self.knowledge.glob("*.md"))
            lines.append("knowledge base: " + ", ".join(guides))
        if self.providers:
            lines.append(
                "model providers: "
                + ", ".join(f"@{p.name}" for p in self.providers)
            )
        if self.themes:
            lines.append(
                "themes: " + ", ".join(t.name for t in self.themes)
            )
        if self.loaders:
            lines.append(
                "loading animations: "
                + ", ".join(loader.name for loader in self.loaders)
            )
        if self.events is not None:
            lines.append("follows turns: " + ", ".join(self.events.on))
        if self.voice is not None:
            lines.append(
                "in the middle of voice mode: " + ", ".join(self.voice.on)
            )
        if self.settings:
            lines.append(
                "settings: " + ", ".join(s.label for s in self.settings)
            )
        for kind, said in (("speaking", "voices"),
                           ("listening", "listening models")):
            names = [s.name for s in self.speech if s.kind == kind]
            if names:
                lines.append(f"{said}: " + ", ".join(
                    f"@{self.name}/{n}" for n in names))
        if self.briefing is not None:
            lines.append("cards in the briefing")
        if self.ui is not None:
            lines += self.ui.contents()

        return lines


def extensions_dir() -> Path:
    return FLASH_DIR / "extensions"


def _inside(root: Path, relative: str, where: str) -> Path:
    """RELATIVE resolved under ROOT, refusing anything that climbs out.

    A manifest that points at ../../.ssh/id_rsa as its prompt would
    otherwise read a file into every conversation it had no business
    reading.
    """

    base = root.resolve()
    path = (base / relative).resolve()

    if path != base and base not in path.parents:
        raise ExtensionError(f"{where}: {relative!r} is outside the extension")

    return path


def _text(value: Any, where: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ExtensionError(f"{where} has to be text")
    return value.strip()


def _timeout(value: Any, where: str) -> int:
    if value is None:
        return DEFAULT_TIMEOUT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExtensionError(f"{where}: timeout has to be whole seconds")
    if not 1 <= value <= MAX_TIMEOUT:
        raise ExtensionError(
            f"{where}: timeout has to be 1 to {MAX_TIMEOUT} seconds"
        )
    return value


def _argv(value: Any, root: Path, where: str) -> list[str]:
    """A `run` list, checked now so it cannot fail at call time.

    A list rather than a command line: nothing here goes through a
    shell, so there is no quoting to get wrong on either platform.
    """

    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise ExtensionError(
            f'{where}: "run" has to be a list of arguments, like '
            '["python", "./tool.py"]'
        )

    for part in value:
        if part.startswith("./"):
            _inside(root, part[2:], where)

    return list(value)


def _file_text(root: Path, relative: Any, where: str) -> str:
    if not isinstance(relative, str) or not relative:
        raise ExtensionError(f"{where} has to be a file path")

    path = _inside(root, relative, where)

    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ExtensionError(f"{where}: could not read {relative}: {exc}")


def _command(entry: Any, root: Path, index: int) -> Command:
    where = f"commands[{index}]"

    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")

    name = _text(entry.get("name"), f"{where}.name", required=True)
    name = name.removeprefix("/")

    if not COMMAND_RE.match(name):
        raise ExtensionError(
            f"{where}: {name!r} is not a command name (lowercase letters, "
            "digits, - and _)"
        )

    where = f"/{name}"
    has_prompt = "prompt" in entry
    has_run = "run" in entry

    if has_prompt == has_run:
        raise ExtensionError(
            f'{where} needs exactly one of "prompt" or "run"'
        )

    return Command(
        name=name,
        description=_text(entry.get("description"), f"{where}.description"),
        prompt=(
            _file_text(root, entry["prompt"], f"{where}.prompt")
            if has_prompt else None
        ),
        run=_argv(entry["run"], root, where) if has_run else None,
        timeout=_timeout(entry.get("timeout"), where),
    )


def _tool(entry: Any, root: Path, index: int) -> Tool:
    where = f"tools[{index}]"

    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")

    name = _text(entry.get("name"), f"{where}.name", required=True)

    if not TOOL_RE.match(name):
        raise ExtensionError(
            f"{where}: {name!r} is not a tool name (letters, digits, - "
            "and _, starting with a letter)"
        )

    where = f"tool {name}"
    parameters = entry.get("parameters", {"type": "object", "properties": {}})

    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ExtensionError(
            f'{where}: "parameters" has to be a JSON schema of type object'
        )

    confirm = entry.get("confirm", False)
    if not isinstance(confirm, bool):
        raise ExtensionError(f'{where}: "confirm" has to be true or false')

    return Tool(
        name=name,
        description=_text(
            entry.get("description"), f"{where}.description", required=True
        ),
        parameters=parameters,
        run=_argv(entry.get("run"), root, where),
        timeout=_timeout(entry.get("timeout"), where),
        confirm=confirm,
    )


def _provider(entry: Any, root: Path, index: int) -> Provider:
    where = f"providers[{index}]"

    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")

    name = _text(entry.get("name"), f"{where}.name", required=True).lower()

    if not NAME_RE.match(name):
        raise ExtensionError(
            f"{where}: {name!r} is not a provider name (lowercase letters, "
            "digits, - and _)"
        )

    where = f"provider {name}"
    timeout = DEFAULT_PROVIDER_TIMEOUT
    if entry.get("timeout") is not None:
        timeout = _timeout(entry.get("timeout"), where)

    return Provider(
        name=name,
        description=_text(entry.get("description"), f"{where}.description"),
        run=_argv(entry.get("run"), root, where),
        timeout=timeout,
    )


def _events(entry: Any, root: Path) -> Events:
    from .progress import EVENTS

    where = '"events"'
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    wanted = entry.get("on", list(EVENTS))
    if not isinstance(wanted, list) or not all(
        isinstance(name, str) for name in wanted
    ):
        raise ExtensionError(
            f'{where}: "on" has to list some of {", ".join(EVENTS)}'
        )
    # One this Flash does not know yet is left out, so an extension made
    # for a newer Flash still installs; it needs one it knows.
    wanted = [name for name in wanted if name in EVENTS]
    if not wanted:
        raise ExtensionError(
            f'{where}: "on" has to list some of {", ".join(EVENTS)}'
        )
    timeout = DEFAULT_TIMEOUT
    if entry.get("timeout") is not None:
        timeout = _timeout(entry.get("timeout"), where)
    return Events(
        run=_argv(entry.get("run"), root, where),
        on=tuple(dict.fromkeys(wanted)), timeout=timeout,
    )


def _voice(entry: Any, root: Path) -> Voice:
    from .voicehooks import DEFAULT_TIMEOUT as VOICE_TIMEOUT
    from .voicehooks import EVENTS as VOICE_EVENTS

    where = '"voice"'
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    wanted = entry.get("on", list(VOICE_EVENTS))
    if not isinstance(wanted, list) or not wanted or not all(
        isinstance(name, str) and name in VOICE_EVENTS for name in wanted
    ):
        raise ExtensionError(
            f'{where}: "on" has to list some of {", ".join(VOICE_EVENTS)}'
        )
    timeout = VOICE_TIMEOUT
    if entry.get("timeout") is not None:
        # Someone is waiting to be answered: never as long as a tool.
        timeout = min(_timeout(entry.get("timeout"), where), 30)
    return Voice(
        run=_argv(entry.get("run"), root, where),
        on=tuple(dict.fromkeys(wanted)), timeout=timeout,
    )


SPEECH_KINDS = ("speaking", "listening")


def _speech(entry: Any, root: Path, index: int) -> Speech:
    where = f"speech[{index}]"
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    name = _text(entry.get("name"), f"{where}.name", required=True).lower()
    if not NAME_RE.match(name):
        raise ExtensionError(
            f"{where}: {name!r} is not a name (lowercase letters, digits, "
            "- and _)")
    kind = entry.get("kind")
    if kind not in SPEECH_KINDS:
        raise ExtensionError(
            f'{where}: "kind" is "speaking" (a voice) or "listening"')
    rate = entry.get("rate", 22050)
    if not isinstance(rate, int) or isinstance(rate, bool) \
            or not 8000 <= rate <= 96000:
        raise ExtensionError(f'{where}: "rate" is a sample rate, in Hz')
    timeout = DEFAULT_TIMEOUT
    if entry.get("timeout") is not None:
        timeout = _timeout(entry.get("timeout"), where)
    return Speech(
        name=name, kind=kind,
        label=_text(entry.get("label"), f"{where}.label") or name,
        run=_argv(entry.get("run"), root, where), rate=rate,
        timeout=timeout,
    )


def _briefing(entry: Any, root: Path) -> Briefing:
    where = '"briefing"'
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    timeout = 10
    if entry.get("timeout") is not None:
        # Run while someone waits for the feed to open.
        timeout = min(_timeout(entry.get("timeout"), where), 30)
    return Briefing(run=_argv(entry.get("run"), root, where),
                    timeout=timeout)


def _setting(entry: Any, index: int) -> Setting:
    where = f"setting {index + 1}"
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    setting_id = _text(entry.get("id"), f"{where} id", required=True)
    if not SETTING_ID_RE.match(setting_id):
        raise ExtensionError(
            f"{where}: {setting_id!r} is not a setting id (lowercase "
            "letters, digits and _, starting with a letter)"
        )
    where = f"setting {setting_id!r}"
    kind = _text(entry.get("type"), f"{where} type") or "text"
    if kind not in SETTING_TYPES:
        raise ExtensionError(
            f"{where}: type is one of {', '.join(SETTING_TYPES)}")
    options: list[str] = []
    if kind == "select":
        raw = entry.get("options")
        if not isinstance(raw, list) or not raw:
            raise ExtensionError(f"{where}: a select needs its options")
        options = [" ".join(str(o).split())[:SETTING_CHARS]
                   for o in raw[:MAX_OPTIONS]]
    bounds = []
    for key in ("min", "max"):
        value = entry.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ExtensionError(f"{where}: {key} has to be a number")
        bounds.append(value)
    setting = Setting(
        id=setting_id,
        label=_text(entry.get("label"), f"{where} label") or setting_id,
        type=kind,
        default=None,
        help=_text(entry.get("help"), f"{where} help"),
        options=options,
        minimum=bounds[0] if kind == "number" else None,
        maximum=bounds[1] if kind == "number" else None,
        secret=kind == "text" and entry.get("secret") is True,
    )
    fallback = {"switch": False, "text": "", "number": 0,
                "select": options[0] if options else ""}[kind]
    default = entry.get("default", fallback)
    try:
        setting.default = setting.check(default)
    except ExtensionError as exc:
        raise ExtensionError(f"{where}: its default does not fit: {exc}")
    return setting


def _entries(manifest: dict, key: str) -> list:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        raise ExtensionError(f'"{key}" has to be a list')
    return value


def _unique(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ExtensionError(f"{what} {name!r} is declared twice")
        seen.add(name)


def _listed(manifest: dict, key: str, root: Path) -> list:
    """KEY's entries: written into the manifest, or in a JSON file it
    names."""

    value = manifest.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        text = _file_text(root, value, f'"{key}"')
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ExtensionError(f'"{key}": {value} is not JSON: {exc}')
    if not isinstance(value, list):
        raise ExtensionError(f'"{key}" has to be a list, or a file of one')
    return value


MOST_GUIDES = 50


def _knowledge(root: Path, folder: Any) -> Path:
    """The extension's folder of guides, each checked as a skill is."""

    from . import skills

    if not isinstance(folder, str) or not folder:
        raise ExtensionError('"knowledge" has to be a folder path')
    path = _inside(root, folder, '"knowledge"')
    if not path.is_dir():
        raise ExtensionError(f'"knowledge": {folder} is not a folder')
    guides = sorted(path.glob("*.md"))
    if not guides:
        raise ExtensionError(f'"knowledge": {folder} has no .md guides')
    if len(guides) > MOST_GUIDES:
        raise ExtensionError(f'"knowledge": at most {MOST_GUIDES} guides')
    for guide in guides:
        try:
            fields, body = skills._parse(guide.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ExtensionError(f'"knowledge": {guide.name}: {exc}')
        name = (fields.get("name") or guide.stem).strip().lower()
        if not skills.NAME_RE.match(name):
            raise ExtensionError(
                f'"knowledge": {guide.name}: {name!r} is not a name '
                "(lowercase letters, digits and -)")
        if not fields.get("description"):
            raise ExtensionError(
                f'"knowledge": {guide.name} needs a description: line at '
                "the top, between --- lines, saying when to read it")
        if not body or len(body) > skills.MAX_SKILL_CHARS:
            raise ExtensionError(
                f'"knowledge": {guide.name} has to hold a guide of at most '
                f"{skills.MAX_SKILL_CHARS} characters")
    return path


def load(root: Path) -> Extension:
    """Read and check the extension at ROOT.

    Everything is checked here, at install time, so a broken manifest
    is refused with a reason instead of failing halfway through some
    later conversation.
    """

    path = root / MANIFEST

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ExtensionError(f"no {MANIFEST} in its top folder")
    except OSError as exc:
        raise ExtensionError(f"could not read {MANIFEST}: {exc}")
    except ValueError as exc:
        raise ExtensionError(f"{MANIFEST} is not valid JSON: {exc}")

    if not isinstance(manifest, dict):
        raise ExtensionError(f"{MANIFEST} has to be a JSON object")

    name = _text(manifest.get("name"), '"name"', required=True).lower()

    if not NAME_RE.match(name):
        raise ExtensionError(
            f"{name!r} is not an extension name (lowercase letters, "
            "digits, - and _)"
        )

    prompt = ""
    if manifest.get("prompt") is not None:
        prompt = _file_text(root, manifest["prompt"], '"prompt"')
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ExtensionError(
                f"the prompt is {len(prompt)} characters; the limit is "
                f"{MAX_PROMPT_CHARS}, since it is sent on every turn"
            )

    backgrounds = None
    if manifest.get("backgrounds") is not None:
        folder = manifest["backgrounds"]
        if not isinstance(folder, str) or not folder:
            raise ExtensionError('"backgrounds" has to be a folder path')
        backgrounds = _inside(root, folder, '"backgrounds"')
        if not backgrounds.is_dir():
            raise ExtensionError(f'"backgrounds": {folder} is not a folder')

    knowledge = None
    if manifest.get("knowledge") is not None:
        knowledge = _knowledge(root, manifest["knowledge"])

    commands = [
        _command(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "commands"))
    ]
    tools = [
        _tool(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "tools"))
    ]

    providers = [
        _provider(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "providers"))
    ]

    from . import loaders as loading  # deferred: both read this module
    from . import themes as colours

    try:
        themes = [
            colours.from_manifest(entry, f"theme {index + 1}", name)
            for index, entry in enumerate(_listed(manifest, "themes", root))
        ]
        loaders = [
            loading.from_manifest(entry, f"loader {index + 1}", name)
            for index, entry in enumerate(
                _listed(manifest, "loaders", root)
            )
        ]
    except ValueError as exc:
        if isinstance(exc, ExtensionError):
            raise
        raise ExtensionError(str(exc))

    _unique([c.name for c in commands], "command")
    _unique([t.name for t in tools], "tool")
    _unique([p.name for p in providers], "provider")
    _unique([t.id for t in themes], "theme")
    _unique([loader.id for loader in loaders], "loader")

    events = (
        _events(manifest["events"], root)
        if manifest.get("events") is not None else None
    )

    voice = (
        _voice(manifest["voice"], root)
        if manifest.get("voice") is not None else None
    )

    settings = [
        _setting(entry, index)
        for index, entry in enumerate(_entries(manifest, "settings"))
    ]
    if len(settings) > MAX_SETTINGS:
        raise ExtensionError(
            f"{len(settings)} settings; the most is {MAX_SETTINGS}")
    _unique([s.id for s in settings], "setting")

    speech = [
        _speech(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "speech"))
    ]
    _unique([s.name for s in speech], "speech model")
    briefing = (
        _briefing(manifest["briefing"], root)
        if manifest.get("briefing") is not None else None
    )

    ui = None
    if manifest.get("ui") is not None:
        from . import ext_ui  # deferred: it reads this module's helpers

        ui = ext_ui.parse(manifest["ui"], root, {c.name for c in commands})

    if not (commands or tools or prompt or backgrounds or providers
            or themes or loaders or events or voice or ui or speech
            or briefing or knowledge):
        raise ExtensionError(
            f"{MANIFEST} declares nothing: no commands, tools, prompt, "
            "backgrounds, providers, themes, loaders, events, voice, "
            "speech, briefing, ui, or knowledge"
        )

    try:
        source = (root / SOURCE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        source = ""

    return Extension(
        name=name,
        path=root,
        description=_text(manifest.get("description"), '"description"'),
        version=_text(manifest.get("version"), '"version"'),
        source=source,
        prompt=prompt,
        commands=commands,
        tools=tools,
        backgrounds=backgrounds,
        knowledge=knowledge,
        providers=providers,
        themes=themes,
        loaders=loaders,
        events=events,
        voice=voice,
        settings=settings,
        ui=ui,
        speech=speech,
        briefing=briefing,
    )


# What is installed, read once and kept until something changes it:
# the tool list is asked for on every turn, and the command list on
# every keystroke the completion menu sees.
_cache: Optional[tuple[list[Extension], list[str]]] = None


def reload() -> None:
    """Forget what was read, so the next call reads the disk again."""

    global _cache
    _cache = None


def _scan() -> tuple[list[Extension], list[str]]:
    found: list[Extension] = []
    problems: list[str] = []

    try:
        folders = sorted(p for p in extensions_dir().iterdir() if p.is_dir())
    except OSError:
        return found, problems

    for folder in folders:
        if folder.name.startswith("."):
            continue
        # An extension removed while something held its folder open:
        # emptied, and nothing to report.
        if not any(folder.iterdir()):
            continue
        try:
            found.append(load(folder))
        except ExtensionError as exc:
            problems.append(f"{folder.name}: {exc}")

    return found, problems


def installed() -> list[Extension]:
    """Every extension that loads, in name order."""

    global _cache
    if _cache is None:
        _cache = _scan()
    return _cache[0]


def problems() -> list[str]:
    """Why each installed extension that does not load does not."""

    installed()
    assert _cache is not None  # nosec B101 -- filled by installed()
    return _cache[1]


def find(name: str) -> Optional[Extension]:
    wanted = name.strip().lower()
    return next((e for e in installed() if e.name == wanted), None)


def providers() -> list[tuple[Extension, Provider]]:
    """Every extension's model providers, the first to claim a name
    winning, which install already keeps from mattering."""

    taken: set[str] = set()
    found = []

    for extension in installed():
        for provider in extension.providers:
            if provider.name not in taken:
                taken.add(provider.name)
                found.append((extension, provider))

    return found


def commands() -> list[tuple[Extension, Command]]:
    """Every extension command, the first extension to claim a name
    winning, which install already keeps from mattering."""

    taken: set[str] = set()
    found = []

    for extension in installed():
        for command in extension.commands:
            if command.name not in taken:
                taken.add(command.name)
                found.append((extension, command))

    return found


def find_command(line: str) -> Optional[tuple[Extension, Command, str]]:
    """The extension command LINE invokes, and what followed its name."""

    if not line.startswith("/"):
        return None

    name, _, rest = line[1:].partition(" ")

    for extension, command in commands():
        if command.name == name:
            return extension, command, rest.strip()

    return None


def tools(reserved: frozenset = frozenset()) -> list[tuple[Extension, Tool]]:
    """Every extension tool whose name is not RESERVED or already taken.

    Built-in tools always win: a later Flash that adds a tool of the
    same name should not have it quietly replaced by an extension.
    """

    taken = set(reserved)
    found = []

    for extension in installed():
        for tool in extension.tools:
            if tool.name not in taken:
                taken.add(tool.name)
                found.append((extension, tool))

    return found


def tool_schemas(reserved: frozenset = frozenset()) -> list[dict[str, Any]]:
    """The extension tools as the chat API takes them."""

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for _extension, tool in tools(reserved)
    ]


def find_tool(
    name: str, reserved: frozenset = frozenset()
) -> Optional[tuple[Extension, Tool]]:
    return next(
        ((e, t) for e, t in tools(reserved) if t.name == name), None
    )


def system_prompt() -> str:
    """Every extension's prompt, each under a header naming it."""

    return "\n\n".join(
        f"=== Extension: {e.name} ===\n\n{e.prompt}"
        for e in installed()
        if e.prompt
    )


def background_dirs() -> list[Path]:
    return [e.backgrounds for e in installed() if e.backgrounds is not None]


def expand_prompt(command: Command, arguments: str) -> str:
    """A prompt command's text with what was typed after it filled in.

    A prompt that never mentions $ARGUMENTS still gets them, on a line
    of their own at the end, so `/review focus on errors` is not
    silently cut to `/review`.
    """

    text = command.prompt or ""

    if ARGUMENTS in text:
        return text.replace(ARGUMENTS, arguments)

    if arguments:
        return f"{text}\n\n{arguments}"

    return text


def argv(extension: Extension, run: list[str]) -> list[str]:
    """RUN ready for subprocess.

    `./` means inside the extension, since the program runs in the
    user's working directory, where the extension's own files are not.
    A bare `python` is the Python Flash runs on, which is there on
    every platform, unlike a `python3` on Windows.
    """

    resolved = [
        str(extension.path / part[2:]) if part.startswith("./") else part
        for part in run
    ]

    if resolved[0] in PYTHON_NAMES:
        resolved[0] = sys.executable

    return resolved


def settings_dir() -> Path:
    return FLASH_DIR / "extension-settings"


def _settings_path(name: str) -> Path:
    return settings_dir() / f"{name}.json"


def settings_values(extension: Extension) -> dict[str, Any]:
    """EXTENSION's settings as the user left them, each one's default
    where they have not, or where what was kept no longer fits."""

    try:
        saved = json.loads(
            _settings_path(extension.name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    values = {}
    for setting in extension.settings:
        try:
            values[setting.id] = setting.check(
                saved.get(setting.id, setting.default))
        except ExtensionError:
            values[setting.id] = setting.default
    return values


def set_setting(extension: Extension, setting_id: str,
                value: Any) -> dict[str, Any]:
    """Change one of EXTENSION's settings; every value, as now."""

    setting = next(
        (s for s in extension.settings if s.id == setting_id), None)
    if setting is None:
        raise ExtensionError(
            f"{extension.name} has no setting {setting_id!r}")
    values = settings_values(extension)
    values[setting.id] = setting.check(value)
    path = _settings_path(extension.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only this user may read it: a secret setting is kept in it.
    path.touch(mode=0o600)
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return values


def shown_values(extension: Extension) -> dict[str, Any]:
    """EXTENSION's settings as a page or a list may show them: a secret
    one only as whether it is set ("" or SECRET_SET), never itself."""

    values = settings_values(extension)
    for setting in extension.settings:
        # One of the vault's secrets, by name, is no secret itself: it is
        # shown as which, so the page can say so.
        if setting.secret and not secret_ref(values.get(setting.id)):
            values[setting.id] = SECRET_SET if values.get(setting.id) else ""
    return values


SECRET_SET = "\u2022" * 8
# A text setting can be one of the vault's secrets instead of a value of
# its own: {{secret:NAME}}, handed to its programs as the secret.
SECRET_REF_RE = re.compile(r"^\{\{secret:([A-Za-z0-9_.-]{1,64})\}\}$")


def secret_ref(value: Any) -> str:
    """The vault secret VALUE names, as {{secret:NAME}}; "" if none."""

    found = SECRET_REF_RE.match(value.strip()) if isinstance(value, str) \
        else None
    return found.group(1) if found else ""


def _env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str) and "{{secret:" in value:
        from . import vault

        try:
            return vault.expand(value)
        except vault.VaultError:
            # A secret since taken out of the vault: none, not its name.
            return ""
    return str(value)


def environment(extension: Extension) -> dict[str, str]:
    env = dict(os.environ)
    env["FLASH_EXTENSION_DIR"] = str(extension.path)
    if extension.settings:
        values = settings_values(extension)
        env["FLASH_SETTINGS"] = json.dumps(
            {k: _env_value(v) if isinstance(v, str) else v
             for k, v in values.items()})
        for setting_id, value in values.items():
            env[f"FLASH_SETTING_{setting_id.upper()}"] = _env_value(value)
    # Run on Flash's own Python, a Python program can import flash too,
    # and use what it has: its email account, say.
    here = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (here, env.get("PYTHONPATH", "")) if part
    )
    return env


def call_tool(extension: Extension, tool: Tool, arguments: dict) -> str:
    """Run TOOL with ARGUMENTS as JSON on stdin; return what it said."""

    try:
        result = subprocess.run(  # nosec B603
            argv(extension, tool.run),
            input=json.dumps(arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=tool.timeout,
            check=False,
            env=environment(extension),
        )
    except subprocess.TimeoutExpired:
        return f"Error: {tool.name} timed out after {tool.timeout} seconds."
    except OSError as exc:
        return f"Error: could not start {tool.name}: {exc}"

    output = result.stdout.strip()

    if result.returncode:
        detail = "\n".join(
            part for part in (output, result.stderr.strip()) if part
        )
        return f"(exit {result.returncode})\n{detail}".rstrip()

    return output or "(no output)"


# Speech -----------------------------------------------------------------


def speech_models(kind: str) -> list[tuple[Extension, Speech]]:
    """Every installed extension's voices ("speaking") or listening
    models ("listening")."""

    return [(e, s) for e in installed() for s in e.speech if s.kind == kind]


def speech_name(extension: Extension, speech: Speech) -> str:
    """How Settings and VOICE_* name it: @extension/name."""

    return f"@{extension.name}/{speech.name}"


def find_speech(name: str, kind: str = ""
                ) -> Optional[tuple[Extension, Speech]]:
    for extension, speech in speech_models(kind) if kind else [
            (e, s) for e in installed() for s in e.speech]:
        if speech_name(extension, speech) == name:
            return extension, speech
    return None


def _run_bytes(extension: Extension, run: list[str], given: bytes,
               timeout: int, what: str) -> bytes:
    try:
        result = subprocess.run(  # nosec B603
            argv(extension, run), input=given, capture_output=True,
            timeout=timeout, check=False, env=environment(extension),
        )
    except subprocess.TimeoutExpired:
        raise ExtensionError(
            f"{what} took longer than {timeout} seconds") from None
    except OSError as exc:
        raise ExtensionError(f"could not start {what}: {exc}") from None
    if result.returncode:
        why = result.stderr.decode("utf-8", "replace").strip()
        raise ExtensionError(
            f"{what} stopped with exit {result.returncode}"
            + (f": {why.splitlines()[-1]}" if why else ""))
    return result.stdout


def speak_pcm(extension: Extension, speech: Speech, text: str,
              pace: float = 1.0) -> tuple[bytes, int]:
    """TEXT said by the extension's voice: 16-bit mono audio, and its
    sample rate."""

    out = _run_bytes(extension, speech.run, json.dumps(
        {"text": text, "pace": pace}).encode("utf-8"), speech.timeout,
        speech_name(extension, speech))
    if out[:4] == b"RIFF":
        import io
        import wave

        with wave.open(io.BytesIO(out)) as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                raise ExtensionError(
                    f"{speech_name(extension, speech)} has to write 16-bit "
                    "mono audio")
            return wav.readframes(wav.getnframes()), wav.getframerate()
    return out[:len(out) - len(out) % 2], speech.rate


def transcribe_pcm(extension: Extension, speech: Speech,
                   pcm: bytes) -> str:
    """What was said in PCM (16 kHz mono 16-bit), as the extension's
    listening model hears it."""

    out = _run_bytes(extension, speech.run, pcm, speech.timeout,
                     speech_name(extension, speech))
    return " ".join(out.decode("utf-8", "replace").split())


# The briefing --------------------------------------------------------------


def briefing_cards() -> list[tuple[Extension, list[dict]]]:
    """What each extension with a briefing program has for it now, as
    the program wrote it; one that fails is left out."""

    found = []
    for extension in installed():
        if extension.briefing is None:
            continue
        try:
            out = _run_bytes(extension, extension.briefing.run, b"",
                             extension.briefing.timeout,
                             f"{extension.name}'s briefing")
            cards = json.loads(out.decode("utf-8", "replace") or "[]")
        except (ExtensionError, ValueError):
            continue
        if isinstance(cards, dict):
            cards = [cards]
        if isinstance(cards, list):
            found.append((extension, [c for c in cards
                                      if isinstance(c, dict)]))
    return found


# Installing ------------------------------------------------------------


# A branch to install from, after the repository: github@owner/repo#dev.
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def split_branch(target: str) -> tuple[str, str]:
    """TARGET without the branch after its #, and that branch ("" for
    the repository's default)."""

    where, _, branch = target.partition("#")
    branch = branch.strip()
    if branch and (not BRANCH_RE.match(branch) or ".." in branch
                   or branch.startswith("-")):
        raise ExtensionError(f"{branch!r} is not a branch name.")
    return where.strip(), branch


def parse_source(spec: str) -> tuple[str, str]:
    """`github@owner/repo`, `git@<any git URL>` or `path@folder`, as
    (kind, target). A repository can name a branch after a #:
    `github@owner/repo#dev`, `git@https://host/x.git#next`. A path can
    also be a git bundle, a file with `.bundle` in its name, as `git
    bundle create` makes to carry a repository about as one file."""

    kind, at, target = spec.strip().partition("@")
    kind = kind.lower()
    target = target.strip()

    if not at or not target:
        raise ExtensionError(
            f"{spec!r} is not an extension source. Use "
            "github@owner/repo, git@<repository URL>, or "
            "path@/some/folder or path@/some/file.bundle for one on disk."
        )

    if kind == "github":
        where, branch = split_branch(target)
        where = where.removesuffix("/").removesuffix(".git")
        if not GITHUB_RE.match(where):
            raise ExtensionError(
                f"{where!r} is not a GitHub repository; expected "
                "owner/repo"
            )
        return kind, where + (f"#{branch}" if branch else "")

    if kind == "git":
        where, branch = split_branch(target)
        # git@github.com:owner/repo.git, as GitHub shows it to copy: the
        # @ that split it off was the URL's own.
        if re.match(r"^[\w.-]+:[^/]", where):
            where = f"git@{where}"
        # Any host git can clone from: https, ssh (git@host:x/y.git), or
        # a file:// URL. Never something git would read as an option.
        if where.startswith("-") or not re.match(
                r"^(https?://|ssh://|git://|file://|[\w.-]+@[\w.-]+:)",
                where):
            raise ExtensionError(
                f"{where!r} is not a git repository URL: it starts "
                "https://, ssh://, git://, file://, or user@host:")
        return kind, where + (f"#{branch}" if branch else "")

    if kind == "path":
        return kind, str(Path(target).expanduser().resolve())

    raise ExtensionError(
        f"Unknown extension source {kind!r}. Use github@owner/repo, "
        "git@<repository URL>, path@/some/folder, or "
        "path@/some/file.bundle."
    )


def is_bundle(target: str) -> bool:
    """Whether the path TARGET names a git bundle rather than a folder:
    `.bundle` in its name, and not a folder (macOS calls some folders
    bundles too)."""

    path = Path(target)
    return ".bundle" in path.name.lower() and not path.is_dir()


def _git(args: list[str]) -> subprocess.CompletedProcess:
    if not shutil.which("git"):
        raise ExtensionError(
            "git is needed to install from GitHub or a bundle, but was "
            "not found."
        )
    # A repository that does not exist, or is private, makes git ask
    # for a username on the terminal, which would hang a session that
    # is only waiting for a clone.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(  # nosec B603 B607
            ["git", *args], capture_output=True, text=True,
            timeout=CLONE_TIMEOUT_SECONDS, check=False, env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ExtensionError(
            f"git took longer than {CLONE_TIMEOUT_SECONDS} seconds."
        ) from None


def _last_line(result: subprocess.CompletedProcess) -> str:
    detail = (result.stderr or result.stdout).strip()
    return detail.splitlines()[-1] if detail else ""


def bundle_branch(target: str) -> str:
    """The branch a bundle's extension is installed from: the one its
    HEAD points at, else main, master, or the first it holds."""

    path = Path(target)
    if not path.is_file():
        raise ExtensionError(f"There is no bundle at {target}.")
    result = _git(["bundle", "list-heads", str(path)])
    if result.returncode:
        why = _last_line(result)
        raise ExtensionError(
            f"{path.name} is not a git bundle" + (f": {why}" if why else ".")
        )
    heads = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.strip().partition(" ")
        if sha and ref:
            heads[ref] = sha
    branches = [r for r in heads if r.startswith("refs/heads/")]
    if not branches:
        raise ExtensionError(f"{path.name} holds no branch to install.")
    if "HEAD" in heads:
        pointed = [r for r in branches if heads[r] == heads["HEAD"]]
        if pointed:
            return pointed[0].removeprefix("refs/heads/")
    for name in ("main", "master"):
        if f"refs/heads/{name}" in heads:
            return name
    return branches[0].removeprefix("refs/heads/")


def source_note(spec: str) -> str:
    """A line to show before installing from SPEC, when there is
    something worth knowing about where it comes from: "" otherwise."""

    try:
        kind, target = parse_source(spec)
    except ExtensionError:
        return ""
    if kind != "path" or not is_bundle(target):
        return ""
    try:
        branch = bundle_branch(target)
    except ExtensionError:
        branch = ""
    on = f" of its {branch} branch" if branch else ""
    return (
        f"{Path(target).name} is a git bundle: this installs the copy{on} "
        "it carries. To update, install a newer bundle the same way."
    )


def canonical(spec: str) -> str:
    """SPEC as it is recorded, so `path@.` still means the same folder
    when the extension is installed again from somewhere else."""

    kind, target = parse_source(spec)
    return f"{kind}@{target}"


def fetch(spec: str) -> Path:
    """Put the extension SPEC names in a new temporary folder.

    The caller owns that folder and removes it. Nothing is installed
    yet: this is what gets read, shown, and confirmed first.
    """

    kind, target = parse_source(spec)
    staging = Path(tempfile.mkdtemp(prefix="flash-extension-"))
    checkout = staging / "extension"

    try:
        if kind == "path" and is_bundle(target):
            # The branch named, since a bundle made from one branch
            # carries no HEAD for a plain clone to check out.
            branch = bundle_branch(target)
            result = _git([
                "clone", "--quiet", "--branch", branch, target,
                str(checkout),
            ])
            if result.returncode:
                why = _last_line(result)
                raise ExtensionError(
                    f"Could not open {Path(target).name}"
                    + (f": {why}" if why else ".")
                )
            shutil.rmtree(checkout / ".git", ignore_errors=True)
            return checkout

        if kind == "path":
            if not Path(target).is_dir():
                raise ExtensionError(f"{target} is not a folder")
            shutil.copytree(
                target, checkout, ignore=shutil.ignore_patterns(".git")
            )
            return checkout

        where, branch = split_branch(target)
        url = f"https://github.com/{where}.git" if kind == "github" \
            else where
        shown = f"github.com/{where}" if kind == "github" else where
        result = _git([
            "clone", "--depth", "1", "--quiet",
            *(["--branch", branch] if branch else []),
            "--", url, str(checkout),
        ])
        if result.returncode:
            why = _last_line(result)
            raise ExtensionError(
                f"Could not download {shown}"
                + (f"'s {branch} branch" if branch else "")
                + (f": {why}" if why else ".")
            )

        shutil.rmtree(checkout / ".git", ignore_errors=True)
        return checkout
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def update_source(name: str) -> str:
    """Where the extension NAME was installed from, to pull it again."""

    extension = find(name)
    if extension is None:
        raise ExtensionError(f"No extension called {name!r} is installed.")
    if not extension.source:
        raise ExtensionError(
            f"{extension.name} does not say where it came from, so it "
            "cannot be pulled again. Install it again from its source.")
    return extension.source


def discard(checkout: Path) -> None:
    """Remove what fetch() made."""

    shutil.rmtree(checkout.parent, ignore_errors=True)


def clashes(
    extension: Extension,
    reserved_commands: frozenset,
    reserved_tools: frozenset,
) -> list[str]:
    """Every name EXTENSION wants that something else already has.

    Its own earlier version does not count: installing over it is how
    an extension is updated.
    """

    found = []
    others = [e for e in installed() if e.name != extension.name]

    for command in extension.commands:
        owner = next(
            (e.name for e in others
             if any(c.name == command.name for c in e.commands)),
            None,
        )
        if f"/{command.name}" in reserved_commands:
            found.append(f"/{command.name} is a built-in command")
        elif owner:
            found.append(f"/{command.name} belongs to {owner}")

    for tool in extension.tools:
        owner = next(
            (e.name for e in others
             if any(t.name == tool.name for t in e.tools)),
            None,
        )
        if tool.name in reserved_tools:
            found.append(f"{tool.name} is a built-in tool")
        elif owner:
            found.append(f"tool {tool.name} belongs to {owner}")

    for provider in extension.providers:
        owner = next(
            (e.name for e in others
             if any(p.name == provider.name for p in e.providers)),
            None,
        )
        if owner:
            found.append(f"provider @{provider.name} belongs to {owner}")

    return found


def install(checkout: Path, spec: str) -> Extension:
    """Move a fetched extension into place, over any older copy."""

    extension = load(checkout)
    (checkout / SOURCE_FILE).write_text(
        canonical(spec) + "\n", encoding="utf-8"
    )

    root = extensions_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / extension.name

    # Copied in beside the old one and swapped: the old one moves aside,
    # the new one takes its place, and only then is the old one emptied.
    # Anything that goes wrong on the way puts the old one back, so an
    # update never leaves less than there was.
    incoming = root / f".{extension.name}.incoming"
    previous = root / f".{extension.name}.previous"
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.copytree(checkout, incoming)

    if previous.exists():
        empty_folder(previous)
        with contextlib.suppress(OSError):
            previous.rmdir()
    moved = False
    if target.exists():
        try:
            target.rename(previous)
            moved = True
        except OSError:
            # Held open (Windows), so it stays where it is: the new
            # copy goes into it instead, file by file.
            shutil.copytree(incoming, target, dirs_exist_ok=True)
            shutil.rmtree(incoming, ignore_errors=True)
    if not target.exists():
        try:
            incoming.rename(target)
        except OSError as exc:
            if moved:
                previous.rename(target)
            shutil.rmtree(incoming, ignore_errors=True)
            raise ExtensionError(
                f"Could not put the new copy in place: {exc}. The one "
                "you had is kept.") from None
    if moved:
        empty_folder(previous)
        with contextlib.suppress(OSError):
            previous.rmdir()

    reload()
    return load(target)


def empty_folder(folder: Path) -> list[str]:
    """Delete everything inside FOLDER, from the deepest up: each file,
    then each folder once it is empty. A read-only one, as git leaves
    its objects on Windows, is made writable first. A link is removed,
    never followed. What could not be removed, relative to FOLDER."""

    left: list[str] = []

    def gone(path: Path, remove: Callable[[Path], None]) -> None:
        try:
            remove(path)
        except PermissionError:
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                remove(path)
            except OSError:
                left.append(str(path.relative_to(folder)))
        except FileNotFoundError:
            pass
        except OSError:
            left.append(str(path.relative_to(folder)))

    for top, dirs, files in os.walk(folder, topdown=False):
        here = Path(top)
        for name in files:
            gone(here / name, Path.unlink)
        for name in dirs:
            path = here / name
            gone(path, Path.unlink if path.is_symlink() else Path.rmdir)
    return left


def remove(name: str) -> bool:
    """Delete an installed extension: everything in its folder, then the
    folder if it can go. False if there was none; ExtensionError if some
    of it could not be removed."""

    name = name.strip().lower()

    # The name becomes a path, so it has to be one of ours.
    if not NAME_RE.match(name):
        return False

    target = extensions_dir() / name

    if not target.is_dir():
        return False

    left = empty_folder(target)
    if left:
        reload()
        raise ExtensionError(
            f"Could not remove {len(left)} of {name}'s files, such as "
            f"{left[0]}. Close what has them open and remove it again."
        )
    # The folder itself too, if it can go: on Windows something may still
    # hold it open, and then it stays, empty, and is passed over.
    with contextlib.suppress(OSError):
        target.rmdir()
    # Its settings go with it; an update, which installs over it, keeps
    # them.
    try:
        _settings_path(name).unlink()
    except OSError:
        pass
    reload()
    return True
