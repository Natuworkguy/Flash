"""Command-line argument parsing for Flash CLI."""

import argparse
from typing import Union

from .version import __version__


def parse_args(argv: Union[list[str], None] = None) -> argparse.Namespace:  # noqa: UP007
    parser = argparse.ArgumentParser(
        prog="flash",
        description="FLASH (Fast Local Agent SHell) CLI",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"Flash CLI {__version__}",
    )
    return parser.parse_args(argv)
