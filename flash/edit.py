"""Exact-string edits to a file, instead of rewriting the whole thing.

`write` makes the model emit every byte of a file it wants to change, so
a one-line fix in a 400-line file costs 400 lines of output. On a local
model that is minutes of generation and, past the output-token limit, a
truncated file. An edit names only the lines that change.

The matcher is deliberately forgiving about one thing and strict about
everything else. Small models reproduce a block's *text* well and its
*indentation* badly, so a block copied out of a `read` result comes back
dedented or shifted a level, and an exact-substring matcher rejects it.
`find_match` falls back to a line-based comparison that allows one
uniform indentation shift across the whole block, then re-applies that
same shift to the replacement, which is reversible and cannot silently
edit the wrong lines. Everything else (a typo, a missing line, an
ambiguous block) is an error carrying the nearest text in the file, so
the model can correct itself on the next call rather than going back to
re-read the file.

Nothing here touches the terminal or the filesystem: `tools.py` owns the
confirmation prompt, the diff, and the write, and these functions stay
pure so they can be tested directly.
"""

import difflib
from dataclasses import dataclass, field
from typing import Optional

# A miss reports the closest thing in the file, but only when it really
# is close; below this ratio the "did you mean" is noise that sends the
# model chasing an unrelated block.
NEAREST_MATCH_CUTOFF = 0.6

# How much of the near-miss to quote back. Enough to see the whitespace
# that broke the match, short enough not to eat the context window.
NEAREST_MATCH_MAX_LINES = 12

MAX_EDITS = 50


@dataclass
class Edit:
    """One replacement: `old` becomes `new`, optionally everywhere."""

    old: str
    new: str
    replace_all: bool = False


@dataclass
class Match:
    """Where `old` was found, and how its indentation was shifted."""

    start: int
    end: int
    # Whitespace the file has in front of every line that `old` lacks.
    add: str = ""
    # Characters of leading whitespace `old` has that the file lacks.
    strip: int = 0

    @property
    def exact(self) -> bool:
        return not self.add and not self.strip


@dataclass
class Result:
    """The outcome of applying edits: new text, or why it failed.

    `error` carries the verdict rather than `text` being None, so a
    caller reads `.text` without first having to prove it is not None,
    and an edit that legitimately empties a file is still a success.
    """

    text: str = ""
    error: str = ""
    replacements: int = 0
    # Human-readable notes worth passing back on success, such as an
    # indentation shift that was absorbed rather than rejected.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


def _leading(line: str) -> str:
    """The whitespace `line` starts with."""

    return line[:len(line) - len(line.lstrip())]


def _shift(line: str, add: str, strip: int) -> Optional[str]:
    """`line` as it would appear in the file under this shift.

    Returns None when `strip` would eat something that is not
    whitespace, which means the shift does not apply to this line.
    """

    if not line.strip():
        # A blank line carries no indentation to compare; a shifted
        # block keeps whatever the file has on its blank lines.
        return line

    if strip:
        if len(line) < strip or line[:strip].strip():
            return None
        return line[strip:]

    return add + line


def _shift_for(file_line: str, old_line: str) -> Optional[tuple[str, int]]:
    """The one shift that could turn `old_line` into `file_line`.

    Either the file has extra leading whitespace the model dropped, or
    the model added leading whitespace the file does not have. A pair
    that differs any other way (tabs against spaces, different text)
    has no shift and returns None.
    """

    if file_line.rstrip() == old_line.rstrip():
        return "", 0

    file_indent = _leading(file_line)
    old_indent = _leading(old_line)

    if file_line.strip() != old_line.strip():
        return None

    if file_indent.startswith(old_indent):
        return file_indent[len(old_indent):], 0

    if old_indent.startswith(file_indent):
        return "", len(old_indent) - len(file_indent)

    return None


