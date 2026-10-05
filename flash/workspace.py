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

# Names for this computer. An OLLAMA_HOST of 127.0.0.1 is the same
# server as localhost, not a second host to list, switch to, or remove.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

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


def host_key(url: str) -> str:
    """Which server URL means, for telling hosts apart: every name for
    this computer on a port counts as the same one. Only for comparing;
    the URL Flash connects to is never rewritten, since localhost and
    127.0.0.1 can reach different sockets."""

    try:
        url = normalize_host(url)
    except WorkspaceError:
        return url
    parsed = urlparse(url)
    if (parsed.hostname or "") in LOOPBACK:
        return f"{parsed.scheme}://localhost:{parsed.port}"
    return url


def hosts(current: str = "") -> list[dict]:
    """The saved hosts, this computer first, plus CURRENT if unsaved.

    `saved` marks the ones in hosts.json: only those can be removed.
    """

    saved = [
        h for h in _read("hosts.json", [])
        if isinstance(h, dict) and h.get("url")
    ]
    local_key = any(
        host_key(str(h.get("url") or "")) == host_key(LOCAL_HOST)
        and str(h.get("key") or "").strip()
        for h in saved
    )
    listed = [{
        "name": LOCAL_NAME, "url": LOCAL_HOST, "saved": False,
        "locked": local_key,
    }]
    seen = {host_key(LOCAL_HOST)}

    for entry in saved:
        try:
            url = normalize_host(entry["url"])
        except WorkspaceError:
            continue
        if host_key(url) in seen:
            continue
        seen.add(host_key(url))
        listed.append({
            "name": str(entry.get("name") or url), "url": url, "saved": True,
            # Whether it has a key, never the key: this goes to the page.
            "locked": bool(str(entry.get("key") or "").strip()),
        })

    if current:
        try:
            url = normalize_host(current)
        except WorkspaceError:
            url = ""
        if url and host_key(url) not in seen:
            listed.append({"name": "Current", "url": url, "saved": False})

    return listed


def listed_url(current: str) -> str:
    """The URL CURRENT goes by in hosts(), so the page can tell which
    entry is in use: 127.0.0.1 shows up as This computer's."""

    key = host_key(current)
    return next(
        (h["url"] for h in hosts(current) if host_key(h["url"]) == key),
        current,
    )


def add_host(name: str, url: str, key: str = "") -> dict:
    """Save a host, with the API key its server asks for if it asks.

    A key goes in hosts.json beside the host, readable only by its
    owner, and never back out to the page.
    """

    url = normalize_host(url)
    name = " ".join((name or "").split())[:MAX_NAME] or urlparse(url).hostname
    key = (key or "").strip()
    if any(c.isspace() for c in key):
        raise WorkspaceError("an API key has no spaces in it")

    with _lock:
        saved = [
            h for h in _read("hosts.json", [])
            if isinstance(h, dict)
            and host_key(str(h.get("url") or "")) != host_key(url)
        ]
        # This computer is always listed, so a key for it is saved as a
        # host of its own: the key is the point of adding it.
        if host_key(url) != host_key(LOCAL_HOST) or key:
            entry = {"name": name, "url": url}
            if key:
                entry["key"] = key
            saved.append(entry)
        _write("hosts.json", saved)
        if any(h.get("key") for h in saved if isinstance(h, dict)):
            _private(store() / "hosts.json")

    return {"name": name, "url": url, "locked": bool(key)}


def _private(path: Path) -> None:
    """Make PATH readable by its owner only, where the system has that."""

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def api_key(url: str) -> str:
    """The API key Flash sends to the Ollama at URL: the one saved with
    the host, else OLLAMA_API_KEY from the environment, else none."""

    wanted = host_key(url)
    for entry in _read("hosts.json", []):
        if not isinstance(entry, dict):
            continue
        if host_key(str(entry.get("url") or "")) == wanted:
            key = str(entry.get("key") or "").strip()
            if key:
                return key
    return (os.environ.get("OLLAMA_API_KEY") or "").strip()


def auth_headers(url: str) -> dict:
    """The header that carries URL's API key, or none without one.

    Lower case, as Ollama's client looks for it: given its own, the
    client leaves it be rather than adding the environment's.
    """

    key = api_key(url)
    return {"authorization": f"Bearer {key}"} if key else {}


def client_options(host: str) -> dict:
    """What an ollama.Client for HOST is made with besides the host: the
    header with HOST's key, when it has one. Nothing otherwise, so a
    client made without a key is made exactly as before."""

    headers = auth_headers(host)
    return {"headers": headers} if headers else {}


def remove_host(url: str) -> bool:
    try:
        url = normalize_host(url)
    except WorkspaceError:
        return False

    with _lock:
        saved = _read("hosts.json", [])
        kept = [
            h for h in saved
            if isinstance(h, dict)
            and host_key(str(h.get("url") or "")) != host_key(url)
        ]
        if len(kept) == len(saved):
            return False
        _write("hosts.json", kept)
    return True


