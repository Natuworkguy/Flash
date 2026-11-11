"""Flows: work that takes several sparks, in order, with your say where it matters.

A flow is a trigger and steps. The trigger is a schedule ("mon 9:00", "daily", any spark schedule) or none, for a
flow run by hand. A step is a spark and what to do, or an approval: a stop for the user's yes or no. Each step runs
once the steps it comes after are done: steps with nothing between them run side by side, so "Ada summarises the
issues" and "Grace collects the numbers" run together, and "Linus writes the report" waits for both and is handed
what they said. An approval step holds the flow until the user answers; a no ends the run there.

A step is handed to its spark as a job (as take_on does), with the reports of the steps before it; it is done when
the spark files its next report. Each flow keeps its last runs, step by step, for the page to draw.
Kept in ~/.flash/flows.json.
"""

from __future__ import annotations

import json
import threading
import time
import types
import uuid
from pathlib import Path
from typing import Any, Optional

MAX_FLOWS = 50
MAX_STEPS = 16
MAX_RUNS = 10
TASK_CHARS = 1200
HANDED_CHARS = 2500       # how much of an earlier step's report the next is handed
STEP_HOURS = 6            # a spark step not done in this long has failed
_lock = threading.RLock()


class FlowError(ValueError):
    pass


def _path() -> Path:
    from . import sparks

    return sparks.sparks_dir().parent / "flows.json"


