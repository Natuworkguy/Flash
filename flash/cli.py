"""Command-line argument parsing for Flash CLI."""

import argparse
from typing import Optional

from .version import __version__


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
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

    parser.add_argument(
        "--extension-install",
        metavar="SOURCE",
        help=(
            "install an extension, e.g. github@owner/repo, or "
            "path@/some/folder for one on disk"
        ),
    )
    parser.add_argument(
        "--extension-remove",
        metavar="NAME",
        help="remove an installed extension",
    )
    parser.add_argument(
        "--extension-list",
        action="store_true",
        help="list installed extensions",
    )

    parser.add_argument(
        "--web",
        action="store_true",
        help="open Flash in your browser instead of the terminal",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="with --web, the port to serve on (default 7433)",
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help=(
            "with --web, let other devices on your network open it, and "
            "print a QR code for your phone"
        ),
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="with --web, print the link instead of opening a browser",
    )

    args = parser.parse_args(argv)
    if args.force and not args.update:
        parser.error("--force can only be used with --update")
    if (args.port is not None or args.no_open or args.lan) and not args.web:
        parser.error("--port, --lan and --no-open go with --web")

    return args
