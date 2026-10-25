"""Secrets: keys, tokens and passwords Flash can use but never sees.

Each is kept by a name, encrypted on disk (vault.json), with the key to
it in the system's own keychain: the macOS Keychain, Windows's DPAPI,
or the Secret Service on Linux (a file only the user can read where
there is none). The model knows the names and writes {{secret:NAME}}
where one goes:

- in a shell command it becomes an environment variable the command
  reads, so the command shown, asked about and kept has only its name;
- in a connector's or extension's arguments it is put in as the call is
  made.

Whatever a tool says back has every secret's value swapped for its
placeholder before the model, the screen or a saved chat sees it. And a
key the user pastes into a message is taken out, kept here under a name,
and the message goes on with its placeholder (capture).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

PLACEHOLDER = re.compile(r"\{\{secret:([A-Za-z0-9_.-]{1,40})\}\}")
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
MAX_VALUE = 8000
# Shorter than this, a value is too likely to be in ordinary text to be
# swapped out of it.
SCRUB_MIN = 6
SERVICE = "Flash vault"

_lock = threading.RLock()
_cache: dict = {}


class VaultError(Exception):
    """Why a secret could not be kept or found."""


def _folder() -> Path:
    # Read when wanted, so a test that moves FLASH_DIR moves it.
    return FLASH_DIR


def _file() -> Path:
    return _folder() / "vault.json"


# --- The key, in the system's keychain ----------------------------------


def _run(argv: list[str], given: str = "") -> Optional[str]:
    try:
        done = subprocess.run(  # nosec B603 -- fixed programs
            argv, input=given, capture_output=True, text=True, timeout=10,
            check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _dpapi(data: bytes, protect: bool) -> Optional[bytes]:
    """Windows: DATA sealed (or opened) for this user only."""

    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.windll.crypt32  # type: ignore[attr-defined]
    given = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                        ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    call = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if not call(ctypes.byref(given), None, None, None, None, 0,
                ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore


def _key_file() -> Path:
    return _folder() / "vault.key"


def _load_key() -> Optional[bytes]:
    if sys.platform == "darwin":
        said = _run(["security", "find-generic-password", "-s", SERVICE,
                     "-a", "key", "-w"])
        if said:
            return said.encode()
    elif os.name == "nt":
        with contextlib.suppress(OSError):
            opened = _dpapi(_key_file().read_bytes(), protect=False)
            if opened:
                return opened
    elif shutil.which("secret-tool"):
        said = _run(["secret-tool", "lookup", "service", "flash-vault",
                     "account", "key"])
        if said:
            return said.encode()
    with contextlib.suppress(OSError):
        return _key_file().read_bytes().strip() or None
    return None


def _store_key(key: bytes) -> str:
    """Keep KEY where the system keeps secrets; where it went."""

    if sys.platform == "darwin" and _run([
            "security", "add-generic-password", "-U", "-s", SERVICE,
            "-a", "key", "-w", key.decode()]) is not None:
        return "the macOS Keychain"
    if os.name == "nt":
        sealed = _dpapi(key, protect=True)
        if sealed:
            _write_private(_key_file(), sealed)
            return "Windows's data protection"
    if sys.platform.startswith("linux") and shutil.which("secret-tool") \
            and _run(["secret-tool", "store", "--label", SERVICE, "service",
                      "flash-vault", "account", "key"],
                     key.decode()) is not None:
        return "the Secret Service"
    _write_private(_key_file(), key)
    return "a file only you can read"


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_bytes(data)
    with contextlib.suppress(OSError):
        os.chmod(partial, 0o600)
    os.replace(partial, path)


def _cipher():
    from cryptography.fernet import Fernet

    key = _load_key()
    if key is None:
        key = Fernet.generate_key()
        _store_key(key)
    return Fernet(key)


def where_kept() -> str:
    """Where the vault's key is, in words, for the doctor and Settings."""

    if sys.platform == "darwin":
        return "the macOS Keychain"
    if os.name == "nt":
        return "Windows's data protection"
    if shutil.which("secret-tool"):
        return "the Secret Service"
    return "a file only you can read (install secret-tool to keep it in " \
        "the Secret Service)"


# --- The secrets ----------------------------------------------------------


def _read() -> dict:
    try:
        found = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write(data: dict) -> None:
    _write_private(_file(), json.dumps(data, indent=1).encode())
    _cache.clear()


def names() -> list[str]:
    return sorted(_read())


def set_secret(name: str, value: str) -> str:
    """Keep VALUE as NAME, replacing one by that name."""

    name = str(name or "").strip()
    value = str(value or "").strip()
    if not NAME.match(name):
        raise VaultError("A secret's name is letters, digits, dots, dashes "
                         "and underscores, up to 40.")
    if not value or len(value) > MAX_VALUE:
        raise VaultError("A secret needs a value, up to 8000 characters.")
    with _lock:
        data = _read()
        data[name] = {"added": time.time(),
                      "value": _cipher().encrypt(value.encode()).decode()}
        _write(data)
    return f"Kept {name}. Flash uses it as {{{{secret:{name}}}}}."


def delete(name: str) -> bool:
    with _lock:
        data = _read()
        if data.pop(name, None) is None:
            return False
        _write(data)
    return True


def get(name: str) -> Optional[str]:
    with _lock:
        if name in _cache:
            return _cache[name]
        entry = _read().get(name)
        if not isinstance(entry, dict):
            return None
        try:
            value = _cipher().decrypt(
                str(entry.get("value") or "").encode()).decode()
        except Exception:  # noqa: BLE001  (a key that no longer opens it)
            return None
        _cache[name] = value
        return value


