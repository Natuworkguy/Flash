"""What the web UI keeps between runs: hosts, projects, and chats.

Hosts are the Ollama servers someone switches between, this computer
and whichever other machines have the GPUs. Projects are folders with
instructions: a chat in one runs its tools in that folder, and the
instructions ride along in its system prompt. Chats are saved as they
change, so a project still has its conversations after a restart.

Everything is JSON under ~/.flash/web, written whole and swapped into
place so a crash mid-write never leaves half a file.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .paths import FLASH_DIR

LOCAL_HOST = "http://localhost:11434"
LOCAL_NAME = "This computer"

HOST_CHECK_SECONDS = 1.5

MAX_NAME = 60
MAX_INSTRUCTIONS = 8000
MAX_DIR_SUGGESTIONS = 12

CHAT_ID_RE = re.compile(r"^[0-9a-f]{8}$")

_lock = threading.Lock()


class WorkspaceError(ValueError):
    """A host, project, or folder that cannot be used, and why."""


def store() -> Path:
    return FLASH_DIR / "web"


def _read(name: str, fallback: Any) -> Any:
    try:
        return json.loads((store() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write(name: str, value: Any) -> None:
    folder = store()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, indent=1), encoding="utf-8")
    os.replace(partial, path)


# --- Hosts ---------------------------------------------------------------


def normalize_host(url: str) -> str:
    """URL as Ollama's client takes it: a scheme, a host, a port."""

    url = (url or "").strip()
    if "://" not in url:
        url = "http://" + url

    # The trailing slash comes off after the scheme is split away, so a
    # bare "http://" is empty rather than a host called "http".
    scheme, _, rest = url.partition("://")
    rest = rest.rstrip("/")
    if not rest:
        raise WorkspaceError("a host needs an address")
    url = f"{scheme.lower()}://{rest}"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WorkspaceError(f"{url!r} is not an http address")
    if parsed.path not in ("", "/") or parsed.query:
        raise WorkspaceError("give just the address, like 10.0.0.5:11434")

    port = parsed.port or 11434
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{port}"


def hosts(current: str = "") -> list[dict]:
    """The saved hosts, this computer first, plus CURRENT if unsaved."""

    saved = [
        h for h in _read("hosts.json", [])
        if isinstance(h, dict) and h.get("url")
    ]
    listed = [{"name": LOCAL_NAME, "url": LOCAL_HOST}]
    seen = {LOCAL_HOST}

    for entry in saved:
        try:
            url = normalize_host(entry["url"])
        except WorkspaceError:
            continue
        if url in seen:
            continue
        seen.add(url)
        listed.append({"name": str(entry.get("name") or url), "url": url})

    if current:
        try:
            url = normalize_host(current)
        except WorkspaceError:
            url = ""
        if url and url not in seen:
            listed.append({"name": "Current", "url": url})

    return listed


def add_host(name: str, url: str) -> dict:
    url = normalize_host(url)
    name = " ".join((name or "").split())[:MAX_NAME] or urlparse(url).hostname

    with _lock:
        saved = [
            h for h in _read("hosts.json", [])
            if isinstance(h, dict) and h.get("url") != url
        ]
        if url != LOCAL_HOST:
            saved.append({"name": name, "url": url})
        _write("hosts.json", saved)

    return {"name": name, "url": url}


def remove_host(url: str) -> bool:
    try:
        url = normalize_host(url)
    except WorkspaceError:
        return False

    with _lock:
        saved = _read("hosts.json", [])
        kept = [
            h for h in saved
            if isinstance(h, dict) and h.get("url") != url
        ]
        if len(kept) == len(saved):
            return False
        _write("hosts.json", kept)
    return True


