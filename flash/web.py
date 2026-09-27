"""Flash in a browser: `flash --web`.

The same agent as the terminal, the same model, tools, confirmations,
skills and extensions, drawn as a web page instead. It serves one page
and a small JSON API from this machine, and streams every token as the
model writes it.

Everything here is standard library. The page is one file,
web/index.html, with no build step and nothing fetched from the
internet, so it works offline like the rest of Flash.

Security is the part that needs care. A server on localhost is
reachable by every web page the browser has open, and this one can run
shell commands. So every request has to carry a token made fresh at
startup, which only the page Flash opened knows; the Host header has
to name this machine, which stops a DNS rebinding attack; and a
request from a page has to come from this server's own origin.
"""

import base64
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import uuid
import webbrowser
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import ollama
import segno
from rich.text import Text

from . import agent as subagents
from . import (
    background,
    checkpoint,
    context,
    extensions,
    learning,
    memory,
    skills,
    updater,
    workspace,
)
from .sysprompt import model_sees_images
from .theme import (
    ACCENT,
    DIM,
    ERROR,
    WARN,
    answer_from,
    capture_tool_output,
    console,
)
from .version import __version__

HOST = "127.0.0.1"
# With --lan: every interface, so a phone on the same network can reach it.
LAN_HOST = "0.0.0.0"  # nosec B104 -- only with --lan, and token-gated
DEFAULT_PORT = 7433

WEB_DIR = Path(__file__).parent / "web"
PAGE = WEB_DIR / "index.html"

# The only files served without the token: a stylesheet cannot send
# one, and there is nothing in a font to protect.
# What a page the agent sent may do: run its own scripts, forms, and
# pop-ups, all without the same-origin grant that would let it act as
# Flash.
HTML_SANDBOX = "sandbox allow-scripts allow-forms allow-popups allow-modals"

# The addresses the page answers to. Each serves the same page, which
# opens whatever the address names: a chat, a project, a settings tab.
PAGE_PATHS = re.compile(
    r"^/(?:c/[0-9a-f]{8}|p/[0-9a-f]{8}|projects|skills|extensions"
    r"|settings(?:/(?:general|usage|memory))?)?/?$"
)

STATIC = {"orbit.woff2": "font/woff2", "logo-icon.svg": "image/svg+xml"}

# KaTeX, which turns the math in replies into MathML for the browser to
# draw with its own math font: just the script, shipped with Flash so
# math renders offline too. Served by exact name, and nothing else.
KATEX_TYPES = {".js": "text/javascript; charset=utf-8"}
STATIC.update({
    path.relative_to(WEB_DIR).as_posix(): KATEX_TYPES[path.suffix]
    for path in sorted((WEB_DIR / "katex").rglob("*"))
    if path.is_file() and path.suffix in KATEX_TYPES
})

# How often an idle event stream says it is still there. Proxies and
# some browsers drop a stream that has been silent for a minute.
PING_SECONDS = 15

# How long a question waits between checks that the turn was stopped.
ASK_POLL_SECONDS = 0.25

TITLE_CHARS = 48

MAX_BODY_BYTES = 1_000_000
# An upload arrives as base64 in JSON: a third bigger than the file.
MAX_UPLOAD_BODY = workspace.MAX_UPLOAD_BYTES * 4 // 3 + 64_000
# The most attachments one message carries.
MAX_ATTACHMENTS = 10

# Sub-agents: how often the watcher looks for finished ones, and how many
# times in a row it may wake a chat before waiting for the person, the
# same limit the terminal keeps.
WATCH_SECONDS = 0.5
MAX_WAKES_IN_A_ROW = 3
WAKE_TEXT = "A sub-agent finished. Flash is reading what it found."

# Search: how many chats come back, how many words a query may have,
# and how much of a message a result quotes around its match.
SEARCH_LIMIT = 40
SEARCH_TERMS = 8
SNIPPET_BEFORE = 50
SNIPPET_AFTER = 110
SEARCHED = ("user", "assistant", "thought")

EXPIRED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flash</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#262624;color:#f5f4ef;font:15px/1.6 -apple-system,system-ui,
sans-serif;text-align:center;padding:24px;box-sizing:border-box}
@media (prefers-color-scheme:light){body{background:#faf9f5;color:#1f1e1d}}
h1{font-size:22px;margin:0 0 8px}p{margin:0;opacity:.7;max-width:420px}
code{font-size:13px}</style></head><body><div>
<h1>This link has expired</h1>
<p>Flash makes a new link each time it starts. Open the one it printed
in your terminal, or run <code>flash --web</code> again.</p>
</div></body></html>"""


# --- Events --------------------------------------------------------------


class Hub:
    """Every open page's event stream, fed from one place.

    Each event is numbered. A page asks for the state, then keeps only
    the live events numbered after it, so nothing is shown twice or
    lost in between.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[Queue] = []
        self.seq = 0

    def subscribe(self) -> Queue:
        queue: Queue = Queue()
        with self._lock:
            self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: Queue) -> None:
        with self._lock:
            if queue in self._queues:
                self._queues.remove(queue)

    def close(self) -> None:
        """End every open stream now, rather than at its next ping."""

        with self._lock:
            for queue in self._queues:
                queue.put(None)

    def publish(self, event: dict) -> dict:
        with self._lock:
            self.seq += 1
            event = {**event, "seq": self.seq}
            for queue in self._queues:
                queue.put(event)
        return event


@dataclass
class Chat:
    """One conversation: what the model sees, and what the page draws."""

    id: str
    title: str = "New chat"
    messages: list[dict] = field(default_factory=list)
    # What the page needs to draw the chat again after a reload. Tokens
    # are not kept one by one: a finished reply is kept whole.
    log: list[dict] = field(default_factory=list)
    partial: str = ""
    thinking: str = ""
    busy: bool = False
    queued: bool = False
    # Messages sent while a turn was running, oldest first: each is
    # {"id", "text", "mode"}. A "steer" one goes to the model at the
    # running turn's next step; a "queue" one becomes the next turn.
    pending: list[dict] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    project: str = ""

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "busy": self.busy,
            "queued": self.queued,
            "pending": [dict(p) for p in self.pending],
            "log": self.log,
            "partial": self.partial,
            "thinking": self.thinking,
            "project": self.project,
            "updated": self.updated,
        }

    def saved(self) -> dict:
        """What goes to disk: the conversation, not the moment."""

        return {
            "id": self.id,
            "title": self.title,
            "messages": self.messages,
            "log": self.log,
            "created": self.created,
            "updated": self.updated,
            "project": self.project,
        }

    @classmethod
    def restore(cls, data: dict) -> "Chat":
        return cls(
            id=data["id"],
            title=str(data.get("title") or "New chat"),
            messages=list(data.get("messages") or []),
            log=list(data.get("log") or []),
            created=float(data.get("created") or time.time()),
            updated=float(data.get("updated") or time.time()),
            project=str(data.get("project") or ""),
        )


@dataclass
class Ask:
    id: str
    chat: str
    question: str
    answer: Optional[str] = None
    done: threading.Event = field(default_factory=threading.Event)


# Events that are part of the conversation and drawn again on reload.
# The rest (tokens, status) only matter to a page that is watching.
KEPT = {
    "user", "assistant", "tool", "result", "diff", "ask", "answered",
    "error", "stats", "note", "thought", "file", "plan",
}


# How a steering message reaches the model: marked, so it reads as the
# user redirecting the work in progress rather than a new request.
STEER_NOTE = "[Sent while you were working. Take it into account from here.]"

# Carries the token from a server to the one that replaces it after an
# update, across the exec that starts the new version.
TOKEN_ENV = "FLASH_WEB_TOKEN"  # nosec B105 -- a variable name

# The most of an update's output the page is sent: the end is what
# explains a failure.
UPDATE_LOG_LINES = 200


