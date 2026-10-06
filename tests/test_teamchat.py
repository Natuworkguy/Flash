# pylint: disable=C0114,C0115,C0116

import time

import pytest

from flash import sparks, teamchat, tools, web
from tests.test_sparks import FakeClient, _reply


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")


@pytest.fixture
def team(model):
    """Desk: Lead, with Scout and Writer reporting to it."""

    made = sparks.create_team("Desk")
    for name in ("Lead", "Scout", "Writer"):
        sparks.create(name, f"{name}'s job.")
        sparks.set_team(name.lower(), "desk")
    sparks.set_lead("scout", "lead")
    sparks.set_lead("writer", "lead")
    return made


def _said(team_id, kind=None):
    return [
        e for e in teamchat.history(team_id)["entries"]
        if kind is None or e["kind"] == kind
    ]


def test_joining_a_team_is_news_in_its_chat(team):
    joined = _said(team.id, teamchat.EVENT)

    assert [(e["name"], e["what"]) for e in joined] == [  # nosec B101
        ("Lead", "joined"), ("Scout", "joined"), ("Writer", "joined"),
    ]


def test_the_lead_answers_a_message_to_no_one(team):
    message = teamchat.say("desk", "Morning, team. Anything new?")
    client = FakeClient([_reply("All quiet here.")])

    posted = teamchat.answer("desk", message, client)

    assert [(e["name"], e["text"]) for e in posted] == [  # nosec B101
        ("Lead", "All quiet here."),
    ]
    talk = [e["text"] for e in _said(team.id) if e["kind"] != "event"]
    assert talk == [  # nosec B101
        "Morning, team. Anything new?", "All quiet here.",
    ]
    # It saw the message it was answering.
    seen = client.calls[0]["messages"][-1]["content"]
    assert "The user: Morning, team. Anything new?" in seen  # nosec B101
    system = client.calls[0]["messages"][0]["content"]
    assert "Desk's group chat" in system  # nosec B101


