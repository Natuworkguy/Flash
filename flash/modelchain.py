"""Which model answers: fallbacks, and Auto.

Fallbacks: a model can have others to try, in order, when it fails (its
host is down, its provider errors, it is not there): "if
@hosted/big-model fails, try @other/big-model, then onyx:31b, then
fail". Each model has its own chain, and "*" is the chain for any model
without one. A model that fails partway through an answer is not
swapped out: only one that fails before it says anything.

Auto: the model called "auto" is not one. Each time the user says
something, System One picks which of the models offered to it should
answer: the user's list (AUTO_MODELS), or the models pinned to the top,
or the default. Without System One, the first of them answers. The pick
holds for the whole turn, every step of it.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from typing import Any, Optional

from . import workspace

AUTO = "auto"
ANY = "*"
FILE = "model-fallbacks.json"
MOST_FALLBACKS = 6
# The models Auto picks between, when the user names them: a comma list.
AUTO_SETTING = "AUTO_MODELS"
MOST_AUTO = 8

_lock = threading.Lock()
# What Auto picked for each message, so every step of a turn keeps it.
_picks: OrderedDict[str, str] = OrderedDict()
PICKS_KEPT = 64


def is_auto(model: Any) -> bool:
    return str(model or "").strip().lower() == AUTO


# Fallbacks ----------------------------------------------------------------


def all_fallbacks() -> dict[str, list[str]]:
    found = workspace._read(FILE, {})
    if not isinstance(found, dict):
        return {}
    return {str(k): [str(m) for m in v if str(m).strip()]
            for k, v in found.items() if isinstance(v, list) and v}


def fallbacks(model: str) -> list[str]:
    """What to try, in order, when MODEL fails: its own chain, or the
    one for every model. Never MODEL itself, nor one twice."""

    chains = all_fallbacks()
    chain = chains.get(model) or chains.get(ANY) or []
    out: list[str] = []
    for name in chain:
        if name != model and name not in out and not is_auto(name):
            out.append(name)
    return out[:MOST_FALLBACKS]


def set_fallbacks(model: str, chain: list[str]) -> list[str]:
    """MODEL's chain is CHAIN from now on; empty clears it."""

    model = str(model or "").strip()
    if not model:
        raise ValueError("Name the model the fallbacks are for, or * for "
                         "every model.")
    clean: list[str] = []
    for name in chain:
        name = str(name or "").strip()
        if name and name != model and name not in clean:
            if is_auto(name):
                raise ValueError("Auto cannot be a fallback.")
            clean.append(name)
    if len(clean) > MOST_FALLBACKS:
        raise ValueError(f"At most {MOST_FALLBACKS} fallbacks.")
    with _lock:
        chains = all_fallbacks()
        if clean:
            chains[model] = clean
        else:
            chains.pop(model, None)
        workspace._write(FILE, chains)
    return clean


def parse_chain(text: str) -> tuple[str, list[str]]:
    """"a > b > c" (or "a -> b, c") as ("a", ["b", "c"])."""

    parts = [p.strip() for p in
             str(text or "").replace("->", ">").replace(",", ">").split(">")]
    parts = [p for p in parts if p]
    if len(parts) < 1:
        raise ValueError("Write it as model > first fallback > second.")
    return parts[0], parts[1:]


def describe(model: str) -> str:
    chain = fallbacks(model)
    return " > ".join([model, *chain]) if chain else model


# Auto ---------------------------------------------------------------------


def auto_models(host: str, default: str = "") -> list[str]:
    """The models Auto picks between: AUTO_MODELS, else the pinned ones,
    else the default alone."""

    named = [m.strip() for m in os.getenv(AUTO_SETTING, "").split(",")
             if m.strip() and not is_auto(m)]
    if not named:
        named = [m for m in workspace.favorite_models(host)
                 if not is_auto(m)]
    if default and not is_auto(default) and default not in named:
        named.append(default)
    return named[:MOST_AUTO]


def _last_asked(messages: Any) -> str:
    for message in reversed(list(messages or [])):
        role = message.get("role") if isinstance(message, dict) \
            else getattr(message, "role", "")
        if role != "user":
            continue
        content = message.get("content") if isinstance(message, dict) \
            else getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


PICK_QUESTION = (
    "Which model should answer the user's latest message? Pick the "
    "smallest, quickest one that will do it well: a bigger or hosted one "
    "only for hard reasoning, long or tricky code, careful writing, or "
    "many steps of work; a small local one for chat, quick facts, and "
    "simple edits."
)


def pick(host: str, messages: Any, default: str = "") -> tuple[str, str]:
    """The model Auto picks to answer MESSAGES, and how it was picked
    ("" when it was already picked for this message)."""

    from . import systemone

    offered = auto_models(host, default)
    if not offered:
        raise ValueError("Auto has no models to pick from: pin some to the "
                         "top of the model menu, or set AUTO_MODELS.")
    asked = _last_asked(messages)
    key = hashlib.sha256(f"{host}\n{','.join(offered)}\n{asked}".encode())\
        .hexdigest()
    with _lock:
        if key in _picks:
            _picks.move_to_end(key)
            return _picks[key], ""
    picked, how = offered[0], "the first of its models"
    if len(offered) > 1 and systemone.enabled() and systemone.model():
        try:
            answer = systemone.ask(
                host, {"request": asked[:4000] or "(nothing said)"},
                {"pick": {"type": "choice", "instructions": PICK_QUESTION,
                          "criteria": {m: m for m in offered}}},
            ).get("pick") or {}
            chosen = str(answer.get("choice") or "")
            if chosen in offered:
                picked, how = chosen, "System One"
        except systemone.SystemOneError:
            how = "the first of its models (System One did not answer)"
    with _lock:
        _picks[key] = picked
        while len(_picks) > PICKS_KEPT:
            _picks.popitem(last=False)
    return picked, how


def forget_picks() -> None:
    with _lock:
        _picks.clear()


def resolve(host: str, model: str, messages: Any,
            default: str = "") -> tuple[str, Optional[str]]:
    """MODEL as it is, or with Auto the one picked, and how it was
    picked when that was just now (None otherwise)."""

    if not is_auto(model):
        return model, None
    picked, how = pick(host, messages, default)
    return picked, how or None


def notes(client: Any) -> list[str]:
    """What CLIENT did of its own accord (providers.Client.take_notes):
    Auto's pick, or the models that failed and the one that answered,
    in words."""

    taken = getattr(client, "take_notes", None)
    out = []
    for note in taken() if callable(taken) else []:
        if note.get("kind") == "auto":
            out.append(f"Auto picked **{note['model']}** ({note['how']}).")
        elif note.get("kind") == "fallback":
            gone = ", ".join(f"{name} ({why})"
                             for name, why in note.get("failed", []))
            out.append(f"{gone} failed, so **{note['model']}** answered.")
    return out
