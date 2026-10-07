# pylint: disable=C0114,C0115,C0116

from types import SimpleNamespace

import pytest
from prompt_toolkit.document import Document

from flash import sparks, sparks_completion
from flash.repl_input import SlashCommandCompleter


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test reads its own sparks, not the last one's, kept."""

    monkeypatch.setattr(sparks_completion, "_cache", {})


@pytest.fixture
def crew():
    """Night Shift: Owl, leading Repo Watch; and Loner on no team."""

    sparks.create_team("Night Shift")
    sparks.create("Owl", "Watch.", title="Night lead")
    sparks.create("Repo Watch", "Watch the repo.")
    sparks.create("Loner", "Alone.")
    for name in ("owl", "repo watch"):
        sparks.set_team(name, "night shift")


def _offered(text):
    """What the menu offers for TEXT, and what picking each makes it."""

    out = []
    for c in SlashCommandCompleter().get_completions(
        Document(text, cursor_position=len(text)),
        SimpleNamespace(completion_requested=True),
    ):
        out.append(text[:len(text) + c.start_position] + c.text)
    return out


def test_sparks_offers_its_actions():
    offered = _offered("/sparks te")

    assert set(offered) == {
        "/sparks teach", "/sparks team", "/sparks teams",
        "/sparks teamchat", "/sparks templates",
    }
    assert len(_offered("/sparks ")) == len(sparks_completion.ACTIONS)


def test_an_action_offers_the_sparks_by_the_name_it_knows(crew):
    assert _offered("/sparks run r") == ["/sparks run repo-watch"]
    # By the name as it is shown, or its @handle, too.
    assert _offered("/sparks chat Repo") == ["/sparks chat repo-watch"]
    assert _offered("/sparks chat @ow") == ["/sparks chat owl"]
    assert set(_offered("/sparks pause ")) == {
        "/sparks pause all", "/sparks pause owl", "/sparks pause repo-watch",
        "/sparks pause loner",
    }


def test_a_sparks_name_alone_reads_its_reports(crew):
    assert "/sparks loner" in _offered("/sparks lo")


def test_the_word_after_a_spark(crew):
    assert _offered("/sparks every owl on") == ["/sparks every owl on call"]
    assert _offered("/sparks budget owl of") == ["/sparks budget owl off"]
    assert set(_offered("/sparks lead loner ")) == {
        "/sparks lead loner owl", "/sparks lead loner repo-watch",
        "/sparks lead loner loner", "/sparks lead loner none",
    }


def test_a_team_is_offered_whole_spaces_and_all(crew):
    assert _offered("/sparks team loner Ni") == [
        "/sparks team loner Night Shift",
    ]
    assert _offered("/sparks team loner Night S") == [
        "/sparks team loner Night Shift",
    ]
    assert _offered("/sparks teamchat n") == ["/sparks teamchat Night Shift"]


def test_teams_offers_its_own_actions_then_teams(crew):
    assert "/sparks teams rules" in _offered("/sparks teams ")
    assert _offered("/sparks teams rules ") == [
        "/sparks teams rules Night Shift",
    ]
    assert _offered("/sparks teams add d") == ["/sparks teams add Dev Team"]


def test_past_what_an_action_takes_nothing_is_offered(crew):
    assert _offered("/sparks run owl ") == []
    assert _offered("/sparks teams rules Night Shift ") == []
    assert _offered("/sparks nonsense ") == []


def test_a_lesson_still_gets_emoji(crew):
    offered = _offered("/sparks teach owl be kind :smil")

    assert offered and not offered[0].endswith(":smil")
    assert offered[0].startswith("/sparks teach owl be kind ")


def test_templates_and_hires(crew):
    template = sparks.TEMPLATES[0]["name"]
    hire = sparks.propose_hire("owl", "Helper", "Help.")

    assert f"/sparks add {template}" in _offered(f"/sparks add {template[:3]}")
    assert _offered("/sparks hire ") == [f"/sparks hire {hire['id']}"]
    assert _offered(f"/sparks hire {hire['id']} y") == [
        f"/sparks hire {hire['id']} yes",
    ]
