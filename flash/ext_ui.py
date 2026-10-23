"""What an extension changes of the web page and the terminal: its
manifest's "ui".

    "ui": {
      "web": "web",
      "css": "style.css",
      "script": "main.js",
      "panels": [{"id": "board", "label": "Board", "icon": "cube",
                  "file": "board.html"}],
      "buttons": [{"id": "tldr", "label": "TL;DR", "icon": "bolt",
                   "where": ["message", "composer"], "key": "c-t",
                   "prompt": "Sum this up in a line: $TEXT"}],
      "status": {"run": ["./status.sh"], "every": 30},
      "greeting": "What's the weather doing?",
      "suggestions": ["Forecast for Paris", "Will it rain today?"],
      "placeholder": "Ask about the weather",
      "banner": "banner.txt"
    }

"web" is the folder the page is served from (/ext/<name>/...): the
stylesheet, the script and the panels are files in it. Everything is
checked when the extension is read, so a mistake is refused at install
rather than breaking the page later.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404  (the extension's own programs)
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .extensions import (
    ExtensionError,
    _argv,
    _inside,
    _text,
    _timeout,
)

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# Where a button can go. "terminal" is a key in the terminal's box.
PLACES = ("sidebar", "composer", "message", "header", "terminal")
# Keys a button may take in the terminal: Ctrl or Alt with a letter.
KEY_RE = re.compile(r"^(c|m|escape)-[a-z]$")
TAKEN_KEYS = {"c-c", "c-d", "c-z", "c-j", "c-m", "c-i", "c-h", "c-o",
              "c-b", "c-k", "c-r", "c-t", "c-l"}
TEXT = "$TEXT"
LABEL_CHARS = 40
GREETING_CHARS = 120
SUGGESTION_CHARS = 80
MOST_SUGGESTIONS = 8
MOST_BUTTONS = 12
MOST_PANELS = 6
BANNER_CHARS = 2000
BANNER_LINES = 20
STATUS_CHARS = 80
STATUS_EVERY = 30
MIN_STATUS_EVERY = 5
STATUS_TIMEOUT = 10
BUTTON_TIMEOUT = 30
WEB_FILES = (".css", ".js", ".html", ".svg", ".png", ".jpg", ".jpeg",
             ".gif", ".webp", ".woff2", ".woff", ".json")


@dataclass
class Button:
    id: str
    label: str
    where: tuple[str, ...]
    icon: str = ""
    key: str = ""
    prompt: str = ""
    command: str = ""
    run: list[str] = field(default_factory=list)
    timeout: int = BUTTON_TIMEOUT

    def summary(self) -> dict:
        return {"id": self.id, "label": self.label, "icon": self.icon,
                "where": list(self.where), "key": self.key,
                "does": "prompt" if self.prompt else
                "command" if self.command else "run",
                "command": self.command}


@dataclass
class Panel:
    id: str
    label: str
    file: str
    icon: str = ""


@dataclass
class Status:
    run: list[str]
    every: int = STATUS_EVERY
    timeout: int = STATUS_TIMEOUT


@dataclass
class UI:
    web: Optional[Path] = None
    css: str = ""
    script: str = ""
    panels: list[Panel] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    status: Optional[Status] = None
    greeting: str = ""
    suggestions: list[str] = field(default_factory=list)
    placeholder: str = ""
    banner: str = ""

    def contents(self) -> list[str]:
        """What it changes, for the install preview."""

        lines = []
        if self.script:
            lines.append(f"runs its own script in the web page: {self.script}")
        if self.css:
            lines.append(f"restyles the web page: {self.css}")
        if self.panels:
            lines.append("panels: " + ", ".join(p.label for p in self.panels))
        if self.buttons:
            lines.append("buttons: " + ", ".join(
                f"{b.label} ({', '.join(b.where)})" for b in self.buttons))
        if self.status is not None:
            lines.append("a status line, every "
                         f"{self.status.every} seconds")
        if self.greeting or self.suggestions:
            lines.append("a new chat's greeting and suggestions")
        if self.placeholder or self.banner:
            lines.append("the terminal's banner and box text")
        return lines

    def summary(self, name: str) -> dict:
        """What the page draws."""

        base = f"/ext/{name}/"
        return {
            "name": name,
            "css": base + self.css if self.css else "",
            "script": base + self.script if self.script else "",
            "panels": [{"id": p.id, "label": p.label, "icon": p.icon,
                        "url": base + p.file} for p in self.panels],
            "buttons": [b.summary() for b in self.buttons
                        if set(b.where) - {"terminal"}],
            "greeting": self.greeting,
            "suggestions": list(self.suggestions),
            "placeholder": self.placeholder,
            "status": self.status is not None,
        }


def _label(value: Any, where: str, most: int = LABEL_CHARS) -> str:
    text = " ".join(_text(value, where, required=True).split())
    if len(text) > most:
        raise ExtensionError(f"{where} is longer than {most} characters")
    return text


def _id(value: Any, where: str) -> str:
    found = _text(value, where, required=True)
    if not ID_RE.match(found):
        raise ExtensionError(
            f"{where}: {found!r} is not an id (lowercase letters, digits, "
            "- and _)")
    return found


def _web_file(web: Optional[Path], value: Any, where: str,
              kinds: tuple[str, ...]) -> str:
    if web is None:
        raise ExtensionError(f'{where} needs "web", the folder it is in')
    name = _text(value, where, required=True)
    path = _inside(web, name, where)
    if path.suffix.lower() not in kinds:
        raise ExtensionError(f"{where}: {name} is not a {'/'.join(kinds)}")
    if not path.is_file():
        raise ExtensionError(f"{where}: there is no {name} in the web folder")
    return path.relative_to(web.resolve()).as_posix()


def _button(entry: Any, root: Path, index: int,
            commands: set[str]) -> Button:
    where = f'"ui" buttons[{index}]'
    if not isinstance(entry, dict):
        raise ExtensionError(f"{where} has to be an object")
    places = entry.get("where", ["composer"])
    if isinstance(places, str):
        places = [places]
    if not isinstance(places, list) or not places or not all(
            p in PLACES for p in places):
        raise ExtensionError(
            f'{where}: "where" has to list some of {", ".join(PLACES)}')
    key = _text(entry.get("key"), f"{where} key").lower()
    if key and (not KEY_RE.match(key) or key in TAKEN_KEYS):
        raise ExtensionError(
            f"{where}: key {key!r} has to be c- or m- with a letter, and "
            "not one Flash uses")
    if "terminal" in places and not key:
        raise ExtensionError(f'{where}: a "terminal" button needs a "key"')
    does = [k for k in ("prompt", "command", "run") if entry.get(k)]
    if len(does) != 1:
        raise ExtensionError(
            f'{where} needs one of "prompt", "command" or "run"')
    command = _text(entry.get("command"), f"{where} command").lstrip("/")
    if command and command not in commands:
        raise ExtensionError(f"{where}: there is no command /{command}")
    return Button(
        id=_id(entry.get("id"), f"{where} id"),
        label=_label(entry.get("label"), f"{where} label"),
        where=tuple(dict.fromkeys(places)),
        icon=_text(entry.get("icon"), f"{where} icon")[:24],
        key=key,
        prompt=_text(entry.get("prompt"), f"{where} prompt")[:4000],
        command=command,
        run=_argv(entry["run"], root, where) if entry.get("run") else [],
        timeout=_timeout(entry.get("timeout"), where)
        if entry.get("timeout") is not None else BUTTON_TIMEOUT,
    )


def parse(entry: Any, root: Path, commands: set[str]) -> UI:
    """The "ui" of the extension at ROOT, whose commands are COMMANDS."""

    if not isinstance(entry, dict):
        raise ExtensionError('"ui" has to be an object')
    ui = UI()
    if entry.get("web") is not None:
        folder = _text(entry["web"], '"ui" web', required=True)
        ui.web = _inside(root, folder, '"ui" web')
        if not ui.web.is_dir():
            raise ExtensionError(f'"ui" web: {folder} is not a folder')
    if entry.get("css") is not None:
        ui.css = _web_file(ui.web, entry["css"], '"ui" css', (".css",))
    if entry.get("script") is not None:
        ui.script = _web_file(ui.web, entry["script"], '"ui" script',
                              (".js",))
    panels = entry.get("panels", [])
    if not isinstance(panels, list) or len(panels) > MOST_PANELS:
        raise ExtensionError(
            f'"ui" panels has to be a list of up to {MOST_PANELS}')
    for index, panel in enumerate(panels):
        where = f'"ui" panels[{index}]'
        if not isinstance(panel, dict):
            raise ExtensionError(f"{where} has to be an object")
        ui.panels.append(Panel(
            id=_id(panel.get("id"), f"{where} id"),
            label=_label(panel.get("label"), f"{where} label"),
            file=_web_file(ui.web, panel.get("file"), f"{where} file",
                           (".html",)),
            icon=_text(panel.get("icon"), f"{where} icon")[:24],
        ))
    buttons = entry.get("buttons", [])
    if not isinstance(buttons, list) or len(buttons) > MOST_BUTTONS:
        raise ExtensionError(
            f'"ui" buttons has to be a list of up to {MOST_BUTTONS}')
    ui.buttons = [_button(b, root, i, commands)
                  for i, b in enumerate(buttons)]
    for what, names in (("panel", [p.id for p in ui.panels]),
                        ("button", [b.id for b in ui.buttons]),
                        ("key", [b.key for b in ui.buttons if b.key])):
        if len(names) != len(set(names)):
            raise ExtensionError(f'"ui": a {what} is declared twice')
    if entry.get("status") is not None:
        status = entry["status"]
        if not isinstance(status, dict):
            raise ExtensionError('"ui" status has to be an object')
        every = status.get("every", STATUS_EVERY)
        if isinstance(every, bool) or not isinstance(every, int) or \
                not MIN_STATUS_EVERY <= every <= 3600:
            raise ExtensionError(
                f'"ui" status: "every" has to be {MIN_STATUS_EVERY} to '
                "3600 seconds")
        ui.status = Status(
            run=_argv(status.get("run"), root, '"ui" status'), every=every,
            timeout=min(_timeout(status.get("timeout"), '"ui" status'),
                        STATUS_TIMEOUT) if status.get("timeout") is not None
            else STATUS_TIMEOUT,
        )
    if entry.get("greeting") is not None:
        ui.greeting = _label(entry["greeting"], '"ui" greeting',
                             GREETING_CHARS)
    suggestions = entry.get("suggestions", [])
    if not isinstance(suggestions, list) or \
            len(suggestions) > MOST_SUGGESTIONS:
        raise ExtensionError(
            f'"ui" suggestions has to be a list of up to {MOST_SUGGESTIONS}')
    ui.suggestions = [_label(s, f'"ui" suggestions[{i}]', SUGGESTION_CHARS)
                      for i, s in enumerate(suggestions)]
    if entry.get("placeholder") is not None:
        ui.placeholder = _label(entry["placeholder"], '"ui" placeholder',
                                GREETING_CHARS)
    if entry.get("banner") is not None:
        from .extensions import _file_text

        banner = _file_text(root, entry["banner"], '"ui" banner')
        lines = banner.splitlines()
        if len(banner) > BANNER_CHARS or len(lines) > BANNER_LINES:
            raise ExtensionError(
                f'"ui" banner: at most {BANNER_LINES} lines and '
                f"{BANNER_CHARS} characters")
        ui.banner = banner
    if not (ui.css or ui.script or ui.panels or ui.buttons or ui.status
            or ui.greeting or ui.suggestions or ui.placeholder
            or ui.banner):
        raise ExtensionError('"ui" changes nothing')
    return ui


# --- What the installed extensions change --------------------------------


def with_ui() -> list:
    from . import extensions

    return [e for e in extensions.installed() if e.ui is not None]


def web_file(name: str, path: str) -> Optional[tuple[bytes, str]]:
    """A file from extension NAME's web folder, and its type; None for
    anything else: no climbing out, and only the kinds a page uses."""

    from . import extensions

    ext = extensions.find(name)
    if ext is None or ext.ui is None or ext.ui.web is None:
        return None
    try:
        found = _inside(ext.ui.web, path, "web")
    except ExtensionError:
        return None
    suffix = found.suffix.lower()
    if suffix not in WEB_FILES or not found.is_file():
        return None
    kinds = {".css": "text/css", ".js": "text/javascript",
             ".html": "text/html", ".svg": "image/svg+xml",
             ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".woff2": "font/woff2",
             ".woff": "font/woff", ".json": "application/json"}
    kind = kinds[suffix]
    if kind.startswith("text/") or kind == "application/json":
        kind += "; charset=utf-8"
    return found.read_bytes(), kind


def greeting() -> str:
    """The newest extension's greeting, for a new chat, or ""."""

    return next((e.ui.greeting for e in reversed(with_ui())
                 if e.ui.greeting), "")


