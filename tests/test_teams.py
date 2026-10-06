# pylint: disable=C0114,C0115,C0116

import json
import time

import pytest

from flash import audit, sparks, tools, web
from tests.test_sparks import FakeClient, _reply


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")


def _costing(response, tokens):
    """RESPONSE, saying it cost TOKENS: half reading, half writing."""

    response.prompt_eval_count = tokens // 2
    response.eval_count = tokens - tokens // 2
    return response


# --- Teams ---------------------------------------------------------------


def test_teams_are_kept_apart_from_sparks():
    team = sparks.create_team("House")
    spark = sparks.create("Scout", "Look around.")

    assert [t.name for t in sparks.teams()] == ["House"]
    assert sparks.find_team("house").id == team.id
    assert spark.team == "" and [s.name for s in sparks.all_sparks()] == [
        "Scout"
    ]
    with pytest.raises(sparks.SparkError, match="already a team"):
        sparks.create_team("house")
    with pytest.raises(sparks.SparkError, match="needs a name"):
        sparks.create_team("  ")


def test_a_spark_joins_a_team_and_leaves_it():
    team = sparks.create_team("House")
    sparks.create("Scout", "Look around.")

    joined = sparks.set_team("scout", "house")
    assert joined.team == team.id
    assert joined.to_dict()["team_name"] == "House"

    left = sparks.set_team("scout", "")
    assert left.team == "" and left.to_dict()["team_name"] == ""


def test_renaming_and_removing_a_team_keeps_its_sparks():
    sparks.create_team("House")
    sparks.create("Scout", "Look around.")
    sparks.create("Lead", "Lead.")
    sparks.set_team("scout", "house")
    sparks.set_team("lead", "house")
    sparks.set_lead("scout", "lead")

    renamed = sparks.update_team(
        "house", name="Home", colour=sparks.COLOURS[3],
    )
    assert (renamed.name, renamed.colour) == ("Home", sparks.COLOURS[3])

    sparks.remove_team("home")

    assert sparks.teams() == []
    scout = sparks.find("scout")
    assert (scout.team, scout.reports_to) == ("", "")


# --- The org chart -------------------------------------------------------


@pytest.fixture
def org():
    """Lead, with Scout and Tester reporting to it, all on Dev."""

    sparks.create_team("Dev")
    for name in ("Lead", "Scout", "Tester"):
        sparks.create(name, f"{name}'s goal.")
        sparks.set_team(name, "dev")
    sparks.set_lead("scout", "lead")
    sparks.set_lead("tester", "lead")
    return sparks.find_team("dev")


def test_the_org_chart_is_a_tree(org):
    lead, scout, tester = (sparks.find(n) for n in ("lead", "scout", "tester"))

    assert sparks.org_chart(org.id) == [{"id": lead.id, "reports": [
        {"id": scout.id, "reports": []}, {"id": tester.id, "reports": []},
    ]}]
    assert {s.name for s in sparks.reports_of(lead)} == {"Scout", "Tester"}
    assert sparks.lead_of(scout).name == "Lead"


def test_a_lead_must_be_on_the_team_and_not_a_circle(org):
    sparks.create("Loner", "Alone.")

    with pytest.raises(sparks.SparkError, match="not on"):
        sparks.set_lead("loner", "lead")
    with pytest.raises(sparks.SparkError, match="itself"):
        sparks.set_lead("lead", "lead")
    with pytest.raises(sparks.SparkError, match="other way round"):
        sparks.set_lead("lead", "scout")


def test_leaving_the_team_cuts_the_lines(org):
    sparks.set_team("lead", "")

    assert sparks.find("scout").reports_to == ""
    assert sparks.find("lead").reports_to == ""


def test_removing_a_spark_moves_its_reports_up(org):
    sparks.create("Intern", "Help.")
    sparks.set_team("intern", "dev")
    sparks.set_lead("intern", "scout")

    sparks.remove("scout")

    assert sparks.lead_of(sparks.find("intern")).name == "Lead"


