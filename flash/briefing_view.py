"""The briefing in the terminal: the same cards as the web's feed, one at a
time, full screen. j and k (or the arrows, or space) move between them,
and each can be answered, opened, asked about, or dismissed for good.

feed() returns what to do once it closes, for the REPL to carry out: an
action and its card, or None.
"""

from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Callable
from typing import Optional

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType

from . import briefing
from .theme import _UNICODE_OK, DIM_HEX, mouse_on

HUES = {
    "email": "#4a8fe7", "imessage": "#34c759", "spark": "#9b7bff",
    "wait": "#f08a4b", "reminder": "#e0a84a", "chat": "#2bb3a3",
    "news": "#e5689b", "team": "#5b8def", "end": "#62c073",
    "ping": "#d97757", "notify": "#c98bff",
}
# What each kind of card can do here. A web chat is not in the terminal:
# its card can be asked about or dismissed.
ACTIONS = {
    "email": ("reply", "open", "ask", "dismiss"),
    "imessage": ("reply", "ask", "dismiss"),
    "spark": ("reply", "open", "ask", "dismiss"),
    "team": ("open", "ask", "dismiss"),
    # A ping is answered where it was said: its team's chat, or the spark.
    "ping": ("reply", "open", "ask", "dismiss"),
}
DEFAULT_ACTIONS = ("ask", "dismiss")
KEYS = {"reply": "r", "open": "o", "ask": "a", "dismiss": "d"}
NAMES = {"reply": "reply", "open": "open", "ask": "ask Flash",
         "dismiss": "dismiss"}
OPEN_NAMES = {"spark": "report", "team": "team chat", "email": "mail",
              "ping": "where it was said"}
WIDTH = 72
BODY_LINES = 9
# A card moving in: how far it starts away, in rows, and each step's wait.
SLIDE_ROWS = 3
SLIDE_STEP = 0.03

RAIL = "┃" if _UNICODE_OK else "|"
TICK = "╵" if _UNICODE_OK else "."
DOT = "·" if _UNICODE_OK else "-"
CHECK = "✓" if _UNICODE_OK else "v"


def actions(card: dict) -> tuple[str, ...]:
    if card["kind"] == "email" and not card.get("link"):
        return ("reply", "ask", "dismiss")
    return ACTIONS.get(card["kind"], DEFAULT_ACTIONS)


def when(card: dict) -> str:
    import time

    if card["kind"] == "reminder":
        return "at " + time.strftime("%H:%M", time.localtime(card["at"]))
    if not card.get("at"):
        return ""
    gone = max(0, time.time() - card["at"])
    if gone < 60:
        return "just now"
    if gone < 3600:
        return f"{int(gone // 60)} min ago"
    if gone < 86400:
        return f"{int(gone // 3600)} h ago"
    return f"{int(gone // 86400)} d ago"


def _wrap(text: str, width: int) -> list[str]:
    lines = []
    for para in str(text or "").splitlines() or [""]:
        lines += textwrap.wrap(para, width) or [""]
    return lines