def _read() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def _write(flows: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.partial")
    tmp.write_text(json.dumps(flows, indent=1), encoding="utf-8")
    tmp.replace(p)


def all_flows() -> list[dict]:
    with _lock:
        return _read()


def find(key: str) -> Optional[dict]:
    key = str(key or "").strip().lower()
    for f in all_flows():
        if f["id"] == key or f["name"].lower() == key:
            return f
    return None


# ----- making one -----
def _spark_id(key: str) -> str:
    from . import sparks

    found = sparks.find(str(key or ""))
    if found is None:
        raise FlowError(f"No spark called {key!r}.")
    return found.id


def _steps(raw: Any) -> list[dict]:
    """Steps as given: a list of {spark, task, after} or {approval: "question"}; AFTER names earlier steps by
    number (1 is the first) or id. Left out, a step comes after the one before it."""
    if not isinstance(raw, list) or not raw:
        raise FlowError("A flow needs at least one step.")
    if len(raw) > MAX_STEPS:
        raise FlowError(f"A flow has at most {MAX_STEPS} steps.")
    out: list[dict] = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise FlowError("Each step is an object: {spark, task} or {approval}.")
        sid = str(s.get("id") or f"s{i + 1}")
        after_raw = s.get("after")
        if after_raw is None:
            after = [out[-1]["id"]] if out else []
        else:
            after = []
            for a in after_raw if isinstance(after_raw, list) else [after_raw]:
                a = str(a).strip()
                if a.isdigit() and 1 <= int(a) <= len(out):
                    after.append(out[int(a) - 1]["id"])
                elif any(o["id"] == a for o in out):
                    after.append(a)
                elif a:
                    raise FlowError(f"Step {i + 1} comes after {a!r}, which is not an earlier step.")
        if s.get("approval") is not None and not s.get("spark"):
            ask = str(s.get("approval") or s.get("task") or "Carry on?").strip()[:300]
            out.append({"id": sid, "kind": "approval", "task": ask, "after": after})
            continue
        task = str(s.get("task") or "").strip()[:TASK_CHARS]
        if not task:
            raise FlowError(f"Step {i + 1} needs a task: what its spark does.")
        out.append({"id": sid, "kind": "spark", "spark": _spark_id(str(s.get("spark") or "")), "task": task, "after": after})
    return out


def _trigger(text: Any) -> dict:
    from . import sparks

    text = str(text or "").strip()
    if text.lower() in ("", "none", "manual", "by hand", "off"):
        return {"text": "", "every": 0, "at": ""}
    try:
        every, at = sparks.parse_schedule(text)
    except sparks.SparkError as exc:
        raise FlowError(str(exc)) from None
    return {"text": text, "every": every, "at": at}


def create(name: str, steps: Any, trigger: Any = "") -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise FlowError("A flow needs a name.")
    flow = {"id": uuid.uuid4().hex[:8], "name": name, "trigger": _trigger(trigger), "steps": _steps(steps),
            "created": time.time(), "next": 0.0, "paused": False, "runs": []}
    with _lock:
        flows = _read()
        if any(f["name"].lower() == name.lower() for f in flows):
            raise FlowError(f"There is already a flow called {name}.")
        if len(flows) >= MAX_FLOWS:
            raise FlowError(f"{MAX_FLOWS} flows is the most.")
        flow["next"] = _next(flow, time.time())
        flows.append(flow)
        _write(flows)
    return flow


def update(key: str, name: Any = None, steps: Any = None, trigger: Any = None, paused: Any = None) -> dict:
    with _lock:
        flows = _read()
        flow = next((f for f in flows if f["id"] == key or f["name"].lower() == str(key).lower()), None)
        if flow is None:
            raise FlowError(f"No flow called {key!r}.")
        if name is not None and str(name).strip():
            flow["name"] = " ".join(str(name).split())[:60]
        if steps is not None:
            flow["steps"] = _steps(steps)
        if trigger is not None:
            flow["trigger"] = _trigger(trigger)
            flow["next"] = _next(flow, time.time())
        if paused is not None:
            flow["paused"] = bool(paused)
        _write(flows)
        return flow


def remove(key: str) -> bool:
    with _lock:
        flows = _read()
        kept = [f for f in flows if not (f["id"] == key or f["name"].lower() == str(key).lower())]
        if len(kept) == len(flows):
            return False
        _write(kept)
        return True


def _next(flow: dict, after: float) -> float:
    from . import sparks

    t = flow.get("trigger") or {}
    if not t.get("text"):
        return 0.0
    return sparks.next_shift(types.SimpleNamespace(at=t.get("at") or "", every=int(t.get("every") or 0) or 1440), after)


# ----- running one -----
def start(key: str, now: Optional[float] = None, why: str = "by hand") -> dict:
    """A new run of the flow KEY. Its first steps are handed out on the next tick (at once here)."""
    now = time.time() if now is None else now
    with _lock:
        flows = _read()
        flow = next((f for f in flows if f["id"] == key or f["name"].lower() == str(key).lower()), None)
        if flow is None:
            raise FlowError(f"No flow called {key!r}.")
        if any(r["state"] == "running" for r in flow["runs"]):
            raise FlowError(f"{flow['name']} is already running.")
        run = {"id": uuid.uuid4().hex[:6], "started": now, "why": why, "state": "running", "ended": 0.0,
               "steps": {s["id"]: {"state": "waiting", "at": 0.0, "done": 0.0, "output": ""} for s in flow["steps"]}}
        flow["runs"] = (flow["runs"] + [run])[-MAX_RUNS:]
        _write(flows)
    tick(now)
    return _run_now(flow["id"], run["id"])


def answer(key: str, step_id: str, yes: bool, now: Optional[float] = None) -> dict:
    """The user's answer to an approval step of the flow KEY's running run."""
    now = time.time() if now is None else now
    with _lock:
        flows = _read()
        flow = next((f for f in flows if f["id"] == key or f["name"].lower() == str(key).lower()), None)
        if flow is None:
            raise FlowError(f"No flow called {key!r}.")
        run = next((r for r in reversed(flow["runs"]) if r["state"] == "running"), None)
        st = run and run["steps"].get(step_id)
        if not st or st["state"] != "asking":
            raise FlowError("That step is not waiting for an answer.")
        st.update(state="done" if yes else "refused", done=now, output="Approved." if yes else "Not approved.")
        _write(flows)
    tick(now)
    return _run_now(flow["id"], run["id"])


def _run_now(flow_id: str, run_id: str) -> dict:
    flow = find(flow_id) or {"runs": []}
    return next((r for r in flow["runs"] if r["id"] == run_id), {})


def stop(key: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        flows = _read()
        for f in flows:
            if f["id"] == key or f["name"].lower() == str(key).lower():
                for r in f["runs"]:
                    if r["state"] == "running":
                        r.update(state="stopped", ended=now)
                        _write(flows)
                        return True
    return False


def _handed(flow: dict, run: dict, step: dict) -> str:
    """The job a spark step is given: its task, the flow it is part of, and what the steps before it found."""
    before = []
    # An approval passes on what came before it: the work the user said yes to.
    todo, seen = list(step["after"]), set()
    while todo:
        sid = todo.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        prev = next((s for s in flow["steps"] if s["id"] == sid), None)
        if prev and prev["kind"] == "approval":
            todo.extend(prev["after"])
            continue
        out = run["steps"].get(sid, {}).get("output", "")
        if prev and out:
            before.append(f"- {prev['task'][:80]}:\n{out[:HANDED_CHARS]}")
    head = f"(Step of the flow \"{flow['name']}\". Do this, then report what you did and found, in full: the next " \
           "steps are handed your report.)\n"
    return head + step["task"] + ("\n\nWhat the steps before this one found:\n" + "\n".join(before) if before else "")


def tick(now: Optional[float] = None) -> list[str]:
    """Flows due by their trigger started; running steps checked for their spark's report; steps whose turn has
    come handed out; finished runs closed. What happened, for the log."""
    from . import notify, sparks

    now = time.time() if now is None else now
    said: list[str] = []
    to_start: list[str] = []
    gives: list[tuple[str, str]] = []
    asks: list[tuple[str, str]] = []
    with _lock:
        flows = _read()
        changed = False
        for flow in flows:
            # due by its trigger
            if flow.get("next") and flow["next"] <= now and not flow.get("paused"):
                flow["next"] = _next(flow, now)
                changed = True
                if not any(r["state"] == "running" for r in flow["runs"]):
                    to_start.append(flow["id"])
            for run in flow["runs"]:
                if run["state"] != "running":
                    continue
                for step in flow["steps"]:
                    st = run["steps"].setdefault(step["id"], {"state": "waiting", "at": 0.0, "done": 0.0, "output": ""})
                    if st["state"] == "working":
                        spark = sparks.find(step["spark"])
                        rep = next((r for r in (spark.reports if spark else []) if r.at > st["at"] and not r.approval), None)
                        if rep is not None:
                            st.update(state="failed" if rep.failed else "done", done=rep.at, output=rep.text[:6000])
                            changed = True
                        elif spark is None or now - st["at"] > STEP_HOURS * 3600:
                            st.update(state="failed", done=now, output="Its spark did not report in time." if spark else "Its spark is gone.")
                            changed = True
                    if st["state"] != "waiting":
                        continue
                    before = [run["steps"].get(a, {}).get("state") for a in step["after"]]
                    if any(b in ("failed", "refused") for b in before):
                        st.update(state="skipped", done=now)
                        changed = True
                    elif all(b == "done" for b in before):
                        if step["kind"] == "approval":
                            st.update(state="asking", at=now)
                            asks.append((flow["name"], step["task"]))
                        else:
                            st.update(state="working", at=now)
                            gives.append((step["spark"], _handed(flow, run, step)))
                        changed = True
                states = [run["steps"][s["id"]]["state"] for s in flow["steps"]]
                if all(s in ("done", "failed", "refused", "skipped") for s in states):
                    ok = all(s == "done" for s in states)
                    run.update(state="done" if ok else "failed", ended=now)
                    said.append(f"{flow['name']}: {'finished' if ok else 'stopped short'}")
                    changed = True
        if changed:
            _write(flows)
    for spark_id, job in gives:
        try:
            sparks.take_on(spark_id, job)
        except sparks.SparkError:
            pass
    for name, ask in asks:
        try:
            notify.desktop(f"{name} needs your yes", ask[:160])
        except Exception:  # noqa: BLE001 - no desktop to notify: the page shows it
            pass
    for fid in to_start:
        try:
            start(fid, now, why="on schedule")
            said.append(f"started {fid}")
        except FlowError:
            pass
    return said


def summary(flow: dict) -> dict:
    """A flow for the page: its steps with their spark's name, colour and shape, and its latest run."""
    from . import sparks

    def spark_of(sid):
        s = sparks.find(sid)
        return {"name": s.name, "colour": s.colour, "shape": getattr(s, "shape", ""), "id": s.id} if s else {"name": "(gone)", "colour": "#888", "id": sid}
    steps = [{**s, **({"who": spark_of(s["spark"])} if s["kind"] == "spark" else {})} for s in flow["steps"]]
    last = flow["runs"][-1] if flow["runs"] else None
    return {"id": flow["id"], "name": flow["name"], "trigger": flow["trigger"].get("text") or "", "paused": flow.get("paused", False),
            "next": flow.get("next") or 0, "steps": steps, "last": last, "runs": len(flow["runs"])}
