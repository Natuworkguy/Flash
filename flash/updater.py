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
from pathlib import Path
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


def is_pipx_install() -> bool:
    """True if the running `flash` command lives in a pipx venv."""

    exe = shutil.which("flash")
    return bool(exe) and "pipx" in Path(exe).resolve().as_posix()


def perform_update() -> tuple[bool, str]:
    """Reinstall Flash from the latest `main` branch.

    Mirrors install.sh: clone `main` to a temp dir and `pipx install
    --force` it. Returns (success, message).
    """

    if not shutil.which("git"):
        return False, "git is required to update but was not found."

    if not shutil.which("pipx"):
        if os.name == "nt":
            reinstall = f"irm {INSTALL_SCRIPT_PS1_URL} | iex"
        else:
            reinstall = f"curl -fsSL {INSTALL_SCRIPT_URL} | bash"
        return False, (
            "pipx is required to update but was not found. Run this "
            f"command:\n{reinstall}"
        )

    if not is_pipx_install():
        return False, (
            "This doesn't look like a pipx install. If you cloned the "
            "repo manually, update it with `git pull` instead."
        )

    tmp_dir = tempfile.mkdtemp(prefix="flash-update-")
    try:
        subprocess.run(  # nosec B603 B607
            ["git", "clone", "--depth", "1", REPO_URL, tmp_dir],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(  # nosec B603 B607
            ["pipx", "install", "--force", tmp_dir],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or str(exc)).strip()
        return False, f"Update failed: {detail}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return True, "Flash updated. Restart flash to use the new version."
