"""Keeping edits inside what the user said they may touch.

A request like "only change web.py", "just edit the tests/ folder" or
"don't touch the README" names files. For the rest of that turn, a write
or edit outside "only" (or inside "don't") is not made: the model is told
what the user said and to ask before going further. A request that names
no files leaves every file open, as before.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Optional

# "only touch X", "just edit X", "change nothing but X", "limit yourself to X"
_ONLY = re.compile(
    r"\b(?:only|just)\s+(?:touch|edit|change|modify|update|work\s+(?:in|on)|write\s+to|fix)\s+(?P<what>(?:[^.;!?\n]|\.(?=\S))+)"
    r"|\b(?:touch|edit|change|modify)\s+(?:only|nothing\s+(?:but|except))\s+(?P<what2>(?:[^.;!?\n]|\.(?=\S))+)"
    r"|\blimit\s+(?:yourself|the\s+changes?|edits?)\s+to\s+(?P<what3>(?:[^.;!?\n]|\.(?=\S))+)",
    re.I)
# "don't touch Y", "do not edit Y", "leave Y alone", "without touching Y", "never change Y"
_NEVER = re.compile(
    r"\b(?:don'?t|do\s+not|never|without)\s+(?:touch|touching|edit|editing|change|changing|modify|modifying)\s+"
    r"(?P<what>(?:[^.;!?\n]|\.(?=\S))+)"
    r"|\bleave\s+(?P<what2>[^;!?\n]+?)\s+(?:alone|as\s+(?:it\s+is|is))",
    re.I)
# what a file or folder looks like in a sentence: a path, a name with an extension, or a glob
_PATHISH = re.compile(r"`([^`]+)`|((?:[\w.*~-]+/)+[\w.*-]*|[\w*-]+\.[A-Za-z0-9*]{1,8}\b|\*[\w.*-]+)")

_only: list[str] = []
_never: list[str] = []


def _paths(text: str) -> list[str]:
    found = []
    for m in _PATHISH.finditer(text):
        p = (m.group(1) or m.group(2) or "").strip().strip("'\"")
        if p and not p.startswith(("http:", "https:")) and p not in found:
            found.append(p)
    return found


def parse(text: str) -> tuple[list[str], list[str]]:
    """The files TEXT limits edits to, and the ones it puts off limits."""

    only, never = [], []
    for m in _ONLY.finditer(text or ""):
        only += _paths(m.group("what") or m.group("what2") or m.group("what3") or "")
    for m in _NEVER.finditer(text or ""):
        never += _paths(m.group("what") or m.group("what2") or "")
    return only, never


def set_request(text: str) -> None:
    """A new turn: what the user's message says about where edits may go."""

    global _only, _never
    _only, _never = parse(text)


def clear() -> None:
    set_request("")


def current() -> tuple[list[str], list[str]]:
    return list(_only), list(_never)


def _matches(path: str, pattern: str) -> bool:
    try:
        full = Path(os.path.expanduser(path)).resolve()
    except (OSError, ValueError):
        full = Path(path)
    try:
        rel = full.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        rel = full.as_posix()
    pat = os.path.expanduser(pattern).replace("\\", "/")
    if pat.startswith("./"):
        pat = pat[2:]
    folder = pat.rstrip("/")
    if pat.endswith("/") or (Path(folder).is_dir() and "*" not in folder):
        return rel == folder or rel.startswith(folder + "/") or ("/" + folder + "/") in "/" + rel
    if "*" in pat:
        return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(full.name, pat) or fnmatch.fnmatch(rel, "*/" + pat)
    return rel == pat or rel.endswith("/" + pat) or full.as_posix() == pat or ("/" not in pat and full.name == pat)


def check(path: str) -> Optional[str]:
    """None when PATH may be written this turn; otherwise what the model is told instead."""

    if not path:
        return None
    for p in _never:
        if _matches(path, p):
            return (f"Not written: the user said not to touch {p} this time. Leave {path} as it is; "
                    "if the change truly needs it, stop and ask the user first.")
    if _only and not any(_matches(path, p) for p in _only):
        allowed = ", ".join(_only)
        return (f"Not written: the user asked for changes only in {allowed}, and {path} is outside that. "
                "Do it inside those files, or stop and ask the user whether you may change this one too.")
    return None
