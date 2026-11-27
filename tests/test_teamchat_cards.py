# pylint: disable=C0114,C0115,C0116,W0212,W0621

import time

import pytest

from flash import pings, sparks, teamchat, tools, web
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


def _data(key):
    return teamchat._read(sparks.find_team(key).id)


def _entry(key, entry_id):
    return next(e for e in _data(key)["entries"] if e["id"] == entry_id)


def _took_on(job, answer="On it."):
    return FakeClient([
        _reply("", ("take_on", {"job": job})),
        _reply(answer),
    ])


# --- The work card on a shift taken on in the chat --------------------------


def test_a_job_taken_on_puts_a_work_card_on_the_answer(team):
    message = teamchat.say("desk", "@scout check issue 12")

    posted = teamchat.answer("desk", message, _took_on("Check issue 12."))

    card = posted[-1]
    assert card["text"] == "On it."  # nosec B101
    assert card["work"]["jobs"] == ["Check issue 12."]  # nosec B101
    shown = teamchat.history("desk")["work"][str(card["id"])]
    assert shown["state"] == "queued"  # nosec B101


def test_the_card_turns_done_with_its_report(team):
    message = teamchat.say("desk", "@scout check issue 12")
    card = teamchat.answer("desk", message, _took_on("Check issue 12."))[-1]
    scout = sparks.find("scout")

    report = sparks.shift(scout.id, client=FakeClient([
        _reply("Issue 12 is fixed upstream."),
    ]))

    assert report.began and not report.quiet  # nosec B101
    end = _entry("desk", card["id"])["work"]["end"]
    assert end["state"] == "done"  # nosec B101
    news = [e for e in _data("desk")["entries"]
            if e.get("what") == "report"]
    assert end["report"] == news[-1]["id"]  # nosec B101
    # Ended, it is no longer looked up on every poll.
    assert str(card["id"]) not in teamchat.history("desk")["work"]  # nosec
    changed = teamchat.history("desk", since=card["id"])["changed"]
    assert card["id"] in [e["id"] for e in changed]  # nosec B101


def test_a_shift_begun_before_the_card_does_not_close_it(team):
    message = teamchat.say("desk", "@scout check issue 12")
    card = teamchat.answer("desk", message, _took_on("Check issue 12."))[-1]
    scout = sparks.find("scout")
    since = card["work"]["since"]

    teamchat.event(team.id, scout, "report", "An older shift's news.",
                   report=sparks.Report(at=time.time(), text="Old.",
                                        began=since - 60))

    assert "end" not in _entry("desk", card["id"])["work"]  # nosec B101
    assert str(card["id"]) in teamchat.history("desk")["work"]  # nosec


def test_a_quiet_answer_with_a_job_is_news_with_the_card(team):
    message = teamchat.say("desk", "@scout check issue 12")

    posted = teamchat.answer("desk", message, _took_on("Check issue 12.", ""))

    took = posted[-1]
    assert took["kind"] == teamchat.EVENT  # nosec B101
    assert took["what"] == "took_on"  # nosec B101
    assert took["work"]["jobs"] == ["Check issue 12."]  # nosec B101


def test_how_a_shift_ended_reads_on_its_card():
    def end(**kw):
        return teamchat._work_end(sparks.Report(at=1.0, text="x", **kw))

    assert end()["state"] == "done"  # nosec B101
    assert end(failed=True)["state"] == "failed"  # nosec B101
    assert end(stopped=True)["state"] == "stopped"  # nosec B101


def test_the_card_says_when_the_spark_is_gone(team):
    message = teamchat.say("desk", "@scout check issue 12")
    card = teamchat.answer("desk", message, _took_on("Check issue 12."))[-1]

    sparks.remove("scout")

    shown = teamchat.history("desk")["work"][str(card["id"])]
    assert shown["state"] == "stopped"  # nosec B101


# --- Approve in the chat ----------------------------------------------------


def test_an_ask_is_a_card_asked_once(team):
    scout = sparks.find("scout")

    asked = teamchat.ask(team.id, scout, "hire:h1", "Hire Ada: test the app")
    again = teamchat.ask(team.id, scout, "hire:h1", "Hire Ada: test the app")

    assert asked["what"] == "ask"  # nosec B101
    assert asked["ask"]["state"] == "open"  # nosec B101
    assert again is None  # nosec B101


