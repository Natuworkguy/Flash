"""The OS sandbox: shell commands run walled in by the system itself.

Off unless the user turns it on (SANDBOX). On, every command Flash runs
goes through the sandbox: it may read the computer, but write only in
the folder it works in and the temporary folders, and never see the
places keys and passwords live (~/.ssh, ~/.aws, ~/.gnupg, Flash's own
settings). On a Mac that is the system's own sandbox (sandbox-exec, the
Seatbelt profiles macOS itself uses); on Linux, bubblewrap (bwrap). The
network stays open, so installs and downloads still work.

With it off, Flash can still choose the sandbox for one command, a
risky script or something it does not trust, as a tool argument.

In Ask first mode a command still waits for a yes, sandboxed or not;
SANDBOX_AUTO_ALLOW (off unless turned on) lets a sandboxed one run
without asking, since what it can do is walled in.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

SETTING = "SANDBOX"
AUTO_ALLOW_SETTING = "SANDBOX_AUTO_ALLOW"

# Where secrets live: hidden from a sandboxed command, read or write.
SECRET_PLACES = (".ssh", ".aws", ".gnupg", ".kube", ".docker",
                 ".config/gcloud", ".azure", ".netrc", ".npmrc", ".pypirc",
                 ".git-credentials", ".flash.env", ".flash/vault")


class SandboxError(Exception):
    """The sandbox cannot run here, and why."""


def _on(name: str) -> bool:
    from .notify import _saved

    return _saved(name).strip().lower() in ("1", "on", "true", "yes")


def enabled() -> bool:
    """Whether every command runs in the sandbox: off unless turned on."""

    return _on(SETTING)


def auto_allow() -> bool:
    """Whether, in Ask first mode, a sandboxed command runs without
    asking: off unless turned on."""

    return _on(AUTO_ALLOW_SETTING)


def set_setting(name: str, on: bool) -> None:
    from .envfile import set_env_var
    from .paths import ENV_PATH

    os.environ[name] = "1" if on else "0"
    set_env_var(ENV_PATH, name, os.environ[name])


def missing() -> str:
    """What this computer lacks for the sandbox: "" when it has it."""

    if sys.platform == "darwin":
        return "" if shutil.which("sandbox-exec") else (
            "macOS's sandbox-exec is missing.")
    if os.name == "nt":
        return "The sandbox is not available on Windows yet."
    return "" if shutil.which("bwrap") else (
        "The sandbox needs bubblewrap: install the bubblewrap package "
        "(sudo apt install bubblewrap, or your system's equivalent).")


def available() -> bool:
    return not missing()


def writable(folder: Optional[str] = None) -> list[Path]:
    """Where a sandboxed command may write: the folder it runs in, unless
    that is the home folder or the top of the disk, and the temporary
    folders."""

    places: list[Path] = []
    try:
        here = Path(folder or os.getcwd()).resolve()
        home = Path.home().resolve()
    except OSError:
        here, home = None, None
    if here is not None and here != home and here.parent != here and \
            here not in home.parents:
        places.append(here)
    # Named, not created: the system's temp folders, which sandboxed
    # commands may write in like any other program.
    tmp = Path("/tmp")  # nosec B108
    for temp in {tmp, Path(os.environ.get("TMPDIR") or tmp)}:
        try:
            places.append(temp.resolve())
        except OSError:
            pass
    return places


def secrets() -> list[Path]:
    home = Path.home()
    return [home / place for place in SECRET_PLACES
            if (home / place).exists()]


def _quote_sb(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def mac_profile(folder: Optional[str] = None) -> str:
    """The Seatbelt profile: everything allowed but writes outside the
    working folder and the temporary ones, and any look at secrets."""

    rules = ["(version 1)", "(allow default)", "(deny file-write*)",
             "(allow file-write*"]
    for place in writable(folder):
        rules.append(f"  (subpath {_quote_sb(place)})")
    rules += ['  (subpath "/private/tmp")',
              '  (subpath "/private/var/folders")',
              '  (literal "/dev/null") (literal "/dev/stdout")',
              '  (literal "/dev/stderr") (literal "/dev/tty")',
              '  (regex #"^/dev/fd/"))']
    hidden = secrets()
    if hidden:
        rules.append("(deny file-read* file-write*")
        for place in hidden:
            kind = "subpath" if place.is_dir() else "literal"
            rules.append(f"  ({kind} {_quote_sb(place.resolve())})")
        rules.append(")")
    return "\n".join(rules)


def bwrap_args(folder: Optional[str] = None) -> list[str]:
    """bubblewrap's arguments: the whole disk read-only, the working
    folder and the temporary ones writable, secrets hidden."""

    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
            "--proc", "/proc", "--die-with-parent", "--unshare-pid",
            "--new-session"]
    for place in writable(folder):
        args += ["--bind", str(place), str(place)]
    for place in secrets():
        target = str(place.resolve())
        if place.is_dir():
            args += ["--tmpfs", target]
        else:
            args += ["--ro-bind", "/dev/null", target]
    return args


def wrap(command: str, folder: Optional[str] = None) -> list[str]:
    """COMMAND, as the argv that runs it in the sandbox. SandboxError
    where there is none."""

    why = missing()
    if why:
        raise SandboxError(why)
    if sys.platform == "darwin":
        return ["sandbox-exec", "-p", mac_profile(folder),
                "/bin/sh", "-c", command]
    return [*bwrap_args(folder), "/bin/sh", "-c", command]


def describe(folder: Optional[str] = None) -> str:
    """Where a sandboxed command may write, said for the model."""

    places = ", ".join(str(p) for p in writable(folder))
    return (f"It ran in the sandbox: it could write only in {places}, "
            "and could not see keys or passwords.")
