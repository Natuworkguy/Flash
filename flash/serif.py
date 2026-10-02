"""The marks the agent uses to set a passage apart in Newsreader.

In the web UI, a ```serif block is a passage set off from the reply in
Newsreader, and <serif>words</serif> sets a phrase in it inside a line.
Everywhere else there is no Newsreader to show: the terminal shows the
block as a quote and the phrase in italics, and speech reads the words
with the marks taken away.
"""

import re

# A whole ```serif (or ~~~serif) block, up to its closing fence or the
# end of the text, since a reply still streaming in has none yet.
_BLOCK = re.compile(
    r"^[ \t]*(```|~~~)[ \t]*serif[ \t]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_INLINE = re.compile(r"<serif>(.+?)</serif>", re.IGNORECASE | re.DOTALL)


def _quoted(match: re.Match) -> str:
    body = match.group(2).rstrip("\n")
    return "\n".join(f"> {line}" if line.strip() else ">"
                     for line in body.split("\n"))


def for_terminal(text: str) -> str:
    """TEXT with the serif marks as the terminal can show them."""

    text = _BLOCK.sub(_quoted, text or "")
    return _INLINE.sub(lambda m: f"*{m.group(1)}*", text)


def plain(text: str) -> str:
    """TEXT with the serif marks taken away, the words kept."""

    text = _BLOCK.sub(lambda m: m.group(2).rstrip("\n"), text or "")
    return _INLINE.sub(r"\1", text)