def test_an_ask_answered_elsewhere_reads_answered(team, monkeypatch):
    scout = sparks.find("scout")
    asked = teamchat.ask(team.id, scout, "hire:h1", "Hire Ada")
    monkeypatch.setattr(sparks, "hires", lambda: [{"id": "h1"}])
    asks = teamchat.history("desk")["asks"]
    assert asks[str(asked["id"])] == "open"  # nosec B101

    monkeypatch.setattr(sparks, "hires", lambda: [])

    assert teamchat.history("desk")["asks"][str(asked["id"])] == (  # nosec
        "answered")


def test_a_yes_in_the_chat_decides_the_hire(team, monkeypatch):
    scout = sparks.find("scout")
    asked = teamchat.ask(team.id, scout, "hire:h1", "Hire Ada")
    monkeypatch.setattr(sparks, "hires", lambda: [{"id": "h1"}])
    decided = []
    monkeypatch.setattr(sparks, "decide_hire",
                        lambda key, yes, why="": decided.append((key, yes)))

    card = teamchat.answer_ask("desk", asked["id"], True)

    assert decided == [("h1", True)]  # nosec B101
    assert card["ask"]["state"] == "yes"  # nosec B101
    with pytest.raises(teamchat.TeamChatError, match="already answered"):
        teamchat.answer_ask("desk", asked["id"], False)


def test_a_no_in_the_chat_answers_the_step(team, monkeypatch):
    scout = sparks.find("scout")
    asked = teamchat.ask(team.id, scout, f"step:{scout.id}:100", "Push it")
    monkeypatch.setattr(teamchat, "_ask_open", lambda ask_id, cache=None: True)
    answered = []
    monkeypatch.setattr(
        sparks, "answer_step",
        lambda key, yes, why="": answered.append((key, yes, why)),
    )

    card = teamchat.answer_ask("desk", asked["id"], False, "Not yet")

    assert answered == [(scout.id, False, "Not yet")]  # nosec B101
    assert card["ask"]["state"] == "no"  # nosec B101


def test_the_page_answers_an_ask(team, monkeypatch):
    scout = sparks.find("scout")
    asked = teamchat.ask(team.id, scout, "hire:h1", "Hire Ada")
    monkeypatch.setattr(sparks, "hires", lambda: [])

    with pytest.raises(ValueError, match="already answered"):
        web.command(web.Session(), {
            "name": "team-chat-answer", "arg": team.id,
            "id": asked["id"], "yes": True,
        })


def test_an_ask_reads_in_the_chat_the_sparks_see(team):
    scout = sparks.find("scout")
    teamchat.ask(team.id, scout, "hire:h1", "Hire Ada")

    seen = teamchat._view(team, sparks.find("lead"),
                          _data("desk")["entries"], 0)

    assert ("asked the user's yes: Hire Ada [waiting on the user]"  # nosec
            in seen)


# --- Thread cards: a title, and Resolve --------------------------------------


def _thread(team_id):
    root = teamchat.say(team_id, "Login is broken again.")
    reply = teamchat.say(team_id, "Since this morning.", thread=root["id"])
    return root, reply


def test_a_spark_names_the_thread_it_starts(team):
    root = teamchat.say("desk", "@scout login is broken")
    asked = teamchat.say("desk", "@scout look into it", thread=root["id"])

    teamchat.answer("desk", asked, FakeClient([
        _reply("", ("send_message", {"text": "Looking.",
                                     "title": "Login bug"})),
        _reply(""),
    ]))

    assert _entry("desk", root["id"])["thread_title"] == (  # nosec B101
        "Login bug")


def test_a_resolved_thread_pings_no_one_until_the_user_writes(
        team, monkeypatch):
    root, _ = _thread("desk")
    pinged = []
    monkeypatch.setattr(pings, "ping", lambda *a, **k: pinged.append(a))

    resolved = teamchat.resolve("desk", root["id"])
    assert resolved["resolved"]  # nosec B101
    teamchat._post(team.id, sparks.find("scout"),
                   {"text": "One more thing.", "thread": root["id"]})
    assert pinged == []  # nosec B101

    teamchat.say("desk", "Still broken.", thread=root["id"])

    assert "resolved" not in _entry("desk", root["id"])  # nosec B101


def test_resolving_opens_again(team):
    root, _ = _thread("desk")
    teamchat.resolve("desk", root["id"])

    opened = teamchat.resolve("desk", root["id"], False)

    assert "resolved" not in opened  # nosec B101


