"""Flows: sparks one after another, side by side, with the user's yes where it matters."""

import time

import pytest

from flash import flows, sparks
from tests.test_teamchat import model  # noqa: F401


@pytest.fixture
def crew(model):  # noqa: F811
    return [sparks.create(n, f"{n}'s work.", every="on call") for n in ("Ada", "Grace", "Linus")]


def _report(spark, text, at):
    sparks._edit(spark.id, lambda s: s.reports.append(sparks.Report(at=at, text=text)))


def _jobs(spark):
    return [i["text"] for i in sparks.find(spark.id).inbox if i.get("job")]


def test_side_by_side_then_after_both_then_a_yes(crew):
    ada, grace, linus = crew
    flow = flows.create("Weekly report", [
        {"spark": "ada", "task": "Summarise last week's issues"},
        {"spark": "grace", "task": "Collect the usage numbers", "after": []},
        {"spark": "linus", "task": "Write the weekly report", "after": [1, 2]},
        {"approval": "Post the report?"},
    ], "mon 9:00")
    assert flow["trigger"]["at"] and flow["next"] > time.time()
    t0 = time.time()
    run = flows.start("weekly report", now=t0)
    assert run["steps"]["s1"]["state"] == "working" and run["steps"]["s2"]["state"] == "working"
    assert run["steps"]["s3"]["state"] == "waiting"
    assert "Summarise last week's issues" in _jobs(ada)[-1] and _jobs(grace)
    _report(ada, "12 issues, 3 bugs.", t0 + 10)
    flows.tick(t0 + 11)
    assert flows.find("weekly report")["runs"][-1]["steps"]["s3"]["state"] == "waiting"   # still waits for Grace
    _report(grace, "2,400 users.", t0 + 20)
    flows.tick(t0 + 21)
    handed = _jobs(linus)[-1]
    assert "12 issues, 3 bugs." in handed and "2,400 users." in handed
    _report(linus, "The report.", t0 + 30)
    flows.tick(t0 + 31)
    run = flows.find("weekly report")["runs"][-1]
    assert run["steps"]["s4"]["state"] == "asking" and run["state"] == "running"
    run = flows.answer("weekly report", "s4", True, now=t0 + 40)
    assert run["state"] == "done"


def test_a_no_ends_the_run_and_what_comes_after_is_skipped(crew):
    ada, _, linus = crew
    flows.create("Ship", [{"approval": "Ship it?"}, {"spark": "ada", "task": "Ship it"}])
    t0 = time.time()
    flows.start("ship", now=t0)
    run = flows.answer("ship", "s1", False, now=t0 + 1)
    assert run["state"] == "failed" and run["steps"]["s2"]["state"] == "skipped" and not _jobs(ada)


def test_a_trigger_starts_it_and_mistakes_say_why(crew):
    flows.create("Daily", [{"spark": "grace", "task": "Count"}], "daily")
    f = flows.find("daily")
    flows.tick(f["next"] + 1)
    assert flows.find("daily")["runs"][-1]["why"] == "on schedule"
    with pytest.raises(flows.FlowError, match="No spark"):
        flows.create("Bad", [{"spark": "nobody", "task": "x"}])
    with pytest.raises(flows.FlowError, match="needs a task"):
        flows.create("Bad", [{"spark": "ada"}])
    with pytest.raises(flows.FlowError, match="not an earlier step"):
        flows.create("Bad", [{"spark": "ada", "task": "x", "after": [3]}])
    with pytest.raises(flows.FlowError, match="already running"):
        flows.start("daily")


def test_the_sparks_page_makes_runs_answers_and_deletes_one(crew):
    from flash import web

    ada, grace, _ = crew
    session = web.Session()
    made = web.command(session, {"name": "flow-create", "arg": "Triage", "trigger": "",
                                 "steps": [{"id": "s1", "spark": ada.id, "task": "Sort the issues", "after": []},
                                           {"id": "s2", "approval": "Assign them?", "after": ["s1"]},
                                           {"id": "s3", "spark": grace.id, "task": "Assign them", "after": ["s2"]}]})["flow"]
    assert [s["kind"] for s in made["steps"]] == ["spark", "approval", "spark"]
    assert made["steps"][0]["who"]["name"] == "Ada" and made["trigger"] == ""
    ran = web.command(session, {"name": "flow-run", "arg": made["id"]})["flow"]
    assert ran["last"]["state"] == "running" and ran["last"]["steps"]["s1"]["state"] == "working"
    _report(ada, "Two bugs.", time.time() + 1)
    flows.tick(time.time() + 2)
    with pytest.raises(ValueError):
        web.command(session, {"name": "flow-answer", "arg": made["id"], "step": "s3", "yes": True})
    answered = web.command(session, {"name": "flow-answer", "arg": made["id"], "step": "s2", "yes": True})["flow"]
    assert answered["last"]["steps"]["s3"]["state"] == "working" and "Two bugs." in _jobs(grace)[-1]
    listed = web.command(session, {"name": "flows"})["flows"]
    assert [f["name"] for f in listed] == ["Triage"]
    web.command(session, {"name": "flow-stop", "arg": "triage"})
    assert web.command(session, {"name": "flows"})["flows"][0]["last"]["state"] == "stopped"
    assert web.command(session, {"name": "flow-update", "arg": "triage", "trigger": "9am weekdays"})["flow"]["next"]
    web.command(session, {"name": "flow-delete", "arg": "triage"})
    assert web.command(session, {"name": "flows"})["flows"] == []
