"""The interactive model picker behind `/model`.

Bare `/model` opens an arrow-key list of the models Ollama holds on this
machine, so switching is a keypress instead of a remembered name and tag.
Typing filters that list, and a name it does not match is offered as a
download: the model streams in under a progress bar, then becomes the
active one. Nothing here is a whitelist: anything the registry serves
can be typed in.
"""

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union

import httpx
from ollama import ResponseError
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.live import Live
from rich.text import Text

from .theme import (
    ACCENT,
    BAR_EMPTY,
    BAR_FULL,
    BRANCH,
    BULLET,
    CURSOR,
    DIM,
    DIM_HEX,
    ELLIPSIS,
    confirm,
    console,
    tool_line,
    tool_result,
)
from .theme import error as show_error

# Rows the picker shows at once. The rest scroll under the cursor rather
# than pushing the prompt off a short terminal.
VISIBLE_ROWS = 8
NAME_WIDTH = 40
BAR_WIDTH = 22
SIZE_WIDTH = 9

# Ollama reports model sizes in decimal units; divide the same way it
# does so the numbers here match what `ollama list` prints.
_UNITS = ("B", "KB", "MB", "GB", "TB")
_STEP = 1000

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR

HINTS = "up/down move   enter use   type to filter   esc cancel"


@dataclass(frozen=True)
class Model:
    """One row of the picker."""

    name: str
    summary: str = ""
    size: str = ""
    # A short tag after the size, e.g. "active".
    note: str = ""


def human_size(count: float) -> str:
    """Bytes in the unit that reads best: 7600000000 -> '7.6 GB'."""

    size = float(count)
    unit = _UNITS[0]

    for unit in _UNITS:
        if size < _STEP or unit == _UNITS[-1]:
            break
        size /= _STEP

    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"


def _elapsed(seconds: float) -> str:
    """A short duration: 74 -> '1m 14s'."""

    whole = int(seconds)

    if whole < _MINUTE:
        return f"{whole}s"

    return f"{whole // _MINUTE}m {whole % _MINUTE:02d}s"


