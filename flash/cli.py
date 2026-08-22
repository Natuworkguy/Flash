"""Command-line argument parsing for Flash CLI."""

import argparse
from typing import Union

from .version import __version__


def parse_args(argv: Union[list[str], None] = None) -> argparse.Namespace:  # noqa: UP007, E501, RUF100
    parser = argparse.ArgumentParser(
        prog="flash",
        description="FLASH (Fast Local Agent SHell) CLI",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"Flash CLI v{__version__}",
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="a flash://?prompt=... URL to open in a new session",
    )
    parser.add_argument(
        "--register-url-scheme",
        action="store_true",
        help="register this machine's handler for flash:// URLs",
    )
    parser.add_argument(
        "--unregister-url-scheme",
        action="store_true",
        help="remove this machine's handler for flash:// URLs",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="check for a newer Flash version and update if one exists",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "with --update, reinstall even if already on the latest "
            "version, without asking for confirmation"
        ),
    )

    args = parser.parse_args(argv)
    if args.force and not args.update:
        parser.error("--force can only be used with --update")

    return args
