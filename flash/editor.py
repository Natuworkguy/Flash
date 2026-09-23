"""Driving VS Code from Flash, when Flash runs in VS Code's terminal.

Everything goes through VS Code's own `code` command: open a file at a
line, or show an edit as a side-by-side diff. It only switches on when
Flash is running inside VS Code's integrated terminal, which sets
TERM_PROGRAM=vscode, so a Flash started in some other terminal never
throws VS Code windows at the user.
"""

import os
import re
import shutil
import subprocess  # nosec B404 -- fixed argv to VS Code's CLI, no shell
import sys
from pathlib import Path
from typing import Optional


def cli() -> Optional[str]:
    """Path to the `code` command, if Flash runs inside VS Code."""

    if os.environ.get("TERM_PROGRAM") != "vscode":
        return None
    return shutil.which("code")


def available() -> bool:
    return cli() is not None


def _launch(*args: str) -> bool:
    command = cli()
    if command is None:
        return False
    try:
        subprocess.Popen(  # nosec B603 -- fixed argv, no shell
            [command, "--reuse-window", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


def open_at(path: str, line: int = 0) -> bool:
    """Open PATH in VS Code, at LINE (1-based) when one is given."""

    target = str(Path(path).expanduser().resolve())
    if line > 0:
        return _launch("--goto", f"{target}:{line}")
    return _launch(target)


# The two files the diff Flash last opened is reading. VS Code's CLI
# has no command that closes a tab, so these are the only handle on it
# from out here.
_showing: Optional[tuple[Path, Path]] = None

# The VS Code setting that decides whether an editor closes when the
# file under it disappears. Off by default, in which case the tab stays
# and marks itself deleted.
CLOSE_SETTING = "workbench.editor.closeOnFileDelete"

_SETTING_RE = re.compile(
    r'"' + re.escape(CLOSE_SETTING) + r'"\s*:\s*(true|false)'
)


def show_diff(old_text: str, new_text: str, name: str, folder: str) -> bool:
    """Open OLD_TEXT and NEW_TEXT side by side in VS Code.

    Both sides are written to FOLDER under NAME's extension, so VS Code
    highlights them as the right language.
    """

    global _showing

    if not available():
        return False

    stem, suffix = os.path.splitext(name)
    before = Path(folder) / f"{stem} (before){suffix}"
    after = Path(folder) / f"{stem} (Flash's edit){suffix}"
    try:
        before.write_text(old_text, encoding="utf-8")
        after.write_text(new_text, encoding="utf-8")
    except OSError:
        return False

    if not _launch("--diff", str(before), str(after)):
        return False

    _showing = (before, after)
    return True


def close_diff() -> bool:
    """Drop the files behind the diff Flash last opened.

    Called once the user has said yes or no, so a decided diff does not
    sit in the editor looking like it is still waiting for one.

    There is no clean way to do this. `code --diff` opens a tab and the
    CLI offers nothing that closes one; the editor's own API can, which
    is why other agents ship a companion extension for it. What is left
    from outside is the files the diff is reading, so they go as soon
    as the answer is in. Whether the tab goes with them is the user's
    `workbench.editor.closeOnFileDelete`. Either way the scratch files
    stop accumulating and the diff stops showing a stale edit.

    Returns whether there was a diff to close.
    """

    global _showing

    if _showing is None:
        return False

    for path in _showing:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    _showing = None
    return True


def _settings_path() -> Optional[Path]:
    """Where VS Code keeps settings.json on this platform."""

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base) / "Code" / "User" / "settings.json" if base else None

    home = Path.home()

    if sys.platform == "darwin":
        return (
            home / "Library" / "Application Support"
            / "Code" / "User" / "settings.json"
        )

    return home / ".config" / "Code" / "User" / "settings.json"


def closes_deleted_editors() -> Optional[bool]:
    """Whether VS Code closes an editor whose file was deleted.

    None when the answer cannot be read at all, which is not the same
    as False and is not worth telling the user about. Read with a
    regular expression rather than a JSON parser because VS Code's
    settings file allows comments and trailing commas, and `json` does
    not.
    """

    path = _settings_path()

    if path is None:
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    found = _SETTING_RE.search(text)

    if found is None:
        # Absent means the default, which VS Code documents as off.
        return False

    return found.group(1) == "true"
