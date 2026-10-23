# pylint: disable=C0114,C0115,C0116

import time

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from flash import ai, briefing, briefing_view


def cards():
    now = time.time()
    return [
        {"id": "email:me:7", "kind": "email", "label": "Email",
         "title": "Lunch?", "body": "Free at noon?", "at": now - 60,
         "who": "Ada", "uid": "7", "account": "me@example.com",
         "link": ""},
        {"id": "team:t1:1", "kind": "team", "label": "Team",
         "title": "Launch crew", "body": "Scout: Done\n1 other: nothing new",
         "at": now - 120, "team": "t1", "colour": "#5ab0f2",
         "who": "1 of 2 with news"},
    ]


def text(out):
    return "".join(t for _, t in out)


def test_a_card_its_keys_and_the_rail():
    drawn = text(briefing_view.draw(cards(), 1, 80, 24))
    lines = drawn.splitlines()

    assert len(lines) == 24  # nosec B101
    assert lines[0].startswith(" Briefing  2 of 2")  # nosec B101
    assert "Launch crew" in drawn and "Scout: Done" in drawn  # nosec B101
    # A team card opens its team chat; an email without a link has no
    # open at all.
    assert "o team chat" in lines[-1]  # nosec B101
    email_keys = text(briefing_view.draw(cards(), 0, 80, 24)).splitlines()
    assert "o mail" not in email_keys[-1]  # nosec B101
    assert "r reply" in email_keys[-1]  # nosec B101
    # One tick a card and one for the end, the one showing drawn long.
    assert drawn.count(briefing_view.RAIL) == 1  # nosec B101
    assert drawn.count(briefing_view.TICK) == 2  # nosec B101


def test_the_end_says_so():
    drawn = text(briefing_view.draw(cards(), 2, 60, 20,
                                    problems=("email",)))
    assert "all caught up" in drawn  # nosec B101
    assert "D dismiss all" in drawn  # nosec B101
    assert "Could not look at email." in drawn  # nosec B101
    nothing = text(briefing_view.draw([], 0, 60, 20))
    assert "Nothing new" in nothing and "dismiss all" not in nothing  # nosec


def run(keys, shown):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        with create_app_session(input=pipe, output=DummyOutput()):
            return briefing_view.feed(shown)


def test_moving_dismissing_and_picking(monkeypatch):
    gone = []
    monkeypatch.setattr(briefing, "dismiss", lambda ids: gone.extend(ids))
    shown = cards()

    # Dismiss the first; the team card takes its place; open it.
    action, card = run("do", shown)

    assert gone == ["email:me:7"]  # nosec B101
    assert (action, card["id"]) == ("open", "team:t1:1")  # nosec B101

    assert run("q", cards()) is None  # nosec B101
    # Down, up, then reply to the email.
    assert run("jkr", cards())[0] == "reply"  # nosec B101


def test_dismiss_all_at_the_end(monkeypatch):
    gone = []
    monkeypatch.setattr(briefing, "dismiss", lambda ids: gone.extend(ids))
    shown = cards()
    assert run("jjDq", shown) is None  # nosec B101
    assert gone == ["email:me:7", "team:t1:1"] and shown == []  # nosec B101


@pytest.fixture
def no_terminal(monkeypatch):
    printed = []
    monkeypatch.setattr(ai.model_picker, "can_pick", lambda: False)
    monkeypatch.setattr(ai.console, "print", printed.append)
    monkeypatch.setattr(briefing, "cards", lambda *a, **kw: cards())
    monkeypatch.setattr(briefing, "opened", lambda: 0.0)
    return printed


def test_without_a_terminal_it_lists_them(no_terminal):
    ai._briefing_command()
    said = [str(p) for p in no_terminal]
    assert said[1].startswith("Email: Lunch?")  # nosec B101
    assert said[-1] == "2 cards"  # nosec B101


def test_what_a_pick_does(monkeypatch):
    carried = []
    ran = []
    monkeypatch.setattr(ai, "carry", carried.append)
    monkeypatch.setattr(ai, "_sparks_command", ran.append)
    monkeypatch.setattr(ai.model_picker, "can_pick", lambda: True)
    monkeypatch.setattr(ai.console, "print", lambda *a, **kw: None)
    monkeypatch.setattr(briefing, "cards", lambda *a, **kw: cards())
    monkeypatch.setattr(briefing, "opened", lambda: 0.0)
    picks = iter([("reply", cards()[0]), ("open", cards()[1]),
                  ("ask", cards()[0])])
    monkeypatch.setattr(briefing_view, "feed", lambda *a: next(picks))

    for _ in range(3):
        ai._briefing_command()

    reply = ('Reply to the email from Ada about "Lunch?" '
             "(uid 7 in me@example.com). Say: ")
    assert carried[0] == reply  # nosec B101
    assert ran == ["teamchat t1"]  # nosec B101
    assert carried[1] == ('From my briefing (email, Ada): "Lunch?": '  # nosec
                          "Free at noon? ")
