"""Asynchronous sub-agents the main agent can spawn to work in parallel.

Each sub-agent is its own tool-calling loop against the same Ollama model,
running on a background thread. The main agent starts one with `agent`,
keeps working, and later calls `agent_result` to collect (or wait for) its
answer. Sub-agents only get the tools in tools.SUBAGENT_TOOL_NAMES, minus
the ones that ask the user first unless autonomous mode is on: a y/n prompt
from a background thread would fight the main prompt for stdin.

Their tool output never reaches the terminal directly. It is recorded as
steps on the SubAgent, which render() draws and /agents watches live.
"""

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

import ollama
from rich.console import Group, RenderableType
from rich.live import Live
from rich.text import Text

from .sysprompt import get_model_system_prompt
from .theme import (
    ACCENT,
    BRANCH,
    BULLET,
    CROSS,
    DIFF_ADD,
    DIM,
    ELLIPSIS,
    ERROR,
    SPINNER_FRAMES,
    TICK,
    WARN,
    ToolSink,
    capture_tool_output,
    console,
    plural,
)

OLLAMA_HOST_DEFAULT = "http://localhost:11434"

MAX_SUBAGENT_ROUNDS = 10
DEFAULT_WAIT_SECONDS = 300.0
MAX_WAIT_SECONDS = 600.0

RUNNING = "running"
DONE = "done"
FAILED = "error"

RECENT_STEPS = 4
RESULT_PREVIEW_LINES = 20
REFRESH_PER_SECOND = 8

# ai.py swaps in its own, so sub-agent requests ask Ollama for the same
# num_ctx and num_predict as the main loop's.
chat_options: Callable[[], dict] = dict

SUBAGENT_SYSTEM_PROMPT = """
=== Sub-agent ===
You are a sub-agent, spawned by another instance of yourself to carry out
one focused piece of work. You cannot talk to the user and get no further
turns after this one, so work autonomously (there is no one to ask for
clarification) and finish the task yourself. Your final reply is the only
thing that reaches whoever spawned you, so make it a complete, self-
contained answer, not a promise to look further.
""".strip()

ROUND_LIMIT_MESSAGE = (
    "You have used every tool round you get. Answer the task now from the "
    "tool results above. Do not call any more tools."
)


@dataclass
class Step:
    """One tool call a sub-agent made, as drawn in its progress view."""

    label: str
    detail: str = ""
    failed: bool = False


@dataclass
class SubAgent:
    """State for one spawned sub-agent."""

    id: str
    task: str
    status: str = RUNNING
    result: str = ""
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    rounds: int = 0
    activity: str = "Starting"
    steps: list[Step] = field(default_factory=list)
    delivered: bool = False

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


_agents: dict[str, SubAgent] = {}
_lock = threading.Lock()


def allowed_tool_names() -> tuple[str, ...]:
    """The tools a sub-agent may call right now."""

    from . import tools as flash_tools  # deferred: avoids a module cycle

    if flash_tools.NO_COMMAND_CONFIRMATION:
        return flash_tools.SUBAGENT_TOOL_NAMES

    return tuple(
        name for name in flash_tools.SUBAGENT_TOOL_NAMES
        if name not in flash_tools.CONFIRMED_TOOL_NAMES
    )


def _tool_call_name_args(call) -> tuple[str, dict]:
    function = getattr(call, "function", None)
    name = getattr(function, "name", "") or ""
    args = getattr(function, "arguments", None) or {}

    return name, dict(args)


def _update(entry: SubAgent, **changes) -> None:
    with _lock:
        for key, value in changes.items():
            setattr(entry, key, value)


def _add_step(entry: SubAgent, step: Step) -> None:
    with _lock:
        entry.steps.append(step)


def _recorder(entry: SubAgent) -> ToolSink:
    """A capture_tool_output sink that logs tool output as ENTRY's steps."""

    def record(kind: str, text: str, style: str) -> None:
        with _lock:
            if kind == "line":
                entry.steps.append(Step(label=text))
                entry.activity = f"Running {text}"
            elif kind == "result" and entry.steps:
                # The last result wins: write reports its diff summary
                # first and what it actually did second.
                entry.steps[-1].detail = text
                entry.steps[-1].failed = style == ERROR

    return record


