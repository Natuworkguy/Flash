"""What Flash has done for its user, and what it can do that they may
not know about yet.

A small tally of every reply, kept across the terminal and the web UI
alike: how many, how many tokens, and how many of them never left the
user's own machines. From it come the numbers on Settings > Usage and
/stats. Beside it, tips: one real feature at a time, shown while the
model is working and changed every TIP_SECONDS, and never at all with
SHOW_TIPS=0.

Every claim here is checked against what actually ran. A turn counts as
private only when the server is on this computer or the local network
and the model is not one of Ollama's cloud models, whose prompts do go
to Ollama's servers.
"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .paths import FLASH_DIR

# What the estimate of what the same tokens would cost elsewhere assumes:
# a middling price for a hosted model, input and output blended. It is
# said out loud wherever the estimate is shown.
PRICE_PER_MILLION = 5.0

# How long each tip stays up while the model works.
TIP_SECONDS = 30

# Real features: what each does, how to try it in the terminal, and in
# the web UI a prompt to try it with ("" where it is a button there, or
# the words say enough). In order, so none is seen twice before every
# one has been seen once.
TIPS = (
    ("Sparks keep working while you are away: give one a goal and a "
     "schedule.", "/sparks new", ""),
    ("Undo takes back every file the last turn changed, new files "
     "included.", "/undo", ""),
    ("Ask for a 3D model of anything, and turn it in the viewer.",
     "make me a 3D model of a desk lamp",
     "Make me a 3D model of a desk lamp."),
    ("Ask for a tune or a drum beat, and play it in a piano-roll player.",
     "write me a lo-fi drum beat", "Write me a lo-fi drum beat."),
    ("Talk to Flash and hear it answer.", "/voice on", ""),
    ("Use Flash from your phone over your Wi-Fi.", "/web lan", ""),
    ("System One double-checks every command in autonomous mode.",
     "/systemone on", ""),
    ("Flash remembers what you ask it to, from one session to the next.",
     "remember that I prefer tabs", "Remember that I prefer tabs."),
    ("Record your screen once in the web UI, and Flash learns the steps "
     "as a skill.", "flash --web", ""),
    ("Sub-agents work on separate parts of a task at the same time.",
     "/agents", ""),
    ("Point Flash at a bigger GPU on your network, and switch back any "
     "time.", "/set OLLAMA_HOST 10.0.0.5:11434", ""),
    ("Flash reads PDFs and Word files, and makes them too.",
     "summarise the newest PDF in my Downloads",
     "Summarise the newest PDF in my Downloads."),
)

_lock = threading.Lock()


def _path() -> Path:
    return FLASH_DIR / "tally.json"


def _read() -> dict:
    try:
        found = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    return found if isinstance(found, dict) else {}


def _write(tally: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(tally), encoding="utf-8")
        os.replace(partial, path)
    except OSError:
        pass


def is_private(host: str, model: str) -> bool:
    """Whether a turn on MODEL at HOST stayed on the user's own machines:
    a server on this computer or the local network, and not one of
    Ollama's cloud models, which run on Ollama's servers."""

    # A provider's model runs wherever its provider sends it, which
    # Flash cannot see, so it is never counted as private.
    if (model or "").startswith("@"):
        return False
    name = (model or "").lower()
    tag = name.rpartition(":")[2]
    if tag == "cloud" or tag.endswith("-cloud") or name.endswith("-cloud"):
        return False
    host = (host or "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host
    hostname = (urlparse(host).hostname or "").lower()
    if hostname in ("localhost", "") or hostname.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def record(host: str, model: str, tokens: int, tools: int = 0) -> None:
    """Count one finished reply."""

    private = is_private(host, model)
    with _lock:
        tally = _read()
        tally.setdefault("since", round(time.time()))
        tally["turns"] = int(tally.get("turns", 0)) + 1
        tally["tokens"] = int(tally.get("tokens", 0)) + max(0, int(tokens))
        tally["tools"] = int(tally.get("tools", 0)) + max(0, int(tools))
        if private:
            tally["private_turns"] = int(tally.get("private_turns", 0)) + 1
            tally["private_tokens"] = (
                int(tally.get("private_tokens", 0)) + max(0, int(tokens))
            )
        _write(tally)


def totals() -> dict:
    """The tally so far, with the share that stayed private and what the
    same tokens would cost at PRICE_PER_MILLION."""

    tally = _read()
    turns = int(tally.get("turns", 0))
    tokens = int(tally.get("tokens", 0))
    private_tokens = int(tally.get("private_tokens", 0))
    return {
        "since": int(tally.get("since", 0)),
        "turns": turns,
        "tokens": tokens,
        "tools": int(tally.get("tools", 0)),
        "private_turns": int(tally.get("private_turns", 0)),
        "private_tokens": private_tokens,
        "private_share": (
            round(100 * int(tally.get("private_turns", 0)) / turns)
            if turns else 100
        ),
        "saved": round(private_tokens / 1_000_000 * PRICE_PER_MILLION, 2),
        "price": PRICE_PER_MILLION,
    }


def tips_on() -> bool:
    try:
        return int(os.environ.get("SHOW_TIPS", "1") or 1) > 0
    except ValueError:
        return True


def tip_now(now: Optional[float] = None) -> Optional[tuple[str, str]]:
    """The tip to show under the terminal's spinner at NOW, as what it
    does and how to try it, or None with SHOW_TIPS=0. Each is up for
    TIP_SECONDS, by the clock, so one wait running into the next keeps
    its tip instead of starting another."""

    if not tips_on():
        return None
    now = time.time() if now is None else now
    what, terminal, _ = TIPS[int(now // TIP_SECONDS) % len(TIPS)]
    return what, terminal


def web_tips() -> list[list[str]]:
    """The tips for the web UI: what each does, and a prompt to try it
    with, or "" where there is none to type. None with SHOW_TIPS=0."""

    return [[what, prompt] for what, _, prompt in TIPS] if tips_on() else []
