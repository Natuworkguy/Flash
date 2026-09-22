"""Driving VS Code from Flash, when Flash runs in VS Code's terminal.

Everything goes through VS Code's own `code` command: open a file at a
line, or show an edit as a side-by-side diff. It only switches on when
Flash is running inside VS Code's integrated terminal, which sets
TERM_PROGRAM=vscode, so a Flash started in some other terminal never
throws VS Code windows at the user.
"""

import os
import shutil
import subprocess  # nosec B404 -- fixed argv to VS Code's CLI, no shell
from pathlib import Path


def cli() -> str | None:
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


def show_diff(old_text: str, new_text: str, name: str, folder: str) -> bool:
    """Open OLD_TEXT and NEW_TEXT side by side in VS Code.

    Both sides are written to FOLDER under NAME's extension, so VS Code
    highlights them as the right language.
    """

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
    return _launch("--diff", str(before), str(after))
