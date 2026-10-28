"""The tutor's graph of propositions and knowledge tracing."""

import pytest

from flash import tools, tutor

DAY = tutor.DAY
PROPS = [
    {"id": "slope", "text": "Slope is rise over run."},
    {"id": "limit", "text": "A limit is the value a function nears."},
    {"id": "derivative", "text": "A derivative is the slope at a point.",
     "needs": ["slope", "limit"]},
]


def test_plan_teaches_groundwork_first():
    tutor.add("Calculus", PROPS)
    ids = [s["id"] for s in tutor.plan("calculus", now=0)]
    assert set(ids) == {"slope", "limit"}
    assert all(s["do"] == "teach" for s in tutor.plan("Calculus", now=0))


def test_measure_moves_knowledge_and_unlocks():
    tutor.add("Calculus", PROPS)
    for _ in range(4):
        tutor.measure("Calculus", "slope", True, now=0)
        tutor.measure("Calculus", "limit", True, now=0)
    plan = tutor.plan("Calculus", now=0)
    assert [s["id"] for s in plan] == ["derivative"]
    said = tutor.measure("Calculus", "derivative", False, now=0)
    assert said["known"] < tutor.KNOWN and said["review_in_days"] == 1
    assert tutor.plan("Calculus", now=0)[0]["do"] == "practice"


def test_trace():
    assert tutor.trace(0.5, True) > 0.5 > tutor.trace(0.5, False)


def test_reviews_come_due_with_widening_gaps():
    tutor.add("S", [{"id": "a", "text": "A."}])
    days = [tutor.measure("S", "a", True, now=0)["review_in_days"]
            for _ in range(5)]
    assert days == [1, 2, 4, 8, 16]
    assert tutor.plan("S", now=0) == []
    due = tutor.plan("S", now=17 * DAY)
    assert due and due[0]["do"] == "review"


def test_bad_graphs_are_refused():
    with pytest.raises(tutor.TutorError, match="not given"):
        tutor.add("S", [{"id": "a", "text": "A.", "needs": ["b"]}])
    with pytest.raises(tutor.TutorError, match="loop"):
        tutor.add("S", [{"id": "a", "text": "A.", "needs": ["b"]},
                        {"id": "b", "text": "B.", "needs": ["a"]}])
    with pytest.raises(tutor.TutorError, match="no subject"):
        tutor.plan("Nothing")


def test_describe_orders_groundwork_first():
    tutor.add("Calculus", PROPS)
    held = tutor.describe("Calculus")
    order = [p["id"] for p in held["propositions"]]
    assert order.index("derivative") > order.index("slope")
    assert (held["known"], held["of"]) == (0, 3)
    tutor.forget("Calculus")
    assert tutor.subjects() == []


def test_tool():
    run = tools.FUNCTIONS["tutor"]
    assert run(action="add", subject="Calc", propositions=PROPS).startswith(
        "Added 3")
    assert "teach slope" in run(action="plan", subject="Calc")
    assert "known" in run(action="measure", subject="Calc", id="slope",
                          right=True)
    assert "0 of 3 known" in run(action="graph", subject="Calc")
    assert run(action="plan", subject="Nope").startswith("Error")
    assert "tutor" in [t["function"]["name"] for t in tools.tools]
