"""Milestones: when a team's project passes a round number of commits,
gets a new release tag or turns a year older, its lead tells the team."""

import subprocess  # nosec B404

import pytest

from flash import checkins, milestones, sparks, teamchat, workspace
from tests.test_spark_reminders import Now
from tests.test_sparks import FakeClient, _reply
from tests.test_teamchat import _said, model  # noqa: F401

DAY = 86400


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], capture_output=True, check=True)  # nosec


def commits(folder, n):
    for i in range(n):
        git(folder, "commit", "-q", "--allow-empty", "-m", f"c{i}")


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    git(folder, "config", "user.email", "me@example.com")
    git(folder, "config", "user.name", "Me")
    commits(folder, 98)
    return folder


@pytest.fixture
def crew(model, repo, monkeypatch):  # noqa: F811
    monkeypatch.setattr(checkins, "is_quiet", lambda now=None: False)
    lead = sparks.create("Lena", "Run the team.")
    helper = sparks.create("Hugo", "Help Lena.")
    team = sparks.create_team("Crew")
    for s in (lead, helper):
        sparks.set_team(s.id, team.id)
    sparks.set_lead(helper.id, lead.id)
    project = workspace.create_project("App", str(repo))
    sparks.set_team_project(team.id, project.id)
    return team, lead


def test_round_numbers_tags_and_birthdays():
    first = {"commits": 98, "tag": "", "born": 1_000_000}
    said, kept = milestones.news(None, first, 1_000_000 + DAY)
    assert said == [] and kept == {"commits": 98, "tag": "", "years": 0}
    said, kept = milestones.news(kept, {"commits": 10_003, "tag": "v1.0", "born": 1_000_000}, 1_000_000 + 400 * DAY)
    # past many round numbers at once: only the biggest is worth saying
    assert said == ["the repository just passed 10,000 commits (10,003 now)",
                    "a new release was tagged: v1.0", "it is 1 year since its first commit"]
    assert milestones.news(kept, {"commits": 10_050, "tag": "v1.0", "born": 1_000_000}, 1_000_000 + 401 * DAY)[0] == []


def test_the_lead_tells_the_team_when_the_repo_passes_100_commits(crew, repo):
    team, lead = crew
    # the first look only notes where the project is
    assert milestones.tick(force=True, start=Now) == []
    commits(repo, 3)
    client = FakeClient([_reply("Hey team, the repo just passed 100 commits! 🎉")])
    told = milestones.tick(force=True, client=client, start=Now)
    assert told == [(team.id, "the repository just passed 100 commits (101 now)")]
    assert _said(team.id, teamchat.SPARK)[-1]["text"].startswith("Hey team, the repo just passed 100 commits")
    assert _said(team.id, teamchat.SPARK)[-1]["spark"] == lead.id
    assert "passed 100 commits" in str(client.calls[0]["messages"])
    # said once
    assert milestones.tick(force=True, start=Now) == []


def test_a_new_tag_is_news(crew, repo):
    team, _ = crew
    milestones.tick(force=True, start=Now)
    git(repo, "tag", "v2.0")
    client = FakeClient([_reply("We just tagged v2.0, nice work everyone.")])
    assert milestones.tick(force=True, client=client, start=Now) == [(team.id, "a new release was tagged: v2.0")]


def test_nothing_in_quiet_hours_and_nothing_missed_after(crew, repo, monkeypatch):
    team, _ = crew
    milestones.tick(force=True, start=Now)
    commits(repo, 5)
    monkeypatch.setattr(checkins, "is_quiet", lambda now=None: True)
    assert milestones.tick(force=True, start=Now) == []
    monkeypatch.setattr(checkins, "is_quiet", lambda now=None: False)
    client = FakeClient([_reply("Morning team, we passed 100 commits overnight!")])
    assert milestones.tick(force=True, client=client, start=Now)[0][1].startswith("the repository just passed 100")


def test_a_team_without_a_repository_is_left_alone(model, tmp_path):  # noqa: F811
    team = sparks.create_team("Loose")
    sparks.set_team(sparks.create("Ada", "Help.").id, team.id)
    sparks.set_team_project(team.id, workspace.create_project("Notes", str(tmp_path)).id)
    assert milestones.tick(force=True, start=Now) == []
    assert milestones.measure(str(tmp_path)) is None