def suggestions() -> list[str]:
    found: list[str] = []
    for ext in with_ui():
        found += ext.ui.suggestions
    return found


def placeholder() -> str:
    return next((e.ui.placeholder for e in reversed(with_ui())
                 if e.ui.placeholder), "")


def banners() -> list[tuple[str, str]]:
    return [(e.name, e.ui.banner) for e in with_ui() if e.ui.banner]


def terminal_buttons() -> list[tuple[Any, Button]]:
    return [(e, b) for e in with_ui() for b in e.ui.buttons
            if "terminal" in b.where and b.key]


def find_button(name: str, button_id: str) -> Optional[tuple[Any, Button]]:
    from . import extensions

    ext = extensions.find(name)
    if ext is None or ext.ui is None:
        return None
    found = next((b for b in ext.ui.buttons if b.id == button_id), None)
    return (ext, found) if found else None


def expand(button: Button, text: str) -> str:
    """A prompt button's message, with TEXT where it says $TEXT (or after
    it, when it does not say)."""

    if TEXT in button.prompt:
        return button.prompt.replace(TEXT, text).strip()
    return f"{button.prompt}\n\n{text}".strip() if text else button.prompt


def press(ext: Any, button: Button, text: str = "", chat: str = "") -> dict:
    """Run a "run" button's program, with {button, text, chat} as JSON on
    stdin. What it prints is what it says: plain text to show, or JSON
    with "say" (to show), "insert" (into the box) or "send" (as a
    message)."""

    from . import extensions

    try:
        done = subprocess.run(  # nosec B603  (its own program, no shell)
            extensions.argv(ext, button.run),
            input=json.dumps({"button": button.id, "text": text,
                              "chat": chat}),
            capture_output=True, text=True, timeout=button.timeout,
            cwd=ext.path, env=extensions.environment(ext), check=False,
        )
    except subprocess.TimeoutExpired:
        return {"say": f"{button.label} took longer than "
                       f"{button.timeout} seconds."}
    except OSError as exc:
        return {"say": f"{button.label} could not run: {exc}"}
    out = done.stdout.strip()
    if done.returncode != 0 and not out:
        out = done.stderr.strip()[-500:] or f"{button.label} failed."
    try:
        said = json.loads(out)
    except ValueError:
        said = None
    if isinstance(said, dict) and any(
            k in said for k in ("say", "insert", "send")):
        return {k: str(said[k]) for k in ("say", "insert", "send")
                if said.get(k)}
    return {"say": out[:8000]} if out else {}


