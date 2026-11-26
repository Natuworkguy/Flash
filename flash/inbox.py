"""The inbox: everything background work left for the user, in one place.

Sparks work on their schedules and flows run on theirs, often with the
user away from the computer. What they leave behind lands here, newest
first, with what waits on the user at the top: a step a spark will not
take without a yes, an approval a flow stopped at, and the reports filed
since the user last looked. Each is answered or opened with one tap, from
the computer or a phone, and the page says it out loud as a notification
when something new comes in.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

REPORT_DAYS = 7
REPORT_CHARS = 700
DETAIL_CHARS = 600


def items() -> list[dict]:
    """What waits on the user, then what came in, newest first."""

    from . import flows, sparks

    asks: list[dict] = []
    news: list[dict] = []
    for spark in sparks.all_sparks():
        who = {"spark": spark.id, "name": spark.name, "colour": spark.colour}
        if spark.waiting:
            p = spark.pending
            asks.append({"kind": "approval", "source": "spark", "id": f"spark:{spark.id}", **who,
                         "ask": str(p.get("label") or "A step that needs your yes"),
                         "detail": " ".join(str(p.get("detail") or "").split())[:DETAIL_CHARS],
                         "at": float(p.get("at") or time.time())})
        since = time.time() - REPORT_DAYS * 86400
        for i, r in enumerate(spark.reports):
            if r.read or r.quiet or r.approval or r.at < since:
                continue
            news.append({"kind": "report", "source": "spark", "id": f"report:{spark.id}:{i}", **who,
                         "text": r.text[:REPORT_CHARS] + ("…" if len(r.text) > REPORT_CHARS else ""),
                         "failed": bool(r.failed), "at": r.at})
    for flow in flows.all_flows():
        run = flow["runs"][-1] if flow.get("runs") else None
        if not run:
            continue
        for step in flow["steps"]:
            st = (run.get("steps") or {}).get(step["id"]) or {}
            if step.get("kind") == "approval" and st.get("state") == "asking":
                before = [run["steps"].get(a, {}).get("output", "") for a in step.get("after") or []]
                asks.append({"kind": "approval", "source": "flow", "id": f"flow:{flow['id']}:{step['id']}",
                             "flow": flow["id"], "step": step["id"], "name": flow["name"], "colour": "",
                             "ask": step.get("task") or "Carry on?",
                             "detail": " ".join(" ".join(before).split())[:DETAIL_CHARS],
                             "at": float(st.get("at") or time.time())})
    for item in extension_items():
        (asks if item["kind"] == "approval" else news).append(item)
    asks.sort(key=lambda i: -i["at"])
    news.sort(key=lambda i: -i["at"])
    return asks + news


# ----- what extensions put in the inbox -----
# Two ways in. An extension's own code says something whenever it likes:
# notify() (Python: `from flash import inbox`), or Flash.inbox() from its
# page. Or its manifest names an "inbox" program, run when the inbox is
# read, that writes a JSON list of items. Either kind of item is a note
# (put away with Done) or, with "ask", a yes or no: the answer is kept
# for the extension's code (answer_of) and handed to its program.
STORE = "inbox-extensions.json"
KEPT = 200
SEEN_KEPT = 1000
PROGRAM_EVERY = 30.0
_lock = threading.Lock()
_program_cache: dict[str, Any] = {"at": 0.0, "items": []}


def _load() -> dict:
    from . import workspace

    found = workspace._read(STORE, {})  # pylint: disable=W0212
    found = found if isinstance(found, dict) else {}
    return {"items": [i for i in found.get("items") or [] if isinstance(i, dict)],
            "seen": [str(i) for i in found.get("seen") or []],
            "answers": dict(found.get("answers") or {})}


def _save(data: dict) -> None:
    from . import workspace

    data["items"] = data["items"][-KEPT:]
    data["seen"] = data["seen"][-SEEN_KEPT:]
    workspace._write(STORE, data)  # pylint: disable=W0212


def _clean(source: str, raw: dict, now: float) -> Optional[dict]:
    """One item as an extension gave it, checked and cut to size."""

    title = " ".join(str(raw.get("title") or "").split())[:120]
    body = str(raw.get("body") or raw.get("text") or "").strip()
    if not title and not body:
        return None
    url = str(raw.get("url") or "")
    if url and not url.startswith(("https://", "http://", "/")):
        url = ""
    colour = str(raw.get("colour") or raw.get("color") or "")
    if colour and not (colour.startswith("#") and len(colour) in (4, 7)):
        colour = ""
    try:
        at = float(raw.get("at") or now)
    except (TypeError, ValueError):
        at = now
    return {"source_name": str(source or "extension")[:60],
            "label": " ".join(str(raw.get("label") or source or "Extension").split())[:60],
            "id": str(raw.get("id") or f"{int(now * 1000)}")[:120], "title": title,
            "body": body[:REPORT_CHARS * 3], "detail": " ".join(str(raw.get("detail") or "").split())[:DETAIL_CHARS],
            "url": url, "colour": colour, "ask": bool(raw.get("ask")), "at": at}


def notify(source: str, title: str, body: str = "", *, id: str = "", url: str = "",  # pylint: disable=W0622
           ask: bool = False, detail: str = "", colour: str = "", at: Optional[float] = None) -> str:
    """Put a note in the inbox from SOURCE (an extension's name), or with
    ASK a question the user answers yes or no. One with the same ID
    replaces the last. Returns the item's id, for answer_of and
    withdraw."""

    now = time.time()
    item = _clean(source, {"title": title, "body": body, "id": id, "url": url, "ask": ask,
                           "detail": detail, "colour": colour, "at": at or now}, now)
    if item is None:
        raise ValueError("an inbox item needs a title or a body")
    with _lock:
        data = _load()
        key = (item["source_name"], item["id"])
        data["items"] = [i for i in data["items"] if (i.get("source_name"), i.get("id")) != key] + [item]
        full = _full_id(item)
        data["seen"] = [s for s in data["seen"] if s != full]
        data["answers"].pop(full, None)
        _save(data)
    return item["id"]


def withdraw(source: str, item_id: str) -> None:
    """Take an item SOURCE put in the inbox back out (it no longer
    matters)."""

    with _lock:
        data = _load()
        data["items"] = [i for i in data["items"] if (i.get("source_name"), i.get("id")) != (source, str(item_id))]
        _save(data)


def answer_of(source: str, item_id: str) -> Optional[dict]:
    """The user's answer to a question SOURCE asked: {"yes", "why", "at"},
    or None while it waits."""

    return _load()["answers"].get(f"ext:{source}:{item_id}")


def _full_id(item: dict) -> str:
    return f"ext:{item['source_name']}:{item['id']}"


def _program_items(now: float) -> list[dict]:
    """What the extensions with an inbox program have now: run at most
    every PROGRAM_EVERY seconds, as the inbox is read often."""

    if now - _program_cache["at"] < PROGRAM_EVERY:
        return list(_program_cache["items"])
    from . import extensions

    found = []
    try:
        for extension, raw in extensions.inbox_items():
            for r in raw:
                item = _clean(extension.name, {"label": extension.name, **r}, now)
                if item:
                    item["program"] = True
                    found.append(item)
    except Exception:  # pylint: disable=W0703 -- an extension never breaks the inbox
        found = []
    _program_cache.update(at=now, items=found)
    return list(found)


def extension_items(now: Optional[float] = None) -> list[dict]:
    """The extensions' items not yet put away or answered, as the inbox
    shows them."""

    now = time.time() if now is None else now
    data = _load()
    done = set(data["seen"]) | set(data["answers"])
    out = []
    for item in data["items"] + _program_items(now):
        full = _full_id(item)
        if full in done or now - item["at"] > REPORT_DAYS * 86400:
            continue
        shown = {"source": "extension", "id": full, "name": item["label"], "colour": item["colour"],
                 "url": item["url"], "at": item["at"]}
        if item["ask"]:
            out.append({"kind": "approval", **shown, "ask": item["title"] or item["body"][:200],
                        "detail": item["detail"] or (item["body"] if item["title"] else "")})
        else:
            text = (f"**{item['title']}**\n\n" if item["title"] else "") + item["body"]
            out.append({"kind": "report", **shown, "text": text})
    return out


def _answer_extension(rest: str, yes: bool, why: str) -> str:
    source, _, item_id = rest.partition(":")
    with _lock:
        data = _load()
        data["answers"][f"ext:{rest}"] = {"yes": bool(yes), "why": str(why or "")[:300], "at": time.time()}
        _save(data)
    from . import extensions

    for extension in extensions.installed():
        if extension.name == source and extension.inbox is not None:
            try:
                extensions.inbox_answer(extension, item_id, yes, why)
            except Exception:  # pylint: disable=W0703 -- the answer is kept either way
                pass
    _program_cache["at"] = 0.0
    return f"Told {source}: yes." if yes else f"Told {source}: no."


def answer(item_id: str, yes: bool, why: str = "") -> str:
    """Approve or deny ITEM_ID, a spark's step or a flow's approval."""

    from . import flows, sparks

    kind, _, rest = str(item_id).partition(":")
    if kind == "spark":
        sparks.answer_step(rest, yes, why)
        return "Approved: it carries on." if yes else "Denied: it was told why, if you said."
    if kind == "flow":
        flow_id, _, step = rest.partition(":")
        flows.answer(flow_id, step, yes)
        return "Approved: the flow carries on." if yes else "Denied: the run ends there."
    if kind == "ext":
        return _answer_extension(rest, yes, why)
    raise ValueError("That is not something to approve.")


def seen_all() -> int:
    """Every report put away at once: how many."""

    n = 0
    for item in items():
        if item["kind"] == "report":
            seen(item["id"])
            n += 1
    return n


def seen(item_id: str) -> None:
    """A report the user has read, or swiped away."""

    from . import sparks

    kind, _, rest = str(item_id).partition(":")
    if kind == "ext":
        with _lock:
            data = _load()
            if item_id not in data["seen"]:
                data["seen"].append(str(item_id))
            _save(data)
        return
    if kind == "report":
        spark_id, _, index = rest.rpartition(":")

        def change(spark) -> None:
            i = int(index)
            if 0 <= i < len(spark.reports):
                spark.reports[i].read = True

        sparks._edit(spark_id, change)  # pylint: disable=W0212
