"""Command-line argument parsing for Flash CLI."""

import argparse
import sys
from typing import Optional

from .version import __version__

EXTENSION_COMMANDS = ("install", "remove", "list")


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
        "command",
        nargs="?",
        choices=EXTENSION_COMMANDS,
        help=(
            "extensions: install SOURCE (github@owner/repo, or "
            "path@/some/folder for one on disk), remove NAME, or list"
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="what to install or remove",
    )
    parser.add_argument(
        "--uri",
        metavar="URL",
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

    parser.add_argument(
        "--sparks",
        action="store_true",
        help=(
            "keep sparks working with no terminal or browser open; what "
            "/sparks always on starts at login"
        ),
    )

    parser.add_argument(
        "--listen",
        action="store_true",
        help=(
            'listen for "Hey Flash" with Flash closed; what its '
            "background service, in the web UI's voice settings, starts "
            "at login"
        ),
    )

    argv = list(sys.argv[1:] if argv is None else argv)
    # A link from a handler registered before --uri: still a link.
    if argv and argv[0].lower().startswith("flash:"):
        argv = ["--uri", *argv]
    args = parser.parse_args(argv)
    if args.command in ("install", "remove") and not args.target:
        parser.error(f"flash {args.command} needs "
                     + ("a source, like github@owner/repo"
                        if args.command == "install" else "a name"))
    if args.target and args.command not in ("install", "remove"):
        parser.error(f"unexpected {args.target!r}")
    # What the rest of Flash reads.
    args.extension_install = args.target if args.command == "install" \
        else None
    args.extension_remove = args.target if args.command == "remove" \
        else None
    args.extension_list = args.command == "list"
    args.url = args.uri
    if args.force and not args.update:
        parser.error("--force can only be used with --update")
    if (args.port is not None or args.no_open or args.lan) and not args.web:
        parser.error("--port, --lan and --no-open go with --web")

    return args