class Updates:
    """Checking for a newer Flash, and installing it, for the page.

    The same check and the same install as /update in the terminal; the
    difference is that the page watches the progress as events instead
    of a spinner.
    """

    def __init__(self, hub: "Hub") -> None:
        self.hub = hub
        self.lock = threading.Lock()
        self.latest: Optional[str] = None
        self.checked = False
        # idle, checking, updating, updated, failed, or restarting.
        self.state = "idle"
        self.step = ""
        self.message = ""
        self.log: list[str] = []
        # Whether a finished update can restart into the new version
        # from the page (see Session.restart).
        self.can_restart = False

    def snapshot(self) -> dict:
        return {
            "current": __version__,
            "latest": self.latest,
            "available": bool(
                self.latest and updater.is_newer(self.latest)
            ),
            "checked": self.checked,
            "state": self.state,
            "step": self.step,
            "message": self.message,
            "log": self.log[-UPDATE_LOG_LINES:],
            "can_restart": self.can_restart,
        }

    def _publish(self) -> None:
        self.hub.publish({"type": "update", "update": self.snapshot()})

    def check(self) -> None:
        """Ask GitHub what the newest version is. A failed check
        leaves what an earlier one found."""

        with self.lock:
            if self.state in ("updating", "restarting"):
                return
            self.state = "checking"
        self._publish()

        latest = updater.fetch_latest_version()

        with self.lock:
            if latest is not None:
                self.latest = latest
                self.checked = True
                self.message = ""
            else:
                self.message = (
                    "Could not check for updates: no network, or GitHub "
                    "did not answer."
                )
            self.state = "idle"
        self._publish()

    def check_in_background(self) -> None:
        threading.Thread(target=self.check, daemon=True).start()

    def start(self) -> None:
        """Install the newest version, on a thread of its own."""

        with self.lock:
            if self.state in ("updating", "restarting"):
                raise ValueError("An update is already running.")
            self.state = "updating"
            self.step = "Starting"
            self.message = ""
            self.log = []
        self._publish()
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self) -> None:
        def on_step(label: str) -> None:
            self.step = label
            self._publish()

        def on_output(line: str) -> None:
            self.log.append(line)
            del self.log[:-UPDATE_LOG_LINES]
            self._publish()

        try:
            ok, message = updater.perform_update(
                on_step=on_step, on_output=on_output,
            )
        except Exception as exc:  # noqa: BLE001 -- shown, never raised
            ok, message = False, f"Update failed: {exc}"

        with self.lock:
            self.state = "updated" if ok else "failed"
            self.step = ""
            self.message = message
            if ok:
                # What is installed now is what main had.
                self.latest = self.latest or __version__
        self._publish()

    def restarting(self) -> None:
        with self.lock:
            self.state = "restarting"
            self.message = ""
        self._publish()


