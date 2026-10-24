# pylint: disable=C0114,C0115,C0116

import time
from types import SimpleNamespace

import pytest

from flash import briefing, computer, mail, sparks, waits, web

NOW = time.mktime((2026, 10, 23, 9, 0, 0, 0, 0, -1))


@pytest.fixture(autouse=True)
def quiet_sources(monkeypatch):
    monkeypatch.setattr(sparks, "all_sparks", lambda: [])
    monkeypatch.setattr(mail, "configured", lambda: False)
    monkeypatch.setattr(computer, "allowed", lambda kind: False)
    briefing._news.clear()
    yield
    briefing._news.clear()


def spark_with(*reports):
    return SimpleNamespace(id="s1", name="Scout", colour="#9b7bff",
                           reports=list(reports))


def chat(chat_id, log, updated=NOW - 60, busy=False, title="Plans"):
    return SimpleNamespace(id=chat_id, title=title, log=log, busy=busy,
                           updated=updated, spark="")


def test_unread_reports_and_finished_waits_come_newest_first(monkeypatch):
    monkeypatch.setattr(sparks, "all_sparks", lambda: [spark_with(
        sparks.Report(at=NOW - 600, text="Found 2 new issues."),
        sparks.Report(at=NOW - 300, text="Nothing new.", quiet=True),
        sparks.Report(at=NOW - 200, text="Read already.", read=True),
        sparks.Report(at=NOW - 3 * 86400, text="Too old."),
        sparks.Report(at=NOW - 100, text="May I push?", approval=True),
    )])
    waits.add(waits.Wait(id="w1", chat="c1", what="the build to pass",
                         then="tell me", status=waits.DONE,
                         said="It passed.", ended=NOW - 400))
    waits.add(waits.Wait(id="w2", chat="c1", what="a reply",
                         then="sum it up", status=waits.GAVE_UP,
                         ended=NOW - 50))
    waits.add(waits.Wait(id="w3", chat="c1", what="still going",
                         then="x", status=waits.WAITING))
    session = SimpleNamespace(chats={"c1": chat("c1", [], NOW - 9e5)})

    cards = briefing.cards(session, now=NOW)

    assert [c["id"] for c in cards] == [  # nosec B101
        "wait:w2", f"spark:s1:{int(NOW - 100)}", "wait:w1",
        f"spark:s1:{int(NOW - 600)}"]
    assert cards[0]["title"] == "Stopped waiting for a reply"  # nosec B101
    assert cards[0]["chat_title"] == "Plans"  # nosec B101
    assert cards[1]["title"] == "Scout needs you"  # nosec B101
    assert cards[2]["body"] == "It passed."  # nosec B101


def test_what_is_coming_up_goes_first_soonest_first():
    for wait_id, hours in (("later", 5), ("soon", 1), ("far", 40)):
        waits.add(waits.Wait(id=wait_id, chat="c1", what=wait_id,
                             then="remind me", at=NOW + hours * 3600,
                             status=waits.WAITING))
    waits.add(waits.Wait(id="done", chat="c1", what="x", then="y",
                         status=waits.DONE, ended=NOW - 10))

    cards = briefing.cards(None, now=NOW)

    assert [c["id"] for c in cards] == [  # nosec B101
        "reminder:soon", "reminder:later", "wait:done"]
    assert cards[0]["label"] == "Coming up"  # nosec B101


