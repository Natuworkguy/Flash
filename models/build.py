#!/usr/bin/env python3
"""Build the sizes a Flash Onyx Modelfile declares in its header."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FROM_PATTERN = re.compile(
    r"^FROM[ \t]+(?P<repo>[^\s:]+)(?::\S+)?[ \t]*$",
    re.MULTILINE,
)
HEADER_PATTERN = re.compile(r"^#[ \t]*(?P<key>\w+)[ \t]*:[ \t]*(?P<value>.*)$")


def header(source: str, path: Path) -> tuple[str, list[str]]:
    """Return the name and sizes commented at the top of SOURCE."""

    fields: dict[str, str] = {}

    for line in source.splitlines():
        if not line.startswith("#"):
            break

        match = HEADER_PATTERN.match(line)

        if match:
            fields[match["key"].lower()] = match["value"].strip()

    if not fields.get("name"):
        raise SystemExit(f"{path}: no `# name:` line.")

    return fields["name"], fields.get("sizes", "").replace(",", " ").split()


def render(source: str, size: str) -> str:
    """Return SOURCE with its FROM tag set to SIZE."""

    match = FROM_PATTERN.search(source)

    if match is None:
        raise SystemExit("no FROM line to size.")

    pinned = f"FROM {match['repo']}:{size}"

    return source[: match.start()] + pinned + source[match.end():]


def build(
    source: str,
    name: str,
    size: str,
    namespace: str | None,
    dry_run: bool,
) -> None:
    """Create NAME at SIZE, under NAMESPACE if one is given."""

    prefix = f"{namespace.rstrip('/')}/" if namespace else ""
    tag = f"{prefix}{name}:{size}" if size else f"{prefix}{name}"

    with tempfile.TemporaryDirectory() as workdir:
        generated = Path(workdir) / f"{name}-{size or 'base'}.Modelfile"
        generated.write_text(
            render(source, size) if size else source,
            encoding="utf-8",
        )
        argv = ["ollama", "create", tag, "-f", str(generated)]

        print(f"$ {' '.join(argv)}")

        if not dry_run:
            subprocess.run(argv, check=True)


def wanted(
    declared: list[str],
    asked: list[str] | None,
    path: Path,
) -> list[str]:
    """Return the sizes to build, checking ASKED against DECLARED."""

    if not asked:
        return declared or [""]

    unknown = [size for size in asked if size not in declared]

    if unknown:
        raise SystemExit(
            f"{path}: no size {', '.join(unknown)}. "
            f"Declared: {', '.join(declared) or 'none'}."
        )

    return asked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "modelfile",
        nargs="+",
        type=Path,
        help="Modelfile to build",
    )
    parser.add_argument(
        "--size",
        action="append",
        metavar="SIZE",
        help="size to build, repeatable (default: every declared size)",
    )
    parser.add_argument(
        "-n",
        "--namespace",
        help="namespace to tag the model under",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="write the generated Modelfile to stdout instead of building",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the ollama commands without running them",
    )
    args = parser.parse_args()

    if args.print_only and len(args.modelfile) != 1:
        parser.error("--print takes one Modelfile")

    building = not args.dry_run and not args.print_only

    if building and shutil.which("ollama") is None:
        raise SystemExit("ollama is not on PATH.")

    for path in args.modelfile:
        source = path.read_text(encoding="utf-8")
        name, declared = header(source, path)
        sizes = wanted(declared, args.size, path)

        if args.print_only:
            if len(sizes) != 1:
                parser.error("--print takes one --size")

            sys.stdout.write(render(source, sizes[0]) if sizes[0] else source)

            return 0

        for size in sizes:
            build(source, name, size, args.namespace, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
