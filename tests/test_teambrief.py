# pylint: disable=C0114,C0115,C0116,W0212,W0621

import time

import pytest

from flash import (
    ai,
    checkins,
    notify,
    sparks,
    teambrief,
    teamchat,
    tools,
    web,
)
from tests.test_sparks import FakeClient, _reply


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")


@pytest.fixture
def team(model, monkeypatch):
    """Desk: Lead, with Scout and Writer reporting to it, briefs on and
    no quiet hours."""

    monkeypatch.setattr(notify, "_saved", lambda name: "")
    monkeypatch.setattr(checkins, "quiet_hours", lambda: None)
    made = sparks.create_team("Desk")
    for name in ("Lead", "Scout", "Writer"):
        sparks.create(name, f"{name}'s job.")
        sparks.set_team(name.lower(), "desk")
    sparks.set_lead("scout", "lead")
    sparks.set_lead("writer", "lead")
    return made


def _at(hour, minute=0):
    """Today at HOUR:MINUTE, this computer's time."""

    day = time.localtime()
    return time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute,
                        0, 0, 0, -1))


class Starts:
    """Stands in for threading.Thread: keeps what would run."""

    def __init__(self):
        self.runs = []

    def __call__(self, target, daemon=True):
        self.runs.append(target)
        return self

    def start(self):
        return None


def test_the_brief_goes_out_as_quiet_hours_end(monkeypatch):
    monkeypatch.setattr(checkins, "quiet_hours", lambda: None)
    assert teambrief.brief_at() == "08:00"  # nosec B101

    monkeypatch.setattr(checkins, "quiet_hours", lambda: (22 * 60, 450))

    assert teambrief.brief_at() == "07:30"  # nosec B101


def test_it_is_on_unless_turned_off(monkeypatch):
    monkeypatch.setattr(notify, "_saved", lambda name: "")
    assert teambrief.enabled()  # nosec B101
    monkeypatch.setattr(notify, "_saved", lambda name: "off")
    assert not teambrief.enabled()  # nosec B101


def test_it_gathers_what_needs_the_user_and_the_news(team):
    teamchat.event(team.id, sparks.find("writer"), "report",
                   "Drafted the launch email.")
    teamchat.event(team.id, sparks.find("scout"), "failed",
                   "The tests would not run.")
    teamchat._post(team.id, sparks.find("scout"),
                   {"text": "@user which branch should I use?"})

    needs, news = teambrief.gather(team)

    assert needs == [  # nosec B101
        "Scout asked you: @user which branch should I use?",
    ]
    assert news == [  # nosec B101
        "Writer filed a report: Drafted the launch email.",
        "Scout's shift failed: The tests would not run.",
    ]


def test_the_lead_posts_one_brief_a_day(team):
    teamchat.event(team.id, sparks.find("writer"), "report",
                   "Drafted the launch email.")
    starts = Starts()
    client = FakeClient([_reply("Morning! Nothing needs you. Writer "
                                "drafted the launch email.")])

    told = teambrief.tick(_at(8, 5), client=client, start=starts)
    starts.runs[0]()

    assert told == [team.id]  # nosec B101
    said = teamchat.history(team.id)["entries"]
    assert said[-1]["name"] == "Lead"  # nosec B101
    assert said[-1]["text"].startswith("Morning!")  # nosec B101
    # The note it was handed stays out of the chat.
    assert not any("Not from the user" in e.get("text", "")  # nosec B101
                   for e in said)
    prompt = client.calls[0]["messages"][-1]["content"]
    assert "Drafted the launch email." in prompt  # nosec B101
    again = teambrief.tick(_at(8, 30), client=client, start=starts)
    assert again == []  # nosec B101


def test_no_brief_before_its_time_or_with_nothing_to_tell(team):
    starts = Starts()

    assert teambrief.tick(_at(7, 0), start=starts) == []  # nosec B101
    # Only joins in the chat: nothing to tell, so no brief.
    assert teambrief.tick(_at(8, 5), start=starts) == []  # nosec B101
    assert starts.runs == []  # nosec B101


def test_no_brief_when_turned_off(team, monkeypatch):
    teamchat.event(team.id, sparks.find("writer"), "report", "Done.")
    monkeypatch.setattr(notify, "_saved", lambda name: "0")

    assert teambrief.tick(_at(8, 5), start=Starts()) == []  # nosec B101


def test_settings_turns_it_off(model, monkeypatch):
    kept = {}
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: kept.update({name: value}))

    said = web.command(web.Session(), {
        "name": "brief-teams-setting", "arg": "off",
    })

    assert said == {"team_brief": False}  # nosec B101
    assert kept == {"TEAM_BRIEF": "0"}  # nosec B101