def test_unread_email_and_imessages(monkeypatch):
    monkeypatch.setattr(mail, "configured", lambda: True)
    asked = {}

    def inbox(**kw):
        asked.update(kw)
        return [{"uid": "7", "from": '"Ada L" <ada@example.com>',
                 "subject": "Lunch?", "snippet": "Free at noon?",
                 "at": NOW - 30, "account": "me@example.com", "link": ""}]

    monkeypatch.setattr(mail, "inbox", inbox)
    monkeypatch.setattr(computer, "allowed",
                        lambda kind: kind == "imessage_read")
    monkeypatch.setattr(computer, "read_imessages", lambda **kw: (
        "2 messages, newest first:\n"
        "Fri Oct 23, 08:58 · Sam (unread): on my way\n"
        "Fri Oct 23, 08:40 · Kim in Climbing (unread): 6pm?\n"
        "Fri Oct 23, 08:30 · You to Sam: ok"))

    cards = briefing.cards(None, now=NOW)

    assert asked["unread"] is True  # nosec B101
    email, sam, kim = cards
    assert (email["kind"], email["who"], email["title"]) == (  # nosec B101
        "email", "Ada L", "Lunch?")
    assert email["id"] == "email:me@example.com:7"  # nosec B101
    assert (sam["title"], sam["body"], sam["at"]) == (  # nosec B101
        "Sam", "on my way", NOW - 120)
    assert (kim["title"], kim["place"]) == (  # nosec B101
        "Kim in Climbing", "Climbing")


def test_chats_that_answered_lately_without_markdown():
    session = SimpleNamespace(chats={
        "a": chat("a", [{"type": "user", "text": "hi"},
                        {"type": "assistant",
                         "text": "## Plan\n- **Book** [it](http://x)"}]),
        "b": chat("b", [{"type": "assistant", "text": "x"},
                        {"type": "user", "text": "still waiting"}]),
        "c": chat("c", [{"type": "assistant", "text": "x"}], busy=True),
        "d": chat("d", [{"type": "assistant", "text": "x"}],
                  updated=NOW - 2 * 86400),
    })

    cards = briefing.cards(session, now=NOW)

    assert [c["chat"] for c in cards] == ["a"]  # nosec B101
    assert cards[0]["body"] == "Plan\nBook it"  # nosec B101


def test_dismissed_cards_stay_gone_and_old_ones_are_let_go():
    waits.add(waits.Wait(id="w1", chat="c", what="a", then="b",
                         status=waits.DONE, ended=NOW - 10))
    assert len(briefing.cards(None, now=NOW)) == 1  # nosec B101

    briefing.dismiss(["wait:w1"], now=NOW - 20 * 86400)
    briefing.dismiss(["other"], now=NOW)
    assert list(briefing.dismissed()) == ["other"]  # nosec B101

    briefing.dismiss(["wait:w1"], now=NOW)
    assert briefing.cards(None, now=NOW) == []  # nosec B101
    briefing.undismiss("wait:w1")
    assert len(briefing.cards(None, now=NOW)) == 1  # nosec B101


def test_a_source_that_fails_is_named_and_the_rest_still_come(monkeypatch):
    monkeypatch.setattr(mail, "configured", lambda: True)

    def broken(**kw):
        raise mail.MailError("no network")

    monkeypatch.setattr(mail, "inbox", broken)
    briefing.note("mcp:notes", "New in notes (from its list tool):\n- milk",
                  now=NOW - 5)
    problems = []

    cards = briefing.cards(None, now=NOW, problems=problems)

    assert problems == ["email: no network"]  # nosec B101
    assert [(c["kind"], c["title"], c["body"]) for c in cards] == [  # nosec
        ("news", "New in notes", "- milk")]


def test_connector_news_is_noted_once():
    briefing.note("mcp:notes", "same", now=NOW)
    briefing.note("mcp:notes", "same", now=NOW + 1)
    briefing.note("mcp:notes", "  ", now=NOW)
    assert len(briefing._news) == 1  # nosec B101


def test_the_page_asks_for_it_and_dismisses(monkeypatch):
    waits.add(waits.Wait(id="w1", chat="c", what="a", then="b",
                         status=waits.DONE, ended=time.time() - 10))
    session = SimpleNamespace(chats={})

    said = web.command(session, {"name": "briefing"})
    assert [c["id"] for c in said["cards"]] == ["wait:w1"]  # nosec B101
    assert said["problems"] == []  # nosec B101

    web.command(session, {"name": "briefing-dismiss", "arg": "all",
                          "cards": ["wait:w1", 5]})
    assert web.command(session, {"name": "briefing"})["cards"] == []  # nosec
    web.command(session, {"name": "briefing-undo", "arg": "wait:w1"})
    said = web.command(session, {"name": "briefing"})
    assert len(said["cards"]) == 1  # nosec B101