def _system_prompt(host: str, model: str, date_prompt: str) -> str:
    parts = [
        get_model_system_prompt(host, model),
        SUBAGENT_SYSTEM_PROMPT,
        date_prompt,
    ]
    return "\n\n".join(part for part in parts if part)


def _call_tool(
    entry: SubAgent, name: str, args: dict, allowed: tuple[str, ...]
) -> str:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    # The reason tool prints straight to the console; a sub-agent's
    # thought belongs in its own log instead.
    if name == "reason":
        thought = str(args.get("thought", "")).strip()
        _add_step(entry, Step(label="Reason", detail=thought))
        return "(noted)"

    if name not in allowed:
        result = f"Unknown tool: {name}. Available: {', '.join(allowed)}."
        _add_step(entry, Step(label=f"{name}()", detail=result, failed=True))
        return result

    return flash_tools.run_tool((name, args))


def _run(entry: SubAgent) -> None:
    from . import tools as flash_tools  # deferred: avoids a module cycle

    try:
        model = flash_tools.MODEL_NAME
        if not model:
            raise RuntimeError("MODEL is not set")

        host = flash_tools.OLLAMA_HOST or OLLAMA_HOST_DEFAULT
        client = ollama.Client(host=host)
        allowed = allowed_tool_names()
        schemas = [
            t for t in flash_tools.tools
            if t["function"]["name"] in allowed
        ]
        messages: list[dict] = [
            {
                "role": "system",
                "content": _system_prompt(
                    host, model, flash_tools.CURRENT_DATE_PROMPT
                ),
            },
            {"role": "user", "content": entry.task},
        ]

        final = ""
        tool_calls: list = []

        with capture_tool_output(_recorder(entry)):
            for round_number in range(1, MAX_SUBAGENT_ROUNDS + 1):
                _update(entry, rounds=round_number, activity="Thinking")
                response = client.chat(
                    model=model,
                    messages=messages,
                    tools=schemas,
                    options=chat_options(),
                )
                message = getattr(response, "message", None)
                final = getattr(message, "content", "") or ""
                tool_calls = list(getattr(message, "tool_calls", None) or [])

                if not tool_calls:
                    break

                calls = [_tool_call_name_args(call) for call in tool_calls]
                messages.append({
                    "role": "assistant",
                    "content": final,
                    "tool_calls": [
                        {"function": {"name": name, "arguments": args}}
                        for name, args in calls
                    ],
                })

                for name, args in calls:
                    result = _call_tool(entry, name, args, allowed)
                    messages.append({
                        "role": "tool",
                        "content": flash_tools.trim_tool_output(result, name),
                        "tool_name": name,
                    })

        if tool_calls:
            _update(entry, activity="Writing its answer")
            messages.append({"role": "system", "content": ROUND_LIMIT_MESSAGE})
            response = client.chat(
                model=model, messages=messages, options=chat_options()
            )
            message = getattr(response, "message", None)
            final = getattr(message, "content", "") or ""

        _update(
            entry,
            status=DONE,
            result=final.strip() or "(sub-agent finished with no output)",
            finished=time.time(),
        )
    except Exception as e:  # noqa: BLE001
        _update(
            entry,
            status=FAILED,
            result=f"{e.__class__.__name__}: {e}",
            finished=time.time(),
        )


def start(task: str) -> str:
    """Spawn a sub-agent for TASK on a background thread; return its ID."""

    entry = SubAgent(id=uuid.uuid4().hex[:6], task=task)
    with _lock:
        _agents[entry.id] = entry

    threading.Thread(target=_run, args=(entry,), daemon=True).start()

    return entry.id


def _snapshot(entry: SubAgent) -> SubAgent:
    return dataclasses.replace(
        entry, steps=[dataclasses.replace(step) for step in entry.steps]
    )


def status(agent_id: str) -> Optional[SubAgent]:
    """A copy of a sub-agent's current state, or None if unknown."""

    with _lock:
        entry = _agents.get(agent_id)
        return _snapshot(entry) if entry else None


