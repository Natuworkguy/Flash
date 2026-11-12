"""The inbox: approvals first, then reports, each answered or put away."""

import time

from flash import inbox, sparks
from tests.test_teamchat import model, team  # noqa: F401  (fixtures)


def test_a_waiting_step_and_a_report_land_in_the_inbox(team, monkeypatch):  # noqa: F811
    def wait(spark):
        spark.status = sparks.WAITING
        spark.pending = {"label": "Send the weekly email", "detail": "to: team@example.com", "at": time.time(), "answer": ""}
        spark.reports.append(sparks.Report(at=time.time() - 60, text="Found two bugs in the parser."))

    sparks._edit("scout", wait)
    got = inbox.items()
    assert got[0]["kind"] == "approval" and got[0]["ask"] == "Send the weekly email" and got[0]["name"] == "Scout"
    report = next(i for i in got if i["kind"] == "report" and "two bugs" in i["text"])
    inbox.seen(report["id"])
    assert not any("two bugs" in i.get("text", "") for i in inbox.items())
    answered = []
    monkeypatch.setattr(sparks, "answer_step", lambda key, yes, why: answered.append((key, yes, why)))
    assert inbox.answer(got[0]["id"], True).startswith("Approved")
    assert answered == [(sparks.find("scout").id, True, "")]


def test_clearing_puts_every_report_away(team):  # noqa: F811
    def report(spark):
        spark.reports += [sparks.Report(at=time.time(), text=f"note {i}") for i in range(3)]

    sparks._edit("lead", report)
    assert inbox.seen_all() >= 3
    assert not [i for i in inbox.items() if i["kind"] == "report"]


def test_only_approvals_can_be_answered():
    import pytest

    with pytest.raises(ValueError):
        inbox.answer("report:x:0", True)
