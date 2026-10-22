"""Sparks started by what happens: a new email, a new iMessage, a change
in a connected MCP server (a new note in a notes app, say).

A spark that checks for these on a schedule spends a shift, and its
tokens, on every look, mostly to find nothing. With this on, the keeper
looks instead, without the model: the mail server is asked what uid the
next email gets, Messages' own database for its newest message, and an
MCP server's read-only tool is called and its answer compared. Only
when something has come in does a shift start, told what it was, to
handle it as the spark's goal says.

Off until turned on (SPARK_EVENTS), and then only for the sparks given
events to start on. The first look at each only learns where things
stand: what was already there starts nothing.
"""

import hashlib
import os
import time
from typing import Optional

SETTING = "SPARK_EVENTS"
EMAIL = "email"
IMESSAGE = "imessage"
MCP = "mcp:"
# How often each is looked at, in seconds: the mail server is asked over
# the network, the others are on this computer.
EVERY = {EMAIL: 60.0, IMESSAGE: 15.0, MCP: 30.0}
# How much of what changed a shift is told.
TOLD_CHARS = 3000
MOST_EMAILS = 10


def enabled() -> bool:
    from .notify import _saved

    return _saved(SETTING).lower() in ("1", "on", "true", "yes")


def kinds() -> list[dict]:
    """What a spark can start on here, for the form: email, iMessages on
    a Mac, and each MCP server set up."""

    from . import computer, connectors

    found = [{"event": EMAIL, "title": "A new email"}]
    if computer.system() == "mac":
        found.append({"event": IMESSAGE, "title": "A new iMessage"})
    for name in connectors.read_config():
        found.append({"event": MCP + name, "title": f"A change in {name}"})
    return found


def parse(events) -> list[str]:
    """EVENTS as a spark keeps them: email, imessage, or mcp:<server>
    (mcp:<server>/<tool> to say which tool to watch), each once. Raises
    ValueError for one it does not know."""

    if isinstance(events, str):
        events = events.replace(",", " ").split()
    found: list[str] = []
    for event in events or []:
        event = str(event).strip().lower()
        if not event:
            continue
        if event in ("mail", "emails"):
            event = EMAIL
        if event in ("imessages", "messages", "texts"):
            event = IMESSAGE
        if event not in (EMAIL, IMESSAGE) and not (
            event.startswith(MCP) and len(event) > len(MCP)
        ):
            raise ValueError(
                f"{event!r} is not something to start on. One of: email, "
                "imessage, or mcp:<server>."
            )
        if event not in found:
            found.append(event)
    return found


def words(event: str) -> str:
    """EVENT as the user would say it."""

    if event == EMAIL:
        return "a new email"
    if event == IMESSAGE:
        return "a new iMessage"
    return f"a change in {event[len(MCP):].split('/')[0]}"


class Watcher:
    """Where each event stood at the last look, and what came in since.
    One lives in the keeper; the first look at each only learns."""

    def __init__(self) -> None:
        self.seen: dict[str, object] = {}
        self.looked: dict[str, float] = {}

    def look(self, events: set[str],
             now: Optional[float] = None) -> dict[str, str]:
        """What came in for each of EVENTS that is due a look, described
        for a shift: only the ones where something did."""

        now = time.time() if now is None else now
        for gone in set(self.seen) - events:
            self.seen.pop(gone, None)
            self.looked.pop(gone, None)
        news: dict[str, str] = {}
        for event in sorted(events):
            kind = event if event in EVERY else MCP
            if event in self.looked and \
                    now - self.looked[event] < EVERY[kind]:
                continue
            self.looked[event] = now
            try:
                said = self._look(event)
            except Exception:  # noqa: BLE001  (a source that fails waits)
                said = ""
            if said:
                news[event] = said[:TOLD_CHARS]
        return news

    def _look(self, event: str) -> str:
        if event == EMAIL:
            return self._email()
        if event == IMESSAGE:
            return self._imessage()
        return self._mcp(event)

    def _email(self) -> str:
        from . import mail

        if not mail.configured():
            return ""
        now = mail.next_uids()
        before = self.seen.get(EMAIL)
        if not isinstance(before, dict):
            self.seen[EMAIL] = now
            return ""
        # An account that could not be asked keeps where it stood.
        self.seen[EMAIL] = {**before, **now}
        lines = []
        for address, next_uid in now.items():
            was = before.get(address)
            if was is None or next_uid <= was:
                continue
            try:
                found = mail.inbox(limit=MOST_EMAILS, account=address)
            except mail.MailError:
                continue
            for message in reversed(found):
                if int(message["uid"] or 0) >= was:
                    lines.append(
                        f"- From {message['from']}: {message['subject']} "
                        f"(uid {message['uid']} in {address}): "
                        f"{message['snippet']}"
                    )
        return ("New email:\n" + "\n".join(lines)) if lines else ""

    def _imessage(self) -> str:
        from . import computer

        if not computer.allowed("imessage_read"):
            return ""
        newest = computer.newest_imessage()
        before = self.seen.get(IMESSAGE)
        self.seen[IMESSAGE] = newest
        if not isinstance(before, int) or newest <= before:
            return ""
        lines = computer.imessages_after(before)
        return ("New iMessages:\n" + "\n".join(f"- {line}" for line in lines)
                if lines else "")

    def _mcp(self, event: str) -> str:
        from . import connectors

        name, _, wanted = event[len(MCP):].partition("/")
        connectors.start()
        server = next(
            (s for s in connectors.servers() if s.name.lower() == name
             and s.state == connectors.READY), None,
        )
        if server is None:
            return ""
        tool = _watched_tool(server.tools, wanted)
        if tool is None:
            return ""
        text, _ = connectors.call(server, tool, {})
        if text.startswith("Error:"):
            return ""
        lines = [line for line in text.splitlines() if line.strip()]
        stamp = hashlib.sha256(text.encode()).hexdigest()
        before = self.seen.get(event)
        self.seen[event] = (stamp, set(lines))
        if not isinstance(before, tuple) or before[0] == stamp:
            return ""
        new = [line for line in lines if line not in before[1]]
        if not new:
            return ""
        return (f"New in {server.name} (from its {tool['name']} tool):\n"
                + "\n".join(new))


def _watched_tool(tools: list[dict], wanted: str) -> Optional[dict]:
    """The tool to watch a server by: the one WANTED names, or else its
    first that only reads and needs nothing given to it."""

    from . import connectors

    for tool in tools:
        if wanted and str(tool.get("name", "")).lower() == wanted:
            return tool
    if wanted:
        return None
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        if connectors.read_only(tool) and not schema.get("required"):
            return tool
    return None


def set_enabled(on: bool) -> None:
    from .envfile import set_env_var
    from .paths import ENV_PATH

    os.environ[SETTING] = "1" if on else "0"
    set_env_var(ENV_PATH, SETTING, os.environ[SETTING])