def host_state(url: str, timeout: float = HOST_CHECK_SECONDS) -> str:
    """How the Ollama at URL answers, quickly: "up", "down", or
    "refused" when something in front of it wants a key it was not
    given, or turned down the one it was."""

    try:
        request = urllib.request.Request(
            normalize_host(url) + "/api/version", headers=auth_headers(url),
        )
        with urllib.request.urlopen(  # nosec B310 -- scheme checked above
            request, timeout=timeout
        ) as response:
            return "up" if response.status == 200 else "down"
    except urllib.error.HTTPError as exc:
        return "refused" if exc.code in (401, 403) else "down"
    except (OSError, ValueError, urllib.error.URLError):
        return "down"


def host_up(url: str, timeout: float = HOST_CHECK_SECONDS) -> bool:
    """Whether an Ollama server answers at URL, quickly."""

    return host_state(url, timeout) == "up"


def hosts_with_health(current: str = "") -> list[dict]:
    """The hosts, each checked at the same time so the slowest sets
    the wait rather than their sum."""

    listed = hosts(current)
    results: dict[str, str] = {}

    def check(url: str) -> None:
        results[url] = host_state(url)

    threads = [
        threading.Thread(target=check, args=(h["url"],), daemon=True)
        for h in listed
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(HOST_CHECK_SECONDS + 0.5)

    return [
        {**h, "up": results.get(h["url"]) == "up",
         "refused": results.get(h["url"]) == "refused"}
        for h in listed
    ]


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

# What can be shown, and as what. A page is the one type that can run a
# script, so it is only ever served sandboxed, on an origin of its own
# (see web.Handler._file). SVG stays out: it has no sandboxed way in.
SHOWN_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    # Documents: read, edited, and commented on beside the chat.
    # Markdown goes out as plain text: a browser shows that in a tab,
    # where text/markdown would only download.
    ".md": "text/plain; charset=utf-8",
    ".markdown": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    # 3D models: turned and zoomed in the page's own viewer, which reads
    # them as data. Nothing in one runs.
    ".glb": "model/gltf-binary",
    ".stl": "model/stl",
    ".obj": "model/obj",
    # Music as notes, played by the page's own synth and drawn as a
    # piano roll. Nothing in one runs.
    ".mid": "audio/midi",
    ".midi": "audio/midi",
    # Screen recordings the user made in the page, played back there.
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}
DOCUMENT_TYPES = (".md", ".markdown", ".txt")
MODEL_TYPES = (".glb", ".stl", ".obj")
MIDI_TYPES = (".mid", ".midi")
MAX_SHOWN_BYTES = 50 * 1024 * 1024
# A document is edited in the page as text, so it stays a size a browser
# edits comfortably.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
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
        raise WorkspaceError(
            f"{path.name} is not an image, a PDF, a web page, a document, "
            "a 3D model, or a MIDI file"
        )

    size = path.stat().st_size
    document = suffix in DOCUMENT_TYPES
    if size > (MAX_DOCUMENT_BYTES if document else MAX_SHOWN_BYTES):
        raise WorkspaceError(f"{path.name} is too large to show")

    file_id = uuid.uuid4().hex[:16]
    folder = store() / "files"
    folder.mkdir(parents=True, exist_ok=True)
    kept = folder / f"{file_id}{suffix}"
    kept.write_bytes(path.read_bytes())
    if document:
        # Where it came from, so an edit made in the page lands in the
        # real file too, not only in this copy.
        _write(f"files/{file_id}.source.json", {"source": str(path.resolve())})

    info = {
        "id": file_id,
        "name": path.name,
        "size": size,
        "mime": SHOWN_TYPES[suffix],
        "kind": (
            "pdf" if suffix == ".pdf"
            else "html" if SHOWN_TYPES[suffix] == "text/html"
            else "doc" if document
            else "model" if suffix in MODEL_TYPES
            else "midi" if suffix in MIDI_TYPES
            else "image"
        ),
    }
    if document:
        info["path"] = str(path.resolve())
    return info


def save_document(file_id: str, text: str) -> dict:
    """Save a document the user edited in the page: into the kept copy,
    and into the file it came from, where that still exists.

    Returns where it went: {"size", "path"}, the path "" when only the
    copy could be written.
    """

    kept = kept_file(file_id)
    if kept is None or kept[0].suffix.lower() not in DOCUMENT_TYPES:
        raise WorkspaceError("no such document")
    data = (text or "").encode("utf-8")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkspaceError("that document is too large to save")

    path = kept[0]
    path.write_bytes(data)
    record = _read(f"files/{file_id}.source.json", {})
    if not isinstance(record, dict):
        record = {}
    source = str(record.get("source") or "")
    written = ""
    if source and Path(source).is_file():
        try:
            Path(source).write_bytes(data)
            written = source
        except OSError as exc:
            raise WorkspaceError(f"could not save {source}: {exc}") from exc
    return {"size": len(data), "path": written}