def _values() -> list[tuple[str, str]]:
    found = [(n, get(n)) for n in names()]
    return sorted(((n, v) for n, v in found if v and len(v) >= SCRUB_MIN),
                  key=lambda item: -len(item[1]))


def scrub(text: str) -> str:
    """TEXT with every secret's value swapped for its placeholder."""

    if not text or not _file().exists():
        return text
    for name, value in _values():
        if value in text:
            text = text.replace(value, f"{{{{secret:{name}}}}}")
    return text


def expand(text: str) -> str:
    """TEXT with each placeholder swapped for its secret's value. Raises
    VaultError naming one there is no secret for."""

    def put(found: re.Match) -> str:
        value = get(found.group(1))
        if value is None:
            raise VaultError(_unknown(found.group(1)))
        return value

    return PLACEHOLDER.sub(put, text)


def expand_args(value):
    """VALUE, a tool's arguments, with every placeholder in its strings
    expanded."""

    if isinstance(value, str):
        return expand(value)
    if isinstance(value, dict):
        return {k: expand_args(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_args(v) for v in value]
    return value


def _unknown(name: str) -> str:
    return (f"There is no secret called {name}. The user can add it with "
            f"/secret set {name}, or in Settings > Secrets.")


def for_shell(command: str, windows: bool = False) -> tuple[str, dict]:
    """COMMAND with each placeholder made an environment variable it
    reads, and those variables, to run it with. Raises VaultError for a
    name there is no secret for."""

    env: dict[str, str] = {}

    def put(found: re.Match) -> str:
        name = found.group(1)
        value = get(name)
        if value is None:
            raise VaultError(_unknown(name))
        var = "FLASH_SECRET_" + re.sub(r"\W", "_", name).upper()
        env[var] = value
        return f"$env:{var}" if windows else f"${{{var}}}"

    return PLACEHOLDER.sub(put, command), env


# --- Keys pasted into a message ------------------------------------------

# Keys whose start says what they are, and what to call each.
_KNOWN = [
    (r"github_pat_[A-Za-z0-9_]{40,}", "github-token"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "github-token"),
    (r"glpat-[A-Za-z0-9_-]{20,}", "gitlab-token"),
    (r"xox[abposr]-[A-Za-z0-9-]{10,}", "slack-token"),
    (r"AKIA[0-9A-Z]{16}", "aws-access-key"),
    (r"AIza[0-9A-Za-z_-]{35}", "google-api-key"),
    (r"sk-[A-Za-z0-9_-]{20,}", "api-key"),
    (r"(?:sk|rk|pk)_live_[A-Za-z0-9]{20,}", "payments-key"),
    (r"hf_[A-Za-z0-9]{30,}", "hub-token"),
    (r"npm_[A-Za-z0-9]{30,}", "npm-token"),
]
# "password: hunter22", "api_key=...", "token ...": what follows.
_SAID = re.compile(
    r"(?i)\b(password|passwd|api[ _-]?key|secret|token|access[ _-]?key)"
    r"\s*(?:is|=|:)\s*[\"']?([^\s\"']{8,})")


def _looks_secret(value: str) -> bool:
    """Whether VALUE, said after "password" or "token", is one: letters
    with digits or marks in them, not a word ("the secret is patience")."""

    value = value.rstrip(".,;)")
    return len(value) >= 8 and bool(re.search(r"[A-Za-z]", value)) and \
        bool(re.search(r"[\d\W_]", value))


def _name_for(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def capture(text: str) -> tuple[str, list[str]]:
    """TEXT with each key or password in it kept in the vault and swapped
    for its placeholder, and the names it was kept under. A value already
    kept is swapped for the name it has."""

    if not text:
        return text, []
    kept: list[str] = []
    taken = set(names())
    known = {v: n for n, v in _values()}
    found: list[tuple[str, str]] = []
    for pattern, base in _KNOWN:
        for match in re.finditer(pattern, text):
            found.append((match.group(0), base))
    for match in _SAID.finditer(text):
        if PLACEHOLDER.search(match.group(2)) or \
                not _looks_secret(match.group(2)):
            continue
        word = re.sub(r"[ _]", "-", match.group(1).lower())
        found.append((match.group(2), word))
    for value, base in found:
        if value not in text:
            continue
        name = known.get(value)
        if name is None:
            try:
                name = _name_for(base, taken)
                set_secret(name, value)
            except (VaultError, ImportError, OSError):
                continue
            known[value] = name
            kept.append(name)
        text = text.replace(value, f"{{{{secret:{name}}}}}")
    return text, kept


def note(kept: list[str]) -> str:
    """What to tell the user about keys taken out of their message."""

    shown = ", ".join(f"{{{{secret:{n}}}}}" for n in kept)
    return (f"Kept what you pasted in your vault as {shown}: Flash uses it "
            "by that name and never sees it. /secret list shows them.")


def prompt_block() -> str:
    """For the system prompt: the names there are, and how to use them."""

    have = names()
    if not have:
        return ""
    return (
        "=== Secrets ===\n"
        "The user keeps these secrets for you to use without seeing them: "
        + ", ".join(have) + ". Write {{secret:NAME}} where one goes, in a "
        "shell command (it is read from the environment) or a connector "
        "tool's arguments; tool output shows them the same way. Never ask "
        "the user for a secret's value, and never try to print or send "
        "one: you cannot see them, and should not."
    )