def test_a_mentioned_spark_answers_instead(team):
    message = teamchat.say("desk", "@scout what did you find?")
    client = FakeClient([_reply("Two bugs.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Scout"]  # nosec B101


def test_a_spark_brings_a_teammate_in(team):
    message = teamchat.say("desk", "Who is writing the summary?")
    client = FakeClient([
        _reply("@writer can you take this?"),
        _reply("On it."),
    ])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Lead", "Writer"]  # nosec B101


def test_sparks_cannot_talk_forever(team, monkeypatch):
    monkeypatch.setattr(teamchat, "REPLY_LIMIT", 2)
    message = teamchat.say("desk", "@scout @writer @lead all of you")
    client = FakeClient([_reply("Here.") for _ in range(5)])

    posted = teamchat.answer("desk", message, client)

    assert len(posted) == 2  # nosec B101


def test_a_spark_that_cannot_answer_says_so(team, monkeypatch):
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    message = teamchat.say("desk", "Hello?")

    posted = teamchat.answer("desk", message, FakeClient([]))

    assert posted[0]["failed"] is True  # nosec B101
    assert "could not answer" in posted[0]["text"]  # nosec B101
    assert teamchat.history("desk")["typing"] == []  # nosec B101


def test_an_empty_message_or_team_is_refused(model):
    sparks.create_team("Empty")
    with pytest.raises(teamchat.TeamChatError, match="no sparks"):
        teamchat.say("empty", "hello")
    with pytest.raises(teamchat.TeamChatError, match="Say something"):
        teamchat.say("empty", "  ")
    with pytest.raises(teamchat.TeamChatError, match="no team"):
        teamchat.say("nowhere", "hello")


def test_a_report_lands_in_the_chat(team):
    sparks.shift(
        sparks.find("scout").id,
        client=FakeClient([_reply("Issue 12 is new.")]),
    )

    news = _said(team.id, teamchat.EVENT)[-1]

    assert (news["name"], news["what"]) == ("Scout", "report")  # nosec B101
    assert news["text"] == "Issue 12 is new."  # nosec B101


def test_a_quiet_shift_stays_out_of_the_chat(team):
    before = len(_said(team.id))
    sparks.shift(
        sparks.find("scout").id, client=FakeClient([_reply("NOTHING NEW")]),
    )

    assert len(_said(team.id)) == before  # nosec B101


def test_every_member_reads_the_latest_of_the_chat(team):
    teamchat.say("desk", "From now on, keep reports to three lines.")

    block = sparks.team_block(sparks.find("writer"))

    assert "The latest in your team's group chat" in block  # nosec B101
    assert "keep reports to three lines" in block  # nosec B101
    loner = sparks.create("Loner", "Alone.")
    assert teamchat.prompt_block(loner) == ""  # nosec B101


def test_unread_counts_what_the_sparks_said(team):
    assert teamchat.unread(team.id) == 3  # nosec B101 (three joined)

    teamchat.mark_read("desk")

    assert teamchat.unread(team.id) == 0  # nosec B101
    teamchat.event(team.id, sparks.find("scout"), "report", "News.")
    assert teamchat.unread(team.id) == 1  # nosec B101


def test_history_hands_over_only_what_is_new(team):
    seq = teamchat.history("desk")["seq"]
    teamchat.say("desk", "One more thing.")

    later = teamchat.history("desk", since=seq)

    said = [e["text"] for e in later["entries"]]
    assert said == ["One more thing."]  # nosec B101


def test_a_message_sent_meanwhile_is_answered_next(team, monkeypatch):
    first = teamchat.say("desk", "First?")
    second = teamchat.say("desk", "Second?")
    client = FakeClient([_reply("To the first."), _reply("To the second.")])
    real_round = teamchat._round
    calls = []

    def round_(team_, message, client_=None):
        calls.append(message["text"])
        if len(calls) == 1:
            # The second comes in while the first is being answered.
            assert teamchat.answer("desk", second, client_) == []  # nosec B101
        return real_round(team_, message, client_)

    monkeypatch.setattr(teamchat, "_round", round_)
    posted = teamchat.answer("desk", first, client)

    assert calls == ["First?", "Second?"]  # nosec B101
    assert [e["text"] for e in posted] == [  # nosec B101
        "To the first.", "To the second.",
    ]


def test_the_page_reads_and_sends(team, monkeypatch):
    sent = []
    monkeypatch.setattr(
        teamchat, "answer", lambda key, message, client=None: sent.append(
            message["text"]
        ),
    )
    session = web.Session()

    posted = web.command(session, {
        "name": "team-chat-say", "arg": team.id, "text": "Hi all",
    })
    deadline = time.monotonic() + 2
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    read = web.command(session, {
        "name": "team-chat", "arg": team.id, "read": True,
    })

    assert posted["entry"]["text"] == "Hi all"  # nosec B101
    assert sent == ["Hi all"]  # nosec B101
    assert read["entries"][-1]["text"] == "Hi all"  # nosec B101
    assert read["unread"] == 0  # nosec B101
    info = web.team_info(sparks.find_team("desk"))
    assert info["chat_unread"] == 0  # nosec B101
    with pytest.raises(ValueError, match="Say something"):
        web.command(session, {
            "name": "team-chat-say", "arg": team.id, "text": "",
        })


def test_the_terminal_shows_and_sends(team, monkeypatch, capsys):
    from flash import ai

    monkeypatch.setattr(
        ai, "_client", lambda: FakeClient([_reply("Here and working.")]),
    )

    ai._sparks_command("teamchat desk Is everyone around?")
    ai._sparks_command("teamchat desk")
    out = capsys.readouterr().out

    assert "Here and working." in out  # nosec B101
    assert "Is everyone around?" in out  # nosec B101
    assert "joined the team" in out  # nosec B101
    assert teamchat.unread(team.id) == 0  # nosec B101


def test_the_terminal_names_a_team_with_spaces(model, monkeypatch, capsys):
    from flash import ai

    sparks.create_team("Dev Team")
    sparks.create("Lead", "Lead.")
    sparks.set_team("lead", "dev team")
    monkeypatch.setattr(ai, "_client", lambda: FakeClient([_reply("Yes?")]))

    ai._sparks_command("teamchat Dev Team hello there")

    said = [e["text"] for e in teamchat.history("dev team")["entries"]]
    assert "hello there" in said and "Yes?" in said  # nosec B101


def test_marking_it_read_twice_writes_once(team):
    changes = []
    teamchat.on_change(changes.append)
    try:
        assert teamchat.mark_read("desk") is True  # nosec B101
        assert teamchat.mark_read("desk") is False  # nosec B101
    finally:
        teamchat._listeners.remove(changes.append)

    assert changes == [team.id]  # nosec B101