def test_a_spark_knows_its_place_on_the_team(org, model):
    lead_block = sparks.team_block(sparks.find("lead"))
    scout_block = sparks.team_block(sparks.find("scout"))

    assert "=== Your team: Dev ===" in lead_block
    assert "Reporting to you: Scout (@scout-spark), Tester" in lead_block
    assert "You report to Lead (@lead-spark)" in scout_block
    assert "- Tester (@tester-spark) [your team]" in scout_block


def test_news_rolls_up_to_the_lead_for_its_next_shift(org, model):
    sparks.shift("scout", client=FakeClient([_reply("Issue 12 is a bug.")]))

    lead = sparks.find("lead")
    assert lead.inbox[-1]["rollup"] and lead.inbox[-1]["from"] == "Scout"
    assert not lead.asked  # news is read at its time, not now
    client = FakeClient([_reply("Told the user about issue 12.")])
    sparks.shift(lead.id, client=client)
    opening = client.calls[0]["messages"][1]["content"]
    assert "News from the sparks that report to you" in opening
    assert "Scout: Issue 12 is a bug." in opening


def test_quiet_news_does_not_roll_up(org, model):
    sparks.shift("scout", client=FakeClient([_reply(sparks.NOTHING_NEW)]))

    assert sparks.find("lead").inbox == []


# --- Budgets -------------------------------------------------------------


def test_budgets_are_off_until_switched_on(model):
    sparks.create("Scout", "Look.")
    client = FakeClient([_costing(_reply("Done."), 5000)])

    sparks.shift("scout", client=client)

    scout = sparks.find("scout")
    assert not scout.budget_on and not scout.paused
    assert sparks.used_this_month(scout) == 5000
    assert scout.to_dict()["used"] == 5000


def test_a_spark_pauses_itself_at_its_budget(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, "2k")
    client = FakeClient([
        _costing(_reply("", ("keep_notes", {"notes": "x"})), 1500),
        _costing(_reply("", ("keep_notes", {"notes": "y"})), 1500),
        _reply("Never reached."),
    ])

    report = sparks.shift("scout", client=client)

    scout = sparks.find("scout")
    assert scout.paused and scout.budget_paused
    assert "used its budget" in report.text
    assert any("Paused: I used my budget of 2,000" in r.text
               for r in scout.reports)
    assert len(client.calls) == 2
    with pytest.raises(sparks.SparkError, match="used its budget"):
        sparks.run_now("scout")
    with pytest.raises(sparks.SparkError, match="used its budget"):
        sparks.set_paused("scout", False)


def test_raising_the_budget_or_switching_it_off_resumes_it(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, 1000)
    sparks.spend(sparks.find("scout").id, 1200)
    assert sparks.find("scout").paused

    raised = sparks.set_budget("scout", True, "5k")

    assert not raised.paused and not raised.budget_paused
    sparks.spend(raised.id, 5000)
    assert sparks.find("scout").paused
    assert not sparks.set_budget("scout", False).paused


def test_a_new_month_starts_it_again(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, 1000)
    sparks.spend(sparks.find("scout").id, 1000)
    sparks._edit("scout", lambda s: s.spent.update(period="1999-01"))

    sparks.renew_budgets()

    scout = sparks.find("scout")
    assert not scout.paused and sparks.used_this_month(scout) == 0


def test_a_spark_out_of_budget_does_not_answer(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, 1000)
    sparks.spend(sparks.find("scout").id, 1000)

    reply = sparks.say("scout", "Hi?", client=FakeClient([]))

    assert reply.failed and "used my budget" in reply.text
    with pytest.raises(sparks.SparkError, match="used its budget"):
        sparks.consult("scout", "Anything?")


@pytest.mark.parametrize("said, tokens", [
    ("50000", 50000), ("50k", 50000), ("2m", 2_000_000), ("1.5M", 1_500_000),
])
def test_budgets_read_as_people_write_them(said, tokens):
    assert sparks.parse_budget(said) == tokens


def test_a_budget_too_small_or_unreadable_is_refused():
    with pytest.raises(sparks.SparkError, match="at least"):
        sparks.parse_budget("10")
    with pytest.raises(sparks.SparkError, match="Could not read"):
        sparks.parse_budget("lots")


