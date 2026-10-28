"""/compare: two models answer the same thing, blind.

Both answer the prompt at once. Their answers are shown as A and B in an
order chosen at random, with nothing saying which is which, and the user
picks the better, or calls it even. Only then are the names shown. Each
pick goes on a tally kept in ~/.flash/compare.json, so over time it says
which model does best for the things this user asks.
"""

from __future__ import annotations

import random
import re
import threading
import time
import uuid
from typing import Any, Optional

from . import btw, workspace

FILE = "compare.json"
USAGE = "Usage: /compare model-a vs model-b: what to ask them"
_lock = threading.Lock()
# Comparisons waiting for a pick, by id: kept only until it is made.
_open: dict[str, dict] = {}


def parse(text: str) -> tuple[list[str], str]:
    """The two models and the prompt in "/compare a vs b: prompt"."""

    text = re.sub(r"^/compare\b", "", str(text or "").strip()).strip()
    found = re.match(r"^(\S+)\s+vs\.?\s+(\S+?)[:;]\s+(.+)$", text, re.S)
    if not found:
        raise ValueError(USAGE)
    return [found.group(1), found.group(2)], found.group(3).strip()


def run(client: Any, models: list[str], prompt: str,
        options: Optional[dict] = None, rng: Optional[random.Random] = None
        ) -> dict:
    """Both models' answers, side by side in a random order: {id,
    prompt, answers: [a, b]}. Who wrote which waits for pick()."""

    asked = [{"role": "user", "content": prompt}]
    answers = ["", ""]
    failed: list = []

    def answer(i: int) -> None:
        try:
            answers[i] = btw.ask(client, models[i], asked, options)
        except Exception as exc:  # noqa: BLE001  (said as its answer)
            failed.append(models[i])
            answers[i] = f"(It could not answer: {exc})"

    threads = [threading.Thread(target=answer, args=(i,)) for i in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    order = [0, 1]
    (rng or random.Random()).shuffle(order)  # nosec B311 -- for blindness
    compare_id = uuid.uuid4().hex[:8]
    with _lock:
        _open[compare_id] = {"models": [models[i] for i in order],
                             "prompt": prompt, "at": time.time()}
    return {"id": compare_id, "prompt": prompt,
            "answers": [answers[i] for i in order]}


def tally() -> dict[str, dict]:
    """Every model's record: {model: {"wins", "losses", "ties"}}."""

    found = workspace._read(FILE, {})
    return found if isinstance(found, dict) else {}


def pick(compare_id: str, choice: str) -> dict:
    """The user's pick, "a", "b" or "tie": which model was which, and the
    tally after it."""

    with _lock:
        held = _open.pop(compare_id, None)
    if held is None:
        raise ValueError("That comparison is gone: run it again.")
    choice = str(choice or "").strip().lower()
    if choice not in ("a", "b", "tie"):
        raise ValueError("Pick a, b or tie.")
    a, b = held["models"]
    with _lock:
        board = tally()
        for name in (a, b):
            board.setdefault(name, {"wins": 0, "losses": 0, "ties": 0})
        if choice == "tie":
            board[a]["ties"] += 1
            board[b]["ties"] += 1
        else:
            won, lost = (a, b) if choice == "a" else (b, a)
            board[won]["wins"] += 1
            board[lost]["losses"] += 1
        workspace._write(FILE, board)
    return {"a": a, "b": b, "choice": choice, "tally": board}


def standings() -> list[str]:
    """The tally as lines, best first."""

    rows = []
    for name, rec in tally().items():
        played = rec["wins"] + rec["losses"] + rec["ties"]
        score = (rec["wins"] + rec["ties"] / 2) / played if played else 0
        rows.append((score, played, name, rec))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    return [f"{name}: {rec['wins']} won, {rec['losses']} lost, "
            f"{rec['ties']} even ({score:.0%})"
            for score, _, name, rec in rows]
