# pylint: disable=C0114,C0115,C0116

import pytest

from flash import extensions, notify, pings, progress, sparks, teamchat
from tests.test_teamchat import (  # noqa: F401  # pylint: disable=W0611
    model,
    team,
)


@pytest.fixture
def heard(monkeypatch):
    """What the page and extensions are told of, with no limit in the
    way."""

    said = {"pings": [], "told": []}
    monkeypatch.setattr(notify, "take_notify_turn", lambda *a: 0)
    monkeypatch.setattr(progress, "tell",
                        lambda event, **data: said["told"].append(
                            (event, data)))
    pings.on_ping(said["pings"].append)
    yield said
    pings.off_ping(said["pings"].append)


def test_only_an_at_user_of_its_own_is_one():
    assert pings.mentions_user("@user can you sign off?")  # nosec B101
    assert pings.mentions_user("Done. @User, have a look")  # nosec B101
    for text in ("@username", "me@user.com", "@users", "user", ""):
        assert not pings.mentions_user(text), text  # nosec B101


def test_a_spark_in_the_team_chat_pings_the_user(team, heard):  # noqa: F811
    scout = sparks.find("scout")
    teamchat._post(team.id, scout, {"text": "Found the leak."})
    assert heard["pings"] == []  # nosec B101

    teamchat._post(team.id, scout,
                   {"text": "@user the fix needs your sign-off"})
    [said] = heard["pings"]
    assert said["name"] == "Scout" and said["where"] == "team"  # nosec B101
    assert said["team"] == team.id and said["spark"] == scout.id  # nosec B101
    assert said["text"] == "@user the fix needs your sign-off"  # nosec B101
    assert heard["told"] == [("mention", said)]  # nosec B101


def test_the_limit_holds_pings_back(team, heard, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notify, "take_notify_turn", lambda *a: 30)
    assert not pings.ping(sparks.find("scout"), "@user hi",  # nosec B101
                          "report")
    assert heard["pings"] == [] and heard["told"] == []  # nosec B101


def test_a_long_report_is_quoted_from_its_at_user(team, heard):  # noqa: F811
    text = "All fine. " * 40 + "One thing: @user the key expired. More."
    pings.ping(sparks.find("scout"), text, "report")
    said = heard["pings"][0]["text"]
    assert said.startswith("One thing: @user")  # nosec B101


def test_no_spark_may_be_called_user(model):  # noqa: F811
    with pytest.raises(sparks.SparkError, match="@user"):
        sparks.create("User", "Anything.")


def test_extensions_can_follow_mentions(tmp_path):
    (tmp_path / "flash-extension.json").write_text(
        '{"name": "x", "events": {"run": ["python", "e.py"], '
        '"on": ["mention"]}}', encoding="utf-8")
    assert extensions.load(tmp_path).events.on == ("mention",)  # nosec B101


def test_an_event_this_flash_does_not_know_is_left_out(tmp_path):
    (tmp_path / "flash-extension.json").write_text(
        '{"name": "x", "events": {"run": ["python", "e.py"], '
        '"on": ["ask", "someday"]}}', encoding="utf-8")
    assert extensions.load(tmp_path).events.on == ("ask",)  # nosec B101
