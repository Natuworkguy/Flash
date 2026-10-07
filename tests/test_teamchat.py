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


def test_a_reply_calls_on_the_spark_it_answers(team):
    said = teamchat._change(team.id, lambda data: teamchat._add(data, {
        "kind": teamchat.SPARK, "spark": sparks.find("writer").id,
        "name": "Writer", "text": "The summary is half done.",
    }))
    message = teamchat.say("desk", "When will it be ready?", said["id"])
    client = FakeClient([_reply("By noon.")])

    posted = teamchat.answer("desk", message, client)

    # Writer answers, not the lead, and reads which message is meant.
    assert [e["name"] for e in posted] == ["Writer"]  # nosec B101
    quoted = message["reply"]["text"]
    assert quoted == "The summary is half done."  # nosec B101
    seen = client.calls[0]["messages"][-1]["content"]
    assert "replying to Writer's message" in seen  # nosec B101
    assert '"The summary is half done."' in seen  # nosec B101


def test_a_reply_and_a_mention_both_answer(team):
    said = teamchat._change(team.id, lambda data: teamchat._add(data, {
        "kind": teamchat.SPARK, "spark": sparks.find("writer").id,
        "name": "Writer", "text": "Draft is up.",
    }))
    message = teamchat.say("desk", "@scout can you check it?", said["id"])
    client = FakeClient([_reply("Thanks."), _reply("Checking.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Writer", "Scout"]  # nosec B101


def test_a_reply_to_news_calls_on_its_spark(team):
    news = teamchat.event(team.id, sparks.find("scout"), "report", "Found 2.")
    message = teamchat.say("desk", "Which two?", news["id"])
    client = FakeClient([_reply("12, 14.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Scout"]  # nosec B101
    assert message["reply"]["what"] == "report"  # nosec B101


def test_a_reply_to_your_own_message_goes_to_the_lead(team):
    first = teamchat.say("desk", "Plan for today?")
    message = teamchat.say("desk", "Also, the release.", first["id"])

    posted = teamchat.answer("desk", message, FakeClient([_reply("Noted.")]))

    assert [e["name"] for e in posted] == ["Lead"]  # nosec B101
    assert "replying to their own message" in teamchat._line(  # nosec B101
        message, {},
    )


def test_a_reply_to_a_message_that_is_gone_is_refused(team):
    with pytest.raises(teamchat.TeamChatError, match="no longer"):
        teamchat.say("desk", "Hm?", 9999)


def test_the_page_sends_a_reply(team, monkeypatch):
    monkeypatch.setattr(teamchat, "answer", lambda *a, **k: None)
    news = teamchat.event(team.id, sparks.find("scout"), "report", "Found 2.")

    posted = web.command(web.Session(), {
        "name": "team-chat-say", "arg": team.id, "text": "Which?",
        "reply_to": news["id"],
    })

    assert posted["entry"]["reply"]["id"] == news["id"]  # nosec B101


def test_everyone_calls_on_the_whole_team(team, monkeypatch):
    monkeypatch.setattr(teamchat, "REPLY_LIMIT", 2)
    message = teamchat.say("desk", "@everyone stand-up: what are you on?")
    client = FakeClient([_reply("Leading."), _reply("Looking."),
                         _reply("Writing.")])

    posted = teamchat.answer("desk", message, client)

    # All three, past the usual limit, the lead first.
    assert [e["name"] for e in posted][0] == "Lead"  # nosec B101
    assert sorted(e["name"] for e in posted) == [  # nosec B101
        "Lead", "Scout", "Writer",
    ]


def test_a_sparks_own_everyone_calls_no_one(team):
    message = teamchat.say("desk", "@writer what's new?")
    client = FakeClient([_reply("Asking @everyone, hold on.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Writer"]  # nosec B101


def test_everyone_in_a_chat_calls_every_spark(model):
    sparks.create("Loner", "Alone.")
    sparks.create("Other", "Other.")

    named = sparks.mentioned("@other and @everyone, hi", everyone=True)

    assert [s.name for s in named] == ["Other", "Loner"]  # nosec B101
    assert sparks.mentioned("@everyone hi") == []  # nosec B101


def test_everyone_answers_at_most_a_dozen(model, monkeypatch):
    monkeypatch.setattr(sparks, "EVERYONE_MOST", 2)
    for name in ("A1", "B2", "C3"):
        sparks.create(name, "Job.")

    named = sparks.mentioned("@c3 @everyone", everyone=True)

    assert [s.name for s in named] == ["C3", "A1"]  # nosec B101


def test_no_spark_can_be_called_everyone(model):
    for name in ("Everyone", "everyone"):
        with pytest.raises(sparks.SparkError, match="kept for @everyone"):
            sparks.create(name, "Job.")
    made = sparks.create("Scout", "Job.")
    with pytest.raises(sparks.SparkError, match="kept for @everyone"):
        sparks.update(made.id, name="Everyone")


def test_a_team_chat_reply_carries_its_documents(team):
    message = teamchat.say("desk", "@writer write up the plan?")
    client = FakeClient([
        _reply("", ("make_document", {"title": "Plan", "content": "Do."})),
        _reply("Done, here it is."),
    ])

    posted = teamchat.answer("desk", message, client)

    assert [f["title"] for f in posted[0]["files"]] == ["Plan"]  # nosec B101


def test_report_news_carries_its_documents(team):
    sparks.shift(sparks.find("scout").id, client=FakeClient([
        _reply("", ("make_document", {"title": "Bugs", "content": "Two."})),
        _reply("Wrote up the bugs."),
    ]))

    news = _said(team.id, teamchat.EVENT)[-1]

    assert [f["title"] for f in news["files"]] == ["Bugs"]  # nosec B101


def test_the_page_opens_a_sparks_document(team, tmp_path):
    writer = sparks.find("writer")
    path = sparks.write_document(writer.id, "Plan", "Do.")
    session = web.Session()

    opened = web.command(session, {
        "name": "spark-document", "arg": writer.id, "path": str(path),
    })
    listed = web.command(session, {
        "name": "spark-documents", "arg": writer.id,
    })

    assert opened["file"]["kind"] == "doc"  # nosec B101
    assert [d["title"] for d in listed["documents"]] == ["Plan"]  # nosec B101
    with pytest.raises(ValueError, match="No such document"):
        web.command(session, {
            "name": "spark-document", "arg": writer.id,
            "path": str(tmp_path / "x.md"),
        })


@pytest.mark.parametrize("said", ["NO_REPLY", "no reply.", "**NO_REPLY**", ""])
def test_a_spark_can_stay_quiet_in_the_team_chat(team, said):
    before = len(_said(team.id))
    message = teamchat.say("desk", "Morning all.")

    posted = teamchat.answer("desk", message, FakeClient([_reply(said)]))

    assert posted == []  # nosec B101
    # Only the user's message is new, and nobody is left typing.
    assert len(_said(team.id)) == before + 1  # nosec B101
    assert teamchat.history("desk")["typing"] == []  # nosec B101


def test_a_quiet_spark_brings_no_one_in(team):
    message = teamchat.say("desk", "@scout @writer anything?")
    client = FakeClient([_reply("NO_REPLY"), _reply("Draft is up.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Writer"]  # nosec B101


def test_a_quiet_spark_that_wrote_a_document_still_posts_it(team):
    message = teamchat.say("desk", "@writer the plan?")
    client = FakeClient([
        _reply("", ("make_document", {"title": "Plan", "content": "Do."})),
        _reply("NO_REPLY"),
    ])

    posted = teamchat.answer("desk", message, client)

    assert posted[0]["text"] == "I wrote this up."  # nosec B101
    assert posted[0]["files"][0]["title"] == "Plan"  # nosec B101


def test_staying_quiet_is_only_for_the_team_chat(model):
    made = sparks.create("Scout", "Watch.")

    reply = sparks.say(made.id, "Hi?", FakeClient([_reply("NO_REPLY")]))

    # In a chat of its own it is posted as it said it: no silence there.
    assert reply.text == "NO_REPLY"  # nosec B101
    prompt = sparks.chat_prompt(made, "h", "m", "")
    assert "NO_REPLY" not in prompt  # nosec B101


def _send(text):
    return _reply("", ("send_message", {"text": text}))


def test_a_spark_can_send_messages_in_a_row(team):
    message = teamchat.say("desk", "@scout any new bugs?")
    client = FakeClient([_send("On it, checking."), _reply("Found 2.")])

    posted = teamchat.answer("desk", message, client)

    assert [(e["name"], e["text"]) for e in posted] == [  # nosec B101
        ("Scout", "On it, checking."), ("Scout", "Found 2."),
    ]
    talk = [e["text"] for e in _said(team.id, teamchat.SPARK)]
    assert talk == ["On it, checking.", "Found 2."]  # nosec B101
    assert teamchat.history("desk")["typing"] == []  # nosec B101
    # It could, since it was told how.
    names = [t["function"]["name"] for t in client.calls[0]["tools"]]
    assert "send_message" in names  # nosec B101


def test_what_it_sent_can_be_all_it_says(team):
    message = teamchat.say("desk", "@scout ping")
    client = FakeClient([_send("Pong."), _reply("NO_REPLY")])

    posted = teamchat.answer("desk", message, client)

    assert [e["text"] for e in posted] == ["Pong."]  # nosec B101
    assert teamchat.history("desk")["typing"] == []  # nosec B101


def test_an_answer_that_repeats_what_it_sent_is_posted_once(team):
    message = teamchat.say("desk", "@scout ping")
    client = FakeClient([_send("Pong."), _reply("Pong.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["text"] for e in posted] == ["Pong."]  # nosec B101


def test_a_spark_cannot_flood_the_chat(team, monkeypatch):
    monkeypatch.setattr(teamchat, "MAX_SENT", 2)
    message = teamchat.say("desk", "@scout talk")
    client = FakeClient([
        _send("One."), _send("Two."), _send("Three."), _reply("Done."),
    ])

    posted = teamchat.answer("desk", message, client)

    texts = [e["text"] for e in posted]
    assert texts == ["One.", "Two.", "Done."]  # nosec B101
    refused = client.calls[3]["messages"][-1]["content"]
    assert "2 messages this turn already" in refused  # nosec B101


def test_a_sent_message_brings_a_teammate_in(team):
    message = teamchat.say("desk", "@scout who writes it up?")
    client = FakeClient([
        _send("@writer can you take this?"), _reply("NO_REPLY"),
        _reply("On it."),
    ])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Scout", "Writer"]  # nosec B101


def test_several_messages_are_one_turn_of_the_limit(team, monkeypatch):
    monkeypatch.setattr(teamchat, "REPLY_LIMIT", 1)
    message = teamchat.say("desk", "@scout @writer both of you")
    client = FakeClient([_send("Looking."), _reply("Found it."),
                         _reply("Here.")])

    posted = teamchat.answer("desk", message, client)

    assert [e["name"] for e in posted] == ["Scout", "Scout"]  # nosec B101


THUMBS = "\U0001f44d"
PARTY = "\U0001f389"


def test_the_user_reacts_and_takes_it_back(team):
    message = teamchat.say("desk", "Shipped it.")

    on = teamchat.react("desk", message["id"], THUMBS)
    off = teamchat.react("desk", message["id"], THUMBS)

    assert on["reactions"] == {THUMBS: ["user"]}  # nosec B101
    assert "reactions" not in off  # nosec B101


@pytest.mark.parametrize("emoji", [
    "❤️", "\U0001f44d\U0001f3fd", "\U0001f468‍\U0001f4bb",
])
def test_any_one_emoji_will_do(team, emoji):
    message = teamchat.say("desk", "Hi.")

    reacted = teamchat.react("desk", message["id"], emoji)

    assert list(reacted["reactions"]) == [emoji]  # nosec B101


@pytest.mark.parametrize("emoji", ["", "ok", "a\U0001f44d", ":)", " "])
def test_a_reaction_must_be_an_emoji(team, emoji):
    message = teamchat.say("desk", "Hi.")

    with pytest.raises(teamchat.TeamChatError, match="emoji"):
        teamchat.react("desk", message["id"], emoji)


def test_a_reaction_to_a_message_that_is_gone_is_refused(team):
    with pytest.raises(teamchat.TeamChatError, match="no longer"):
        teamchat.react("desk", 999, THUMBS)


def test_a_message_carries_only_so_many_reactions(team, monkeypatch):
    monkeypatch.setattr(teamchat, "MAX_REACTIONS", 1)
    message = teamchat.say("desk", "Hi.")
    teamchat.react("desk", message["id"], THUMBS)

    with pytest.raises(teamchat.TeamChatError, match="1 reactions"):
        teamchat.react("desk", message["id"], PARTY)
    # One already on it can still be added to.
    teamchat.react("desk", message["id"], THUMBS, by="lead-id")


def test_the_page_hears_of_a_reaction_to_what_it_has(team):
    message = teamchat.say("desk", "Shipped it.")
    seq = teamchat.history("desk")["seq"]

    teamchat.react("desk", message["id"], PARTY)
    later = teamchat.history("desk", since=seq)
    nothing = teamchat.history("desk", since=later["seq"])
    teamchat.say("desk", "Next.")

    assert later["entries"] == []  # nosec B101
    assert [e["id"] for e in later["changed"]] == [message["id"]]  # nosec B101
    assert later["changed"][0]["reactions"] == {PARTY: ["user"]}  # nosec B101
    assert nothing["changed"] == []  # nosec B101
    # No message takes the number the reaction moved the chat on to.
    ids = [e["id"] for e in _said(team.id)]
    assert len(ids) == len(set(ids))  # nosec B101


def test_the_page_reacts(team):
    message = teamchat.say("desk", "Shipped it.")
    session = web.Session()

    reacted = web.command(session, {
        "name": "team-chat-react", "arg": team.id, "id": message["id"],
        "emoji": THUMBS,
    })

    assert reacted["entry"]["reactions"] == {THUMBS: ["user"]}  # nosec B101
    with pytest.raises(ValueError, match="emoji"):
        web.command(session, {
            "name": "team-chat-react", "arg": team.id,
            "id": message["id"], "emoji": "yes",
        })


def test_a_spark_reacts_instead_of_answering(team):
    message = teamchat.say("desk", "@scout thanks for the fix!")
    client = FakeClient([
        _reply("", ("react", {"message": message["id"], "emoji": THUMBS})),
        _reply("NO_REPLY"),
    ])

    posted = teamchat.answer("desk", message, client)

    scout = sparks.find("scout")
    assert posted == []  # nosec B101
    reacted = [e for e in _said(team.id) if e["id"] == message["id"]][0]
    assert reacted["reactions"] == {THUMBS: [scout.id]}  # nosec B101
    assert teamchat.history("desk")["typing"] == []  # nosec B101
    # It saw the message's number, to react to it by.
    seen = client.calls[0]["messages"][-1]["content"]
    assert f"#{message['id']} The user: @scout thanks" in seen  # nosec B101


def test_a_spark_reacting_twice_keeps_its_reaction(team):
    message = teamchat.say("desk", "@scout well done")
    react = ("react", {"message": message["id"], "emoji": THUMBS})
    client = FakeClient([_reply("", react), _reply("", react),
                         _reply("NO_REPLY")])

    teamchat.answer("desk", message, client)

    reacted = [e for e in _said(team.id) if e["id"] == message["id"]][0]
    assert reacted["reactions"] == {  # nosec B101
        THUMBS: [sparks.find("scout").id],
    }


def test_a_spark_is_told_when_it_cannot_react(team):
    message = teamchat.say("desk", "@scout hi")
    client = FakeClient([
        _reply("", ("react", {"message": 999, "emoji": THUMBS})),
        _reply("", ("react", {"message": message["id"], "emoji": "yes"})),
        _reply("Hi."),
    ])

    teamchat.answer("desk", message, client)

    told = [c["messages"][-1]["content"] for c in client.calls[1:]]
    assert "no longer in the chat" in told[0]  # nosec B101
    assert "emoji" in told[1]  # nosec B101


def test_sparks_read_who_reacted(team):
    message = teamchat.say("desk", "Release is out.")
    teamchat.react("desk", message["id"], PARTY)
    teamchat.react("desk", message["id"], PARTY, by=sparks.find("lead").id)

    block = teamchat.prompt_block(sparks.find("scout"))

    assert f"[reactions: {PARTY} the user, Lead]" in block  # nosec B101


def test_the_terminal_shows_reactions(team, capsys):
    from flash import ai

    message = teamchat.say("desk", "Release is out.")
    teamchat.react("desk", message["id"], PARTY)

    ai._sparks_command("teamchat desk")

    assert f"{PARTY} 1" in capsys.readouterr().out  # nosec B101