def _ago(when: Union[datetime, None]) -> str:  # noqa: UP007, RUF100
    """Roughly how long ago WHEN was: 'pulled 3 days ago'."""

    if when is None:
        return ""

    # Ollama's timestamps carry an offset, but a hand-built one might not.
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    seconds = (datetime.now(timezone.utc) - when).total_seconds()

    if seconds < _HOUR:
        return "pulled just now"

    if seconds < _DAY:
        hours = int(seconds // _HOUR)
        return f"pulled {hours} hour{'' if hours == 1 else 's'} ago"

    days = int(seconds // _DAY)

    return f"pulled {days} day{'' if days == 1 else 's'} ago"


def _tagged(name: str) -> str:
    """NAME as Ollama stores it: gemma4 -> gemma4:latest.

    Only the part after the last slash can carry a tag; the rest may be a
    namespace or a registry host, and a host can hold a port colon.
    """

    return name if ":" in name.rpartition("/")[2] else f"{name}:latest"


def _listing(client) -> Union[list, None]:  # noqa: UP007, RUF100
    """Everything Ollama holds, or None if it could not be asked.

    None and an empty list mean different things to a caller offering to
    download something, since "no idea" must not be reported as "not
    installed", so an unreachable backend stays distinguishable.
    """

    try:
        # ollama raises a plain ConnectionError, itself an OSError, when
        # the backend is not up.
        return [model for model in client.list().models if model.model]
    except (OSError, ResponseError, ValueError):
        return None


def installed_names(client) -> Union[set[str], None]:  # noqa: UP007, RUF100
    """Every model Ollama holds locally, or None if it could not be asked."""

    listing = _listing(client)

    return None if listing is None else {
        _tagged(model.model) for model in listing
    }


def is_installed(client, name: str) -> Union[bool, None]:  # noqa: UP007
    """Whether Ollama already has NAME, or None if it could not be asked."""

    here = installed_names(client)

    return None if here is None else _tagged(name) in here


def _describe(model) -> str:
    """What one model is, out of what the listing already told us:
    'gemma3, 12.2B, Q4_K_M, pulled 3 days ago'."""

    details = getattr(model, "details", None)

    return ", ".join(
        str(part)
        for part in (
            getattr(details, "family", "") if details else "",
            getattr(details, "parameter_size", "") if details else "",
            getattr(details, "quantization_level", "") if details else "",
            _ago(getattr(model, "modified_at", None)),
        )
        if part
    )


def installed_models(
    client,
    current: str = "",
) -> Union[list[Model], None]:  # noqa: UP007, RUF100
    """Every model on this machine, as picker rows.

    The one in use leads the list, so opening the picker and pressing
    Enter changes nothing. None when Ollama could not be asked, which is
    not the same answer as the empty list a fresh install gives.
    """

    listing = _listing(client)

    if listing is None:
        return None

    active = _tagged(current) if current else ""
    rows = [
        Model(
            _tagged(model.model),
            _describe(model),
            human_size(model.size or 0),
            "active" if _tagged(model.model) == active else "",
        )
        for model in listing
    ]

    return sorted(rows, key=lambda row: (row.note != "active", row.name))


def _matching(models: list[Model], query: str) -> list[Model]:
    """The models a filter query keeps, in list order."""

    wanted = query.strip().lower()

    if not wanted:
        return list(models)

    return [
        model
        for model in models
        if wanted in model.name.lower() or wanted in model.summary.lower()
    ]


def _fit(text: str, width: int) -> str:
    """TEXT cut to WIDTH columns, ending in an ellipsis where it was cut."""

    if width <= 0:
        return ""

    if len(text) <= width:
        return text

    return text[: max(0, width - len(ELLIPSIS))] + ELLIPSIS


def choose(models: list[Model]) -> Union[str, None]:  # noqa: UP007, RUF100
    """Run the picker over MODELS; return a model name, or None if cancelled.

    Typing filters the list. A query that matches nothing is handed back
    as typed, which is how a model that is not on this machine yet gets
    named: the caller downloads it.
    """

    index = 0
    top = 0
    query = ""

    def rows() -> list[Model]:
        return _matching(models, query)

    def clamp() -> None:
        """Keep the cursor on a real row, and that row on screen."""

        nonlocal index, top

        index = max(0, min(index, len(rows()) - 1))
        top = min(top, index)
        top = max(top, index - VISIBLE_ROWS + 1, 0)

    def render() -> StyleAndTextTuples:
        visible = rows()
        columns = get_app().output.get_size().columns
        width = min(
            max((len(model.name) for model in visible), default=0),
            NAME_WIDTH,
        )

        out: StyleAndTextTuples = [
            (f"fg:{ACCENT}", f"{BULLET} "),
            ("bold", "Switch model"),
        ]

        if query:
            out += [
                (f"fg:{DIM_HEX}", "   filter: "),
                ("", query),
            ]

        out.append(("", "\n"))

        if not visible:
            typed = query.strip()
            out.append((
                f"fg:{DIM_HEX}",
                (
                    f"     nothing here matches. Enter downloads {typed!r}\n"
                    if typed
                    else "     no models here yet. Type a name to "
                         "download one\n"
                ),
            ))

        for offset, model in enumerate(
            visible[top:top + VISIBLE_ROWS], start=top
        ):
            selected = offset == index
            out.append(
                (f"fg:{ACCENT}", f"  {CURSOR} ")
                if selected
                else ("", "    ")
            )
            out.append((
                f"fg:{ACCENT} bold" if selected else "",
                _fit(model.name, width).ljust(width),
            ))

            if model.size:
                out.append((f"fg:{DIM_HEX}", model.size.rjust(SIZE_WIDTH)))

            if model.note:
                out.append((f"fg:{DIM_HEX}", f"  {model.note}"))

            out.append(("", "\n"))

        if len(visible) > VISIBLE_ROWS:
            out.append((
                f"fg:{DIM_HEX}",
                f"     {index + 1} of {len(visible)}\n",
            ))

        if visible:
            out.append((
                f"fg:{DIM_HEX}",
                f"     {_fit(visible[index].summary, columns - 6)}\n",
            ))

        out.append((f"fg:{DIM_HEX}", f"     {_fit(HINTS, columns - 6)}"))

        return out

    keys = KeyBindings()

    def move(step: int) -> None:
        nonlocal index

        count = len(rows())

        if count:
            index = (index + step) % count

        clamp()

    @keys.add("up")
    @keys.add("c-p")
    def _up(_event) -> None:
        move(-1)

    @keys.add("down")
    @keys.add("c-n")
    def _down(_event) -> None:
        move(1)

    @keys.add("enter")
    def _accept(event) -> None:
        visible = rows()

        if visible:
            event.app.exit(result=visible[index].name)
        elif query.strip():
            event.app.exit(result=query.strip())

    @keys.add("escape")
    @keys.add("c-c")
    @keys.add("c-d")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    @keys.add("backspace")
    def _erase(_event) -> None:
        nonlocal query

        query = query[:-1]
        clamp()

    @keys.add("<any>")
    def _type(event) -> None:
        nonlocal query

        if len(event.data) == 1 and event.data.isprintable():
            query += event.data
            clamp()

    @keys.add(Keys.BracketedPaste)
    def _paste(event) -> None:
        """A pasted model name arrives whole, not a keypress at a time."""

        nonlocal query

        pasted = "".join(ch for ch in event.data if ch.isprintable())

        if pasted:
            query += pasted
            clamp()

    app: Application = Application(
        layout=Layout(
            Window(
                FormattedTextControl(render),
                always_hide_cursor=True,
                dont_extend_height=True,
                wrap_lines=False,
            )
        ),
        key_bindings=keys,
        erase_when_done=True,
    )

    return app.run()


def can_pick() -> bool:
    """Whether there is a terminal to draw the picker on."""

    return sys.stdin.isatty() and console.is_terminal


class _Layers:
    """Byte counts for the layers one download streams, as they arrive.

    Ollama reports progress per layer, not per model, so the totals are
    summed here to make one bar out of them. What a layer had already
    completed when it first appeared is held separately: a model that is
    up to date reports every layer finished on sight, and none of those
    bytes crossed the network on this run.
    """

    def __init__(self) -> None:
        self.first: dict[str, int] = {}
        self.done: dict[str, int] = {}
        self.total: dict[str, int] = {}

    def update(self, digest: str, completed: int, total: int) -> None:
        if not digest:
            return

        if digest not in self.first:
            self.first[digest] = completed

        self.done[digest] = max(completed, self.done.get(digest, 0))
        self.total[digest] = max(total, self.total.get(digest, 0))

    @property
    def completed(self) -> int:
        return sum(self.done.values())

    @property
    def size(self) -> int:
        return sum(self.total.values())

    @property
    def downloaded(self) -> int:
        """Bytes that actually moved during this download."""

        return sum(
            self.done[digest] - first for digest, first in self.first.items()
        )


def _bar(fraction: float) -> Text:
    """A BAR_WIDTH progress bar filled to FRACTION."""

    filled = round(BAR_WIDTH * max(0.0, min(1.0, fraction)))

    bar = Text()
    bar.append(BAR_FULL * filled, style=ACCENT)
    bar.append(BAR_EMPTY * (BAR_WIDTH - filled), style=DIM)

    return bar


def _progress(layers: _Layers, status: str, seconds: float) -> Text:
    """The live line under the download's tool line."""

    line = Text(f"  {BRANCH}  ", style=DIM)
    size = layers.size

    # Everything before the first layer arrives, and the verifying and
    # manifest-writing steps after the last one, have no bytes to show.
    if size <= 0:
        line.append(status or f"working{ELLIPSIS}", style=DIM)
        return line

    completed = min(layers.completed, size)

    line.append_text(_bar(completed / size))
    line.append(f"  {completed / size * 100:3.0f}%", style=ACCENT)
    line.append(f"  {human_size(completed)} / {human_size(size)}", style=DIM)

    if layers.downloaded and seconds >= 1:
        line.append(
            f"  {human_size(layers.downloaded / seconds)}/s", style=DIM
        )

    return line


def download(client, name: str) -> bool:
    """Pull NAME, drawing its progress. True once Ollama has the model.

    Reports its own outcome, since every caller wants it said the same
    way, and an interrupted download is not an error: Ollama keeps the
    blobs it already has, so asking again picks up where this left off.
    """

    tool_line(f"Pull({name})")

    layers = _Layers()
    started = time.monotonic()
    last: Union[tuple, None] = None  # noqa: UP007, RUF100

    try:
        with Live(
            _progress(layers, "", 0.0),
            console=console,
            transient=True,
            refresh_per_second=10,
        ) as live:
            for update in client.pull(name, stream=True):
                status = update.status or ""
                layers.update(
                    update.digest or "",
                    update.completed or 0,
                    update.total or 0,
                )

                # Chunks arrive far faster than the eye reads them, so
                # the line is rebuilt only when it would say something
                # new: a different step, or another megabyte in.
                key = (status, layers.completed // 1_000_000)

                if key != last:
                    last = key
                    live.update(
                        _progress(layers, status, time.monotonic() - started)
                    )
    except ResponseError as exc:
        show_error(f"Could not pull {name}: {exc.error or exc}")
        return False
    # Only the non-streaming calls get ollama's error wrapping, so a
    # backend that is down, or a connection that drops halfway through a
    # multi-gigabyte download, surfaces here as a raw httpx error.
    except (httpx.HTTPError, OSError, ValueError) as exc:
        show_error(f"Could not pull {name}: {exc}\n\nIs Ollama running?")
        return False
    except KeyboardInterrupt:
        tool_result(
            f"Interrupted. Ask for {name} again to pick up where it stopped."
        )
        return False

    moved = layers.downloaded

    if moved:
        tool_result(
            f"{name} is ready "
            f"({human_size(moved)} in {_elapsed(time.monotonic() - started)})"
        )
    else:
        tool_result(f"{name} was already up to date.")

    return True


def fetch_if_missing(client, name: str) -> bool:
    """Offer to download NAME when Ollama does not have it.

    True once the model is there to use. An unreachable Ollama cannot say
    either way, so nothing is offered and the name is taken on trust.
    """

    if is_installed(client, name) is not False:
        return True

    if not confirm(f"{name} is not installed. Download it now?"):
        return False

    return download(client, name)


def pick_model(
    client,
    current: str = "",
) -> Union[str, None]:  # noqa: UP007, RUF100
    """The bare `/model` flow: pick one of this machine's models, or type
    the name of one to download. Returns the model to switch to, or None
    when there is no terminal, no answer from Ollama, or no choice made.
    """

    if not can_pick():
        return None

    rows = installed_models(client, current)

    if rows is None:
        return None

    chosen = choose(rows)

    if not chosen:
        return None

    # A picked row came out of the listing, so it needs no second look;
    # only a name typed past the filter can be one Ollama lacks.
    if any(row.name == chosen for row in rows):
        return chosen

    return chosen if fetch_if_missing(client, chosen) else None