def kept_file(file_id: str) -> Optional[tuple[Path, str]]:
    """The stored copy of a shown file, and its type, or None. That is
    a file the agent showed, or one the user attached that a browser
    can show, like a photo."""

    if not FILE_ID_RE.match(file_id or ""):
        return None
    for suffix, mime in SHOWN_TYPES.items():
        path = store() / "files" / f"{file_id}{suffix}"
        if path.is_file():
            return path, mime
    uploaded = upload_path(file_id)
    # An upload is served back only as a picture, a PDF, a page, or a
    # 3D model: a text file the user attached is the model's to read,
    # not the page's.
    suffix = uploaded.suffix.lower() if uploaded is not None else ""
    if suffix in SHOWN_TYPES and suffix not in DOCUMENT_TYPES:
        return uploaded, SHOWN_TYPES[suffix]
    return None


# --- Files the user attached --------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_UNSAFE_NAME = re.compile(r"[^\w.\- ]+")
# A screen recording's stills: how many, and how big each.
MAX_STILLS = 16
MAX_STILL_BYTES = 1024 * 1024
STILLS = "stills"


def upload_path(file_id: str) -> Optional[Path]:
    """Where a file the user attached is kept, or None."""

    if not FILE_ID_RE.match(file_id or ""):
        return None
    try:
        kept = [p for p in (store() / "uploads" / file_id).iterdir()
                if p.is_file()]
    except OSError:
        return None
    return kept[0] if len(kept) == 1 else None


def upload_info(file_id: str) -> Optional[dict]:
    """What the page shows for an attached file, and its path."""

    path = upload_path(file_id)
    if path is None:
        return None
    suffix = path.suffix.lower()
    mime = SHOWN_TYPES.get(suffix, "application/octet-stream")
    return {
        "id": file_id,
        "name": path.name,
        "size": path.stat().st_size,
        "mime": mime,
        "kind": (
            "image" if mime.startswith("image/")
            else "video" if mime.startswith("video/")
            else "pdf" if suffix == ".pdf"
            else "model" if suffix in MODEL_TYPES
            else "midi" if suffix in MIDI_TYPES
            else "file"
        ),
        "path": str(path),
        **({"stills": len(recording_stills(file_id))}
           if mime.startswith("video/") else {}),
    }


def recording_stills(file_id: str) -> list[dict]:
    """A screen recording's stills, in order: each {path, at, said}, AT
    its time in seconds and SAID what the user said from there to the
    next one, if they narrated."""

    path = upload_path(file_id)
    if path is None:
        return []
    folder = path.parent / STILLS
    try:
        index = json.loads((folder / "index.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    stills = []
    for item in index if isinstance(index, list) else []:
        still = folder / str(item.get("file", ""))
        if still.parent == folder and still.is_file():
            stills.append({
                "path": str(still), "at": float(item.get("at") or 0),
                "said": str(item.get("said") or ""),
            })
    return stills


def keep_upload(
    name: str, data: bytes, stills: Optional[list] = None,
) -> dict:
    """Keep a file the user attached in the page, and describe it.

    Under its own name, in a folder of its own, so the model's tools
    can open it by a real path and the name still says what it is.
    Nothing is run or unpacked: it is only written down. A screen
    recording comes with STILLS, the moments the page caught as the
    screen changed: each {"jpeg": bytes, "at": seconds, "said": text},
    kept beside it for the model to see.
    """

    if not data:
        raise WorkspaceError("that file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise WorkspaceError(
            f"that file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )
    clean = _UNSAFE_NAME.sub("_", Path(name or "").name).strip(" .")[:100]
    clean = clean or "upload"
    file_id = uuid.uuid4().hex[:16]
    folder = store() / "uploads" / file_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / clean).write_bytes(data)
    if stills:
        kept = folder / STILLS
        kept.mkdir()
        index = []
        for number, still in enumerate(stills[:MAX_STILLS], start=1):
            jpeg = still.get("jpeg") or b""
            if not jpeg or len(jpeg) > MAX_STILL_BYTES:
                continue
            file = f"{number:02d}.jpg"
            (kept / file).write_bytes(jpeg)
            index.append({
                "file": file, "at": round(float(still.get("at") or 0), 1),
                "said": " ".join(str(still.get("said") or "").split()),
            })
        (kept / "index.json").write_text(json.dumps(index), "utf-8")
    info = upload_info(file_id) or {}
    info.pop("path", None)
    return info


# --- Chats ---------------------------------------------------------------


def _chat_file(chat_id: str) -> Optional[Path]:
    # The id becomes a file name, so it has to be one of ours.
    if not CHAT_ID_RE.match(chat_id or ""):
        return None
    return store() / "chats" / f"{chat_id}.json"


def chat_saved(chat_id: str) -> bool:
    """Whether a chat has a file, open in this process or not."""

    path = _chat_file(chat_id)
    return path is not None and path.exists()


def save_chat(saved: dict) -> None:
    path = _chat_file(saved.get("id", ""))
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # A name of its own: two threads saving one chat at once must not
    # move each other's half-written file.
    partial = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:8]}.partial")
    try:
        partial.write_text(json.dumps(saved), encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


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
