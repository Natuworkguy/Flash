"""The checklist a model keeps while it works on a multi-step task.

One plan per session, held in module state: the `plan` tool replaces it,
`check_step` ticks a box, and both redraw it so the user watches the work
go by rather than waiting in silence for a wall of text at the end.

The text form returned to the model mirrors what the terminal shows, so
its next turn can see which step it is on without a second tool call.
"""

from rich.text import Text

from .theme import (
    ACCENT,
    BRANCH,
    CHECK_DONE,
    CHECK_TODO,
    DIM,
    console,
    plural,
)

DONE = "done"
ACTIVE = "active"
TODO = "todo"

MAX_STEPS = 20
MAX_STEP_LEN = 120

_steps: list[dict] = []


def clear() -> None:
    """Drop the plan -- called when the conversation is reset."""

    _steps.clear()


def steps() -> list[dict]:
    """The current plan, as {'text', 'status'} dicts."""

    return [dict(step) for step in _steps]


def _advance() -> None:
    """Make the first unfinished step the active one."""

    pending = [step for step in _steps if step["status"] != DONE]
    for step in pending:
        step["status"] = TODO
    if pending:
        pending[0]["status"] = ACTIVE


def set_steps(items: list[str]) -> None:
    """Replace the plan with `items`, starting on the first step."""

    _steps.clear()
    for text in items[:MAX_STEPS]:
        _steps.append({"text": text[:MAX_STEP_LEN], "status": TODO})
    _advance()


def mark_done(index: int) -> str:
    """Tick step `index` (1-based). Returns '' on success, else why not."""

    if not _steps:
        return "There is no plan yet -- call plan first."
    if not 1 <= index <= len(_steps):
        return (
            f"Step {index} does not exist; the plan has "
            f"{len(_steps)} step{plural(len(_steps))}."
        )

    _steps[index - 1]["status"] = DONE
    _advance()
    return ""


def done_count() -> int:
    """How many steps are ticked."""

    return sum(1 for step in _steps if step["status"] == DONE)


def headline() -> str:
    """The tool_line() label, e.g. 'Plan(2/4 done)'."""

    if not _steps:
        return "Plan(empty)"
    return f"Plan({done_count()}/{len(_steps)} done)"


def render() -> None:
    """Print the checklist, indented under the most recent tool_line()."""

    if not _steps:
        console.print(Text(f"  {BRANCH}  No plan yet.", style=DIM))
        return

    for position, step in enumerate(_steps):
        lead = f"  {BRANCH}  " if position == 0 else "     "
        if step["status"] == DONE:
            box, style = CHECK_DONE, f"{DIM} strike"
        elif step["status"] == ACTIVE:
            box, style = CHECK_TODO, f"bold {ACCENT}"
        else:
            box, style = CHECK_TODO, DIM

        line = Text(lead, style=DIM)
        line.append(f"{box} {step['text']}", style=style)
        console.print(line)


def as_text() -> str:
    """The plan as plain text, for the model to read back."""

    if not _steps:
        return "No plan yet."

    marks = {DONE: "[x]", ACTIVE: "[>]", TODO: "[ ]"}
    lines = [
        f"{position}. {marks[step['status']]} {step['text']}"
        for position, step in enumerate(_steps, start=1)
    ]
    header = f"Plan ({done_count()}/{len(_steps)} done):"
    return "\n".join([header, *lines])