class Session:
    """Every chat, and the one turn allowed to run at a time.

    One at a time because a local backend generates one reply at a
    time anyway, and because the plan, the undo history, and the
    confirmation setting are shared by the whole of Flash.
    """

    def __init__(self, hub: Optional[Hub] = None) -> None:
        self.hub = hub or Hub()
        self.lan = False
        self.updates = Updates(self.hub)
        # An extension fetched and shown, waiting for the user's yes:
        # (id, checkout, source). One at a time.
        self.staged: Optional[tuple[str, Path, str]] = None
        # Starts this same `flash --web` again, as whatever version is
        # installed now. Only a server that owns its process can: one
        # beside a terminal session would take the session down with it.
        self.restart: Optional[Callable[[], None]] = None
        # Opens the server again listening on the network, or not. Set
        # by whatever runs the server (see _attach).
        self.switch_lan: Optional[Callable[[bool], None]] = None
        self.chats: dict[str, Chat] = {
            data["id"]: Chat.restore(data)
            for data in workspace.load_chats()
        }
        self.asks: dict[str, Ask] = {}
        self.turn_lock = threading.Lock()
        self._lock = threading.Lock()
        # Which chat started each sub-agent, and how many times in a row
        # each chat has been woken for one, with no message in between.
        self.agents: dict[str, str] = {}
        self.wakes: dict[str, int] = {}
        self._watching: Optional[threading.Thread] = None
        self._stop_watching = threading.Event()

    # Chats ---------------------------------------------------------

    def new_chat(self, project: str = "") -> Chat:
        if project and workspace.project(project) is None:
            raise KeyError(project)
        chat = Chat(id=uuid.uuid4().hex[:8], project=project)
        with self._lock:
            self.chats[chat.id] = chat
        self.emit(chat, {"type": "chats"})
        return chat

    def chat(self, chat_id: str) -> Chat:
        chat = self.chats.get(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        return chat

    def delete_chat(self, chat_id: str) -> None:
        chat = self.chats.pop(chat_id, None)
        if chat is not None:
            chat.stop.set()
        workspace.delete_chat(chat_id)
        self.hub.publish({"type": "chats", "chat": chat_id})

    def save(self, chat: Chat) -> None:
        """Keep a chat that has something in it; an empty one is not
        worth a file."""

        chat.updated = time.time()
        if chat.log and chat.id in self.chats:
            workspace.save_chat(chat.saved())

    def clear_chat(self, chat: Chat) -> None:
        chat.stop.set()
        self.unqueue(chat)
        chat.messages.clear()
        chat.log.clear()
        chat.partial = chat.thinking = ""
        workspace.delete_chat(chat.id)
        self.emit(chat, {"type": "cleared"})

    def emit(self, chat: Chat, event: dict) -> dict:
        event = {**event, "chat": chat.id}
        if event["type"] in KEPT:
            chat.log.append({k: v for k, v in event.items() if k != "chat"})
        return self.hub.publish(event)

    def state(self, lite: bool = False) -> dict:
        """Everything a page needs to draw, from scratch or to catch up.

        LITE leaves out each chat's log: the page asks for that when a
        title or a status changed, and already has the conversation.
        """

        from . import ai  # deferred: ai imports half of Flash

        chats = sorted(self.chats.values(), key=lambda c: c.created)
        summaries = [c.summary() for c in chats]

        if lite:
            for summary in summaries:
                for key in ("log", "partial", "thinking"):
                    summary.pop(key)

        return {
            "seq": self.hub.seq,
            "chats": summaries,
            "projects": [asdict(p) for p in workspace.projects()],
            # The picture behind a new chat, drawn by the page: sent
            # with the whole state only, not with every quick refresh.
            "scene": None if lite else scene_data(ai.Config.background or ""),
            # The words the loader cycles through, the terminal's own.
            "words": [s["now"] for s in ai._load_thinking_states()],
            "status": {
                **status(ai), "lan": self.lan,
                "can_switch_lan": self.switch_lan is not None,
                "update": self.updates.snapshot(),
            },
        }

    # Sub-agents ----------------------------------------------------

    def adopt(self, chat: Chat, agent_ids: set) -> None:
        """Note that CHAT started these sub-agents, and watch for them."""

        if not agent_ids:
            return
        for agent_id in agent_ids:
            self.agents[agent_id] = chat.id
        self.hub.publish({"type": "status"})
        if self._watching is None:
            self._watching = threading.Thread(target=self._watch, daemon=True)
            self._watching.start()

    def owned(self, chat_id: str) -> set:
        return {a for a, c in list(self.agents.items()) if c == chat_id}

    def _watch(self) -> None:
        """Wake a chat when a sub-agent it started finishes.

        The terminal does this at its prompt. Here nothing else would:
        the model ended its turn expecting to be told, and without this
        the answer sat unread until someone typed again.
        """

        running = None
        while not self._stop_watching.wait(WATCH_SECONDS):
            now = subagents.running_count()
            if now != running:
                running = now
                self.hub.publish({"type": "status"})

            for chat_id in set(self.agents.values()):
                chat = self.chats.get(chat_id)
                if chat is None or chat.busy or chat.queued:
                    continue
                if not subagents.unseen(self.owned(chat_id)):
                    continue
                if self.wakes.get(chat_id, 0) >= MAX_WAKES_IN_A_ROW:
                    continue
                self.wakes[chat_id] = self.wakes.get(chat_id, 0) + 1
                self.send(chat, ai_wake_note(), wake=True)

    def close(self) -> None:
        self._stop_watching.set()

    def reopen(self) -> None:
        """Carry on under a new server after a LAN switch."""

        self._stop_watching.clear()

    def agent_list(self, chat_id: str) -> list[dict]:
        """What a chat's sub-agents are doing, for the page's menu."""

        owned = self.owned(chat_id)
        return [
            {
                "id": entry.id,
                "task": entry.task,
                "status": entry.status,
                "activity": entry.activity,
                "seconds": round(entry.elapsed),
                "steps": [step.label for step in entry.steps[-3:]],
                "result": entry.result[:400],
            }
            for entry in subagents.list_all()
            if entry.id in owned
        ]

    # Search --------------------------------------------------------

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
        """Chats containing every word of QUERY, best first.

        A chat whose title has them all comes first, then the ones that
        mention them most, then the most recent. Each result carries up
        to three messages that matched, by their place in the chat's
        log, so the page can jump to the one picked. No words at all
        lists the most recent chats.
        """

        terms = [t for t in query.lower().split() if t][:SEARCH_TERMS]
        found = []

        for chat in list(self.chats.values()):
            log = list(chat.log)
            if not log:
                continue

            messages = [
                (index, entry["type"], str(entry.get("text") or ""))
                for index, entry in enumerate(log)
                if entry.get("type") in SEARCHED and entry.get("text")
            ]
            title = chat.title.lower()
            said = "\n".join(text for _, _, text in messages)
            everything = f"{title}\n{said}".lower()
            if not all(term in everything for term in terms):
                continue

            hits = []
            mentions = 0
            for index, kind, text in messages if terms else ():
                flat = " ".join(text.split())
                low = flat.lower()
                count = sum(low.count(term) for term in terms)
                if not count:
                    continue
                mentions += count
                at = min(low.find(term) for term in terms if term in low)
                hits.append({
                    "index": index,
                    "type": kind,
                    "snippet": _snippet(flat, at),
                })

            found.append((
                (bool(terms) and all(t in title for t in terms), mentions,
                 chat.updated),
                {
                    "chat": chat.id,
                    "title": chat.title,
                    "project": chat.project,
                    "updated": chat.updated,
                    "hits": hits[:3],
                    "matches": len(hits),
                },
            ))

        found.sort(key=lambda pair: pair[0], reverse=True)
        return [result for _, result in found[:limit]]

    # Questions -----------------------------------------------------

    def answerer(self, chat: Chat):
        """Ask the page, and wait for it, for the tools on this turn."""

        def ask(question: str) -> str:
            entry = Ask(id=uuid.uuid4().hex[:8], chat=chat.id,
                        question=question)
            self.asks[entry.id] = entry
            self.emit(chat, {
                "type": "ask", "id": entry.id, "question": question,
            })

            while not entry.done.wait(ASK_POLL_SECONDS):
                if chat.stop.is_set():
                    entry.answer = "n"
                    break

            self.asks.pop(entry.id, None)
            answer = entry.answer or "n"
            self.emit(chat, {
                "type": "answered", "id": entry.id, "answer": answer,
            })
            return answer

        return ask

    def answer(self, ask_id: str, answer: str) -> bool:
        entry = self.asks.get(ask_id)
        if entry is None:
            return False
        entry.answer = "y" if str(answer).lower().startswith("y") else "n"
        entry.done.set()
        return True

    # Turns ---------------------------------------------------------

    def send(
        self, chat: Chat, text: str, wake: bool = False, mode: str = "",
        files: Optional[list] = None,
    ) -> dict:
        """Start a turn on a thread of its own and return at once.

        While one is already running, the message waits in the chat's
        pending list instead: MODE "steer" hands it to the model at the
        running turn's next step, and anything else makes it the next
        turn. WAKE is a turn nobody typed: a sub-agent finished, and the
        model is woken to report back. It never waits in line.
        """

        text = text.strip()
        attached = attachments(files)
        if not text and not attached:
            return {}

        with self._lock:
            if chat.busy or chat.queued:
                if wake:
                    return {}
                item = {
                    "id": uuid.uuid4().hex[:8],
                    "text": text,
                    "mode": "steer" if mode == "steer" else "queue",
                    "files": attached,
                }
                chat.pending.append(item)
                waiting = True
            else:
                chat.stop.clear()
                chat.queued = True
                waiting = False

        if waiting:
            self._publish_pending(chat)
            return {"pending": item["id"]}

        self._begin(chat, text, wake, attached)
        threading.Thread(
            target=self._run, args=(chat, text, attached), daemon=True
        ).start()
        return {}

    def _begin(
        self, chat: Chat, text: str, wake: bool = False,
        files: Optional[list] = None,
    ) -> None:
        """Put a turn's message in the chat, as its turn starts."""

        if wake:
            self.emit(chat, {"type": "note", "text": WAKE_TEXT})
            return
        self.wakes[chat.id] = 0
        if chat.title == "New chat":
            named = text or (files[0]["name"] if files else "")
            chat.title = " ".join(named.split())[:TITLE_CHARS] or chat.title
            self.hub.publish({"type": "chats", "chat": chat.id})
        event: dict = {"type": "user", "text": text}
        if files:
            event["files"] = files
        self.emit(chat, event)

    def _publish_pending(self, chat: Chat) -> None:
        self.hub.publish({
            "type": "pending", "chat": chat.id,
            "pending": [dict(p) for p in chat.pending],
        })

    def take_steers(self, chat: Chat) -> list[dict]:
        """The steering messages waiting for this turn, taken off the
        list and shown in the chat where the model reads them."""

        with self._lock:
            steers = [p for p in chat.pending if p["mode"] == "steer"]
            chat.pending = [p for p in chat.pending if p["mode"] != "steer"]
        if not steers:
            return []
        self._publish_pending(chat)
        for item in steers:
            event: dict = {"type": "user", "text": item["text"],
                           "steer": True}
            if item.get("files"):
                event["files"] = item["files"]
            self.emit(chat, event)
        return steers

    def _take_next(self, chat: Chat) -> Optional[dict]:
        """The next waiting message to run as a turn, if any. A steer
        the turn ended before it could read comes first: it is older.
        Stopping emptied the list, so anything here was sent after it,
        and runs. Called with the lock held."""

        if not chat.pending:
            return None
        item = chat.pending.pop(0)
        chat.queued = True
        chat.stop.clear()
        return item

    def unqueue(self, chat: Chat) -> list[dict]:
        """Drop every waiting message, handing back the messages."""

        with self._lock:
            items, chat.pending = chat.pending, []
        if items:
            self._publish_pending(chat)
        return items

    def _run(
        self, chat: Chat, text: str, files: Optional[list] = None,
    ) -> None:
        # A local backend runs one generation at a time, and this turn
        # is the one somebody is now waiting on.
        learning.cancel()

        # One runner per chat, so its waiting messages go in the order
        # they were sent. The lock is let go between turns, so another
        # chat is not held up behind a long queue.
        following: Optional[dict] = {"text": text, "files": files or []}
        while following is not None:
            text, files = following["text"], following.get("files") or []
            with self.turn_lock:
                chat.queued = False
                chat.busy = True
                self.emit(chat, {"type": "busy", "busy": True})
                try:
                    found = (
                        workspace.project(chat.project)
                        if chat.project else None
                    )
                    if chat.stop.is_set():
                        self.emit(chat, {"type": "note", "text": "Stopped."})
                    else:
                        with inside(found.path if found else None):
                            run_turn(self, chat, text, found, files)
                except Exception as exc:  # noqa: BLE001
                    self.emit(chat, {
                        "type": "error",
                        "text": f"{exc.__class__.__name__}: {exc}",
                    })
                finally:
                    with self._lock:
                        following = self._take_next(chat)
                        chat.busy = False
                    chat.partial = chat.thinking = ""
                    self.save(chat)
                    self.emit(chat, {"type": "busy", "busy": False})
                    self.hub.publish({"type": "status"})
            if following is not None:
                self._publish_pending(chat)
                self._begin(
                    chat, following["text"], False,
                    following.get("files") or [],
                )


# --- A turn --------------------------------------------------------------


@dataclass
class Streamed:
    content: str = ""
    thinking: str = ""
    calls: list = field(default_factory=list)
    stopped: bool = False
    tokens: int = 0
    seconds: float = 0.0


def stream_reply(
    session: Session,
    chat: Chat,
    client: Any,
    messages: list,
    tools_arg: Optional[list],
) -> Streamed:
    """One model call, sent to the page a token at a time."""

    from . import ai  # deferred: ai imports half of Flash

    out = Streamed()
    content: list[str] = []
    thinking: list[str] = []

    parts = client.chat(
        model=ai.Config.model,
        messages=messages,
        tools=tools_arg,
        options=ai._chat_options(),
        stream=True,
    )

    try:
        for part in parts:
            # Leaving the loop closes the stream, which is what tells
            # Ollama to stop generating.
            if chat.stop.is_set():
                out.stopped = True
                break

            message = getattr(part, "message", None)
            thought = getattr(message, "thinking", "") or ""
            text = getattr(message, "content", "") or ""

            if thought:
                thinking.append(thought)
                chat.thinking += thought
                session.emit(chat, {"type": "thinking", "text": thought})
            if text:
                content.append(text)
                chat.partial += text
                session.emit(chat, {"type": "token", "text": text})

            out.calls.extend(getattr(message, "tool_calls", None) or [])

            if getattr(part, "done", False):
                out.tokens = int(getattr(part, "eval_count", 0) or 0)
                out.seconds = (
                    int(getattr(part, "eval_duration", 0) or 0) / 1e9
                )
    finally:
        close = getattr(parts, "close", None)
        if close is not None:
            close()

    out.content = "".join(content)
    out.thinking = "".join(thinking)
    return out


def _sink(session: Session, chat: Chat):
    """Where a tool's output goes: to the page, as it happens."""

    def sink(kind: str, text: str, style: str) -> None:
        if kind == "line":
            session.emit(chat, {"type": "tool", "label": text})
        elif kind == "result":
            session.emit(chat, {
                "type": "result", "text": text,
                "error": style == ERROR,
                "warn": style == WARN,
            })
        elif kind == "diff":
            session.emit(chat, {"type": "diff", "text": text})
        elif kind == "plan":
            session.emit(chat, {"type": "plan", "steps": json.loads(text)})
        elif kind == "file":
            try:
                kept = workspace.keep_file(text)
                session.emit(chat, {"type": "file", **kept})
            except (workspace.WorkspaceError, OSError) as exc:
                session.emit(chat, {
                    "type": "result", "text": f"Could not show it: {exc}",
                    "error": True,
                })

    return sink


def _finish_reply(session: Session, chat: Chat, reply: Streamed) -> None:
    """Record a reply the page has watched stream in."""

    chat.partial = chat.thinking = ""
    session.emit(chat, {
        "type": "assistant",
        "text": reply.content,
        "thinking": reply.thinking,
    })


def _snippet(flat: str, at: int) -> str:
    """The stretch of FLAT around AT, with the cut ends marked."""

    start = max(0, at - SNIPPET_BEFORE)
    end = min(len(flat), at + SNIPPET_AFTER)
    # Cut at a space rather than through a word.
    if start > 0:
        space = flat.find(" ", start, at)
        start = space + 1 if space != -1 else start
    if end < len(flat):
        space = flat.rfind(" ", at, end)
        end = space if space > at else end
    return (
        ("…" if start > 0 else "")
        + flat[start:end]
        + ("…" if end < len(flat) else "")
    )


@contextmanager
def inside(folder: Optional[str]):
    """Run a project's turn in its folder, then go back.

    One turn runs at a time, so for its length the process can simply
    be in the folder: every tool that takes a relative path, and every
    shell command, then works on the project without being told where
    it is.
    """

    if not folder:
        yield
        return

    if not os.path.isdir(folder):
        raise FileNotFoundError(
            f"the project's folder {folder} is not there any more"
        )

    before = os.getcwd()
    os.chdir(folder)
    try:
        yield
    finally:
        os.chdir(before)


def project_prompt(found: "workspace.Project") -> str:
    lines = [
        f"=== Project: {found.name} ===",
        f"You are working in {found.path}; relative paths start there.",
    ]
    if found.instructions:
        lines += ["", found.instructions]
    return "\n".join(lines)


def attachments(files: Optional[list]) -> list[dict]:
    """The files a message carries, as the page shows them: those that
    are really there, and no more than MAX_ATTACHMENTS."""

    found = []
    for file_id in (files or [])[:MAX_ATTACHMENTS]:
        info = workspace.upload_info(str(file_id))
        if info is not None:
            info.pop("path", None)
            found.append(info)
    return found


def outgoing(ai, text: str, files: Optional[list]) -> tuple[str, list]:
    """What the model gets for a message with attachments: its images
    alongside it, and any other file named by path, for its tools to
    open."""

    images: list[str] = []
    notes: list[str] = []
    for meta in files or []:
        info = workspace.upload_info(meta.get("id", ""))
        if info is None:
            continue
        if info["kind"] == "image":
            images.append(info["path"])
        else:
            notes.append(f"Attached file: {info['path']}")
    asked = text or (ai.DEFAULT_IMAGE_PROMPT if images else "")
    content = "\n\n".join(part for part in (asked, "\n".join(notes)) if part)
    return content, images


def run_turn(
    session: Session,
    chat: Chat,
    text: str,
    found: "Optional[workspace.Project]" = None,
    files: Optional[list] = None,
) -> None:
    """One exchange, with as many tool rounds as it takes.

    The same steps as the terminal's main loop, minus the drawing: the
    same system prompt, history budget, tools, undo point, and learning
    review, so a chat here and a chat in the terminal behave alike.
    """

    from . import ai  # deferred: ai imports half of Flash
    from . import tools as flash_tools

    if not ai.Config.model:
        session.emit(chat, {
            "type": "error",
            "text": "No model is set. Pick one with Alt+M or /model.",
        })
        return

    client = ollama.Client(host=ai.Config.host)
    started = time.monotonic()
    tokens = 0
    generating = 0.0

    # What this chat's sub-agents have done since, carried in the same
    # message the way the terminal carries it, so the model hears about
    # its own sub-agents and never another chat's.
    owned = session.owned(chat.id)
    news, delivered = subagents.notices(owned) if owned else ("", [])
    said, images = outgoing(ai, text, files)
    if images and not model_sees_images(ai.Config.host, ai.Config.model):
        session.emit(chat, {"type": "note", "text": (
            f"{ai.Config.model} reports no vision support; the image is "
            "sent anyway, but expect an error."
        )})
    content = "\n\n".join(part for part in (news, said) if part)
    chat.messages.append(ai._message("user", content, images or None))

    with capture_tool_output(_sink(session, chat)), \
            answer_from(session.answerer(chat)):
        ai._fit_and_compact(ai.console, client, chat.messages)
        prompt = ai._session_system_prompt()
        if found is not None:
            prompt = f"{prompt}\n\n{project_prompt(found)}".strip()
        system = ai._message("system", prompt)
        checkpoint.start_turn(ai._turn_label(text))

        offered = flash_tools.turn_tools()
        convo = [system, *chat.messages]
        keep_from = len(convo)
        tool_count = 0
        reply = Streamed()

        for _round in range(ai.Config.max_tool_rounds):
            reply = stream_reply(session, chat, client, convo, offered)
            tokens += reply.tokens
            generating += reply.seconds

            if reply.stopped or not reply.calls:
                break

            _finish_reply(session, chat, reply)

            named = [ai._tool_call_name_args(call) for call in reply.calls]
            convo.append({
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"function": {"name": name, "arguments": args}}
                    for name, args in named
                ],
            })

            for name, args in named:
                if chat.stop.is_set():
                    output = "Stopped by the user before this ran."
                elif name == "reason":
                    # It prints straight to the terminal, which is not
                    # where anyone is looking.
                    session.emit(chat, {
                        "type": "thought",
                        "text": str(args.get("thought", "")).strip(),
                    })
                    output = "(noted)"
                else:
                    before = {e.id for e in subagents.list_all()}
                    output = flash_tools.run_tool((name, args))
                    if name == "agent":
                        after = {e.id for e in subagents.list_all()}
                        session.adopt(chat, after - before)
                    tool_count += 1
                convo.append({
                    "role": "tool",
                    "content": flash_tools.trim_tool_output(output, name),
                    "tool_name": name,
                })

            images = flash_tools.take_pending_images()
            if images:
                convo.append(
                    ai._message("user", ai.TOOL_IMAGE_NOTE, images)
                )

            # Whatever the user sent to steer this turn, read before the
            # model decides its next step.
            for steer in session.take_steers(chat):
                said, shown = outgoing(ai, steer["text"], steer.get("files"))
                convo.append(ai._message(
                    "user", f"{STEER_NOTE}\n{said}", shown or None,
                ))

            if chat.stop.is_set():
                reply = Streamed(stopped=True)
                break
        else:
            # Out of tool rounds: one more call, with no tools, for the
            # answer from what the tools found.
            convo.append(ai._tool_limit_message())
            reply = stream_reply(session, chat, client, convo, None)
            tokens += reply.tokens
            generating += reply.seconds

    if reply.stopped and not reply.content:
        chat.partial = chat.thinking = ""
        session.emit(chat, {"type": "note", "text": "Stopped."})
    else:
        _finish_reply(session, chat, reply)

    chat.messages.extend(ai._worth_keeping(convo, keep_from))
    if reply.content:
        chat.messages.append(ai._message("assistant", reply.content))

    session.emit(chat, {
        "type": "stats",
        # When, and on what: the settings page counts days and models.
        "at": round(time.time()),
        "model": ai.Config.model or "",
        "tokens": tokens,
        "rate": round(tokens / generating, 1) if generating else 0,
        "seconds": round(time.monotonic() - started, 1),
        "tools": tool_count,
    })

    if delivered and not reply.stopped:
        subagents.mark_delivered(delivered)

    if not reply.stopped:
        learning.after_turn(
            chat.messages, tool_count,
            host=ai.Config.host, model=ai.Config.model or "",
        )