# --- The audit log -------------------------------------------------------


def test_a_shift_is_logged_call_by_call(model):
    sparks.create("Scout", "Look.")
    sparks.shift("scout", client=FakeClient([
        _reply("", ("keep_notes", {"notes": "seen 12"})),
        _reply("Issue 12."),
    ]))

    kinds = [e["kind"] for e in sparks.audit_log("scout")["entries"]]

    assert kinds == ["made", "shift_start", "tool", "shift_end"]
    tool = sparks.audit_log("scout")["entries"][2]
    assert tool["tool"] == "keep_notes" and tool["args"] == {
        "notes": "seen 12"
    }
    assert tool["during"] == "shift"


def test_changes_and_approvals_are_logged(model):
    sparks.create("Scout", "Look.")
    sparks.update("scout", goal="Look harder.")
    sparks.teach("scout", "Skip docs.")
    sparks.set_paused("scout", True)

    entries = sparks.audit_log("scout")["entries"]

    changed = next(e for e in entries if e["kind"] == "changed")
    assert changed["changes"]["goal"] == {"from": "Look.",
                                          "to": "Look harder."}
    assert [e["kind"] for e in entries][-2:] == ["taught", "paused"]


def test_the_log_shows_when_it_was_edited_by_hand(model):
    spark = sparks.create("Scout", "Look.")
    sparks.teach("scout", "Skip docs.")
    assert sparks.audit_log("scout")["intact"]

    path = sparks.audit_dir() / f"{spark.id}.jsonl"
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["goal"] = "Something else."
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n")

    log = sparks.audit_log("scout")
    assert not log["intact"] and log["checked"] == 1


def test_the_log_outlives_the_spark(model):
    spark = sparks.create("Scout", "Look.")
    sparks.remove("scout")

    assert [e["kind"] for e in sparks.audit_log(spark.id)["entries"]] == [
        "made", "removed",
    ]
    assert '"kind": "removed"' in sparks.audit_export(spark.id)


def test_long_fields_are_cut_in_the_log(tmp_path):
    entry = audit.record(tmp_path, "x", "tool", result="a" * 5000)

    assert len(entry["result"]) < 2100 and entry["result"].endswith("more]")


# --- Hiring --------------------------------------------------------------


def test_a_hire_waits_for_the_user_then_joins_the_team(org, model):
    hire = sparks.propose_hire(
        "lead", "Docs", "Keep the docs up to date.", "Doc writer", "daily",
        "Only edit docs/.", "Docs keep going stale.",
    )

    assert sparks.find("docs") is None
    assert [h["id"] for h in sparks.hires()] == [hire["id"]]
    assert sparks.unread_total() >= 1

    sparks.decide_hire(hire["id"], True)

    docs = sparks.find("docs")
    assert docs.team == org.id and docs.reports_to == sparks.find("lead").id
    assert docs.title == "Doc writer" and docs.every == 1440
    assert sparks.hires() == []
    told = sparks.find("lead").inbox[-1]
    assert told["decision"] and "said yes to hiring Docs" in told["text"]


def test_a_declined_hire_is_never_made(org):
    hire = sparks.propose_hire("lead", "Docs", "Docs.", reason="Why not.")

    sparks.decide_hire(hire["id"], False, "Not now.")

    assert sparks.find("docs") is None
    assert "said no to hiring Docs: Not now." in sparks.find(
        "lead"
    ).inbox[-1]["text"]
    with pytest.raises(sparks.SparkError, match="not waiting"):
        sparks.decide_hire(hire["id"], True)


def test_a_lead_can_only_propose_letting_its_own_reports_go(org):
    with pytest.raises(sparks.SparkError, match="does not report to you"):
        sparks.propose_removal("scout", "tester")

    hire = sparks.propose_removal("lead", "tester", "Tests moved to CI.")
    assert sparks.find("tester") is not None

    sparks.decide_hire(hire["id"], True)
    assert sparks.find("tester") is None


