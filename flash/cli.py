"""Command-line argument parsing for Flash CLI, and Tab completion of
it in the shell (argcomplete)."""

import argparse
import os
import sys
from typing import Any, Optional

from .version import __version__

EXTENSION_COMMANDS = ("install", "update", "remove", "list")
# Commands of their own, not about extensions.
OTHER_COMMANDS = ("doctor", "completion")
# The shells `flash completion` writes the hook for.
SHELLS = ("bash", "zsh", "fish", "tcsh", "powershell")
# zsh's completion system, which the hook's compdef needs, started when
# nothing has started it (a .zshrc without oh-my-zsh or the like).
ZSH_COMPLETION_SYSTEM = """\
if ! (( $+functions[compdef] )); then
  autoload -Uz compinit && compinit -i
fi
"""
# Each command as zsh and fish list it beside the word.
COMMANDS_SAID = {
    "install": "install an extension",
    "update": "update an extension, or all of them",
    "remove": "remove an extension",
    "list": "list the installed extensions",
    "doctor": "check that everything Flash needs works",
    "completion": "print what turns on Tab completion",
}
# Where an extension to install can come from, offered as it is typed.
SOURCES = ("github@", "git@", "path@")


def _targets(prefix: str, parsed_args: Any, **_kwargs: Any) -> list[str]:
    """What can follow the command, as Tab offers it: the shells for
    completion, installed extensions to update or remove, and where one
    can be installed from."""

    command = getattr(parsed_args, "command", None)
    if command == "completion":
        return [s for s in SHELLS if s.startswith(prefix)]
    if command in ("update", "remove"):
        from . import extensions  # only read when it is asked for

        try:
            names = [e.name for e in extensions.installed()]
        except (OSError, ValueError):
            names = []
        return [n for n in names if n.startswith(prefix)]
    if command == "install":
        if prefix.startswith("path@"):
            # The folder typed so far ("", "/", "~/code/"), and the start
            # of the name in it.
            head, slash, start = prefix[5:].rpartition("/")
            folder = head + slash
            base = os.path.expanduser(folder) if folder else "."
            try:
                found = sorted(os.listdir(base))
            except OSError:
                return []
            return [
                f"path@{folder}{name}"
                + ("/" if os.path.isdir(os.path.join(base, name)) else "")
                for name in found
                if name.startswith(start)
                and (start.startswith(".") or not name.startswith("."))
            ]
        return [s for s in SOURCES if s.startswith(prefix)]
    return []


def _commands(prefix: str, **_kwargs: Any) -> dict[str, str]:
    """The commands, each with a few words on what it does."""

    return {name: said for name, said in COMMANDS_SAID.items()
            if name.startswith(prefix)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flash",
        description="FLASH (Fast Local Agent SHell) CLI",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"Flash CLI v{__version__}",
    )
    command = parser.add_argument(
        "command",
        nargs="?",
        choices=EXTENSION_COMMANDS + OTHER_COMMANDS,
        help=(
            "extensions: install SOURCE (github@owner/repo, "
            "github@owner/repo#branch, git@<repository URL>, or "
            "path@/some/folder for one on disk), update [NAME] (pull it "
            "again from where it came from; all of them with no NAME), "
            "remove NAME, or list; "
            "doctor checks that everything Flash needs works; "
            "completion [SHELL] prints what turns on Tab completion for "
            "flash in your shell (bash by default)"
        ),
    )
    command.completer = _commands  # type: ignore[attr-defined]
    target = parser.add_argument(
        "target",
        nargs="?",
        help="what to install, update or remove; with completion, the "
             "shell",
    )
    target.completer = _targets  # type: ignore[attr-defined]
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="answer yes to every question, as for installing an "
             "extension",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="with doctor, run the commands that fix what it found",
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
        "--shortcut",
        nargs="?",
        const="add",
        choices=("add", "remove"),
        help=(
            "add a Flash shortcut that opens the web UI in your browser "
            "(the Start menu, ~/Applications, or the applications menu), "
            "or remove it"
        ),
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

    return parser


def complete() -> None:
    """Answer the shell's Tab, when it is asking (argcomplete sets
    _ARGCOMPLETE), and exit; else return. Kept apart from the rest of
    Flash, so a Tab never waits on it loading."""

    if "_ARGCOMPLETE" not in os.environ:
        return
    try:
        import argcomplete
    except ImportError:
        # Never installed: nothing to offer, and a Tab never installs.
        sys.exit(0)
    try:
        argcomplete.autocomplete(build_parser(),
                                 always_complete_options=False)
    except Exception:  # an argcomplete too old for this Python
        # No offers, and no traceback in the middle of what is typed,
        # unless argcomplete's own debugging is on.
        if os.environ.get("_ARC_DEBUG"):
            raise
        sys.exit(0)
    sys.exit(0)


def completion_script(shell: str = "") -> str:
    """What turns Tab completion on for flash in SHELL, to eval or
    source."""

    shell = (shell or "bash").strip().lower()
    if shell not in SHELLS:
        raise SystemExit(
            f"flash completion: no completion for {shell!r}. One of: "
            + ", ".join(SHELLS) + ".")
    try:
        import argcomplete
    except ImportError:
        raise SystemExit(
            "flash completion needs the argcomplete package: "
            f"{sys.executable} -m pip install argcomplete") from None
    # Text for the user's shell to read; nothing is run here.
    script = argcomplete.shellcode(["flash"], shell=shell)  # nosec B604
    if shell == "zsh":
        # Its hook needs zsh's completion system, which a .zshrc with no
        # framework never starts: started here when it is not running.
        script = ZSH_COMPLETION_SYSTEM + script
    return script


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # A link from a handler registered before --uri: still a link.
    if argv and argv[0].lower().startswith("flash:"):
        argv = ["--uri", *argv]
    args = parser.parse_args(argv)
    if args.command in ("install", "remove") and not args.target:
        parser.error(f"flash {args.command} needs "
                     + ("a source, like github@owner/repo"
                        if args.command == "install" else "a name"))
    if args.target and args.command not in ("install", "update", "remove",
                                            "completion"):
        parser.error(f"unexpected {args.target!r}")
    # What the rest of Flash reads.
    args.extension_install = args.target if args.command == "install" \
        else None
    args.extension_remove = args.target if args.command == "remove" \
        else None
    args.extension_list = args.command == "list"
    # "" for every extension.
    args.extension_update = (args.target or "") \
        if args.command == "update" else None
    args.url = args.uri
    if args.force and not args.update:
        parser.error("--force can only be used with --update")
    if (args.port is not None or args.no_open or args.lan) and not args.web:
        parser.error("--port, --lan and --no-open go with --web")

    return args
