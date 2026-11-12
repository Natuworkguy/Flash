"""Rich answers: a reply can mix its text with charts, comparisons,
calculators, buttons and forms where they explain something better.

The model writes each as a ```ui block holding JSON. The web UI draws
it as a live piece of the reply; the terminal shows it as Markdown
(bars drawn in text, tables, numbered choices); speech reads its words.
How often: More, the default, or Less (VISUALS=less in ~/.flash.env, or
the web UI's Settings), when the model keeps to text and writes a block
only where the user asks for one or the answer needs it.
"""

import ast
import json
import math
import operator
import os
import re
from typing import Any, Optional

SETTING = "VISUALS"

# A whole ```ui (or ~~~ui) block, up to its closing fence or the end of
# the text, since a reply still streaming in has none yet.
_BLOCK = re.compile(
    r"^[ \t]*(```|~~~)[ \t]*ui[ \t]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)

TYPES = (
    "chart", "compare", "stats", "steps", "flow", "calc", "choices",
    "form", "checklist", "card", "molecule", "chats", "map",
)

# What the model is told about the blocks it can write.
with open(
    os.path.join(os.path.dirname(__file__), "visuals_prompt.txt"),
    encoding="utf-8",
) as _file:
    PROMPT = _file.read().strip()


# Added to it when the user wants fewer.
LESS = (
    "With rich answers set to less, keep to text: write a ```ui block only "
    "when the user asks for a chart, a calculator, buttons or a form, or "
    "when the answer cannot be followed without one, and then at most one "
    "in a reply."
)


def level() -> str:
    """"more" or "less". An older 0, off, false or no reads as less."""

    value = (os.environ.get(SETTING) or "").strip().lower()
    return "less" if value in ("less", "0", "off", "false", "no") else "more"


def prompt_block() -> str:
    """The rich answers section of the system prompt."""

    return PROMPT if level() == "more" else f"{PROMPT}\n\n{LESS}"


# Formulas: arithmetic over the inputs, nothing else. "^" is a power, as
# people write it, and binds as one, rather than Python's exclusive or.
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {
    "min": min, "max": max, "round": round, "floor": math.floor,
    "ceil": math.ceil, "abs": abs, "sqrt": math.sqrt, "pow": math.pow,
    "log": math.log10, "ln": math.log, "exp": math.exp,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def evaluate(formula: str, values: dict) -> Optional[float]:
    """FORMULA worked out with VALUES for its names, or None when it is
    not one this understands or cannot be worked out (a division by
    zero, a root of a negative)."""

    try:
        tree = ast.parse(str(formula).replace("^", "**"), mode="eval")
    except SyntaxError:
        return None

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) \
                and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in values:
                return float(values[node.id])
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ValueError(node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FUNCTIONS and not node.keywords:
            args = (walk(a) for a in node.args)
            return float(_FUNCTIONS[node.func.id](*args))
        raise ValueError(type(node).__name__)

    try:
        result = walk(tree)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(result, complex) or math.isnan(result) or math.isinf(result):
        return None
    return result


def _number(value: Any) -> str:
    """VALUE as a person writes it: 12, 12.5, 1,200."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _amount(value: Any, spec: dict, decimals: Optional[int] = None) -> str:
    if isinstance(value, (int, float)) and decimals is not None:
        value = round(float(value), decimals)
    text = _number(value)
    return f"{spec.get('prefix', '')}{text}{spec.get('suffix', '')}" + (
        f" {spec['unit']}" if spec.get("unit") else ""
    )


def parse(body: str) -> Optional[list]:
    """The specs in a ```ui block's BODY, a list of dicts with a known
    type, or None when it is not that."""

    try:
        data = json.loads(body)
    except ValueError:
        return None
    specs = data if isinstance(data, list) else [data]
    if not specs or not all(
        isinstance(s, dict) and s.get("type") in TYPES for s in specs
    ):
        return None
    return specs


def _values(series: dict) -> list:
    return [v if isinstance(v, (int, float)) and not isinstance(v, bool)
            else 0 for v in series.get("values") or []]


BAR_WIDTH = 24


def _chart_markdown(spec: dict) -> list:
    labels = [str(x) for x in spec.get("labels") or []]
    series = [s for s in spec.get("series") or [] if isinstance(s, dict)]
    unit = spec.get("unit", "")
    if not labels or not series:
        return []
    if spec.get("kind") == "line" or len(series) > 1:
        names = " | ".join(str(s.get("name", "")) for s in series)
        lines = [f"| | {names} |", "|---" * (len(series) + 1) + "|"]
        for i, label in enumerate(labels):
            row = [
                _number(_values(s)[i]) + unit if i < len(_values(s)) else ""
                for s in series
            ]
            lines.append(f"| {label} | " + " | ".join(row) + " |")
        return lines
    values = _values(series[0])
    total = sum(v for v in values if v > 0) or 1
    top = max([abs(v) for v in values] or [1]) or 1
    width = max(len(x) for x in labels)
    lines = ["```"]
    for label, value in zip(labels, values):
        length = max(1, round(abs(value) / top * BAR_WIDTH))
        bar = "█" * length if value else ""
        share = (
            f" ({value / total:.0%})" if spec.get("kind") == "donut" else ""
        )
        lines.append(
            f"{label.ljust(width)}  {bar} {_number(value)}{unit}{share}"
        )
    lines.append("```")
    return lines


def _markdown(spec: dict) -> str:
    kind = spec.get("type")
    title = str(spec.get("title") or spec.get("prompt") or "").strip()
    lines = [f"**{title}**", ""] if title and kind != "choices" else []

    if kind == "chart":
        lines += _chart_markdown(spec)
    elif kind == "compare":
        for item in spec.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = f"**{item.get('name', '')}**"
            if item.get("pick"):
                name += " (recommended)"
            if item.get("tag"):
                name += f" · {item['tag']}"
            points = [f"- {p}" for p in item.get("points") or []]
            lines += [name, *points, ""]
    elif kind == "stats":
        for item in spec.get("items") or []:
            if isinstance(item, dict):
                note = f" · {item['note']}" if item.get("note") else ""
                value, label = item.get("value", ""), item.get("label", "")
                lines.append(f"- **{value}** {label}{note}")
    elif kind == "steps":
        for i, item in enumerate(spec.get("items") or [], 1):
            if isinstance(item, dict):
                text = f": {item['text']}" if item.get("text") else ""
                lines.append(f"{i}. **{item.get('title', '')}**{text}")
    elif kind == "flow":
        nodes = [n for n in spec.get("nodes") or [] if isinstance(n, dict)]
        lines.append(" → ".join(f"**{n.get('label', '')}**" for n in nodes))
        lines += [""] + [f"- **{n.get('label', '')}**: {n['detail']}"
                         for n in nodes if n.get("detail")]
    elif kind == "calc":
        inputs = [i for i in spec.get("inputs") or [] if isinstance(i, dict)]
        values = {
            str(i.get("id")): i.get("value", 0) for i in inputs
            if isinstance(i.get("value", 0), (int, float))
        }
        lines += [
            f"- {i.get('label', i.get('id'))}: {_amount(i.get('value', 0), i)}"
            for i in inputs
        ]
        for out in spec.get("outputs") or []:
            if not isinstance(out, dict):
                continue
            result = evaluate(out.get("formula", ""), values)
            shown = "?" if result is None else _amount(
                result, out, int(out.get("decimals", 2)))
            lines.append(f"- **{out.get('label', '')}: {shown}**")
    elif kind == "choices":
        if title:
            lines += [title, ""]
        options = spec.get("options") or []
        lines += [f"{i}. {o}" for i, o in enumerate(options, 1)]
    elif kind == "form":
        for field in spec.get("fields") or []:
            if isinstance(field, dict):
                options = field.get("options") or []
                hint = f" ({' / '.join(map(str, options))})" if options else ""
                label = field.get("label", field.get("id", ""))
                lines.append(f"- {label}:{hint}")
    elif kind == "checklist":
        lines += [f"- [ ] {item}" for item in spec.get("items") or []]
    elif kind == "card":
        if spec.get("badge"):
            lines = [f"**{title}** · {spec['badge']}", ""] if title else [f"{spec['badge']}", ""]
        if spec.get("value") not in (None, ""):
            lines.append(f"## {spec['value']}")
        if spec.get("sub") or spec.get("subtitle"):
            lines.append(str(spec.get("sub") or spec.get("subtitle")))
        rows = [r for r in spec.get("rows") or [] if isinstance(r, dict) and not r.get("divider")]
        if rows:
            lines += ["", "---"]
            for r in rows:
                note = f" ({r['note']})" if r.get("note") else ""
                lines.append(f"- {r.get('label', '')}: **{r.get('value', '')}**{note}")
    elif kind == "map":
        route = spec.get("route")
        if isinstance(route, dict) and route.get("summary"):
            lines.append(f"Route: {route['summary']}")
        n = 0
        for p in spec.get("places") or []:
            if not isinstance(p, dict):
                continue
            mark = "Start" if p.get("home") else str(n := n + 1)
            name = p.get("name") or p.get("address") or ""
            try:
                link = f"https://www.openstreetmap.org/?mlat={float(p['lat'])}&mlon={float(p['lng'])}#map=17/{float(p['lat'])}/{float(p['lng'])}"
                name = f"[{name}]({link})"
            except (KeyError, TypeError, ValueError):
                pass
            lines.append(f"{mark}. {name}" + (f": {p['note']}" if p.get("note") else ""))
    elif kind == "chats":
        for i in spec.get("items") or []:
            if isinstance(i, dict):
                lines.append(f"- {i.get('title', 'Untitled')} ({i.get('when', '')}): {i.get('snippet', '')}".rstrip(": "))
    elif kind == "molecule":
        name, link = molecule_page(spec)
        if name:
            lines.append(f"3D structure: [{name}]({link})")
        if spec.get("caption"):
            lines.append(str(spec["caption"]))
    return "\n".join(lines).strip()


def molecule_page(spec: dict) -> tuple[str, str]:
    """The name of the structure a molecule block shows, and the page to see it on."""

    pdb = str(spec.get("pdb") or "").strip().upper()
    af = str(spec.get("alphafold") or spec.get("uniprot") or "").strip().upper()
    cid = str(spec.get("cid") or "").strip()
    smiles = str(spec.get("smiles") or "").strip()
    if pdb:
        return f"PDB {pdb}", f"https://www.rcsb.org/3d-view/{pdb}"
    if af:
        return f"AlphaFold {af}", f"https://alphafold.ebi.ac.uk/entry/{af}"
    if cid:
        return f"PubChem CID {cid}", f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=3D-Conformer"
    if smiles:
        return smiles, f"https://pubchem.ncbi.nlm.nih.gov/#query={smiles}"
    return "", ""


def _terminal_block(match: re.Match) -> str:
    specs = parse(match.group(2))
    if specs is None:
        # Not finished streaming, or not ours: shown as the code it is.
        return match.group(0)
    return "\n\n".join(_markdown(s) for s in specs)


def for_terminal(text: str) -> str:
    """TEXT with each ```ui block as Markdown the terminal can show."""

    return _BLOCK.sub(_terminal_block, text or "")


def _spoken(spec: dict) -> str:
    kind = spec.get("type")
    title = str(spec.get("title") or "").strip()
    if kind == "choices":
        options = [str(o) for o in spec.get("options") or []]
        said = (
            f"{', '.join(options[:-1])} or {options[-1]}"
            if len(options) > 1 else "".join(options)
        )
        prompt = str(spec.get("prompt") or "").strip()
        return f"{prompt} {said}.".strip() if said else prompt
    if kind == "stats":
        return ". ".join(
            f"{i.get('label', '')}: {i.get('value', '')}"
            for i in spec.get("items") or [] if isinstance(i, dict)
        )
    if kind == "card":
        bits = [title, str(spec.get("value") or "")] + [
            f"{r.get('label', '')}: {r.get('value', '')}"
            for r in spec.get("rows") or [] if isinstance(r, dict) and not r.get("divider")
        ]
        return ". ".join(b for b in bits if b)
    if kind == "map":
        names = [str(p.get("name", "")) for p in spec.get("places") or [] if isinstance(p, dict) and not p.get("home")]
        route = spec.get("route") if isinstance(spec.get("route"), dict) else {}
        said = (f"{title}. " if title else "") + (f"{route['summary']}. " if route.get("summary") else "")
        return (said + (", ".join(names[:4]) + ("." if names else ""))).strip() + " The map is on screen."
    if kind == "chats":
        names = [str(i.get("title", "")) for i in spec.get("items") or [] if isinstance(i, dict)]
        return ("Found " + ", ".join(names[:3]) + ".") if names else ""
    if kind == "molecule":
        name = title or molecule_page(spec)[0]
        return f"{name}, in 3D on screen." if name else ""
    if kind == "steps":
        return ". ".join(
            str(i.get("title", "")) for i in spec.get("items") or []
            if isinstance(i, dict)
        )
    return f"{title}, on screen." if title else ""


def plain(text: str) -> str:
    """TEXT with each ```ui block as the few words worth reading aloud."""

    def spoken(match: re.Match) -> str:
        specs = parse(match.group(2))
        if specs is None:
            return match.group(0)
        return " ".join(filter(None, (_spoken(s) for s in specs)))

    return _BLOCK.sub(spoken, text or "")
