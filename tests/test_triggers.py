# pylint: disable=C0114,C0115,C0116

import pytest

from flash import computer, connectors, mail, sparks, tools, triggers


@pytest.fixture(autouse=True)
def events_on(monkeypatch, tmp_path):
    monkeypatch.setenv(triggers.SETTING, "1")
    monkeypatch.setattr("flash.notify.ENV_PATH", tmp_path / ".env")


def test_events_read_as_a_spark_keeps_them():
    assert triggers.parse("email, imessages mcp:Notes mail") == [  # nosec
        "email", "imessage", "mcp:notes"]
    assert triggers.parse(["mcp:notes/search"]) == [  # nosec B101
        "mcp:notes/search"]
    with pytest.raises(ValueError, match="mcp:<server>"):
        triggers.parse("printer")
    assert triggers.words("mcp:notes/search") == "a change in notes"  # nosec


def test_new_email_is_seen_after_the_first_look(monkeypatch):
    uids = {"me@example.com": 40}
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "next_uids", lambda: dict(uids))
    monkeypatch.setattr(mail, "inbox", lambda limit, account: [
        {"uid": "41", "from": "Bo <bo@example.com>", "subject": "Contract",
         "snippet": "Can you sign by Monday?"},
        {"uid": "39", "from": "Old", "subject": "Seen", "snippet": ""},
    ])
    watcher = triggers.Watcher()

    # What was already there starts nothing.
    assert watcher.look({"email"}, now=0) == {}  # nosec B101
    # Nothing new.
    assert watcher.look({"email"}, now=100) == {}  # nosec B101
    uids["me@example.com"] = 42
    # Too soon after the last look: the server is not asked.
    assert watcher.look({"email"}, now=120) == {}  # nosec B101

    said = watcher.look({"email"}, now=200)["email"]
    assert "From Bo <bo@example.com>: Contract" in said  # nosec B101
    assert "Seen" not in said  # nosec B101


def test_new_imessages_are_seen_and_told(monkeypatch):
    newest = [7]
    monkeypatch.setattr(computer, "allowed", lambda kind: True)
    monkeypatch.setattr(computer, "newest_imessage", lambda: newest[0])
    monkeypatch.setattr(computer, "imessages_after", lambda number: [
        f"Thu Oct 22, 14:03 · Sam Lee: after {number}"])
    watcher = triggers.Watcher()

    assert watcher.look({"imessage"}, now=0) == {}  # nosec B101
    newest[0] = 9
    said = watcher.look({"imessage"}, now=20)["imessage"]
    assert said == "New iMessages:\n- Thu Oct 22, 14:03 · Sam Lee: after 7"


def test_imessages_are_not_read_without_the_switch(monkeypatch):
    monkeypatch.setattr(computer, "allowed", lambda kind: False)
    monkeypatch.setattr(computer, "newest_imessage",
                        lambda: pytest.fail("read without the switch"))

    assert triggers.Watcher().look({"imessage"}, now=0) == {}  # nosec


class FakeServer:
    name = "notes"
    state = connectors.READY
    tools = [
        {"name": "save", "annotations": {"readOnlyHint": False}},
        {"name": "note", "inputSchema": {"required": ["id"]},
         "annotations": {"readOnlyHint": True}},
        {"name": "search", "annotations": {"readOnlyHint": True}},
    ]


def test_a_new_note_in_an_mcp_server_is_told(monkeypatch):
    answer = ["2 notes:\n\n[a1] Tue, from watch\nOld idea"]
    called = []
    monkeypatch.setattr(connectors, "start", lambda: None)
    monkeypatch.setattr(connectors, "servers", lambda: [FakeServer()])

    def call(server, tool, arguments):
        called.append(tool["name"])
        return answer[0], []

    monkeypatch.setattr(connectors, "call", call)
    watcher = triggers.Watcher()

    assert watcher.look({"mcp:notes"}, now=0) == {}  # nosec B101
    answer[0] = ("3 notes:\n\n[b2] Wed, from watch\nA game where you "
                 "grow a city\n\n[a1] Tue, from watch\nOld idea")
    said = watcher.look({"mcp:notes"}, now=40)["mcp:notes"]

    # Its first read-only tool that needs nothing: search, not note.
    assert called == ["search", "search"]  # nosec B101
    assert "A game where you grow a city" in said  # nosec B101
    assert "Old idea" not in said  # nosec B101


def test_an_event_starts_the_spark_that_wants_it(monkeypatch):
    builder = sparks.Spark(id="b1", name="Note Keeper", goal="g",
                           events=["mcp:notes"], every=sparks.ON_CALL)
    other = sparks.Spark(id="o1", name="Other", goal="g",
                         events=["email"])
    paused = sparks.Spark(id="p1", name="Paused", goal="g",
                          events=["mcp:notes"], paused=True)
    edited = {}

    def edit(key, change):
        spark = {"b1": builder, "o1": other, "p1": paused}[key]
        change(spark)
        edited[key] = spark
        return spark

    monkeypatch.setattr(sparks, "all_sparks",
                        lambda: [builder, other, paused])
    monkeypatch.setattr(sparks, "_edit", edit)
    monkeypatch.setattr(sparks, "log", lambda *a, **k: None)
    monkeypatch.setattr(sparks, "wake", lambda: None)
    looked = []

    def look(events, now=None):
        looked.append(events)
        return {"mcp:notes": "New in notes:\n[b2] A game"}

    monkeypatch.setattr(sparks._events, "look", look)

    started = sparks.event_tick()

    assert looked == [{"mcp:notes", "email"}]  # nosec B101
    assert [s.id for s in started] == ["b1"]  # nosec B101
    assert builder.asked and builder.inbox[0]["event"] == "mcp:notes"
    opening = sparks._opening("", builder.inbox)
    assert "started because of what just came in" in opening  # nosec
    assert "[b2] A game" in opening  # nosec B101
    assert "Handed to you" not in opening  # nosec B101