# --- Status and commands -------------------------------------------------


def ai_wake_note() -> str:
    from . import ai  # deferred: ai imports half of Flash

    return ai.WAKE_NOTE


def _streaks(days: set, today: date) -> tuple[int, int]:
    """The run of active days up to today, and the longest run."""

    longest = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous == day - timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day

    # A streak survives until a whole day goes by without a message,
    # so it still counts in the morning, before today's first one.
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)

    return current, longest


def usage(chats, today: Optional[date] = None) -> dict:
    """What the settings page's dashboard shows, counted from the
    saved web chats.

    Newer turns record when they ran and on which model;
    an older one counts on the day its chat was last used.
    """

    today = today or datetime.now(timezone.utc).astimezone().date()
    per_day: Counter = Counter()
    models: Counter = Counter()
    messages = turns = tokens = tools = 0
    seconds = generating = 0.0
    used = 0

    for chat in chats:
        log = list(chat.log)
        if log:
            used += 1
        for entry in log:
            if entry.get("type") == "user":
                messages += 1
            if entry.get("type") != "stats":
                continue
            turns += 1
            at = entry.get("at") or chat.updated
            # Days as this computer's clock has them, like the page's.
            day = datetime.fromtimestamp(at, timezone.utc).astimezone()
            per_day[day.date()] += 1
            if entry.get("model"):
                models[entry["model"]] += 1
            count = int(entry.get("tokens") or 0)
            rate = float(entry.get("rate") or 0)
            tokens += count
            tools += int(entry.get("tools") or 0)
            seconds += float(entry.get("seconds") or 0)
            if rate > 0:
                generating += count / rate

    current, longest = _streaks(set(per_day), today)

    return {
        "chats": used,
        "messages": messages,
        "turns": turns,
        "tokens": tokens,
        "tools": tools,
        "seconds": round(seconds),
        "rate": round(tokens / generating, 1) if generating else 0,
        "days": {day.isoformat(): n for day, n in sorted(per_day.items())},
        "active_days": len(per_day),
        "streak": current,
        "longest_streak": longest,
        "models": models.most_common(),
        "today": today.isoformat(),
        "memories": len(memory.list_memory()),
        "skills": len(skills.all_skills()),
        "projects": len(workspace.projects()),
    }


