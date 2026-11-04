"""Skills that get better: runs scored, rewrites tried, kept only if
they do better."""

import pytest

from flash import learning, skills, skilltrials, web

BODY = "1. Run make deploy\n2. Check the site"
BETTER = "1. Run make build first\n2. Run make deploy\n3. Check the site"


def turn(skill="deploy", errors=0, task="deploy the site", reply="Done."):
    """A turn that read SKILL and ran a tool, ERRORS of them failing."""

    messages = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": task},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "skill_view",
                          "arguments": {"name": skill}}}]},
        {"role": "tool", "content": "Skill: deploy ...",
         "tool_name": "skill_view"},
    ]
    for n in range(max(1, errors)):
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "run_command",
                              "arguments": '{"command": "make deploy"}'}}]},
            {"role": "tool", "content": "Error: make: no rule" if n < errors
             else "ok", "tool_name": "run_command"},
        ]
    messages.append({"role": "assistant", "content": reply})
    return messages


def now(start):
    """Run a background job here and now, as the tests need it done."""

    class Now:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            start.append(self.args[0])
            self.target(*self.args)

    return Now


@pytest.fixture
def made():
    skills.manage("create", "deploy", description="Deploy the site",
                  content=BODY, managed_only=True)


def test_a_run_is_what_came_after_reading_the_skill(made):
    found = skilltrials.runs_in(turn(errors=2))
    run = found["deploy"]
    assert run["task"] == "deploy the site"
    assert run["errors"] == 2 and run["tools"] == 2
    assert run["failures"] == ["Error: make: no rule"] * 2
    assert run["version"] == skilltrials.version_of(BODY)
    assert skilltrials.score(run) == 0.5
    # A guide, a skill that is not there, and a file of the skill are
    # not runs.
    assert skilltrials.runs_in(turn(skill="use-the-canvas")) == {}
    assert skilltrials.runs_in(turn(skill="nope")) == {}
    asked = turn()
    asked[3]["tool_calls"][0]["function"]["arguments"]["path"] = "a.md"
    assert skilltrials.runs_in(asked) == {}


def test_a_steer_does_not_start_a_new_turn(made):
    messages = turn(errors=1)
    # said while the tools ran: still the same turn
    messages.insert(6, {"role": "user", "content": "use staging"})
    assert skilltrials.runs_in(messages)["deploy"]["task"] == \
        "deploy the site"


def test_a_poor_skill_is_rewritten_tried_and_kept_when_better(
        made, monkeypatch):
    started = []
    asked = []
    monkeypatch.setattr(skilltrials, "_ask_model", lambda host, model: (
        lambda messages: asked.append(messages) or
        f"Here:\n```markdown\n{BETTER}\n```"))
    for _ in range(2):
        skilltrials.after_turn(turn(errors=2), host="h", model="m",
                               start=now(started))
    assert not started
    skilltrials.after_turn(turn(errors=2), host="h", model="m",
                           start=now(started))
    assert started == ["deploy"]
    # The model saw the skill and how its runs went.
    shown = asked[0][1]["content"]
    assert BODY in shown and "failed: Error: make: no rule" in shown
    assert skills.find("deploy").body.strip() == BETTER
    trial = skilltrials.trial("deploy")
    assert trial["from"] == skilltrials.version_of(BODY)
    assert trial["baseline"] == 0.5
    assert any("Trying a new version" in n for n in skilltrials.news())
    # Three good runs of the new one: kept.
    for _ in range(3):
        skilltrials.after_turn(turn(), host="h", model="m",
                               start=now(started))
    assert skilltrials.trial("deploy") is None
    assert skills.find("deploy").body.strip() == BETTER
    assert skilltrials.history("deploy")[-1]["event"] == "kept"
    assert any("Kept the new version" in n for n in learning.news())
    # And the old one is there to go back to.
    shown = skilltrials.report("deploy")
    assert shown["versions"] == [skilltrials.version_of(BODY)]
    said = skilltrials.restore("deploy", skilltrials.version_of(BODY))
    assert "back to version" in said
    assert skills.find("deploy").body.strip() == BODY