def draw(cards: list[dict], at: int, columns: int, rows: int,
         shift: int = 0, said: str = "",
         problems: tuple[str, ...] = (),
         click: Optional[Callable[[str], None]] = None,
         ) -> StyleAndTextTuples:
    """One card filling COLUMNS by ROWS, with the rail at the right and
    the keys at the foot; AT past the last card is the end. SHIFT moves
    the card down (or up) that many rows, as it slides in. With CLICK,
    a click on a key at the foot calls it with that key's action."""

    def tapped(action: str):
        def handler(event) -> object:
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            assert click is not None  # nosec B101
            click(action)
            return None
        return handler

    def key(style: str, text: str, action: str) -> tuple:
        return (style, text, tapped(action)) if click else (style, text)

    width = max(20, min(WIDTH, columns - 8))
    pad = " " * max(2, (columns - width - 4) // 2)
    total = len(cards)
    card = cards[at] if at < total else None
    hue = (card.get("colour") if card and card["kind"] in ("team", "ping")
           else None) or HUES.get(card["kind"] if card else "end",
                                  HUES["end"])

    head: StyleAndTextTuples = [("bold", " Briefing")]
    if total:
        head.append((f"fg:{DIM_HEX}", f"  {min(at + 1, total)} of {total}"))
    lines: list[StyleAndTextTuples] = [head, []]

    body: list[StyleAndTextTuples] = []
    if card:
        meta: StyleAndTextTuples = [
            (f"bg:{hue} fg:#ffffff bold", f" {card['label']} "),
            (f"fg:{DIM_HEX}", f"  {when(card)}"),
        ]
        if card.get("chat_title"):
            meta.append((f"fg:{DIM_HEX}", f" {DOT} {card['chat_title']}"))
        body += [meta, []]
        if card.get("who") and card["kind"] != "imessage":
            who = ("From " if card["kind"] == "email" else "") + card["who"]
            body.append([("bold", who)])
        for line in _wrap(card["title"], width)[:3]:
            body.append([(f"fg:{hue} bold", line)])
        body.append([])
        text = _wrap(card["body"], width)
        room = max(2, min(BODY_LINES, rows - 14))
        for line in text[:room]:
            body.append([("", line)])
        if len(text) > room:
            body.append([(f"fg:{DIM_HEX}", "…" if _UNICODE_OK else "...")])
    else:
        none = not total
        body += [
            [(f"fg:{hue} bold", f"{CHECK} " + (
                "Nothing new" if none else "You're all caught up"))],
            [],
            [("", "No unread email, messages, reports or finished waits "
                  "since yesterday." if none else
                  "That's everything since yesterday.")],
        ]
        if problems:
            body += [[], [(f"fg:{DIM_HEX}", "Could not look at "
                           + ", ".join(problems) + ".")]]

    # The card sits low, as on a video feed, with room below for the keys.
    top = max(0, rows - 3 - len(lines) - len(body) + shift)
    lines += [[] for _ in range(top)]
    lines += [[("", pad)] + line if line else [] for line in body]

    keys: StyleAndTextTuples = [("", pad)]
    for action in actions(card) if card else ():
        name = OPEN_NAMES.get(card["kind"], "chat") if action == "open" \
            else NAMES[action]
        keys += [key(f"fg:{hue} bold", KEYS[action], action),
                 key("", f" {name}   ", action)]
    if not card and total:
        keys += [key(f"fg:{hue} bold", "D", "clear"),
                 key("", " dismiss all   ", "clear")]
    keys += [(f"fg:{DIM_HEX}", "j/k move  "),
             key(f"fg:{DIM_HEX}", "q close", "close")]
    if said:
        keys += [(f"fg:{DIM_HEX}", f"   {said}")]
    while len(lines) < rows - 2:
        lines.append([])
    lines = lines[:rows - 2] + [[], keys]

    # The rail: a tick a card, the one showing drawn long.
    marks = min(total + 1, max(0, rows - 6))
    first = max(0, min(at - marks // 2, total + 1 - marks))
    start = max(1, (rows - marks) // 2)
    out: StyleAndTextTuples = []
    for row, line in enumerate(lines):
        out += line
        n = row - start + first
        if marks and start <= row < start + marks:
            used = sum(len(text) for _, text in line)
            out.append(("", " " * max(1, columns - used - 2)))
            out.append((f"fg:{hue} bold", RAIL) if n == at
                       else (f"fg:{DIM_HEX}", TICK))
        if row < len(lines) - 1:
            out.append(("", "\n"))
    return out


def feed(cards: list[dict],
         problems: tuple[str, ...] = ()) -> Optional[tuple[str, dict]]:
    """Show CARDS until the user closes them or picks an action: that
    action and its card, or None. Dismissing happens here."""

    state = {"at": 0, "shift": 0, "said": ""}

    def render() -> StyleAndTextTuples:
        size = get_app().output.get_size()
        drawn = draw(cards, state["at"], size.columns, size.rows,
                     state["shift"], state["said"], problems,
                     click=clicked)
        # The wheel moves between cards, wherever it turns.
        return [part if len(part) > 2 else (*part, wheel)
                for part in drawn]

    def wheel(event) -> object:
        if event.event_type == MouseEventType.SCROLL_UP:
            go(-1)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            go(1)
        else:
            return NotImplemented
        return None

    def clicked(action: str) -> None:
        app = get_app()
        if action == "close":
            app.exit(result=None)
        elif action == "clear":
            clear_all()
        else:
            act(action)(app)

    async def slide(direction: int) -> None:
        for step in range(SLIDE_ROWS, -1, -1):
            state["shift"] = step * direction
            get_app().invalidate()
            await asyncio.sleep(SLIDE_STEP)

    def go(step: int) -> None:
        before = state["at"]
        state["at"] = max(0, min(len(cards), before + step))
        state["said"] = ""
        if state["at"] != before:
            get_app().create_background_task(slide(1 if step > 0 else -1))

    keys = KeyBindings()

    @keys.add("j")
    @keys.add("down")
    @keys.add("space")
    @keys.add("pagedown")
    def _next(_event) -> None:
        go(1)

    @keys.add("k")
    @keys.add("up")
    @keys.add("pageup")
    def _back(_event) -> None:
        go(-1)

    @keys.add("home")
    def _first(_event) -> None:
        go(-len(cards) - 1)

    @keys.add("end")
    def _last(_event) -> None:
        go(len(cards) + 1)

    @keys.add("q")
    @keys.add("escape")
    @keys.add("c-c")
    @keys.add("c-d")
    def _close(event) -> None:
        event.app.exit(result=None)

    def clear_all() -> None:
        if state["at"] < len(cards) or not cards:
            return
        briefing.dismiss(c["id"] for c in cards)
        cards.clear()
        state.update(at=0, said="All dismissed")

    @keys.add("D")
    def _clear(_event) -> None:
        clear_all()

    def act(action: str):
        def run(event) -> None:
            app = getattr(event, "app", event)
            if state["at"] >= len(cards):
                return
            card = cards[state["at"]]
            if action not in actions(card):
                return
            if action == "dismiss":
                briefing.dismiss([card["id"]])
                cards.pop(state["at"])
                state["said"] = "Dismissed"
                get_app().create_background_task(slide(1))
                return
            app.exit(result=(action, card))
        return run

    for action, key in KEYS.items():
        keys.add(key)(act(action))

    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(render),
                             always_hide_cursor=True)),
        key_bindings=keys, full_screen=True, mouse_support=mouse_on(),
    )
    return app.run()
