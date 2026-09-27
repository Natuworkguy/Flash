"""Em and en dashes, and the horizontal bar, out of the model's replies.

The system prompt asks for none, and models write them anyway. So this
takes them out in code rather than trusting the prompt: a dash between
two numbers becomes a hyphen ("1-5"), an en dash joining two words
does too ("Jan-Mar"), one opening a list item becomes the Markdown
bullet, and every other one, the kind that sets a phrase off, becomes
a comma. The horizontal bar, an em dash by another name, goes the same
way. Fenced blocks and inline code keep theirs, since there a dash
is part of what was written, not how it was said.
"""

import re
import string

DASHES = "\u2013\u2014\u2015"  # en, em, horizontal bar
_D = f"[{DASHES}]"

# Code, kept as written. A fence still open runs to the end, the way
# Markdown draws it, and the way it looks halfway through a stream.
_CODE_RE = re.compile(
    r"^ {0,3}(`{3,}|~{3,})[^\n]*"
    r"(?:\n[\s\S]*?(?:\n {0,3}\1[^\n]*(?=\n|\Z)|\Z)|\Z)"
    r"|`[^`\n]+`",
    re.MULTILINE,
)
# Stands in for a piece of code while the prose around it is fixed.
_HOLD = "\ue000{}\ue001"
_HELD_RE = re.compile("\ue000(\\d+)\ue001")

_RULES = (
    # 1-5, 9:00 - 17:00: a range, spaced however it was.
    (re.compile(rf"(?<=\d)([ \t]*){_D}+([ \t]*)(?=\d)"), r"\1-\2"),
    # A list item the model started with a dash.
    (re.compile(rf"^([ \t]*){_D}+[ \t]+", re.MULTILINE), r"\1- "),
    # Trailing off at the end of a line: nothing to join to.
    (re.compile(rf"[ \t]*{_D}+[ \t]*$", re.MULTILINE), ""),
    # Right before other punctuation, which already does the job.
    (re.compile(rf"[ \t]*{_D}+[ \t]*(?=[,;:.!?)])"), ""),
    # Right after it, likewise: "wait, no", and "(aside)".
    (re.compile(rf"(?<=[,;:])[ \t]*{_D}+[ \t]*"), " "),
    (re.compile(rf"(?<=\()[ \t]*{_D}+[ \t]*"), ""),
    # An en dash tight between two words is a compound or a range.
    (re.compile("(?<=\\w)\u2013(?=\\w)"), "-"),
    # Setting a phrase off: a comma.
    (re.compile(rf"[ \t]*{_D}+[ \t]*"), ", "),
)


def undash(text: str) -> str:
    """TEXT with its dashes replaced, code left alone."""

    if not text or not any(d in text for d in DASHES):
        return text

    kept: list[str] = []

    def hold(match: re.Match) -> str:
        kept.append(match.group(0))
        return _HOLD.format(len(kept) - 1)

    text = _CODE_RE.sub(hold, text)
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return _HELD_RE.sub(lambda m: kept[int(m.group(1))], text)


# What a stream holds back until more arrives: trailing space and dashes,
# since what a dash becomes depends on what comes after it.
_UNSETTLED = string.whitespace + DASHES


class DashGuard:
    """undash for a reply that arrives a piece at a time.

    Each piece fed in gives back what can be shown so far, with the
    dashes already dealt with. A dash, and the space around it, waits
    for the next piece, and so does inline code not yet closed. text()
    is the whole reply, exactly undash of everything fed in.
    """

    def __init__(self) -> None:
        self._raw: list[str] = []
        self._sent = 0

    def feed(self, piece: str) -> str:
        self._raw.append(piece)
        raw = "".join(self._raw)
        settled = raw.rstrip(_UNSETTLED)
        settled = settled[:_open_code(settled)]
        return self._send(undash(settled))

    def flush(self) -> str:
        """Whatever was still held back, now that nothing more is coming."""

        return self._send(self.text())

    def text(self) -> str:
        return undash("".join(self._raw))

    def _send(self, shown: str) -> str:
        # Shown text only grows. In the rare case a later piece changes
        # how an earlier one reads, the finished reply puts it right.
        piece = shown[self._sent:]
        self._sent = max(self._sent, len(shown))
        return piece


def _open_code(text: str) -> int:
    """Where inline code still waiting for its closing backtick starts,
    or the end of TEXT if there is none. Only the last line can have
    any: a span does not cross lines. Inside a fenced block this holds
    back the rest of a line now and then, which changes nothing there.
    """

    line_start = text.rfind("\n") + 1
    last = text[line_start:]
    if last.count("`") % 2:
        return line_start + last.rfind("`")
    return len(text)