def _lines_match(
    file_lines: list[str],
    old_lines: list[str],
    add: str,
    strip: int,
) -> bool:
    """Whether every line of `old_lines` matches under one shift."""

    for file_line, old_line in zip(file_lines, old_lines):
        shifted = _shift(old_line, add, strip)
        if shifted is None or shifted.rstrip() != file_line.rstrip():
            return False

    return True


def _line_offsets(text: str) -> list[int]:
    """The character offset each line of `text` starts at."""

    offsets = [0]
    for index, char in enumerate(text):
        if char == "\n":
            offsets.append(index + 1)

    return offsets


def _shifted_matches(text: str, old: str) -> list[Match]:
    """Every place `old` appears once indentation is allowed to shift.

    Compared line by line and ignoring trailing whitespace, so a block
    the model dedented, indented, or copied without its trailing spaces
    still lands. The shift it found rides along in the Match so the
    replacement can be re-indented the same way.
    """

    old_lines = old.split("\n")
    text_lines = text.split("\n")
    offsets = _line_offsets(text)
    span = len(old_lines)

    if span > len(text_lines):
        return []

    # The shift is read off the first line that carries indentation;
    # an all-blank `old` has nothing to anchor to.
    anchor = next(
        (i for i, line in enumerate(old_lines) if line.strip()), None
    )
    if anchor is None:
        return []

    found: list[Match] = []
    next_free = 0
    for start in range(len(text_lines) - span + 1):
        if start < next_free:
            # A repeated block can match at overlapping line offsets.
            # Counting those twice would make a single occurrence look
            # ambiguous, and replacing both would corrupt the file.
            continue

        window = text_lines[start:start + span]
        shift = _shift_for(window[anchor], old_lines[anchor])
        if shift is None:
            continue

        add, strip = shift
        if not _lines_match(window, old_lines, add, strip):
            continue

        begin = offsets[start]
        end = begin + sum(len(line) + 1 for line in window) - 1
        found.append(Match(begin, end, add, strip))
        next_free = start + span

    return found


def find_matches(text: str, old: str) -> list[Match]:
    """Every occurrence of `old` in `text`, exact matches preferred.

    An exact substring match is always right, so it wins outright. The
    indentation-tolerant pass only runs when there is no exact match at
    all, which keeps a shifted near-miss from ever competing with the
    literal text the model asked for.
    """

    if not old:
        return []

    exact = []
    start = text.find(old)
    while start != -1:
        exact.append(Match(start, start + len(old)))
        # Non-overlapping, like str.replace: "aa" occurs once in "aaa",
        # not twice, so a self-overlapping block is not called ambiguous.
        start = text.find(old, start + len(old))

    if exact:
        return exact

    return _shifted_matches(text, old)


def _reindent(new: str, match: Match) -> str:
    """`new`, shifted the same way `old` had to be to match the file."""

    if match.exact:
        return new

    lines = []
    for line in new.split("\n"):
        shifted = _shift(line, match.add, match.strip)
        # A replacement line that cannot take the shift (it is indented
        # less than the block it replaces) keeps its own indentation
        # rather than losing characters off the front.
        lines.append(line if shifted is None else shifted)

    return "\n".join(lines)


def _quote(lines: list[str]) -> str:
    """Quote file lines back with their whitespace made visible."""

    shown = lines[:NEAREST_MATCH_MAX_LINES]
    body = "\n".join(shown)
    if len(lines) > len(shown):
        body += f"\n... {len(lines) - len(shown)} more lines"

    return body