def status(ai) -> dict:
    """The facts the page shows beside the composer."""

    host = ai.Config.host
    named = next(
        (h["name"] for h in workspace.hosts(host)
         if workspace.host_key(h["url"]) == workspace.host_key(host)),
        host,
    )
    return {
        "version": __version__,
        "model": ai.Config.model or "",
        "host": host,
        # The entry in the host list that is in use, by the URL the list
        # gives it: 127.0.0.1 is listed as This computer's localhost.
        "host_url": workspace.listed_url(host),
        "host_name": named,
        "auto": bool(ai.Config.no_command_confirmation),
        "compact": bool(ai.Config.auto_compact),
        # The scene actually in effect: a name that no longer finds one,
        # because the extension that brought it was removed, is none.
        "background": (
            ai.Config.background
            if ai.Config.background and background.find(ai.Config.background)
            else ""
        ),
        "cwd": str(Path.cwd()),
        "folder": Path.cwd().name,
        "home": str(Path.home()),
        "learning": learning.running(),
        "agents": subagents.running_count(),
    }


def _normal(url: str) -> str:
    try:
        return workspace.normalize_host(url)
    except workspace.WorkspaceError:
        return url


def context_share(ai, chat: Chat) -> int:
    budget = ai._history_budget()
    if not budget or not chat.messages:
        return 0
    return min(100, round(100 * context.total_tokens(chat.messages) / budget))


# How long the page waits for a host to list its models. One that has
# not answered by then is reported as not answering, rather than
# holding the model menu shut until the connection gives up.
MODEL_LIST_SECONDS = 4.0


def list_models(ai) -> Optional[list[str]]:
    """The models on the current host, or None if it did not answer."""

    found: dict = {}

    def ask() -> None:
        try:
            found["listed"] = ollama.Client(host=ai.Config.host).list()
        except Exception:  # noqa: BLE001
            found["failed"] = True

    # On a thread of its own, so a host that never answers costs this
    # long and no longer; the thread gives up when the connection does.
    asking = threading.Thread(target=ask, daemon=True)
    asking.start()
    asking.join(MODEL_LIST_SECONDS)
    if "listed" not in found:
        return None

    models = getattr(found["listed"], "models", None) or []
    return sorted(
        str(getattr(m, "model", "") or "") for m in models
        if getattr(m, "model", "")
    )


def scene_data(name: str) -> Optional[dict]:
    """A background scene as the page draws it: its palette, and every
    pixel as an index into that palette, row by row. None when there is
    no such scene, or it cannot be read."""

    path = background.find(name) if name else None
    if path is None:
        return None
    try:
        scene = background.load(path)
    except background.SceneError:
        return None

    palette: list[str] = []
    index: dict[str, int] = {}
    pixels: list[int] = []
    for row in scene.rows:
        for colour in row:
            if colour not in index:
                index[colour] = len(palette)
                palette.append(colour)
            pixels.append(index[colour])

    return {
        "name": path.stem, "title": scene.name,
        "width": scene.width, "height": scene.height,
        "palette": palette, "pixels": pixels,
    }


def _extension_info(ext: "extensions.Extension") -> dict:
    return {
        "name": ext.name,
        "description": ext.description,
        "version": ext.version,
        "source": ext.source,
        "contents": ext.contents(),
        "commands": [f"/{c.name}" for c in ext.commands],
        "tools": [t.name for t in ext.tools],
    }


def _discard_staged(session: Session) -> None:
    if session.staged is not None:
        extensions.discard(session.staged[1])
        session.staged = None


def _stage_extension(session: Session, spec: str) -> dict:
    """Fetch an extension and say what it adds, installing nothing.

    The same checks as /extension install in the terminal: it has to be
    a real extension, and none of its names may already be taken. The
    fetched copy waits here for the user's yes.
    """

    from .repl_input import RESERVED_COMMANDS
    from .tools import FUNCTIONS

    _discard_staged(session)
    try:
        source = extensions.canonical(spec)
        checkout = extensions.fetch(spec)
    except (extensions.ExtensionError, OSError) as exc:
        raise ValueError(str(exc)) from None

    try:
        ext = extensions.load(checkout)
    except extensions.ExtensionError as exc:
        extensions.discard(checkout)
        raise ValueError(
            f"{source} is not a Flash extension: {exc}"
        ) from None

    clashes = extensions.clashes(
        ext, RESERVED_COMMANDS, frozenset(FUNCTIONS)
    )
    if clashes:
        extensions.discard(checkout)
        raise ValueError(
            f"Cannot install {ext.name}: " + "; ".join(clashes) + "."
        )

    staged_id = uuid.uuid4().hex[:12]
    session.staged = (staged_id, checkout, spec)
    existing = extensions.find(ext.name)

    return {
        **_extension_info(ext),
        "id": staged_id,
        "source": source,
        "update": existing is not None,
        "replaces": (
            existing.source
            if existing and existing.source and existing.source != source
            else ""
        ),
        "runs_programs": bool(ext.commands or ext.tools),
    }


