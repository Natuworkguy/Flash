"""Front matter: the fields between --- lines that open a post or a page.

A post written for a blog or a docs site starts with its title, date and
tags between two --- lines. The web UI draws them as a table of fields;
the terminal shows the same as a Markdown table, with the first field as
its head. Only a block at the very top counts, and only when every line
in it reads as a field, so a rule, a line of text and another rule stay
just that.
"""

import re

_BLOCK = re.compile(
    r"\A\s*---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL,
)
_FIELD = re.compile(r"^([A-Za-z0-9_][\w .-]*?)\s*:(?:\s+(.*))?$")
_ITEM = re.compile(r"^\s*-\s+(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split(text: str) -> tuple[list[tuple[str, list[str]]], str] | None:
    """TEXT's front matter as (field, values) pairs, and the text after
    it; None when it has none. A field with a list has an item per value;
    one with text has that text alone."""

    match = _BLOCK.match(text or "")
    if not match:
        return None
    fields: list[tuple[str, list[str]]] = []
    for line in match.group(1).split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        field = _FIELD.match(line)
        if field:
            value = _unquote(field.group(2) or "")
            if value.startswith("[") and value.endswith("]"):
                items = [_unquote(v) for v in value[1:-1].split(",")]
                fields.append((field.group(1), [v for v in items if v]))
            elif re.fullmatch(r"[|>][+-]?", value):
                fields.append((field.group(1), []))
            else:
                fields.append((field.group(1), [value] if value else []))
            continue
        # Under a field: a list of items, or more of its text, indented.
        item = _ITEM.match(line)
        if fields and item:
            fields[-1][1].append(_unquote(item.group(1)))
        elif fields and line[:1].isspace():
            values = fields[-1][1]
            if values:
                values[-1] = f"{values[-1]} {line.strip()}"
            else:
                values.append(line.strip())
        else:
            return None
    if not fields:
        return None
    return fields, text[match.end():]


def for_terminal(text: str) -> str:
    """TEXT with its front matter as a Markdown table the terminal draws."""

    found = split(text)
    if not found:
        return text
    fields, rest = found

    def cell(value: str) -> str:
        return value.replace("|", "\\|")

    rows = [
        f"| {cell(key)} | {cell(' · '.join(values))} |"
        for key, values in fields
    ]
    rows.insert(1, "| --- | --- |")
    return "\n".join(rows) + "\n\n" + rest.lstrip("\n")