def test_nothing_is_looked_at_while_events_are_off(monkeypatch):
    monkeypatch.setenv(triggers.SETTING, "0")
    given = sparks.Spark(id="o1", name="Other", goal="g", events=["email"])
    monkeypatch.setattr(sparks, "all_sparks", lambda: [given])
    monkeypatch.setattr(sparks._events, "look",
                        lambda *a, **k: pytest.fail("looked while off"))

    assert sparks.event_tick() == []  # nosec B101


def test_the_templates_start_on_events_with_no_schedule():
    found = {t["name"]: t for t in sparks.TEMPLATES}
    assert found["Email Handler"]["events"] == ["email"]  # nosec B101
    assert sparks.on_call(found["Email Handler"])  # nosec B101


def test_make_spark_takes_events(monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    made = {}

    def create(*args, **kwargs):
        made.update(kwargs)
        raise sparks.SparkError("stop here")

    monkeypatch.setattr(sparks, "create", create)
    said = tools.make_spark("Builder", "Build ideas", "on call",
                            model="m", events="mcp:notes")
    assert made["events"] == ["mcp:notes"]  # nosec B101
    assert said == "Error: stop here"  # nosec B101

    said = tools.make_spark("Builder", "Build ideas", model="m",
                            events="printer")
    assert said.startswith("Error:") and "mcp:<server>" in said  # nosec


# --- Freezing until something comes in ---------------------------------------


def test_a_frozen_spark_skips_its_schedule_until_its_event(monkeypatch):
    monkeypatch.setenv(triggers.SETTING, "0")
    frozen = sparks.Spark(id="f1", name="Replier", goal="g",
                          waiting_on=["imessage"])
    assert sparks.schedule_words(frozen) == (  # nosec B101
        "frozen until a new iMessage comes in")
    monkeypatch.setattr(sparks, "all_sparks", lambda: [frozen])
    assert sparks.due(now=frozen.next_run + 10) == []  # nosec B101

    def edit(key, change):
        change(frozen)
        return frozen

    monkeypatch.setattr(sparks, "_edit", edit)
    monkeypatch.setattr(sparks, "log", lambda *a, **k: None)
    monkeypatch.setattr(sparks, "wake", lambda: None)
    monkeypatch.setattr(sparks._events, "look", lambda events, now=None: {
        "imessage": "New iMessages:\n- Sam Lee: are you free?"})

    # Events off, and still woken: it asked for exactly this.
    assert [s.id for s in sparks.event_tick()] == ["f1"]  # nosec B101
    assert frozen.waiting_on == [] and frozen.asked  # nosec B101
    assert "are you free?" in frozen.inbox[0]["text"]  # nosec B101


def test_a_spark_freezes_itself_from_a_shift(monkeypatch):
    spark = sparks.Spark(id="f2", name="Replier", goal="g")
    monkeypatch.setattr(sparks, "_edit",
                        lambda key, change: change(spark) or spark)
    wait = sparks._waiting_for_events(spark)

    said = wait({"events": "imessage email"})

    assert spark.waiting_on == ["imessage", "email"]  # nosec B101
    assert "End this shift" in said  # nosec B101
    assert wait({"events": "printer"}).startswith("Error:")  # nosec B101

    monkeypatch.setattr(sparks, "over_budget", lambda s: False)
    monkeypatch.setattr(sparks, "wake", lambda: None)
    sparks.run_now("f2")
    assert spark.waiting_on == []  # nosec B101


# --- Chats waiting for something to come in ----------------------------------


def test_a_chat_waits_for_new_messages_and_keeps_waiting(monkeypatch):
    from flash import waits

    wait = waits.make("c1", {"what": "new texts", "then": "answer them",
                             "events": "imessage", "keep": True}, now=100)
    assert wait.events == ["imessage"] and wait.keep  # nosec B101
    assert wait.give_up is not None  # nosec B101

    newest = [7]
    # Looked at again at once, not as the server spaces its looks.
    monkeypatch.setattr(triggers, "EVERY", dict.fromkeys(triggers.EVERY, 0))
    monkeypatch.setattr(computer, "allowed", lambda kind: True)
    monkeypatch.setattr(computer, "newest_imessage", lambda: newest[0])
    monkeypatch.setattr(computer, "imessages_after",
                        lambda n: ["Thu 14:03 · Sam Lee: are you free?"])

    assert waits.check(wait) == (False, "")  # nosec B101  (learns)
    newest[0] = 8
    found, said = waits.check(wait)
    assert found and "Sam Lee: are you free?" in said  # nosec B101

    with pytest.raises(waits.WaitError, match="keep only goes"):
        waits.make("c1", {"what": "x", "then": "y", "keep": True,
                          "in_minutes": 5})
    with pytest.raises(waits.WaitError, match="not both"):
        waits.make("c1", {"what": "x", "then": "y", "events": "email",
                          "check": "a reply is in"})