def nearest(text: str, old: str) -> Optional[str]:
    """The block in `text` that comes closest to `old`, if one is close.

    A failed edit is nearly always one wrong character, and the model
    cannot see which one from "not found". Handing back the real text
    turns a re-read of the whole file into a corrected retry.
    """

    old_lines = old.split("\n")
    text_lines = text.split("\n")
    span = len(old_lines)

    if not text_lines or span > len(text_lines):
        return None

    best_ratio = 0.0
    best: Optional[list[str]] = None
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2("\n".join(line.strip() for line in old_lines))

    for start in range(len(text_lines) - span + 1):
        window = text_lines[start:start + span]
        matcher.set_seq1("\n".join(line.strip() for line in window))
        # real_quick_ratio and quick_ratio are cheap upper bounds; they
        # skip the expensive comparison for windows that cannot win.
        if matcher.real_quick_ratio() <= best_ratio:
            continue
        if matcher.quick_ratio() <= best_ratio:
            continue

        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = window

    if best is None or best_ratio < NEAREST_MATCH_CUTOFF:
        return None

    return _quote(best)


def _not_found(text: str, old: str) -> str:
    hint = nearest(text, old)
    message = (
        "Error: old_string was not found in the file. It has to match "
        "the file exactly, character for character."
    )

    if hint is None:
        return (
            message + " Nothing in the file comes close to it, so read "
            "the file again and copy the lines you mean straight out of "
            "the result."
        )

    return (
        f"{message} The closest the file gets is:\n\n{hint}\n\n"
        "Copy that text exactly if it is the block you meant, minus the "
        "line numbers the read tool adds."
    )


def _ambiguous(count: int, old: str) -> str:
    first = old.split("\n", 1)[0].strip()
    return (
        f"Error: old_string appears {count} times in the file, so this "
        f"edit is ambiguous. Add the surrounding lines to old_string "
        f"until it appears only once (the line above and below "
        f"{first!r} is usually enough), or pass replace_all=true to "
        "change every occurrence."
    )


def apply_one(text: str, edit: Edit) -> Result:
    """Apply one edit to `text`."""

    if not edit.old:
        return Result(error=(
            "Error: old_string is empty. Use the write tool to create a "
            "file; edit only changes text that is already there."
        ))

    if edit.old == edit.new:
        return Result(error=(
            "Error: old_string and new_string are identical, so this "
            "edit would change nothing."
        ))

    matches = find_matches(text, edit.old)

    if not matches:
        return Result(error=_not_found(text, edit.old))

    if len(matches) > 1 and not edit.replace_all:
        return Result(error=_ambiguous(len(matches), edit.old))

    chosen = matches if edit.replace_all else matches[:1]

    result = text
    # Replaced back to front so an earlier replacement cannot move the
    # offsets of the ones still to come.
    for match in reversed(chosen):
        replacement = _reindent(edit.new, match)
        result = result[:match.start] + replacement + result[match.end:]

    notes = []
    shifted = [match for match in chosen if not match.exact]
    if shifted:
        notes.append(
            "Matched on text alone: old_string's indentation did not "
            "match the file, so the file's own indentation was kept."
        )

    return Result(text=result, replacements=len(chosen), notes=notes)


def apply_edits(text: str, edits: list[Edit]) -> Result:
    """Apply edits in order, all of them or none.

    Each edit sees the text the one before it produced, so a later edit
    can touch a line an earlier one wrote. The first failure aborts the
    whole batch: a file left half-edited is harder to recover from than
    one that was never touched, and the model can see from the error
    exactly which edit to fix.
    """

    if not edits:
        return Result(error="Error: no edits were given.")

    if len(edits) > MAX_EDITS:
        return Result(error=(
            f"Error: {len(edits)} edits is more than the {MAX_EDITS} "
            "allowed in one call. Split them across several calls."
        ))

    result = text
    total = 0
    notes: list[str] = []

    for index, edit in enumerate(edits, start=1):
        step = apply_one(result, edit)
        if not step.ok:
            if len(edits) == 1:
                return step

            detail = step.error.removeprefix("Error: ")
            return Result(error=(
                f"Error: edit {index} of {len(edits)} failed, so none of "
                f"them were applied and the file is unchanged. {detail}"
            ))

        result = step.text
        total += step.replacements
        for note in step.notes:
            if note not in notes:
                notes.append(note)

    return Result(text=result, replacements=total, notes=notes)