def pressed(name: str, button_id: str, text: str = "",
            chat: str = "") -> dict:
    """What a button does, for whoever pressed it: {"send": message} to
    send, {"insert": text} for the box, {"say": text} to show."""

    import shlex

    from . import extensions

    found = find_button(name, button_id)
    if found is None:
        return {"say": "That button is gone."}
    ext, button = found
    if button.prompt:
        return {"send": expand(button, text)}
    if button.run:
        return press(ext, button, text, chat)
    command = next((c for c in ext.commands if c.name == button.command),
                   None)
    if command is None:
        return {"say": f"/{button.command} is gone."}
    if command.prompt is not None:
        return {"send": extensions.expand_prompt(command, text)}
    try:
        words = shlex.split(text) if text else []
    except ValueError:
        words = text.split()
    try:
        done = subprocess.run(  # nosec B603  (its own program, no shell)
            extensions.argv(ext, command.run or []) + words,
            capture_output=True, text=True, timeout=command.timeout,
            cwd=ext.path, env=extensions.environment(ext), check=False,
        )
    except subprocess.TimeoutExpired:
        return {"say": f"/{command.name} took longer than "
                       f"{command.timeout} seconds."}
    except OSError as exc:
        return {"say": f"/{command.name} could not run: {exc}"}
    out = (done.stdout.strip() or done.stderr.strip())[-8000:]
    return {"say": out or f"/{command.name} said nothing."}


