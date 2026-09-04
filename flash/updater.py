"""Version checking and self-update for Flash CLI.

Flash has no package registry or release tags, so `main`'s version.py is
the single source of truth for "latest" -- this mirrors how the local
version is derived (see pyproject.toml). Update checks must never break or
noticeably delay the CLI, so every network failure here is swallowed and
reported as "unknown" rather than raised.
"""

import os
import re
import shutil
import subprocess  # nosec B404
import tempfile
import urllib.request
from collections.abc import Callable
from typing import Union

from .version import (
    INSTALL_SCRIPT_PS1_URL,
    INSTALL_SCRIPT_URL,
    REPO_URL,
    VERSION_URL,
    __version__,
)

_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
_TIMEOUT_SECONDS = 3

# A failed update still has to be diagnosable, but pipx and git both
# print pages of progress, and only the end of it says what went wrong.
_MAX_ERROR_LINES = 12


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    """True if LATEST is a newer version than CURRENT."""

    try:
        return _parse_version(latest) > _parse_version(current)
    except ValueError:
        return latest != current


def fetch_latest_version() -> Union[str, None]:  # noqa: UP007, RUF100
    """Return the version on the repo's main branch, or None on failure."""

    try:
        with urllib.request.urlopen(  # nosec B310
            VERSION_URL, timeout=_TIMEOUT_SECONDS
        ) as response:
            body = response.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None

    match = _VERSION_RE.search(body)
    return match.group(1) if match else None


def check_for_update() -> Union[str, None]:  # noqa: UP007, RUF100
    """Return the latest version string if newer than the running one."""

    latest = fetch_latest_version()
    return latest if latest and is_newer(latest) else None


def _tail(*outputs: str) -> str:
    """The last few meaningful lines of a command that failed."""

    lines = [
        line.rstrip()
        for output in outputs
        for line in (output or "").splitlines()
        if line.strip()
    ]

    return "\n".join(lines[-_MAX_ERROR_LINES:])


def _failed(what: str, code: int, output: str) -> str:
    """Explain a failed step, ending with what the command itself said.

    The message is rendered as Markdown, so the command's own output goes
    in a fenced block; left bare, it would be reflowed into a paragraph.
    """

    detail = _tail(output)

    if not detail:
        return f"{what} (exit code {code})."

    return f"{what} (exit code {code}):\n\n```\n{detail}\n```"


def _stream(
    command: list[str],
    on_output: Union[Callable[[str], None], None] = None,  # noqa: UP007
) -> tuple[int, str]:
    """Run `command`, handing each line it prints to `on_output`.

    git and pipx are chatty, and the caller draws a spinner while they
    work. Letting them write straight to the terminal shreds both, so
    their output is read here a line at a time and passed back out to be
    printed through the same console, in order. Returns `(exit code,
    everything it printed)`.
    """

    lines: list[str] = []

    with subprocess.Popen(  # nosec B603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # pipx decorates its output with emoji, which a Windows console's
        # default encoding will not decode.
        encoding="utf-8",
        errors="replace",
    ) as running:
        for raw in running.stdout or ():
            line = raw.rstrip()
            lines.append(line)

            if on_output is not None and line.strip():
                on_output(line)

        code = running.wait()

    return code, "\n".join(lines)


def perform_update(
    on_step: Union[Callable[[str], None], None] = None,  # noqa: UP007
    on_output: Union[Callable[[str], None], None] = None,  # noqa: UP007
) -> tuple[bool, str]:
    """Reinstall Flash from the latest `main` branch.

    Mirrors install.sh: clone `main` to a temp dir and `pipx install
    --force` it. Returns (success, message).

    `on_step` is called with a short label before each step, and
    `on_output` with every line git and pipx print, so the caller can
    show the log as it happens instead of letting those commands write
    over its spinner.
    """

    def step(label: str) -> None:
        if on_step is not None:
            on_step(label)

    if not shutil.which("git"):
        return False, "git is required to update but was not found."

    if not shutil.which("pipx"):
        if os.name == "nt":
            reinstall = f"irm {INSTALL_SCRIPT_PS1_URL} | iex"
        else:
            reinstall = f"curl -fsSL {INSTALL_SCRIPT_URL} | bash"
        return False, (
            "pipx is required to update but was not found. Run this "
            f"command:\n\n```\n{reinstall}\n```"
        )

    tmp_dir = tempfile.mkdtemp(prefix="flash-update-")

    try:
        # Nothing to uninstall is the normal case on a fresh machine, so
        # this step is allowed to fail.
        step("Removing the old version")
        _stream(["pipx", "uninstall", "flash"], on_output)

        step("Downloading the latest version")
        code, output = _stream(
            ["git", "clone", "--depth", "1", REPO_URL, tmp_dir],
            on_output,
        )
        if code != 0:
            return False, _failed(
                "Could not download the update", code, output
            )

        step("Installing")
        code, output = _stream(
            ["pipx", "install", "--force", tmp_dir],
            on_output,
        )
        if code != 0:
            return False, _failed(
                "Could not install the update", code, output
            )
    except OSError as exc:
        return False, f"Update failed: {exc}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return True, "Flash updated. Restart flash to use the new version."