def list_all() -> list[SubAgent]:
    """Copies of every sub-agent spawned this session, oldest first."""

    with _lock:
        return [_snapshot(entry) for entry in _agents.values()]


def running_count() -> int:
    """How many sub-agents are still working."""

    with _lock:
        return sum(1 for e in _agents.values() if e.status == RUNNING)


def unseen() -> list[SubAgent]:
    """Finished sub-agents whose answer the main agent has not had yet."""

    with _lock:
        return [
            _snapshot(e) for e in _agents.values()
            if e.status != RUNNING and not e.delivered
        ]


def mark_delivered(agent_ids: list[str]) -> None:
    """Record that the main agent has seen these sub-agents' answers."""

    with _lock:
        for agent_id in agent_ids:
            if agent_id in _agents:
                _agents[agent_id].delivered = True


NOTICE_TASK_CHARS = 200


def notices() -> tuple[str, list[str]]:
    """News for the main agent's next turn, and the finished IDs it covers.

    The main loop keeps only final replies in its history, never tool
    results, so a sub-agent's ID and answer are gone by the next turn.
    This carries them over: every finished answer not yet seen, and every
    sub-agent still running, so the model never has to guess an ID. The
    caller passes the IDs to mark_delivered once the turn goes through.
    """

    from . import tools as flash_tools  # deferred: avoids a module cycle

    finished = unseen()
    running = [e for e in list_all() if e.status == RUNNING]

    if not finished and not running:
        return "", []

    def task(entry: SubAgent) -> str:
        text = " ".join(entry.task.split())
        if len(text) > NOTICE_TASK_CHARS:
            text = text[:NOTICE_TASK_CHARS].rstrip() + ELLIPSIS
        return text

    blocks = []
    for entry in finished:
        if entry.status == DONE:
            answer = flash_tools.trim_tool_output(entry.result)
            blocks.append(
                f"Sub-agent {entry.id} finished. Task: {task(entry)}\n"
                f"Its answer:\n{answer}"
            )
        else:
            blocks.append(
                f"Sub-agent {entry.id} failed. Task: {task(entry)}\n"
                f"Error: {entry.result}"
            )
    for entry in running:
        blocks.append(
            f"Sub-agent {entry.id} is still running. Task: {task(entry)}"
        )

    text = (
        "=== Sub-agent updates (added automatically, not typed by the "
        "user) ===\n"
        + "\n\n".join(blocks)
        + "\n=== End of sub-agent updates ==="
    )
    return text, [entry.id for entry in finished]


# --- Progress view -------------------------------------------------------


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _row(*parts) -> Text:
    return Text.assemble(*parts, no_wrap=True, overflow="ellipsis")


def _status_row(entry: SubAgent) -> Text:
    lead = f"  {BRANCH}  "

    if entry.status == RUNNING:
        frame = SPINNER_FRAMES[
            int(time.monotonic() * REFRESH_PER_SECOND) % len(SPINNER_FRAMES)
        ]
        return _row(
            (lead, DIM),
            (f"{frame} ", ACCENT),
            (entry.activity, f"bold {ACCENT}"),
            (f" · round {entry.rounds} · {_clock(entry.elapsed)}", DIM),
        )

    if entry.status == DONE:
        count = len(entry.steps)
        summary = (
            f" · {count} step{plural(count)} · "
            f"{entry.rounds} round{plural(entry.rounds)} · "
            f"{_clock(entry.elapsed)}"
        )
        return _row(
            (lead, DIM),
            (f"{TICK} Done", DIFF_ADD),
            (summary, DIM),
        )

    return _row(
        (lead, DIM),
        (f"{CROSS} Failed: {_first_line(entry.result)}", ERROR),
        (f" · {_clock(entry.elapsed)}", DIM),
    )


def _step_row(step: Step, *, in_flight: bool) -> Text:
    if step.failed:
        mark, style = CROSS, ERROR
    elif in_flight:
        mark, style = ELLIPSIS, ACCENT
    else:
        mark, style = TICK, DIM

    return _row(
        ("     ", DIM),
        (f"{mark} ", style),
        (step.label, ERROR if step.failed else ""),
        (f"  {_first_line(step.detail)}", DIM),
    )