def host_up(url: str, timeout: float = HOST_CHECK_SECONDS) -> bool:
    """Whether an Ollama server answers at URL, quickly."""

    try:
        with urllib.request.urlopen(  # nosec B310 -- scheme checked above
            normalize_host(url) + "/api/version", timeout=timeout
        ) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def hosts_with_health(current: str = "") -> list[dict]:
    """The hosts, each checked at the same time so the slowest sets
    the wait rather than their sum."""

    listed = hosts(current)
    results: dict[str, bool] = {}

    def check(url: str) -> None:
        results[url] = host_up(url)

    threads = [
        threading.Thread(target=check, args=(h["url"],), daemon=True)
        for h in listed
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(HOST_CHECK_SECONDS + 0.5)

    return [{**h, "up": results.get(h["url"], False)} for h in listed]


# --- Projects ------------------------------------------------------------


@dataclass
class Project:
    id: str
    name: str
    path: str
    instructions: str = ""
    created: float = field(default_factory=time.time)


def _folder(path: str) -> str:
    folder = Path(os.path.expanduser((path or "").strip())).resolve()
    if not str(path or "").strip() or not folder.is_dir():
        raise WorkspaceError(f"{path!r} is not a folder on this computer")
    return str(folder)


def _name(name: str, folder: str) -> str:
    return " ".join((name or "").split())[:MAX_NAME] or Path(folder).name


def _instructions(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_INSTRUCTIONS:
        raise WorkspaceError(
            f"instructions are limited to {MAX_INSTRUCTIONS} characters, "
            "since they are sent on every turn"
        )
    return text


def projects() -> list[Project]:
    found = []
    for entry in _read("projects.json", []):
        try:
            found.append(Project(**entry))
        except TypeError:
            continue
    return sorted(found, key=lambda p: p.created)


def project(project_id: str) -> Optional[Project]:
    return next((p for p in projects() if p.id == project_id), None)


def _save_projects(listed: list[Project]) -> None:
    _write("projects.json", [asdict(p) for p in listed])


def create_project(name: str, path: str, instructions: str = "") -> Project:
    folder = _folder(path)
    made = Project(
        id=uuid.uuid4().hex[:8],
        name=_name(name, folder),
        path=folder,
        instructions=_instructions(instructions),
    )
    with _lock:
        _save_projects([*projects(), made])
    return made


def update_project(project_id: str, **changes: Any) -> Project:
    with _lock:
        listed = projects()
        found = next((p for p in listed if p.id == project_id), None)
        if found is None:
            raise WorkspaceError("no such project")

        if "path" in changes:
            found.path = _folder(changes["path"])
        if "name" in changes:
            found.name = _name(changes["name"], found.path)
        if "instructions" in changes:
            found.instructions = _instructions(changes["instructions"])

        _save_projects(listed)
    return found


def delete_project(project_id: str) -> bool:
    """Forget a project. Its folder, and its chats, are left alone."""

    with _lock:
        listed = projects()
        kept = [p for p in listed if p.id != project_id]
        if len(kept) == len(listed):
            return False
        _save_projects(kept)
    return True


def folder_suggestions(typed: str) -> list[str]:
    """Folders that complete TYPED, for the new project form.

    A browser cannot open a folder picker on the machine Flash runs on,
    so the form completes paths instead, the way a shell would.
    """

    typed = os.path.expanduser(typed or "~/")
    # "/" works as a separator everywhere, Windows included, and it is
    # what a person typing a path in a browser reaches for.
    cut = max(typed.rfind("/"), typed.rfind(os.sep))
    base, prefix = typed[:cut], typed[cut + 1:]
    parent = Path(base or os.sep)

    try:
        entries = sorted(parent.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    shown = []
    for entry in entries:
        if not entry.name.lower().startswith(prefix.lower()):
            continue
        if entry.name.startswith(".") and not prefix.startswith("."):
            continue
        try:
            if entry.is_dir():
                shown.append(str(entry))
        except OSError:
            continue
        if len(shown) >= MAX_DIR_SUGGESTIONS:
            break

    home = str(Path.home())
    return [
        "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p
        for p in shown
    ]


# --- Files the agent showed ---------------------------------------------

# What can be shown, and as what. Only types a browser displays without
# running anything: no SVG or HTML, which could carry a script onto the
# page's own origin.
SHOWN_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}
MAX_SHOWN_BYTES = 50 * 1024 * 1024
FILE_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def keep_file(source: str) -> dict:
    """Copy a file the agent showed into the store, and describe it.

    A copy, because what the agent shows often lives in Flash's scratch
    folder, which is gone once Flash exits, and a chat should still
    show its files after a restart.
    """

    path = Path(source)
    suffix = path.suffix.lower()
    if suffix not in SHOWN_TYPES:
        raise WorkspaceError(f"{path.name} is not an image or a PDF")

    size = path.stat().st_size
    if size > MAX_SHOWN_BYTES:
        raise WorkspaceError(f"{path.name} is too large to show")

    file_id = uuid.uuid4().hex[:16]
    folder = store() / "files"
    folder.mkdir(parents=True, exist_ok=True)
    kept = folder / f"{file_id}{suffix}"
    kept.write_bytes(path.read_bytes())

    return {
        "id": file_id,
        "name": path.name,
        "size": size,
        "mime": SHOWN_TYPES[suffix],
        "kind": "pdf" if suffix == ".pdf" else "image",
    }


def kept_file(file_id: str) -> Optional[tuple[Path, str]]:
    """The stored copy of a shown file, and its type, or None."""

    if not FILE_ID_RE.match(file_id or ""):
        return None
    for suffix, mime in SHOWN_TYPES.items():
        path = store() / "files" / f"{file_id}{suffix}"
        if path.is_file():
            return path, mime
    return None


# --- Chats ---------------------------------------------------------------


def _chat_file(chat_id: str) -> Optional[Path]:
    # The id becomes a file name, so it has to be one of ours.
    if not CHAT_ID_RE.match(chat_id or ""):
        return None
    return store() / "chats" / f"{chat_id}.json"


def save_chat(saved: dict) -> None:
    path = _chat_file(saved.get("id", ""))
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(saved), encoding="utf-8")
    os.replace(partial, path)


def load_chats() -> list[dict]:
    folder = store() / "chats"
    found = []
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and _chat_file(data.get("id", "")):
            found.append(data)
    return found


def delete_chat(chat_id: str) -> None:
    path = _chat_file(chat_id)
    if path is not None:
        path.unlink(missing_ok=True)
