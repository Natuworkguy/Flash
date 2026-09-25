"""Extensions: slash commands, tools, prompts, and scenes from outside.

An extension is a folder with a `flash-extension.json` at its root,
usually a GitHub repository, installed with

    flash --extension-install github@owner/repo

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
      "backgrounds": "scenes"
    }

Everything an extension runs is a program, not Python loaded into
Flash, so an extension can be written in anything, cannot take the
session down with it, and needs nothing installed into Flash's own
environment. A tool gets its arguments as JSON on stdin and answers
on stdout; a command gets whatever was typed after it as arguments.
"""

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
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
MAX_TIMEOUT = 600

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

    commands = [
        _command(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "commands"))
    ]
    tools = [
        _tool(entry, root, index)
        for index, entry in enumerate(_entries(manifest, "tools"))
    ]

    _unique([c.name for c in commands], "command")
    _unique([t.name for t in tools], "tool")

    if not (commands or tools or prompt or backgrounds):
        raise ExtensionError(
            f"{MANIFEST} declares nothing: no commands, tools, prompt, or "
            "backgrounds"
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


def environment(extension: Extension) -> dict[str, str]:
    env = dict(os.environ)
    env["FLASH_EXTENSION_DIR"] = str(extension.path)
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


# Installing ------------------------------------------------------------


def parse_source(spec: str) -> tuple[str, str]:
    """`github@owner/repo` or `path@folder`, as (kind, target)."""

    kind, at, target = spec.strip().partition("@")
    kind = kind.lower()
    target = target.strip()

    if not at or not target:
        raise ExtensionError(
            f"{spec!r} is not an extension source. Use "
            "github@owner/repo, or path@/some/folder for one on disk."
        )

    if kind == "github":
        target = target.removesuffix("/").removesuffix(".git")
        if not GITHUB_RE.match(target):
            raise ExtensionError(
                f"{target!r} is not a GitHub repository; expected "
                "owner/repo"
            )
        return kind, target

    if kind == "path":
        return kind, str(Path(target).expanduser().resolve())

    raise ExtensionError(
        f"Unknown extension source {kind!r}. Use github@owner/repo or "
        "path@/some/folder."
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
        if kind == "path":
            if not Path(target).is_dir():
                raise ExtensionError(f"{target} is not a folder")
            shutil.copytree(
                target, checkout, ignore=shutil.ignore_patterns(".git")
            )
            return checkout

        if not shutil.which("git"):
            raise ExtensionError(
                "git is needed to install from GitHub but was not found."
            )

        # A repository that does not exist, or is private, makes git
        # ask for a username on the terminal, which would hang a
        # session that is only waiting for a clone.
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

        try:
            result = subprocess.run(  # nosec B603 B607
                [
                    "git", "clone", "--depth", "1", "--quiet",
                    f"https://github.com/{target}.git", str(checkout),
                ],
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
                check=False,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ExtensionError(
                f"Downloading {target} took longer than "
                f"{CLONE_TIMEOUT_SECONDS} seconds."
            )

        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ExtensionError(
                f"Could not download github.com/{target}"
                + (f": {detail.splitlines()[-1]}" if detail else ".")
            )

        shutil.rmtree(checkout / ".git", ignore_errors=True)
        return checkout
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


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

    # Copied in beside the old one and swapped, so a copy that fails
    # partway leaves the working version where it was.
    incoming = root / f".{extension.name}.incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.copytree(checkout, incoming)

    if target.exists():
        shutil.rmtree(target)
    incoming.rename(target)

    reload()
    return load(target)


def remove(name: str) -> bool:
    """Delete an installed extension. False if there was none."""

    name = name.strip().lower()

    # The name becomes a path, so it has to be one of ours.
    if not NAME_RE.match(name):
        return False

    target = extensions_dir() / name

    if not target.is_dir():
        return False

    shutil.rmtree(target)
    reload()
    return True