def test_only_a_thread_can_be_resolved(team):
    message = teamchat.say("desk", "No replies here.")

    with pytest.raises(teamchat.TeamChatError, match="no thread"):
        teamchat.resolve("desk", message["id"])


def test_a_thread_counts_its_files(team):
    root = teamchat.say("desk", "Here is the plan.")

    teamchat._post(team.id, sparks.find("scout"), {
        "text": "Notes.", "thread": root["id"],
        "files": [{"path": "notes.md", "title": "Notes"}],
    })

    assert _entry("desk", root["id"])["thread_files"] == 1  # nosec B101


# --- The draft card ---------------------------------------------------------


def _drafted(team_id):
    message = teamchat.say(team_id, "@writer draft the launch email")
    posted = teamchat.answer(team_id, message, FakeClient([
        _reply("", ("draft_message", {
            "kind": "email", "to": "ada@example.com", "subject": "Launch",
            "body": "We launch on Monday.",
        })),
        _reply(""),
    ]))
    return next(e for e in posted if e.get("draft"))


def test_a_spark_writes_a_draft_for_the_user_to_send(team):
    draft = _drafted("desk")

    assert draft["draft"] == {  # nosec B101
        "kind": "email", "to": "ada@example.com", "body":
        "We launch on Monday.", "subject": "Launch", "cc": "",
        "state": "open",
    }
    assert "draft email" in draft["text"]  # nosec B101


def test_a_draft_goes_as_the_user_left_it(team, monkeypatch):
    draft = _drafted("desk")
    went = []
    monkeypatch.setattr(teamchat, "_deliver",
                        lambda d: went.append(dict(d)) or d["to"])

    sent = teamchat.send_draft("desk", draft["id"], {"body": "Monday, 9am."})

    assert went[0]["body"] == "Monday, 9am."  # nosec B101
    assert sent["draft"]["state"] == "sent"  # nosec B101
    assert sent["draft"]["sent_to"] == "ada@example.com"  # nosec B101
    with pytest.raises(teamchat.TeamChatError):
        teamchat.send_draft("desk", draft["id"])


def test_a_draft_set_aside_is_never_sent(team, monkeypatch):
    draft = _drafted("desk")
    monkeypatch.setattr(teamchat, "_deliver",
                        lambda d: pytest.fail("sent"))  # pragma: no cover

    dropped = teamchat.send_draft("desk", draft["id"], send=False)

    assert dropped["draft"]["state"] == "dropped"  # nosec B101


def test_no_drafts_on_a_call(team):
    assert "draft_message" in teamchat.TEAM_CHAT_PROMPT  # nosec B101
    assert "draft_message" in teamchat.DM_PROMPT  # nosec B101


# --- Cards with buttons ------------------------------------------------------


def test_sparks_are_told_which_cards_they_can_post(team):
    said = teamchat.cards_prompt()

    for kind in teamchat.CARD_KINDS:
        assert f'"type": "{kind}"' in said  # nosec B101
    assert '"type": "sheet"' not in said  # nosec B101


# --- Hidden notes -----------------------------------------------------------


def test_a_hidden_note_leaves_the_chat_unread(team):
    teamchat.say("desk", "Seen it.")
    teamchat.mark_read("desk")
    read = _data("desk").get("read")

    teamchat.say("desk", "(Not from the user.)", hidden=True)

    assert _data("desk").get("read") == read  # nosec B101


@pytest.fixture
def fake_shell(monkeypatch):
    ran = []

    def shell(command, timeout=None):
        ran.append(command)
        return f"(ran {command})"

    monkeypatch.setitem(tools.FUNCTIONS, "shell", shell)
    return ran


def test_a_step_waiting_on_the_user_asks_in_the_team_chat(team, fake_shell):
    scout = sparks.find("scout")
    sparks.shift(scout.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"}),
               ("keep_notes", {"notes": "pulled"})),
    ]))
    asks = [e for e in _data("desk")["entries"] if e.get("what") == "ask"]
    assert len(asks) == 1 and "git pull" in asks[0]["ask"]["detail"]  # nosec
    assert teamchat.history("desk")["asks"][str(asks[0]["id"])] == (  # nosec
        "open")

    card = teamchat.answer_ask("desk", asks[0]["id"], True)

    assert card["ask"]["state"] == "yes"  # nosec B101
    assert not sparks.find("scout").waiting  # nosec B101
    assert scout.id in [s.id for s in sparks.due()]  # nosec B101
