# pylint: disable=C0114,C0115,C0116

import time
from datetime import datetime, timedelta

import pytest

from flash import agent, tools, waits, web


@pytest.fixture
def session(monkeypatch):
    """A page's session, its chat, and every turn nobody typed that a
    wait started, kept rather than run."""

    found = web.Session()
    # Ticked by hand, so a test sees each step.
    monkeypatch.setattr(found, "ensure_watching", lambda: None)
    chat = found.new_chat()
    chat.log.append({"type": "user", "text": "deploy when CI is green"})
    woke = []

    def send(chat, text, wake=False, note="", **_):
        woke.append({"chat": chat.id, "text": text, "wake": wake,
                     "note": note})
        return {}

    monkeypatch.setattr(found, "send", send)
    found.chat_ = chat
    found.woke = woke
    return found


def settle(session, seconds=5.0):
    """Let the looks started on their own threads finish."""

    deadline = time.monotonic() + seconds
    while session._checking and time.monotonic() < deadline:
        time.sleep(0.02)


def come(wait_id):
    """Its time, or its next look, is now."""

    waits.update(wait_id, next=time.time() - 1)


def only(session):
    found = waits.for_chat(session.chat_.id)
    assert len(found) == 1  # nosec B101
    return found[0]


def test_a_time_comes_and_the_task_carries_on(session):
    said = web.wait_for(session, session.chat_, {
        "what": "the download", "then": "check the file and unzip it",
        "in_minutes": 120,
    })

    wait = only(session)
    assert f"id {wait.id}" in said and "end your turn" in said  # nosec
    assert wait.at == pytest.approx(time.time() + 7200, abs=5)  # nosec
    # It shows in the chat, kept with it.
    card = session.chat_.log[-1]
    assert card["type"] == "wait" and card["status"] == "waiting"  # nosec

    session.tick_waits()
    assert session.woke == []  # nosec B101  (not yet)

    come(wait.id)
    session.tick_waits()

    assert session.woke[0]["wake"] is True  # nosec B101
    assert "Now, as you planned: check the file and unzip it" in (  # nosec
        session.woke[0]["text"]
    )
    assert session.woke[0]["note"] == "Done waiting: the download"  # nosec
    assert card["status"] == "done"  # nosec B101
    # Only ever once.
    session.tick_waits()
    assert len(session.woke) == 1  # nosec B101


def test_a_busy_chat_hears_it_once_it_is_free(session):
    web.wait_for(session, session.chat_, {
        "what": "the build", "then": "ship it", "in_minutes": 1,
    })
    come(only(session).id)
    session.chat_.busy = True

    session.tick_waits()
    assert session.woke == []  # nosec B101

    session.chat_.busy = False
    session.tick_waits()
    assert len(session.woke) == 1  # nosec B101


