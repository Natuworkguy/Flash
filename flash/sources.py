"""Where every number came from.

A brief that says "revenue was $12,400" is only as good as the reader's
trust in it. Each figure in a brief or a report says where it was read:
a link, or the email, file, sheet, page or command it came from. This is
the rule the prompts carry, and the check a spark's report goes through
before it is sent: a line with a figure and nothing saying where it came
from is sent back, once, to be cited.
"""

from __future__ import annotations

import re

RULE = (
    "Every number in a brief, a summary or a report says where it came "
    "from, right beside it: a link, or the email, file, sheet, page or "
    "command it was read from. For example: \"Sales were $12,400 last "
    "week (Q3 sheet, row 14)\", \"3 new issues "
    "(github.com/acme/app/issues)\". A figure you worked out says what "
    "from. One you cannot source, leave out or say it is a guess."
)

# A figure: money, a percentage, a number with a unit or a decimal, or
# any number of two digits or more. Not a time, a date's year alone, a
# version, or the number of a list item.
_FIGURE = re.compile(
    r"(?<![\w.:/#-])"
    r"(?:[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn|million|billion))?"
    r"|\d[\d,]*(?:\.\d+)?\s?%"
    r"|\d[\d,]*\.\d+"
    r"|\d{1,3}(?:,\d{3})+"
    r"|\d{2,})"
    r"(?![\w:/.-]*\d)",
    re.I,
)
_YEAR = re.compile(r"^(19|20)\d\d$")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_DATEISH = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.I,
)
# What counts as saying where it came from.
_CITED = re.compile(
    r"https?://|\]\(|\[\d+\]|\bsources?\b|\baccording to\b|\bper the\b"
    r"|\((?:from|via|in|on|per|see)\s[^()]+\)"
    r"|\([^()]*(?:\b(?:sheet|email|mail|file|page|report|doc|dashboard"
    r"|invoice|statement|row|tab|issue|pr|commit|log|calendar|site"
    r"|github|gmail|drive|notion|slack|imessage|api|command)\b"
    r"|\.(?:com|org|io|dev|net|md|csv|xlsx|json|txt|pdf)\b)[^()]*\)",
    re.I,
)


# A number that names a thing rather than counts it: issue 12, PR 340, step 3, version 2, page 14, room 101.
_LABEL = re.compile(
    r"\b(?:issues?|prs?|pull requests?|tickets?|bugs?|steps?|parts?|chapters?|pages?|lines?|versions?|v|"
    r"orders?|invoices?|rooms?|flights?|items?|tasks?|cards?|no\.?|number|ids?|builds?|runs?|commits?|"
    r"episodes?|seasons?|levels?|rounds?|slides?|figures?|tables?|sections?|rules?|phases?|weeks?|days?)"
    r"\s+#?\d[\w.-]*",
    re.I,
)


def figures(line: str) -> list[str]:
    """The figures in LINE that a reader would want sourced."""

    text = _LIST_ITEM.sub("", _LABEL.sub("", _DATEISH.sub("", _TIME.sub("", line))))
    found = []
    for match in _FIGURE.finditer(text):
        figure = match.group(0).strip()
        if _YEAR.match(figure):
            continue
        found.append(figure)
    return found


def uncited(text: str) -> list[str]:
    """The lines of TEXT with a figure and nothing saying where it came
    from. A table's rows count as cited when its heading row names a
    source column, and a line under a "Sources" heading is a source."""

    out = []
    in_sources = False
    source_column = False
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = line.lstrip("#*_ ").lower()
        if bare.startswith(("sources", "source:", "where this came from")):
            in_sources = True
            continue
        if in_sources:
            continue
        if line.startswith("|"):
            if re.search(r"\|\s*(source|from|where)\s*\|", line, re.I):
                source_column = True
                continue
            if source_column or re.fullmatch(r"[|:\-\s]+", line):
                continue
        else:
            source_column = False
        if figures(line) and not _CITED.search(line):
            out.append(line)
    return out


def nudge(text: str) -> str:
    """What a report with uncited figures is sent back with, or ""."""

    lines = uncited(text)
    if not lines:
        return ""
    shown = "\n".join(f"- {line[:200]}" for line in lines[:6])
    return (
        "[Before this goes to the user: these lines give figures without "
        f"saying where they came from:\n{shown}\n{RULE} Write the whole "
        "answer again with the sources beside them, changing nothing "
        "else.]"
    )
