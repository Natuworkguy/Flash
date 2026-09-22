# pylint: disable=C0114,C0115,C0116

import pytest

from flash import plan
from flash.tools import check_step, plan_tool


@pytest.fixture(autouse=True)
def _fresh_plan():
    plan.clear()
    yield
    plan.clear()


def _statuses():
    return [step["status"] for step in plan.steps()]


def test_a_new_plan_starts_on_its_first_step():
    plan.set_steps(["one", "two", "three"])
    assert _statuses() == [plan.ACTIVE, plan.TODO, plan.TODO]
    assert plan.headline() == "Plan(0/3 done)"


def test_ticking_a_step_moves_the_marker_on():
    plan.set_steps(["one", "two", "three"])
    assert plan.mark_done(1) == ""
    assert _statuses() == [plan.DONE, plan.ACTIVE, plan.TODO]
    assert plan.headline() == "Plan(1/3 done)"


def test_steps_can_be_ticked_out_of_order():
    plan.set_steps(["one", "two", "three"])
    plan.mark_done(2)
    assert _statuses() == [plan.ACTIVE, plan.DONE, plan.TODO]


def test_a_finished_plan_has_nothing_active():
    plan.set_steps(["one", "two"])
    plan.mark_done(1)
    plan.mark_done(2)
    assert _statuses() == [plan.DONE, plan.DONE]
    assert plan.headline() == "Plan(2/2 done)"


def test_ticking_outside_the_plan_explains_itself():
    plan.set_steps(["one"])
    assert "does not exist" in plan.mark_done(4)
    assert _statuses() == [plan.ACTIVE]


def test_ticking_without_a_plan_says_so():
    assert "no plan yet" in plan.mark_done(1)


def test_a_new_plan_replaces_the_old_one():
    plan.set_steps(["one", "two"])
    plan.mark_done(1)
    plan.set_steps(["other"])
    assert [step["text"] for step in plan.steps()] == ["other"]
    assert _statuses() == [plan.ACTIVE]


def test_long_plans_are_capped():
    plan.set_steps([f"step {n}" for n in range(plan.MAX_STEPS + 5)])
    assert len(plan.steps()) == plan.MAX_STEPS


def test_text_form_marks_done_active_and_pending():
    plan.set_steps(["one", "two", "three"])
    plan.mark_done(1)
    assert plan.as_text() == (
        "Plan (1/3 done):\n"
        "1. [x] one\n"
        "2. [>] two\n"
        "3. [ ] three"
    )


def test_tool_accepts_a_newline_separated_string():
    plan_tool("- one\n2. two\n* three")
    assert [step["text"] for step in plan.steps()] == ["one", "two", "three"]


def test_tool_accepts_step_objects():
    plan_tool([{"step": "one"}, {"text": "two"}])
    assert [step["text"] for step in plan.steps()] == ["one", "two"]


def test_tool_rejects_an_empty_plan():
    assert plan_tool([]) == "A plan needs at least one step."
    assert plan.steps() == []


def test_check_tool_accepts_a_numeric_string():
    plan_tool(["one", "two"])
    check_step("1")
    assert _statuses() == [plan.DONE, plan.ACTIVE]


def test_check_tool_rejects_nonsense():
    plan_tool(["one"])
    assert "whole number" in check_step("soon")
    assert _statuses() == [plan.ACTIVE]


def test_render_survives_an_empty_plan():
    plan.render()          # must not raise
    assert plan.headline() == "Plan(empty)"
