"""The ``flash://`` URL scheme: parsing and OS handler registration.

A ``flash://?prompt=What+is+Python`` link opens Flash with that prompt
queued for the first turn. Because any web page can open such a link, the
prompt is always shown and confirmed before it is sent (see ai.py), and
slash commands and ``!`` shell escapes are rejected outright here.
"""

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCHEME = "flash"
DESKTOP_FILE = "flash-url.desktop"


class SchemeError(Exception):
    """Raised when a flash: URL is malformed or cannot be registered."""


def looks_like_flash_url(text: str) -> bool:
    """True if TEXT is meant to be a flash: URL rather than a prompt."""

    return text.strip().lower().startswith(f"{SCHEME}:")


def _clean(prompt: str) -> str:
    # Control characters (ANSI escapes in particular) could dress the
    # confirmation prompt up as something it is not, so drop them.
    return "".join(" " if ch < " " or ch == "\x7f" else ch
                   for ch in prompt).strip()


def parse_flash_url(url: str) -> str:
    """Return the prompt carried by a ``flash://?prompt=...`` URL."""

    parts = urlsplit(url.strip())

    if parts.scheme.lower() != SCHEME:
        raise SchemeError(f"Not a {SCHEME}: URL: {url}")

    values = parse_qs(parts.query).get("prompt") or []
    prompt = _clean(values[0] if values else "")

    if not prompt:
        raise SchemeError(
            f"{SCHEME}: URL carries no ?prompt= value: {url}"
        )

    if prompt[0] in "/!":
        raise SchemeError(
            f"{SCHEME}: URLs may not start with '/' or '!'; they carry "
            "prompts for the model, not Flash commands."
        )

    return prompt


def launch_command() -> list[str]:
    """The command an OS handler should run to open a flash: URL."""

    # Prefer the binary this process was started from: during an install it
    # is the one that was just put in place, which may not be on PATH yet.
    argv0 = Path(sys.argv[0] or "")
    if argv0.stem.lower() == SCHEME and argv0.is_file():
        return [str(argv0.resolve())]

    exe = shutil.which(SCHEME)
    if exe:
        return [exe]

    return [sys.executable, "-m", SCHEME]


def _register_windows(command: list[str]) -> str:
    import winreg

    line = " ".join(f'"{part}"' for part in command) + ' "%1"'
    root = winreg.HKEY_CURRENT_USER

    with winreg.CreateKey(root, rf"Software\Classes\{SCHEME}") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:Flash Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

    path = rf"Software\Classes\{SCHEME}\shell\open\command"
    with winreg.CreateKey(root, path) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, line)

    return rf"HKEY_CURRENT_USER\Software\Classes\{SCHEME} -> {line}"


def _unregister_windows() -> bool:
    import winreg

    root = winreg.HKEY_CURRENT_USER
    subkeys = [
        rf"Software\Classes\{SCHEME}\shell\open\command",
        rf"Software\Classes\{SCHEME}\shell\open",
        rf"Software\Classes\{SCHEME}\shell",
        rf"Software\Classes\{SCHEME}",
    ]

    removed = False
    for subkey in subkeys:
        try:
            winreg.DeleteKey(root, subkey)
            removed = True
        except FileNotFoundError:
            continue

    return removed


def _desktop_path() -> Path:
    return (
        Path.home() / ".local" / "share" / "applications" / DESKTOP_FILE
    )


def _register_linux(command: list[str]) -> str:
    exec_line = " ".join(command) + " %u"
    path = _desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Flash CLI\n"
        f"Exec={exec_line}\n"
        "Terminal=true\n"
        "NoDisplay=true\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n",
        encoding="utf-8",
    )

    _run_quiet(
        ["xdg-mime", "default", DESKTOP_FILE, f"x-scheme-handler/{SCHEME}"]
    )
    _run_quiet(["update-desktop-database", str(path.parent)])

    return f"{path} -> {exec_line}"


def _unregister_linux() -> bool:
    path = _desktop_path()
    if not path.exists():
        return False

    path.unlink()
    _run_quiet(["update-desktop-database", str(path.parent)])
    return True


def _run_quiet(command: list[str]) -> None:
    if not shutil.which(command[0]):
        return

    try:
        subprocess.run(  # nosec B603
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


_MACOS_HELP = (
    "The flash:// handler cannot be installed or uninstalled on macOS."
)


def _host_os() -> str:
    if os.name == "nt":
        return "windows"

    return "macos" if sys.platform == "darwin" else "freedesktop"


def register() -> str:
    """Register this machine's handler for flash: URLs."""

    host = _host_os()

    if host == "macos":
        raise SchemeError(_MACOS_HELP)

    command = launch_command()

    if host == "windows":
        return _register_windows(command)

    return _register_linux(command)


def unregister() -> bool:
    """Remove this machine's handler for flash: URLs."""

    host = _host_os()

    if host == "macos":
        raise SchemeError(_MACOS_HELP)

    if host == "windows":
        return _unregister_windows()

    return _unregister_linux()