def _install_staged(session: Session, staged_id: str) -> dict:
    if session.staged is None or session.staged[0] != staged_id:
        raise ValueError("Check the extension again before installing it.")

    _, checkout, spec = session.staged
    try:
        ext = extensions.install(checkout, spec)
    except (extensions.ExtensionError, OSError) as exc:
        raise ValueError(f"Could not install it: {exc}") from None
    finally:
        _discard_staged(session)

    session.hub.publish({"type": "status"})
    return _extension_info(ext)


def command(session: Session, body: dict) -> dict:
    """The page's commands: models, modes, chats, undo."""

    from . import ai  # deferred: ai imports half of Flash

    name = str(body.get("name", ""))
    arg = str(body.get("arg", "") or "").strip()
    chat_id = str(body.get("chat", "") or "")

    if name == "new":
        return {"chat": session.new_chat(str(body.get("project") or "")).id}

    if name == "delete":
        session.delete_chat(chat_id)
        return {}

    if name == "rename":
        chat = session.chat(chat_id)
        chat.title = arg[:TITLE_CHARS] or chat.title
        session.save(chat)
        session.hub.publish({"type": "chats", "chat": chat.id})
        return {}

    if name == "clear":
        session.clear_chat(session.chat(chat_id))
        return {}

    if name == "stop":
        chat = session.chat(chat_id)
        chat.stop.set()
        # What was waiting goes back to the page, to send again or not.
        items = session.unqueue(chat)
        return {
            "restored": [item["text"] for item in items],
            "files": [f for item in items for f in item.get("files") or []],
        }

    if name in ("pending-remove", "pending-mode"):
        chat = session.chat(chat_id)
        with session._lock:
            found = next((p for p in chat.pending if p["id"] == arg), None)
            if found is None:
                raise ValueError("That message has already been sent.")
            if name == "pending-remove":
                chat.pending.remove(found)
            else:
                found["mode"] = (
                    "steer" if body.get("mode") == "steer" else "queue"
                )
        session._publish_pending(chat)
        return {"pending": [dict(p) for p in chat.pending]}

    if name == "update-check":
        session.updates.check()
        return session.updates.snapshot()

    if name == "update":
        session.updates.start()
        return session.updates.snapshot()

    if name == "update-restart":
        if session.restart is None:
            raise ValueError(
                "This Flash runs beside a terminal session. Quit it there "
                "and start it again to use the new version."
            )
        if any(c.busy or c.queued for c in session.chats.values()):
            raise ValueError("Wait for the reply to finish first.")
        session.updates.restarting()
        # Late enough that this answer reaches the page first.
        threading.Timer(RESTART_DELAY, session.restart).start()
        return session.updates.snapshot()

    if name == "skills":
        return {"skills": [
            {
                "name": s.name,
                "description": s.description,
                "by": s.by,
                "size": len(s.body),
            }
            for s in skills.all_skills()
        ]}

    if name == "skill":
        found = skills.find(arg)
        if found is None:
            raise ValueError(f"No skill called {arg!r}.")
        return {
            "name": found.name, "description": found.description,
            "by": found.by, "content": found.body,
        }

    if name in ("skill-create", "skill-save", "skill-delete"):
        action = {
            "skill-create": "create", "skill-save": "rewrite",
            "skill-delete": "delete",
        }[name]
        try:
            message = skills.manage(
                action, arg,
                description=str(body.get("description") or ""),
                content=str(body.get("content") or ""),
                by="you",
            )
        except (skills.SkillError, OSError) as exc:
            raise ValueError(str(exc)) from None
        # The list of skills rides in the system prompt.
        learning.refresh()
        return {"message": message}

    if name == "extensions":
        return {
            "extensions": [_extension_info(e) for e in extensions.installed()],
            "problems": extensions.problems(),
        }

    if name == "extension-preview":
        return _stage_extension(session, arg)

    if name == "extension-install":
        return _install_staged(session, arg)

    if name == "extension-cancel":
        _discard_staged(session)
        return {}

    if name == "extension-remove":
        if not extensions.remove(arg):
            raise ValueError(f"No extension called {arg!r}.")
        # Its scenes, commands and prompt text went with it.
        session.hub.publish({"type": "status"})
        return {"removed": arg}

    if name == "lan":
        if session.switch_lan is None:
            raise ValueError("This Flash cannot reopen its server.")
        on = arg in ("on", "1", "true")
        if on == session.lan:
            return {"lan": on, "switching": False}
        session.switch_lan(on)
        return {"lan": on, "switching": True}

    if name == "backgrounds":
        listed = [scene_data(n) for n in background.names()]
        return {
            "current": ai.Config.background or "",
            "scenes": [scene for scene in listed if scene],
        }

    if name == "background-scene":
        return {"scene": scene_data(ai.Config.background or "")}

    if name == "background":
        if arg.lower() in ("", "off", "none"):
            ai.unset_config_var("BACKGROUND")
            session.hub.publish({"type": "status"})
            return {"background": "", "scene": None}
        scene = scene_data(arg)
        if scene is None:
            raise ValueError(f"No background called {arg!r}.")
        # The same setting /background writes, so the terminal and the
        # page show the same scene.
        ai.set_config_var("BACKGROUND", scene["name"])
        session.hub.publish({"type": "status"})
        return {"background": scene["name"], "scene": scene}

    if name == "compact-setting":
        on = arg in ("on", "1", "true")
        ai.set_config_var("AUTO_COMPACT", "1" if on else "0")
        session.hub.publish({"type": "status"})
        return {"compact": on}

    if name == "auto":
        on = (
            not ai.Config.no_command_confirmation
            if arg in ("", "toggle") else arg in ("on", "1", "true")
        )
        ai.set_config_var("NO_COMMAND_CONFIRMATION", "1" if on else "0")
        session.hub.publish({"type": "status"})
        return {"auto": on}

    if name == "model":
        if arg:
            ai.set_config_var("MODEL", arg)
            session.hub.publish({"type": "status"})
        models = list_models(ai)
        return {"model": ai.Config.model or "", "models": models or [],
                "reachable": models is not None}

    if name == "hosts":
        return {"hosts": workspace.hosts_with_health(ai.Config.host),
                "current": workspace.listed_url(ai.Config.host)}

    if name == "host":
        url = workspace.normalize_host(arg)
        ai.set_config_var("OLLAMA_HOST", url)
        ai.forget_model_facts()
        session.hub.publish({"type": "status"})
        # The quick check first: a host that is down has no models to
        # list, and asking would only make the switch wait.
        up = workspace.host_up(url)
        return {"host": url, "host_url": workspace.listed_url(url),
                "models": (list_models(ai) or []) if up else [], "up": up}

    if name == "host-add":
        added = workspace.add_host(str(body.get("label") or ""), arg)
        return {**added, "up": workspace.host_up(added["url"])}

    if name == "host-remove":
        if workspace.host_key(arg) == workspace.host_key(ai.Config.host):
            raise ValueError(
                "That host is in use. Switch to another one first."
            )
        return {"removed": workspace.remove_host(arg)}

    if name == "project-new":
        made = workspace.create_project(
            str(body.get("label") or ""), arg,
            str(body.get("instructions") or ""),
        )
        session.hub.publish({"type": "projects"})
        return asdict(made)

    if name == "project-update":
        changes = {
            key: str(body[key]) for key in ("name", "path", "instructions")
            if key in body
        }
        updated = workspace.update_project(arg, **changes)
        session.hub.publish({"type": "projects"})
        return asdict(updated)

    if name == "project-delete":
        workspace.delete_project(arg)
        session.hub.publish({"type": "projects"})
        return {}

    if name == "dirs":
        return {"dirs": workspace.folder_suggestions(arg)}

    if name == "undo":
        message = checkpoint.undo()
        if chat_id in session.chats:
            session.emit(session.chat(chat_id), {
                "type": "note", "text": message,
            })
        return {"message": message}

    if name == "usage":
        return usage(session.chats.values())

    if name == "memory":
        return {
            "entries": memory.list_memory(),
            "prompt": memory.IMPORT_PROMPT,
            "path": str(memory.MEMORY_PATH),
        }

    if name == "memory-preview":
        known = {e.lower() for e in memory.list_memory()}
        facts = memory.parse_import(str(body.get("text") or ""))
        return {
            "new": [f for f in facts if f.lower() not in known],
            "known": sum(f.lower() in known for f in facts),
        }

    if name == "memory-import":
        added, skipped = memory.import_memory(str(body.get("text") or ""))
        # Memory rides in the system prompt, which is kept as it was
        # when the server started until something asks for it again.
        learning.refresh()
        return {"added": added, "skipped": skipped,
                "entries": memory.list_memory()}

    if name in ("memory-add", "memory-edit"):
        # One line each: the file holds one fact per line.
        text = " ".join(str(body.get("text") or "").split())
        try:
            if name == "memory-add":
                if not text:
                    raise ValueError("Nothing to remember.")
                memory.add_memory(text)
            else:
                memory.edit_memory(int(arg), text)
        except IndexError as exc:
            raise ValueError(str(exc)) from None
        learning.refresh()
        return {"entries": memory.list_memory(), "text": text}

    if name == "memory-forget":
        try:
            memory.forget_memory(int(arg))
        except IndexError as exc:
            raise ValueError(str(exc)) from None
        learning.refresh()
        return {"entries": memory.list_memory()}

    if name == "context":
        return {"share": context_share(ai, session.chat(chat_id))}

    raise ValueError(f"unknown command {name!r}")