# --- Status lines ---------------------------------------------------------

_status: dict[str, tuple[float, str]] = {}
_status_lock = threading.Lock()
_running: set[str] = set()


def _refresh(ext: Any) -> None:
    from . import extensions

    try:
        done = subprocess.run(  # nosec B603  (its own program, no shell)
            extensions.argv(ext, ext.ui.status.run), capture_output=True,
            text=True, timeout=ext.ui.status.timeout, cwd=ext.path,
            env=extensions.environment(ext), check=False,
        )
        line = next((ln.strip() for ln in done.stdout.splitlines()
                     if ln.strip()), "")
    except (OSError, subprocess.TimeoutExpired):
        line = ""
    with _status_lock:
        _status[ext.name] = (time.time(), line[:STATUS_CHARS])
        _running.discard(ext.name)


def status_lines(wait: bool = False) -> list[tuple[str, str]]:
    """Each extension's status line, as last read: one older than its
    "every" is read again on a thread of its own (or here, with WAIT),
    so nothing that asks waits on a slow program."""

    now = time.time()
    found = []
    for ext in with_ui():
        if ext.ui.status is None:
            continue
        with _status_lock:
            at, line = _status.get(ext.name, (0.0, ""))
            due = now - at >= ext.ui.status.every and \
                ext.name not in _running
            if due:
                _running.add(ext.name)
        if due:
            if wait:
                _refresh(ext)
                line = _status[ext.name][1]
            else:
                threading.Thread(target=_refresh, args=(ext,),
                                 daemon=True).start()
        if line:
            found.append((ext.name, line))
    return found


def forget_status() -> None:
    with _status_lock:
        _status.clear()
        _running.clear()