def test_a_spark_proposes_a_hire_from_its_shift(org, model):
    client = FakeClient([
        _reply("", ("propose_hire", {
            "name": "Docs", "goal": "Docs.", "reason": "Stale docs.",
        })),
        _reply("Asked to hire Docs."),
    ])

    report = sparks.shift("lead", client=client)

    offered = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert {"propose_hire", "propose_removal"} <= offered
    assert "ProposeHire(Docs)" in report.steps
    assert sparks.hires()[0]["spark"]["name"] == "Docs"


def test_too_many_proposals_at_once_are_refused(org):
    for n in range(sparks.MAX_PENDING_HIRES):
        sparks.propose_hire("lead", f"Help {n}", "Help.")

    with pytest.raises(sparks.SparkError, match="already have proposals"):
        sparks.propose_hire("lead", "One more", "Help.")


# --- Team templates ------------------------------------------------------


def test_a_team_shares_as_one_code_and_comes_back_whole(org):
    sparks.teach("scout", "Skip docs.")
    code = sparks.share_team_code("dev")

    seen = sparks.read_team_code(code)
    team = sparks.add_team(code, model="m")

    assert code.startswith(sparks.TEAM_SHARE_PREFIX)
    assert seen["name"] == "Dev" and len(seen["sparks"]) == 3
    assert team.name == "Dev 2"
    copies = {s.name: s for s in sparks.members(team.id)}
    assert set(copies) == {"Lead 2", "Scout 2", "Tester 2"}
    assert copies["Scout 2"].reports_to == copies["Lead 2"].id
    assert copies["Scout 2"].lessons == ["Skip docs."]
    assert copies["Lead 2"].reports_to == ""


def test_a_built_in_team_template(model):
    team = sparks.add_team("dev team", model="m")

    chart = sparks.org_chart(team.id)
    lead = sparks.find(chart[0]["id"])
    assert lead.name == "Repo Watch"
    assert {sparks.find(r["id"]).name for r in chart[0]["reports"]} == {
        "Test Runner", "Dependency Check",
    }
    listed = sparks.team_templates()
    assert [t["name"] for t in listed] == ["Dev Team", "Personal Desk"]


def test_a_code_that_is_not_a_team_says_so():
    with pytest.raises(sparks.SparkError, match="not a team's code"):
        sparks.read_team_code("flash-spark:abc")
    with pytest.raises(sparks.SparkError, match="not whole"):
        sparks.read_team_code("flash-team:%%%")


# --- The page ------------------------------------------------------------


def test_the_page_makes_teams_and_lines_and_reads_them(org):
    session = web.Session()

    listed = web.command(session, {"name": "sparks"})
    assert listed["teams"][0]["name"] == "Dev"
    assert listed["teams"][0]["chart"][0]["reports"]
    made = web.command(session, {"name": "team-create", "arg": "Ops"})
    assert made["team"]["name"] == "Ops"
    moved = web.command(session, {
        "name": "spark-team", "arg": "scout", "team": "ops",
    })
    assert moved["spark"]["team_name"] == "Ops"
    log = web.command(session, {"name": "spark-audit", "arg": "scout"})
    assert log["intact"] and log["entries"][-1]["kind"] == "team"


def test_the_page_decides_hires_and_sets_budgets(org):
    session = web.Session()
    hire = sparks.propose_hire("lead", "Docs", "Docs.")

    listed = web.command(session, {"name": "sparks"})
    assert listed["hires"][0]["id"] == hire["id"]
    web.command(session, {"name": "hire-decide", "arg": hire["id"],
                          "yes": True})
    assert sparks.find("docs") is not None

    got = web.command(session, {
        "name": "spark-budget", "arg": "scout", "on": True, "tokens": "50k",
    })
    assert got["spark"]["budget_on"] and got["spark"]["budget"] == 50000


def test_the_page_adds_a_team_from_a_template():
    session = web.Session()

    listed = web.command(session, {"name": "team-templates"})
    added = web.command(session, {"name": "team-add", "arg": "Personal Desk",
                                  "model": "m"})

    assert listed["templates"][0]["team"]["sparks"]
    assert added["team"]["name"] == "Personal Desk"
    assert len(sparks.members(added["team"]["id"])) == 3