def test_a_command_is_run_until_it_succeeds(session, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(tools, "remote_answer",
                        lambda question: asked.append(question) or "y")
    ready = tmp_path / "ready"
    monkeypatch.chdir(tmp_path)

    web.wait_for(session, session.chat_, {
        "what": "the flag file", "then": "read it", "command": "test -f ready",
        "every_minutes": 1,
    })
    wait = only(session)
    assert "test -f ready" in asked[0]  # nosec B101  (asked once)
    assert wait.cwd == str(tmp_path)  # nosec B101

    session.tick_waits()
    settle(session)
    looked = waits.find(wait.id)
    assert looked.checks == 1 and not looked.found  # nosec B101
    assert looked.next > time.time() + 30  # nosec B101  (a minute on)
    assert session.woke == []  # nosec B101

    ready.write_text("go")
    come(wait.id)
    session.tick_waits()
    settle(session)
    session.tick_waits()

    assert "What you were waiting for is so." in session.woke[0]["text"]
    assert len(asked) == 1  # nosec B101


def test_a_command_the_user_refuses_sets_nothing(session, monkeypatch):
    monkeypatch.setattr(tools, "remote_answer", lambda question: "n")

    said = web.wait_for(session, session.chat_, {
        "what": "x", "then": "y", "command": "true",
    })

    assert said.startswith("Blocked by user")  # nosec B101
    assert waits.for_chat(session.chat_.id) == []  # nosec B101


def test_a_check_in_words_is_looked_for(session, monkeypatch):
    looks = []

    def look(task):
        looks.append(task)
        return True, "READY\nSam replied at 9:14 asking for Thursday."

    monkeypatch.setattr(agent, "run_now", look)
    web.wait_for(session, session.chat_, {
        "what": "Sam's reply", "then": "draft an answer",
        "check": "a reply from Sam is in my inbox",
    })

    session.tick_waits()
    settle(session)
    session.tick_waits()

    assert "a reply from Sam is in my inbox" in looks[0]  # nosec B101
    assert "change" in looks[0].lower()  # nosec B101  (looks only)
    assert "Sam replied at 9:14" in session.woke[0]["text"]  # nosec


def test_not_yet_looks_again_later(session, monkeypatch):
    monkeypatch.setattr(agent, "run_now",
                        lambda task: (True, "NOT YET\nNo reply."))
    web.wait_for(session, session.chat_, {
        "what": "a reply", "then": "answer", "check": "they replied",
        "every_minutes": 30,
    })

    session.tick_waits()
    settle(session)

    wait = only(session)
    assert wait.status == waits.WAITING and wait.said == "No reply."
    assert wait.next == pytest.approx(time.time() + 1800, abs=10)  # nosec


def test_a_check_gives_up_and_says_so(session, monkeypatch):
    # Autonomous mode: a command's wait asks nothing first.
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    web.wait_for(session, session.chat_, {
        "what": "the site to come back", "then": "tell me",
        "command": "false", "give_up_after_hours": 1,
    })
    wait = only(session)
    waits.update(wait.id, give_up=time.time() - 1, next=time.time() - 1)

    session.tick_waits()

    assert "You stopped waiting" in session.woke[0]["text"]  # nosec
    assert session.woke[0]["note"].startswith("Stopped waiting")  # nosec
    assert only(session).status == waits.GAVE_UP  # nosec B101


def test_cancelled_by_the_model_the_page_or_deleting_the_chat(session):
    for n in range(3):
        web.wait_for(session, session.chat_, {
            "what": f"thing {n}", "then": "go", "in_minutes": 60,
        })
    first, second, third = waits.for_chat(session.chat_.id)

    assert "Stopped waiting" in web.cancel_wait(  # nosec B101
        session, session.chat_, {"id": first.id})
    web.command(session, {"name": "wait-cancel", "arg": second.id})
    assert "Error" in web.cancel_wait(  # nosec B101
        session, session.chat_, {"id": second.id})
    session.delete_chat(session.chat_.id)

    assert {w.status for w in waits.for_chat(session.chat_.id)} == {  # nosec
        waits.CANCELLED,
    }
    assert third.status == waits.CANCELLED  # nosec B101


def test_a_wait_outlasts_a_restart(session):
    web.wait_for(session, session.chat_, {
        "what": "Monday", "then": "send the report", "in_minutes": 5,
    })
    come(only(session).id)
    waits.forget_cache()

    # Flash was not running when its time came: it is over at once.
    assert waits.due()[0].what == "Monday"  # nosec B101


@pytest.mark.parametrize("args, why", [
    ({"then": "x", "in_minutes": 5}, "what you are waiting for"),
    ({"what": "x", "in_minutes": 5}, "what to do"),
    ({"what": "x", "then": "y"}, "Give a time"),
    ({"what": "x", "then": "y", "in_minutes": -1}, "more than 0"),
    ({"what": "x", "then": "y", "at": "soon"}, "is not a time"),
    ({"what": "x", "then": "y", "at": "2001-01-01T09:00"}, "already passed"),
    ({"what": "x", "then": "y", "in_minutes": 60 * 24 * 40}, "at most"),
])
def test_what_a_wait_needs(args, why):
    with pytest.raises(waits.WaitError, match=why):
        waits.make("c1", args)


def test_times_and_looks_have_sensible_bounds():
    later = (datetime.now().astimezone() + timedelta(days=1)).replace(
        microsecond=0)
    # Given as local time, with no zone, as the model gives it.
    timed = waits.make("c1", {"what": "x", "then": "y",
                              "at": later.replace(tzinfo=None).isoformat()})
    assert timed.at == later.timestamp() and timed.give_up is None  # nosec

    looked = waits.make("c1", {"what": "x", "then": "y", "check": "z",
                               "every_minutes": 0.1})
    assert looked.every == waits.MIN_CHECK_EVERY  # nosec B101
    assert looked.give_up == pytest.approx(  # nosec B101
        looked.created + waits.DEFAULT_GIVE_UP_HOURS * 3600)

    for n in range(waits.MAX_OPEN_PER_CHAT):
        waits.add(waits.make("c2", {"what": f"{n}", "then": "y",
                                    "in_minutes": 5}))
    with pytest.raises(waits.WaitError, match="already waits"):
        waits.make("c2", {"what": "one more", "then": "y", "in_minutes": 5})


def test_the_model_is_offered_it_in_a_chat():
    chat = web.Chat(id="c3")
    names = [t["function"]["name"] for t in web.chat_tools(chat)]

    assert "wait_for" in names and "cancel_wait" in names  # nosec B101


def test_new_messages_wake_the_chat_each_time_and_it_keeps_waiting(
        session, monkeypatch):
    came = [""]
    monkeypatch.setattr(waits, "check", lambda wait: (bool(came[0]),
                                                      came[0]))
    said = web.wait_for(session, session.chat_, {
        "what": "new texts", "then": "answer each one",
        "events": "imessage", "keep": True,
    })
    assert "waking for each one" in said  # nosec B101

    session.tick_waits()
    settle(session)
    assert session.woke == []  # nosec B101  (nothing yet)

    for text in ("Sam: are you free?", "Bo: lunch?"):
        came[0] = f"New iMessages:\n- {text}"
        come(only(session).id)
        session.tick_waits()
        settle(session)
        session.tick_waits()
        came[0] = ""

    assert [w["wake"] for w in session.woke] == [True, True]  # nosec
    assert "Sam: are you free?" in session.woke[0]["text"]  # nosec B101
    assert "Bo: lunch?" in session.woke[1]["text"]  # nosec B101
    assert "answer each one" in session.woke[1]["text"]  # nosec B101
    wait = only(session)
    assert wait.status == waits.WAITING and not wait.found  # nosec B101
    assert wait.next == pytest.approx(  # nosec B101
        time.time() + waits.EVENT_SECONDS, abs=5)


def test_an_event_wait_without_keep_is_over_once_one_comes_in(
        session, monkeypatch):
    monkeypatch.setattr(waits, "check",
                        lambda wait: (True, "New email:\n- From Sam: Re"))
    web.wait_for(session, session.chat_, {
        "what": "Sam's reply", "then": "summarise it", "events": "email",
    })

    session.tick_waits()
    settle(session)
    session.tick_waits()

    assert only(session).status == waits.DONE  # nosec B101
    assert "From Sam: Re" in session.woke[0]["text"]  # nosec B101