# --- HTTP ----------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """The page, the API, and the event stream."""

    server: "Server"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        """Quiet: the terminal running the server is not a log."""

    # Checks ---------------------------------------------------------

    def _host_allowed(self, host: str) -> bool:
        """Whether HOST names this server.

        A name that is not this machine is how a DNS rebinding attack
        looks: an attacker's domain, pointed at 127.0.0.1 after the
        page loaded. On the network, any IP address is fine, since it
        reached this server, and so is the machine's own name.
        """

        name, _, port = host.rpartition(":")
        if port != str(self.server.server_address[1]):
            return False

        name = name.strip("[]").lower()
        if name in ("127.0.0.1", "localhost", "::1"):
            return True
        if not self.server.lan:
            return False

        try:
            ipaddress.ip_address(name)
            return True
        except ValueError:
            pass

        machine = socket.gethostname().lower().removesuffix(".local")
        return name in (machine, machine + ".local")

    def _same_site(self) -> bool:
        host = self.headers.get("Host", "")
        if not self._host_allowed(host):
            return False

        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == host

    def _cookie(self) -> str:
        """The token this browser was given, if it was given one."""

        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        morsel = jar.get(self.server.cookie_name)
        return morsel.value if morsel else ""

    def _authorized(self, query: dict) -> bool:
        """Whether this request may use Flash.

        The token arrives one of three ways: in the link Flash printed,
        in a header the page adds, or in the cookie the link left behind,
        which is what lets a reload work once the page has taken the
        token out of the address bar.
        """

        if not self._same_site():
            return False

        for given in (
            self.headers.get("X-Flash-Token"),
            query.get("token", [""])[0],
            self._cookie(),
        ):
            if given and secrets.compare_digest(given, self.server.token):
                return True
        return False

    # Responses ------------------------------------------------------

    def _send(
        self,
        status: int,
        body: bytes,
        kind: str,
        headers: Optional[dict] = None,
    ) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if self.server.closing.is_set():
            # A server replaced after a LAN switch still answers what
            # arrives on a connection the browser kept open, then hangs
            # up, so the browser's next request reaches the new one.
            self.send_header("Connection", "close")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.server.closing.is_set():
            self.close_connection = True
        self.wfile.write(body)

    def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _refuse(self) -> None:
        self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)

    def _refuse_page(self) -> None:
        """A page, not JSON, for a person who opened a stale link."""

        self._send(HTTPStatus.FORBIDDEN, EXPIRED_PAGE.encode(),
                   "text/html; charset=utf-8")

    # Routes ---------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)

        name = url.path.removeprefix("/static/")
        if name in STATIC and url.path.startswith("/static/"):
            if not self._same_site():
                self._refuse()
                return
            self._send(HTTPStatus.OK, (WEB_DIR / name).read_bytes(),
                       STATIC[name])
            return

        if not self._authorized(query):
            if PAGE_PATHS.match(url.path):
                self._refuse_page()
            else:
                self._refuse()
            return

        if PAGE_PATHS.match(url.path):
            # The page, and a cookie holding the token: HttpOnly so no
            # script can read it, SameSite=Strict so no other site's
            # request carries it, and no expiry, so it goes when the
            # browser closes. A new server makes a new token, which the
            # old cookie no longer matches.
            cookie = (
                f"{self.server.cookie_name}={self.server.token}; Path=/; "
                "HttpOnly; SameSite=Strict"
            )
            self._send(
                HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8",
                {"Set-Cookie": cookie},
            )
        elif url.path == "/api/state":
            self._json(self.server.session.state(
                lite=query.get("lite", [""])[0] == "1"
            ))
        elif url.path == "/api/events":
            self._events()
        elif url.path.startswith("/api/files/"):
            self._file(url.path.removeprefix("/api/files/"),
                       download=query.get("download", [""])[0] == "1")
        elif url.path == "/api/agents":
            self._json({"agents": self.server.session.agent_list(
                query.get("chat", [""])[0]
            )})
        elif url.path == "/api/search":
            self._json({"results": self.server.session.search(
                query.get("q", [""])[0]
            )})
        elif url.path == "/api/qr":
            self._json(phone_link(self.server))
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        url = urlparse(self.path)

        if not self._authorized({}):
            self._refuse()
            return

        # JSON only. An HTML form on another site can post to any
        # address, but not as JSON, and a script there that tries is
        # stopped by the browser before the request is sent.
        kind = self.headers.get("Content-Type", "").split(";")[0].strip()
        if kind != "application/json":
            self._json({"error": "expected JSON"},
                       HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return

        length = int(self.headers.get("Content-Length") or 0)
        limit = (
            MAX_UPLOAD_BODY if url.path == "/api/upload" else MAX_BODY_BYTES
        )
        if length > limit:
            self._json(
                {"error": "too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            )
            return

        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected an object")
            self._json(self._post(url.path, body))
        except KeyError:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _post(self, path: str, body: dict) -> dict:
        session = self.server.session

        if path == "/api/send":
            chat_id = str(body.get("chat") or "")
            chat = (
                session.chat(chat_id) if chat_id else session.new_chat()
            )
            sent = session.send(
                chat, str(body.get("text", "")),
                mode=str(body.get("mode") or ""),
                files=[str(f) for f in body.get("files") or []],
            )
            return {"chat": chat.id, **sent}

        if path == "/api/upload":
            try:
                data = base64.b64decode(
                    str(body.get("data") or ""), validate=True
                )
            except (ValueError, TypeError):
                raise ValueError("that upload did not arrive whole") from None
            return workspace.keep_upload(str(body.get("name") or ""), data)

        if path == "/api/answer":
            return {
                "ok": session.answer(
                    str(body.get("id", "")), str(body.get("answer", ""))
                )
            }

        if path == "/api/command":
            return command(session, body)

        raise ValueError(f"unknown endpoint {path}")

    def _file(self, file_id: str, download: bool) -> None:
        """A file the agent showed, as the type it was stored as.

        Only types a browser shows are ever stored, and nosniff keeps it
        from guessing another. A web page is served sandboxed: it runs
        on an origin of its own, so its scripts cannot reach this one's
        cookie, its API, or the rest of the page. The name in a download
        comes from the page, which knows it.
        """

        kept = workspace.kept_file(file_id)
        if kept is None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        path, mime = kept
        try:
            data = path.read_bytes()
        except OSError:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        headers = {
            "Content-Disposition": "attachment" if download else "inline",
        }
        if mime == "text/html":
            headers["Content-Security-Policy"] = HTML_SANDBOX
        self._send(HTTPStatus.OK, data, mime, headers)

    def _events(self) -> None:
        """A Server-Sent Events stream of everything that happens."""

        queue = self.server.session.hub.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        # Once the stream ends the connection goes with it, rather than
        # waiting for another request, so the page sees it end. Set
        # after the headers: send_header("Connection", "keep-alive")
        # quietly turns this back off.
        self.close_connection = True

        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not self.server.closing.is_set():
                try:
                    event = queue.get(timeout=PING_SECONDS)
                except Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                data = f"data: {json.dumps(event)}\n\n".encode()
                self.wfile.write(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.session.hub.unsubscribe(queue)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        session: Optional[Session] = None,
        lan: bool = False,
    ):
        # Set before binding: a port already in use makes the base class
        # call server_close() from inside its own __init__.
        self.closing = threading.Event()
        super().__init__((LAN_HOST if lan else HOST, port), Handler)
        # A restart after an update keeps the token, so an open page
        # carries on signed in rather than being locked out.
        kept = os.environ.pop(TOKEN_ENV, "")
        self.token = kept if len(kept) >= 16 else secrets.token_urlsafe(24)
        # Cookies ignore the port, so two servers on one machine each
        # need a name of their own.
        self.cookie_name = f"flash_{self.server_address[1]}"
        self.session = session or Session()
        self.session.lan = lan
        self.lan = lan
        # A LAN switch in progress: the server that replaces this one,
        # once it is listening, and whether to leave the session open.
        self.swapping = False
        self.swapped = threading.Event()
        self.replacement: Optional[Server] = None
        self.keep_session = False

    @property
    def port(self) -> int:
        return self.server_address[1]

    def handle_error(self, request, client_address) -> None:
        """Say nothing when a browser simply hangs up.

        A reload, a closed tab, or a phone locking its screen all drop
        the connection mid-request. The base class prints a traceback
        for each into the terminal running Flash; anything else still
        gets one.
        """

        import sys

        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    @property
    def url(self) -> str:
        """The link for this machine's own browser."""

        return f"http://{HOST}:{self.port}/?token={self.token}"

    @property
    def network_url(self) -> Optional[str]:
        """The link for another device, when there is a network to use."""

        if not self.lan:
            return None
        return f"http://{lan_address()}:{self.port}/?token={self.token}"

    def server_close(self) -> None:
        self.closing.set()
        self.session.hub.close()
        if not self.keep_session:
            self.session.close()
        super().server_close()


def lan_address() -> str:
    """This machine's address on the local network.

    Connecting a UDP socket sends nothing; it only asks the system
    which interface it would route through, which is the one a phone
    on the same Wi-Fi can reach.
    """

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def qr_svg(url: str) -> str:
    """The QR code as an SVG that scales to whatever box holds it.

    omitsize gives it a viewBox rather than a fixed width and height.
    With a fixed size, the page shrinking it to its box clipped the
    right and bottom edges, quiet zone included, which a camera needs.
    """

    return segno.make(url, error="m").svg_inline(
        omitsize=True, dark="#000", light="#fff", border=2
    )


def qr_text(url: str) -> str:
    """The QR code as half blocks, for a terminal with a dark background."""

    out = io.StringIO()
    segno.make(url, error="m").terminal(out=out, compact=True, border=2)
    return out.getvalue()


def phone_link(server: "Server") -> dict:
    """What the page's "open on your phone" panel shows."""

    url = server.network_url
    return {
        "lan": url is not None,
        "url": url or "",
        "svg": qr_svg(url) if url else "",
    }


def announce(server: "Server") -> None:
    """Say where the web UI is, with a QR code for a phone."""

    line = Text("Flash web UI at ", style=DIM)
    line.append(server.url, style=f"bold {ACCENT}")
    console.print(line)

    if server.network_url is None:
        console.print(Text(
            "Only this machine can reach it, and only with that link. "
            "Add --lan (or /web lan) to open it on your phone.",
            style=DIM,
        ))
        return

    phone = Text("On your network: ", style=DIM)
    phone.append(server.network_url, style=f"bold {ACCENT}")
    console.print(phone)
    console.print(Text(qr_text(server.network_url), no_wrap=True,
                       overflow="ignore"))
    console.print(Text(
        "Scan it with your phone's camera. If the phone cannot connect, "
        "let Python accept incoming connections in your firewall (on a "
        "Mac: System Settings, Network, Firewall).",
        style=DIM,
    ))
    console.print(Text(
        "Anyone on this network who has the link can use Flash as you, "
        "and it travels unencrypted, so use --lan only on a network you "
        "trust.",
        style=WARN,
    ))


def _listen(
    port: int, lan: bool, session: Optional[Session] = None,
) -> "Server":
    try:
        return Server(port, session=session, lan=lan)
    except OSError as exc:
        raise OSError(
            f"could not listen on port {port} ({exc.strerror}); pass "
            "--port to pick another"
        ) from exc


# How long a restart waits, so the page hears it is coming.
RESTART_DELAY = 0.4


def _restart(server: "Server") -> None:
    """Start this `flash --web` again as the version now installed.

    exec replaces the process in place: the same port, the same
    arguments, and through TOKEN_ENV the same token, so an open page
    reloads straight into the new version. No second tab opens.
    """

    os.environ[TOKEN_ENV] = server.token
    # As it is now, which a LAN switch may have changed since it began.
    args = [a for a in sys.argv[1:] if a != "--lan"]
    if server.lan:
        args.append("--lan")
    if "--no-open" not in args:
        args.append("--no-open")
    console.print(Text("Restarting to finish the update.", style=DIM))
    os.execv(  # nosec B606 -- this same interpreter, running Flash again
        sys.executable, [sys.executable, "-m", "flash", *args]
    )


# How long a LAN switch waits, so the page hears the answer first.
SWITCH_DELAY = 0.4


def _switch_lan(server: "Server", lan: bool) -> None:
    """Open SERVER again, listening on the network or only here.

    Which interfaces a server listens on is fixed when it opens, so this
    closes it and opens another on the same port, with the same token
    and the same session: chats, running turns, and open pages all
    carry on, and the pages reconnect by themselves. If the new one
    cannot listen, the old choice is opened again instead.
    """

    server.swapping = True
    server.keep_session = True
    server.shutdown()
    server.server_close()
    server.session.reopen()

    for choice in (lan, server.lan):
        os.environ[TOKEN_ENV] = server.token
        try:
            server.replacement = Server(
                server.port, session=server.session, lan=choice,
            )
            break
        except OSError:
            os.environ.pop(TOKEN_ENV, None)

    server.swapped.set()
    if server.replacement is not None:
        announce(server.replacement)


def _attach(server: "Server", standalone: bool) -> None:
    """Give the page its handles on the process running SERVER."""

    global _background

    session = server.session
    session.switch_lan = lambda on: threading.Timer(
        SWITCH_DELAY, _switch_lan, (server, on),
    ).start()
    if standalone:
        if os.name != "nt":
            # Windows cannot replace a running Flash at all: its update
            # finishes after this one quits, so there is nothing to
            # restart.
            session.restart = lambda: _restart(server)
            session.updates.can_restart = True
    else:
        _background = server


def _serve(server: "Server", standalone: bool, running: list) -> None:
    """Serve until stopped, carrying on through LAN switches. RUNNING
    holds the server serving now, for whoever has to close it."""

    running[:] = [server]
    while True:
        server.serve_forever(poll_interval=0.5)
        if not server.swapping:
            return
        server.swapped.wait()
        if server.replacement is None:
            return
        server = server.replacement
        running[:] = [server]
        _attach(server, standalone)


def serve(
    port: int = DEFAULT_PORT, open_browser: bool = True, lan: bool = False
) -> None:
    """Run the web UI until Ctrl+C."""

    server = _listen(port, lan)
    announce(server)
    console.print(Text("Ctrl+C stops it.", style=DIM))

    _attach(server, standalone=True)
    server.session.updates.check_in_background()

    if open_browser:
        webbrowser.open(server.url)

    running = [server]
    try:
        _serve(server, True, running)
    except KeyboardInterrupt:
        console.print(Text("Stopped.", style=DIM))
    finally:
        running[0].server_close()


# The server /web started from inside a terminal session, if any.
_background: Optional[Server] = None


def start_background(
    port: Optional[int] = None, lan: bool = False
) -> Server:
    """Serve on a thread of its own, beside the terminal session.

    A second /web with a different --lan choice starts over, since
    which interfaces a server listens on is fixed when it opens.
    """

    if _background is not None and _background.lan == lan:
        return _background

    stop_background()
    server = _listen(DEFAULT_PORT if port is None else port, lan)
    _attach(server, standalone=False)
    server.session.updates.check_in_background()
    threading.Thread(
        target=_serve, args=(server, False, []), daemon=True,
    ).start()
    return server


def stop_background() -> bool:
    global _background

    if _background is None:
        return False

    _background.shutdown()
    _background.server_close()
    _background = None
    return True