# --- The terminal --------------------------------------------------------


def test_the_terminal_adds_a_team_and_decides_a_hire(monkeypatch):
    from flash import ai

    monkeypatch.setattr(ai.Config, "model", "m")
    ai._sparks_command("teams add Dev Team")
    team = sparks.find_team("dev team")
    assert len(sparks.members(team.id)) == 3

    hire = sparks.propose_hire("repo-watch", "Docs", "Docs.")
    ai._sparks_command(f"hire {hire['id']} yes")
    assert sparks.lead_of(sparks.find("docs")).name == "Repo Watch"

    ai._sparks_command("team docs none")
    assert sparks.find("docs").team == ""
    ai._sparks_command("budget repo-watch 50k")
    assert sparks.find("repo-watch").budget == 50000
    ai._sparks_command("budget repo-watch off")
    assert not sparks.find("repo-watch").budget_on


# --- Budget periods ------------------------------------------------------


def test_a_budget_counts_by_the_month_unless_told_otherwise(model):
    spark = sparks.create("Scout", "Look.")
    assert sparks.period_of(spark) == "month"
    assert sparks.this_period(spark) == "this month"

    daily = sparks.set_budget("scout", True, "20k", "daily")
    assert sparks.period_of(daily) == "day"
    assert sparks.budget_words(daily) == "20,000 tokens a day"
    assert daily.to_dict()["period_words"] == "today"
    assert sparks.budget_words(
        sparks.set_budget("scout", True, None, "hour")
    ) == "20,000 tokens an hour"


def test_a_daily_budget_pauses_and_says_when_it_starts_again(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, "1k", "day")

    sparks.spend(sparks.find("scout").id, 1500)

    scout = sparks.find("scout")
    assert scout.paused
    said = scout.reports[-1].text
    assert "1,000 tokens a day for today" in said
    assert "when the day turns" in said
    assert "today" in sparks.out_of_budget(scout)


def test_a_new_period_starts_a_daily_budget_again(model):
    sparks.create("Scout", "Look.")
    sparks.set_budget("scout", True, "1k", "week")
    sparks.spend(sparks.find("scout").id, 1000)
    sparks._edit("scout", lambda s: s.spent.update(period="1999-W01"))

    sparks.renew_budgets()

    assert not sparks.find("scout").paused


def test_a_spark_from_before_periods_counts_by_the_month(model):
    spark = sparks.create("Scout", "Look.")
    month = time.strftime("%Y-%m")
    sparks._edit(spark.id, lambda s: setattr(
        s, "spent", {"month": month, "tokens": 700},
    ))

    assert sparks.used_this_period(sparks.find("scout")) == 700


@pytest.mark.parametrize("said, tokens, period", [
    ("50k", 50000, None), ("50k/day", 50000, "day"),
    ("50k a week", 50000, "week"), ("2m monthly", 2_000_000, "month"),
    ("5000 per hour", 5000, "hour"),
])
def test_budgets_read_with_their_period(said, tokens, period):
    assert sparks.parse_budget_with_period(said) == (tokens, period)


def test_a_period_that_cannot_be_read_says_so():
    with pytest.raises(sparks.SparkError, match="hour, day, week or month"):
        sparks.parse_period("fortnight")


def test_the_terminal_sets_a_period(model):
    from flash import ai

    sparks.create("Scout", "Look.")
    ai._sparks_command("budget scout 30k/day")
    assert sparks.budget_words(sparks.find("scout")) == "30,000 tokens a day"
    ai._sparks_command("budget scout week")
    assert sparks.budget_words(sparks.find("scout")) == "30,000 tokens a week"


def test_the_page_sets_a_period():
    sparks.create("Scout", "Look.")

    got = web.command(web.Session(), {
        "name": "spark-budget", "arg": "scout", "on": True,
        "tokens": "10k", "period": "hour",
    })

    assert got["spark"]["budget_period"] == "hour"
    assert got["spark"]["period_words"] == "this hour"
