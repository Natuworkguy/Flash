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

# How long the deferred Windows install waits for flash to close before
# trying anyway. Long enough for a slow exit, short of hanging.
_WAIT_SECONDS = 300

# `pipx install --force` builds the venv again from scratch, so every
# package injected into the old one is gone by the time the update
# finishes. Voice mode is optional, so putting these back never decides
# whether the update succeeded. Kept in step with install.sh,
# install.ps1, and requirements-voice.txt.
VOICE_PACKAGES = ("sounddevice", "vosk", "piper-tts")

_VOICE_FAILED = (
    "Voice packages could not be reinstalled, so voice mode stays "
    "unavailable until you run the installer again."
)


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


def _voice_commands() -> list[list[str]]:
    """The two commands that put the voice packages back, in order.

    `inject` leaves a package that is already in the venv at whatever
    version it was, so the forced reinstall behind it is what actually
    moves one. `runpip` hands its arguments straight to pip, which is
    why the subcommand is `install` and not `pip install`.
    """

    packages = list(VOICE_PACKAGES)

    return [
        ["pipx", "inject", "flash"] + packages,
        [
            "pipx", "runpip", "flash", "install",
            "--upgrade", "--force-reinstall",
        ] + packages,
    ]


def _install_voice(
    on_output: Union[Callable[[str], None], None] = None,  # noqa: UP007
) -> bool:
    """Reinstall the voice packages the update wiped. True if they took."""

    for command in _voice_commands():
        code, _output = _stream(command, on_output)

        if code != 0:
            return False

    return True


def _quoted(value: str) -> str:
    """A PowerShell single-quoted literal, which only escapes quotes."""

    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _handoff_command(shell: str, pipx: str, tmp_dir: str) -> list[str]:
    """The command that installs the update once flash is gone.

    Waits out flash, gives Windows a moment to release the files,
    installs, then clears the clone away. It keeps its window open on
    failure so the error is still there to read. A wait that times out
    installs anyway, so the worst case is a visible pipx error rather
    than a window that hangs.
    """

    clone = _quoted(tmp_dir)
    packages = " ".join(VOICE_PACKAGES)

    # The reinstall wipes the injected voice packages, and this window is
    # the only thing still running once flash is gone, so they go back
    # here. PowerShell 5.1 has no `&&`, so each step is its own
    # statement, and a voice failure never changes the install's own
    # exit code.
    voice = (
        "if ($code -eq 0) { "
        f"& {_quoted(pipx)} inject flash {packages}; "
        f"& {_quoted(pipx)} runpip flash install --upgrade "
        f"--force-reinstall {packages}; "
        "if ($LASTEXITCODE -ne 0) { "
        "Write-Host 'Voice packages failed to install. "
        "Voice mode stays unavailable.' } }; "
    )

    script = (
        # This process is the venv's python, and pipx replaces that.
        f"try {{ Wait-Process -Id {os.getpid()} -Timeout {_WAIT_SECONDS} "
        "-ErrorAction Stop } catch {}; "
        # `flash.exe` starts that python and outlives it, and any other
        # session holds the same two files open. Waiting by name covers
        # both without having to guess at which process is which.
        "Get-Process -Name flash -ErrorAction SilentlyContinue | "
        f"Wait-Process -Timeout {_WAIT_SECONDS} "
        "-ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 500; "
        f"& {_quoted(pipx)} install --force {clone}; "
        "$code = $LASTEXITCODE; "
        + voice +
        f"Remove-Item -Recurse -Force {clone} -ErrorAction SilentlyContinue; "
        "if ($code -ne 0) { "
        "Read-Host 'Update failed. Press Enter to close' } "
        "else { Write-Host 'Flash updated. Start flash again.' }"
    )

    return [
        shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script
    ]


def _detached(command: list[str]) -> None:
    """Start `command` in a console of its own, outliving this process."""

    subprocess.Popen(  # nosec B603
        command,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )


def perform_update(
    on_step: Union[Callable[[str], None], None] = None,  # noqa: UP007
    on_output: Union[Callable[[str], None], None] = None,  # noqa: UP007
) -> tuple[bool, str]:
    """Reinstall Flash from the latest `main` branch.

    Mirrors install.sh: clone `main` to a temp dir, `pipx install
    --force` it, then put the voice packages that reinstall wiped back
    in. Returns (success, message).

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

    pipx = shutil.which("pipx")

    if pipx is None:
        if os.name == "nt":
            reinstall = f"irm {INSTALL_SCRIPT_PS1_URL} | iex"
        else:
            reinstall = f"curl -fsSL {INSTALL_SCRIPT_URL} | bash"
        return False, (
            "pipx is required to update but was not found. Run this "
            f"command:\n\n```\n{reinstall}\n```"
        )

    tmp_dir = tempfile.mkdtemp(prefix="flash-update-")
    # Windows hands the clone off to a second process, which needs it to
    # still be there long after this function has returned.
    keep_clone = False

    try:
        step("Downloading the latest version")
        code, output = _stream(
            ["git", "clone", "--depth", "1", REPO_URL, tmp_dir],
            on_output,
        )
        if code != 0:
            return False, _failed(
                "Could not download the update", code, output
            )

        # Windows locks every running executable, and reinstalling means
        # deleting two of them: the venv's python, which is this very
        # process, and flash's own launcher. pipx cannot do that from
        # here at all -- neither uninstall nor `install --force` -- so
        # the install waits for flash to exit and runs on its own.
        if os.name == "nt":
            shell = shutil.which("powershell") or shutil.which("pwsh")

            if shell is None:
                return False, (
                    "PowerShell is needed to finish an update on Windows "
                    "but was not found. Reinstall with this command "
                    f"instead:\n\n```\nirm {INSTALL_SCRIPT_PS1_URL} | "
                    "iex\n```"
                )

            step("Scheduling the install")
            _detached(_handoff_command(shell, pipx, tmp_dir))
            keep_clone = True

            return True, (
                "Flash will finish updating in a window of its own as "
                "soon as you quit this one. Windows will not let it "
                "replace flash while flash is running."
            )

        # `--force` reinstalls over whatever is already there, which is
        # why this never uninstalls first: on Windows that would mean
        # deleting the running executable, and everywhere else it is
        # simply a step that buys nothing.
        step("Installing")
        code, output = _stream(
            ["pipx", "install", "--force", tmp_dir],
            on_output,
        )
        if code != 0:
            return False, _failed(
                "Could not install the update", code, output
            )

        # The reinstall took the injected voice packages with it. Voice
        # is optional, so this reports and moves on rather than calling
        # a working update a failure.
        step("Restoring voice mode")

        if not _install_voice(on_output):
            return True, (
                "Flash updated. Restart flash to use the new version. "
                f"{_VOICE_FAILED}"
            )
    except OSError as exc:
        return False, f"Update failed: {exc}"
    finally:
        if not keep_clone:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return True, "Flash updated. Restart flash to use the new version."
