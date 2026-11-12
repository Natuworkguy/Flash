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

import time

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
    asks.sort(key=lambda i: -i["at"])
    news.sort(key=lambda i: -i["at"])
    return asks + news


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
    if kind == "report":
        spark_id, _, index = rest.rpartition(":")

        def change(spark) -> None:
            i = int(index)
            if 0 <= i < len(spark.reports):
                spark.reports[i].read = True

        sparks._edit(spark_id, change)  # pylint: disable=W0212