def render(
    entry: SubAgent,
    *,
    header: bool = True,
    recent: Optional[int] = RECENT_STEPS,
    result: bool = False,
) -> RenderableType:
    """One sub-agent's progress: status, latest steps, maybe its answer."""

    rows: list[RenderableType] = []

    if header:
        rows.append(_row(
            (f"{BULLET} ", ACCENT),
            (f"Agent {entry.id}", "bold"),
            (f"  {entry.task}", DIM),
        ))

    rows.append(_status_row(entry))

    steps = entry.steps
    if recent is not None and len(steps) > recent:
        hidden = len(steps) - recent
        rows.append(Text(
            f"     {ELLIPSIS} {hidden} earlier step{plural(hidden)}",
            style=DIM,
        ))
        steps = steps[-recent:]

    for position, step in enumerate(steps):
        in_flight = (
            entry.status == RUNNING
            and position == len(steps) - 1
            and entry.activity.startswith("Running")
        )
        rows.append(_step_row(step, in_flight=in_flight))

    if result and entry.status != RUNNING:
        lines = entry.result.splitlines()
        shown = "\n".join(lines[:RESULT_PREVIEW_LINES])
        rows.append(Text(""))
        rows.append(Text(shown, style=DIM))
        if len(lines) > RESULT_PREVIEW_LINES:
            more = len(lines) - RESULT_PREVIEW_LINES
            rows.append(Text(
                f"{ELLIPSIS} {more} more line{plural(more)}", style=DIM
            ))

    return Group(*rows)


def _render_all(*, hint: bool) -> RenderableType:
    entries = list_all()
    active = sum(1 for e in entries if e.status == RUNNING)

    rows: list[RenderableType] = [Text(
        f"{len(entries)} sub-agent{plural(len(entries))} · {active} running",
        style=DIM,
    )]
    for entry in entries:
        rows.append(Text(""))
        rows.append(render(entry))

    if hint and active:
        rows.append(Text(""))
        rows.append(Text(
            "Ctrl+C to go back; sub-agents keep running.", style=DIM
        ))

    return Group(*rows)


def _live(
    view: Callable[[], RenderableType], busy: Callable[[], bool]
) -> None:
    """Redraw VIEW in place while BUSY holds, then leave its last frame."""

    if not busy():
        console.print(view())
        return

    try:
        with Live(
            get_renderable=view,
            console=console,
            refresh_per_second=REFRESH_PER_SECOND,
        ):
            while busy():
                time.sleep(1 / REFRESH_PER_SECOND)
    finally:
        # A Live's frames never pass through console.print, so the one
        # it leaves behind has to be noted down to be drawn again.
        console.keep(view())


def follow(agent_id: str, timeout: float) -> Optional[SubAgent]:
    """Wait up to TIMEOUT seconds for a sub-agent, drawing it live."""

    if status(agent_id) is None:
        return None

    deadline = time.monotonic() + timeout

    def busy() -> bool:
        entry = status(agent_id)
        return (
            entry is not None
            and entry.status == RUNNING
            and time.monotonic() < deadline
        )

    def view() -> RenderableType:
        entry = status(agent_id)
        return render(entry, header=False) if entry else Text("")

    _live(view, busy)

    return status(agent_id)


def watch(agent_id: str = "") -> None:
    """The /agents command: every sub-agent live, or one in full."""

    if agent_id:
        entry = status(agent_id)
        if entry is None:
            console.print(Text(f"No sub-agent with ID {agent_id!r}.", WARN))
            return

        def view() -> RenderableType:
            current = status(agent_id)
            if current is None:
                return Text("")
            return render(current, recent=None, result=True)

        def busy() -> bool:
            current = status(agent_id)
            return current is not None and current.status == RUNNING

    else:
        if not list_all():
            console.print(Text("No sub-agents yet.", style=DIM))
            return

        def view() -> RenderableType:
            return _render_all(hint=True)

        def busy() -> bool:
            return running_count() > 0

    try:
        _live(view, busy)
    except KeyboardInterrupt:
        pass
