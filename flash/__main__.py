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


def main() -> None:
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
