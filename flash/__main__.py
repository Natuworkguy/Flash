# PYTHON_ARGCOMPLETE_OK
# pylint: disable=C0114

import os
import subprocess  # nosec B404
import sys

# Set for a Flash started again after installing what it needs, so a
# package that still will not import is said, not installed forever.
REPAIRED = "FLASH_REPAIRED"


def _repair(error: ModuleNotFoundError) -> None:
    """A package Flash needs is missing: install what Flash needs into
    this Python, and start Flash again the same way. Anything else, or a
    second time, is raised as it was."""

    from . import installer

    if os.environ.get(REPAIRED) or not installer.repair(error):
        raise error
    again = {**os.environ, REPAIRED: "1"}
    sys.exit(subprocess.call(  # nosec B603  (this Python, Flash, its args)
        [sys.executable, "-m", "flash", *sys.argv[1:]], env=again))


def _no_console() -> None:
    """Started with no console (pythonw, from a shortcut or a login task),
    Python has nowhere to print, and printing would fail: what Flash says
    goes to ~/.flash/background.log instead."""

    if sys.stdout is not None and sys.stderr is not None:
        return
    from pathlib import Path

    log = Path.home() / ".flash" / "background.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        out = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or out
    sys.stderr = sys.stderr or out


def main() -> None:
    _no_console()
    # Tab completion, and the shell hook for it, before the rest of
    # Flash loads: a Tab has to answer at once.
    from . import cli

    cli.complete()
    if sys.argv[1:2] == ["completion"]:
        args = cli.parse_args()
        print(cli.completion_script(args.target or ""))
        return
    try:
        from .ai import main as flash
    except ModuleNotFoundError as error:
        _repair(error)
        return
    try:
        flash()
    except ModuleNotFoundError as error:
        _repair(error)


if __name__ == "__main__":
    main()