def a_team(monkeypatch, members, said=()):
    from flash import teamchat

    team = SimpleNamespace(id="t1", name="Launch crew", colour="#5ab0f2")
    monkeypatch.setattr(sparks, "teams", lambda: [team])
    monkeypatch.setattr(sparks, "members", lambda team_id: members)
    monkeypatch.setattr(sparks, "all_sparks", lambda: members)
    monkeypatch.setattr(teamchat, "_read", lambda team_id: {
        "entries": [{"kind": kind, "at": at, "text": "x"}
                    for kind, at in said]})
    return team


def member(spark_id, name, *reports, status=sparks.IDLE, activity=""):
    return SimpleNamespace(id=spark_id, name=name, colour="#9b7bff",
                           reports=list(reports), status=status,
                           activity=activity)


def test_a_team_says_what_changed_since_the_last_briefing(monkeypatch):
    since = NOW - 3600
    a_team(monkeypatch, [
        member("a", "Scout",
               sparks.Report(at=NOW - 7200, text="Old news."),
               sparks.Report(at=NOW - 600, text="## Done\n**Shipped** v2.")),
        member("b", "Fixer", sparks.Report(at=NOW - 300, text="May I merge?",
                                           approval=True)),
        member("c", "Tester", status=sparks.WORKING,
               activity="Running the tests"),
        member("d", "Sleepy"),
    ], said=[("spark", NOW - 200), ("user", NOW - 100),
             ("spark", NOW - 5000)])

    cards = briefing.cards(None, now=NOW, since=since)

    # One card for the team, and not one for each report as well.
    assert [c["kind"] for c in cards] == ["team"]  # nosec B101
    team = cards[0]
    assert team["title"] == "Launch crew"  # nosec B101
    assert team["body"].splitlines() == [  # nosec B101
        "Scout: Done",
        "Fixer (needs you): May I merge?",
        "Tester: working, running the tests",
        "1 new message in the team chat",
        "2 others: nothing new",
    ]
    assert team["at"] == NOW - 200 and team["team"] == "t1"  # nosec B101
    assert team["colour"] == "#5ab0f2"  # nosec B101


def test_a_team_with_nothing_new_has_no_card(monkeypatch):
    a_team(monkeypatch, [
        member("a", "Scout", sparks.Report(at=NOW - 7200, text="Old.",
                                           read=True)),
        member("b", "Tester", status=sparks.WORKING),
    ], said=[("user", NOW - 10)])

    # Only working is not news by itself, nor is the user's own message.
    cards = briefing.cards(None, now=NOW, since=NOW - 3600)
    assert cards == []  # nosec B101


def test_since_is_the_briefing_before_this_one():
    first = briefing.opened(now=NOW)
    assert first == NOW - briefing.LOOK_BACK  # nosec B101
    assert briefing.last_since(NOW + 60) == first  # nosec B101  (refresh)

    assert briefing.opened(now=NOW + 3600) == NOW  # nosec B101
    assert briefing.last_since(NOW + 3700) == NOW  # nosec B101

    # Weeks away: a week's news at most.
    later = NOW + 30 * 86400
    assert briefing.opened(now=later) == later - briefing.MOST_SINCE  # nosec


def test_new_until_the_briefing_shows_it():
    waits.add(waits.Wait(id="w1", chat="c", what="a", then="b",
                         status=waits.DONE, ended=time.time() - 10))
    assert briefing.has_new() is True  # nosec B101

    briefing.mark_seen(c["id"] for c in briefing.cards())
    assert briefing.has_new() is False  # nosec B101

    waits.add(waits.Wait(id="w2", chat="c", what="b", then="c",
                         status=waits.DONE, ended=time.time() - 5))
    assert briefing.has_new() is True  # nosec B101
