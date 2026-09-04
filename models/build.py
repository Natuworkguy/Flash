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
NAME_PLACEHOLDER = "{{name}}"
LICENSE_PATH = Path(__file__).resolve().parent.parent / "LICENSE"
LICENSE_PATTERN = re.compile(r"^LICENSE\b", re.MULTILINE)
OWN_TERMS = (
    "The Modelfile and system prompt behind this model are covered by "
    "this license:"
)
BASE_TERMS = (
    "Built on {base}. The terms {base} ships under still apply to this "
    "model and are not replaced by the license above. Run "
    "`ollama show --license {base}` to read them."
)


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


def spoken(name: str) -> str:
    """Return NAME as the model says it: flash-onyx-2.2 -> Flash Onyx 2.2."""

    return " ".join(word.capitalize() for word in name.split("-"))


def licensing() -> str:
    """Return the repository's license, or "" if it is not there."""

    if not LICENSE_PATH.is_file():
        print(f"warning: no {LICENSE_PATH}", file=sys.stderr)

        return ""

    return LICENSE_PATH.read_text(encoding="utf-8").strip()


def notice(body: str, terms: str) -> str:
    """Return BODY with a LICENSE block for it and for its base model."""

    if not terms or LICENSE_PATTERN.search(body):
        return body

    match = FROM_PATTERN.search(body)
    block = [OWN_TERMS, "", terms]

    if match:
        block += ["", BASE_TERMS.format(base=match["repo"])]

    return f'{body.rstrip()}\n\nLICENSE """\n' + "\n".join(block) + '\n"""\n'


def render(source: str, name: str, size: str, terms: str) -> str:
    """Return SOURCE named, sized to SIZE, and licensed under TERMS."""

    body = notice(source.replace(NAME_PLACEHOLDER, spoken(name)), terms)

    if not size:
        return body

    match = FROM_PATTERN.search(body)

    if match is None:
        raise SystemExit("no FROM line to size.")

    pinned = f"FROM {match['repo']}:{size}"

    return body[: match.start()] + pinned + body[match.end():]


def run(argv: list[str], dry_run: bool) -> None:
    """Show ARGV, and run it unless DRY_RUN."""

    print(f"$ {' '.join(argv)}")

    if not dry_run:
        subprocess.run(argv, check=True)


def build(
    source: str,
    name: str,
    size: str,
    terms: str,
    namespace: str | None,
    dry_run: bool,
    push: bool,
) -> None:
    """Create NAME at SIZE, under NAMESPACE if one is given."""

    prefix = f"{namespace.rstrip('/')}/" if namespace else ""
    tag = f"{prefix}{name}:{size}" if size else f"{prefix}{name}"

    with tempfile.TemporaryDirectory() as workdir:
        generated = Path(workdir) / f"{name}-{size or 'base'}.Modelfile"
        generated.write_text(
            render(source, name, size, terms),
            encoding="utf-8",
        )
        run(["ollama", "create", tag, "-f", str(generated)], dry_run)

    if push:
        run(["ollama", "push", tag], dry_run)


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
        "--push",
        action="store_true",
        help="push each built model to the Ollama registry",
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

    if args.push and args.print_only:
        parser.error("--print builds nothing to push.")

    if args.push and not args.namespace:
        parser.error(
            "--push needs --namespace: the registry tags models as "
            "NAMESPACE/name."
        )

    building = not args.dry_run and not args.print_only

    if building and shutil.which("ollama") is None:
        raise SystemExit("ollama is not on PATH.")

    terms = licensing()

    for path in args.modelfile:
        source = path.read_text(encoding="utf-8")
        name, declared = header(source, path)
        sizes = wanted(declared, args.size, path)

        if args.print_only:
            if len(sizes) != 1:
                parser.error("--print takes one --size")

            sys.stdout.write(render(source, name, sizes[0], terms))

            return 0

        for size in sizes:
            build(
                source,
                name,
                size,
                terms,
                args.namespace,
                args.dry_run,
                args.push,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