def test_a_rewrite_that_does_no_better_is_undone(made, monkeypatch):
    started = []
    monkeypatch.setattr(skilltrials, "_ask_model", lambda host, model: (
        lambda messages: f"```\n{BETTER}\n```"))
    for _ in range(3):
        skilltrials.after_turn(turn(errors=2), host="h", model="m",
                               start=now(started))
    assert skills.find("deploy").body.strip() == BETTER
    # As poor as before: no better, so not kept.
    for _ in range(3):
        skilltrials.after_turn(turn(errors=2), host="h", model="m",
                               start=now(started))
    assert skills.find("deploy").body.strip() == BODY
    assert skilltrials.history("deploy")[-1]["event"] == "rolled back"
    # Not tried again on the runs it was already tried on.
    assert not skilltrials.due("deploy")
    assert started == ["deploy"]


def test_a_skill_the_user_wrote_is_only_rewritten_when_asked(monkeypatch):
    skills.manage("create", "mine", description="Mine", content=BODY,
                  by="you")
    started = []
    for _ in range(4):
        skilltrials.after_turn(turn("mine", errors=3), host="h", model="m",
                               start=now(started))
    assert not started and skills.find("mine").body.strip() == BODY
    said = skilltrials.improve(
        "mine", lambda messages: f"```markdown\n{BETTER}\n```", asked=True)
    assert said.startswith("Trying")
    assert skilltrials.trial("mine")["asked"] is True
    with pytest.raises(skilltrials.TrialError):
        skilltrials.improve("mine", lambda messages: "NO CHANGE")
    # Off: nothing is rewritten on its own, though runs are still kept.
    monkeypatch.setenv("SKILL_TRIALS", "off")
    assert skilltrials.mode() == "off"


def test_no_change_and_a_change_by_hand_during_a_trial(made):
    skilltrials.record(turn(errors=1))
    said = skilltrials.improve("deploy", lambda messages: "NO CHANGE")
    assert said.startswith("Nothing")
    assert skilltrials.history("deploy")[-1]["event"] == "unchanged"
    skilltrials.improve("deploy", lambda messages: f"```\n{BETTER}\n```")
    skills.manage("rewrite", "deploy", content="By hand.")
    skilltrials.record(turn())
    skilltrials.judge("deploy")
    assert skilltrials.trial("deploy") is None
    assert skilltrials.history("deploy")[-1]["event"] == "ended"
    assert skills.find("deploy").body.strip() == "By hand."


def test_a_rating_scores_the_runs_behind_the_reply(made):
    skilltrials.record(turn(reply="All deployed."))
    learning.rated([], "All deployed.", False, host="h", model="")
    assert skilltrials.runs("deploy")[-1]["rating"] == "bad"
    assert skilltrials.score(skilltrials.runs("deploy")[-1]) == 0.0


def test_restore_and_improve_refuse_what_they_cannot_do(made):
    with pytest.raises(skilltrials.TrialError):
        skilltrials.restore("deploy", "../../x")
    with pytest.raises(skilltrials.TrialError):
        skilltrials.restore("deploy", "0123abcd")
    with pytest.raises(skilltrials.TrialError):
        skilltrials.improve("deploy", lambda m: "")  # never used yet
    with pytest.raises(skilltrials.TrialError):
        skilltrials.improve("use-the-canvas", lambda m: "")


def test_learning_keeps_runs_and_the_page_shows_them(made, monkeypatch):
    learning.after_turn(turn(errors=1), 1, host="h", model="")
    session = web.Session()
    listed = {s["name"]: s for s in web.command(
        session, {"name": "skills"})["skills"]}
    assert listed["deploy"]["score"] == 0.75
    assert listed["deploy"]["trial"] is None
    assert listed["use-the-canvas"]["score"] is None
    shown = web.command(session, {"name": "skill", "arg": "deploy"})
    assert shown["trials"]["recent"][0]["errors"] == 1
    assert web.command(session, {"name": "skill", "arg":
                                 "use-the-canvas"})["trials"] is None
    monkeypatch.setattr(skilltrials, "_ask_model", lambda host, model: (
        lambda messages: f"```\n{BETTER}\n```"))
    said = web.command(session, {"name": "skill-improve", "arg": "deploy"})
    assert said["said"].startswith("Trying")
    with pytest.raises(ValueError):
        web.command(session, {"name": "skill-improve", "arg": "deploy"})
    back = web.command(session, {"name": "skill-restore", "arg": "deploy",
                                 "version": skilltrials.version_of(BODY)})
    assert "back to version" in back["said"]
    assert skilltrials.trial("deploy") is None
