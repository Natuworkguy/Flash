# pylint: disable=C0114,C0115,C0116

import json
import os
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path

import pytest

from flash import sparks, tools, web
from flash.theme import answer_from, capture_tool_output


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, name, arguments):
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeResponse:
    def __init__(self, message):
        self.message = message


class FakeClient:
    """Replays a fixed sequence of responses, one per chat() call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, model, messages, tools=None, options=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


def _reply(content="", *calls):
    return FakeResponse(FakeMessage(
        content=content,
        tool_calls=[FakeToolCall(name, args) for name, args in calls],
    ))


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(tools, "OLLAMA_HOST", "http://localhost:11434")


@pytest.mark.parametrize("words, minutes", [
    ("", sparks.DEFAULT_EVERY_MINUTES),
    ("30m", 30),
    ("every 2 hours", 120),
    ("2h", 120),
    ("daily", 1440),
    ("every day", 1440),
    ("weekly", 10080),
    ("45", 45),
    (90, 90),
])
def test_a_schedule_reads_as_minutes(words, minutes):
    assert sparks.parse_every(words) == minutes


@pytest.mark.parametrize("words", ["5m", "soon", "every blue moon", 1])
def test_a_schedule_too_often_or_unreadable_is_refused(words):
    with pytest.raises(sparks.SparkError):
        sparks.parse_every(words)


def test_a_schedule_reads_back_in_words():
    assert sparks.every_words(60) == "hour"
    assert sparks.every_words(120) == "2 hours"
    assert sparks.every_words(1440) == "day"
    assert sparks.every_words(45) == "45 minutes"


def test_a_spark_is_kept_and_found_by_name_handle_or_id():
    made = sparks.create("Price Watch", "Check the price.", "Only read.", "2h")

    assert made.handle == "@price-watch-spark"
    assert (sparks.sparks_dir() / f"{made.id}.json").is_file()
    for key in (made.id, "price watch", "@price-watch-spark", "price-watch"):
        assert sparks.find(key).id == made.id
    assert sparks.find("nobody") is None
    assert sparks.all_sparks()[0].every == 120
    # Due at once: its first shift is not a schedule away.
    assert [s.id for s in sparks.due()] == [made.id]


def test_two_sparks_cannot_share_a_handle():
    sparks.create("Scout", "Look around.")

    with pytest.raises(sparks.SparkError):
        sparks.create("scout", "Look again.")


def test_a_spark_needs_a_name_and_a_goal():
    with pytest.raises(sparks.SparkError):
        sparks.create("", "A goal.")
    with pytest.raises(sparks.SparkError):
        sparks.create("Named", "  ")


def test_each_new_spark_gets_the_next_colour():
    first = sparks.create("One", "Goal.")
    second = sparks.create("Two", "Goal.")

    assert first.colour == sparks.COLOURS[0]
    assert second.colour == sparks.COLOURS[1]


def test_a_paused_spark_is_not_due():
    made = sparks.create("Scout", "Look around.")
    sparks.set_paused(made.id, True)

    assert sparks.due() == []
    sparks.set_paused(made.id, False)
    assert [s.id for s in sparks.due()] == [made.id]


def test_a_shift_files_its_report_and_keeps_its_notes(model):
    made = sparks.create("Scout", "Watch the issues.", "Never push.")
    client = FakeClient([
        _reply("", ("keep_notes", {"notes": "Seen issue 12."})),
        _reply("Issue 12 looks like a bug."),
    ])

    report = sparks.shift(made.id, client=client)

    assert report.text == "Issue 12 looks like a bug."
    assert not report.quiet and not report.read
    assert "Kept notes" in report.steps
    kept = sparks.find(made.id)
    assert kept.notes == "Seen issue 12."
    assert kept.runs == 1 and kept.unread == 1
    assert kept.status == sparks.IDLE
    assert kept.next_run == pytest.approx(report.at + 3600)
    # The shift saw its goal and boundaries, and could keep notes.
    system = client.calls[0]["messages"][0]["content"]
    assert "Watch the issues." in system and "Never push." in system
    names = [t["function"]["name"] for t in client.calls[0]["tools"]]
    assert "keep_notes" in names


def test_the_next_shift_reads_notes_lessons_and_the_last_report(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("keep_notes", {"notes": "Seen issue 12."})),
        _reply("Issue 12 looks like a bug."),
    ]))
    at = sparks.find(made.id).reports[-1].at
    sparks.teach(made.id, "Only tell me about crashes.", at)
    client = FakeClient([_reply(sparks.NOTHING_NEW)])

    sparks.shift(made.id, client=client)

    system = client.calls[0]["messages"][0]["content"]
    assert "Seen issue 12." in system
    assert "Only tell me about crashes." in system
    assert "Issue 12 looks like a bug." in system
    first = sparks.find(made.id).reports[0]
    assert first.feedback == "Only tell me about crashes." and first.read


def test_a_shift_with_nothing_new_is_quiet(model):
    made = sparks.create("Scout", "Watch the issues.")

    report = sparks.shift(
        made.id, client=FakeClient([_reply("Nothing new.")])
    )

    assert report.quiet and report.read
    assert sparks.find(made.id).unread == 0
    assert sparks.news() == []


def test_a_shift_without_a_model_fails_and_says_why(monkeypatch):
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    made = sparks.create("Scout", "Watch the issues.")

    report = sparks.shift(made.id)

    assert report.failed and "no model" in report.text
    assert sparks.find(made.id).status == sparks.FAILED


def test_a_shift_only_calls_the_tools_it_is_allowed(model):
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient([
        _reply("", ("make_spark", {"name": "Another", "goal": "More."})),
        _reply("Done."),
    ])

    report = sparks.shift(made.id, client=client)

    assert report.text == "Done."
    assert [s.name for s in sparks.all_sparks()] == ["Scout"]
    assert "Unknown tool" in client.calls[1]["messages"][-1]["content"]


def test_news_points_out_each_report_once(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))

    first = sparks.news()

    assert [(s.name, r.text) for s, r in first] == [("Scout", "Found one.")]
    assert sparks.news() == []
    sparks.mark_read(made.id)
    assert sparks.unread_total() == 0


def test_lessons_can_be_taught_and_forgotten():
    made = sparks.create("Scout", "Watch the issues.")
    sparks.teach(made.id, "Be brief.")
    sparks.teach(made.id, "Skip docs issues.")

    assert sparks.find(made.id).lessons == ["Be brief.", "Skip docs issues."]
    sparks.forget_lesson(made.id, 1)
    assert sparks.find(made.id).lessons == ["Skip docs issues."]
    with pytest.raises(sparks.SparkError):
        sparks.forget_lesson(made.id, 5)
    with pytest.raises(sparks.SparkError):
        sparks.teach(made.id, "   ")


def test_changing_the_schedule_moves_the_next_shift(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))

    changed = sparks.update(made.id, every="daily", name="Lookout")

    assert changed.every == 1440 and changed.name == "Lookout"
    assert changed.next_run == pytest.approx(changed.last_run + 86400)


def test_a_spark_left_working_by_a_quit_flash_is_let_go(monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    sparks._edit(made.id, lambda s: setattr(s, "status", sparks.WORKING))
    seen = []
    monkeypatch.setattr(sparks, "shift", seen.append)
    monkeypatch.setattr(sparks, "due", lambda: [])

    def stop(_timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(sparks._nudge, "wait", stop)
    with pytest.raises(KeyboardInterrupt):
        sparks._keep()

    assert sparks.find(made.id).status == sparks.IDLE


def test_a_broken_file_is_skipped():
    sparks.sparks_dir().mkdir(parents=True)
    (sparks.sparks_dir() / "junk.json").write_text("{not json")
    sparks.create("Scout", "Watch the issues.")

    assert [s.name for s in sparks.all_sparks()] == ["Scout"]


def test_make_spark_asks_first(monkeypatch):
    lines = []
    with capture_tool_output(lambda kind, text, style: lines.append(text)):
        with answer_from(lambda question: "n"):
            result = tools.make_spark("Scout", "Watch the issues.", "daily")

    assert result == "Blocked by user"
    assert sparks.all_sparks() == []


def test_make_spark_makes_one_once_agreed(monkeypatch):
    asked = []
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: asked.append(question) or "y"):
            result = tools.make_spark(
                "Scout", "Watch the issues.", "daily", "Only read."
            )

    assert "Made Scout (@scout-spark)" in result
    assert "every day" in asked[0]
    made = sparks.find("scout")
    assert made.every == 1440 and made.boundaries == "Only read."


def test_make_spark_refuses_a_schedule_too_tight():
    with capture_tool_output(lambda kind, text, style: None):
        result = tools.make_spark("Scout", "Watch.", "1m")

    assert result.startswith("Error:")
    assert sparks.all_sparks() == []


def test_make_spark_is_not_a_sub_agent_tool():
    assert "make_spark" in tools.FUNCTIONS
    assert "make_spark" not in tools.SUBAGENT_TOOL_NAMES


class TestWeb:
    def test_the_page_makes_lists_and_teaches_sparks(self):
        session = web.Session()
        made = web.command(session, {
            "name": "spark-create", "arg": "Scout",
            "goal": "Watch the issues.", "every": "60",
        })["spark"]

        listed = web.command(session, {"name": "sparks"})["sparks"]
        assert [s["handle"] for s in listed] == ["@scout-spark"]

        web.command(session, {
            "name": "spark-teach", "arg": made["id"], "lesson": "Be brief.",
        })
        web.command(session, {"name": "spark-pause", "arg": made["id"]})
        kept = web.command(session, {"name": "sparks"})["sparks"][0]
        assert kept["lessons"] == ["Be brief."] and kept["paused"]

        web.command(session, {"name": "spark-remove", "arg": made["id"]})
        assert web.command(session, {"name": "sparks"})["sparks"] == []

    def test_a_bad_spark_is_a_plain_error(self):
        session = web.Session()

        with pytest.raises(ValueError, match="schedule"):
            web.command(session, {
                "name": "spark-create", "arg": "Scout",
                "goal": "Watch.", "every": "whenever",
            })
        with pytest.raises(ValueError, match="No spark"):
            web.command(session, {"name": "spark-run", "arg": "ghost"})

    def test_the_sparks_page_has_an_address(self):
        assert web.PAGE_PATHS.match("/sparks")

    def test_unread_reports_reach_the_sidebar(self, model):
        made = sparks.create("Scout", "Watch the issues.")
        sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))

        assert web.Session().state(lite=True)["status"]["sparks_unread"] == 1


# --- The keeper, across processes ----------------------------------------


def test_run_now_is_asked_for_in_the_file_even_while_paused(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.set_paused(made.id, True)

    sparks.run_now("scout")

    assert sparks.find(made.id).asked
    assert [s.id for s in sparks.due()] == [made.id]
    sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))
    assert not sparks.find(made.id).asked
    assert sparks.due() == []


_REPO = Path(__file__).resolve().parent.parent


def _other_flash_env() -> dict:
    """Another Flash's environment: the same packages, the test's home."""

    home = str(sparks.FLASH_DIR.parent)
    return {
        **os.environ, "HOME": home, "USERPROFILE": home,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }


def test_only_one_process_holds_the_keeper_lock():
    sparks.sparks_dir().mkdir(parents=True)
    assert sparks._take_floor()
    # Held already, asking again in this process is a yes.
    assert sparks._take_floor()

    other = subprocess.run(
        [sys.executable, "-c",
         "from flash import sparks; print(sparks._take_floor())"],
        capture_output=True, text=True, check=True,
        env=_other_flash_env(), cwd=_REPO,
    )
    assert other.stdout.strip() == "False"

    sparks._give_floor()
    freed = subprocess.run(
        [sys.executable, "-c",
         "from flash import sparks; print(sparks._take_floor())"],
        capture_output=True, text=True, check=True,
        env=_other_flash_env(), cwd=_REPO,
    )
    assert freed.stdout.strip() == "True"


def test_the_keeper_runs_what_is_due_and_says_it_is_there(monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    ran, heard = [], []
    stop = threading.Event()

    def fake_shift(spark_id):
        ran.append(spark_id)
        report = sparks.Report(at=time.time(), text="Found one.")
        sparks._edit(spark_id, lambda s: (
            s.reports.append(report),
            setattr(s, "next_run", time.time() + 3600),
        ))
        return report

    monkeypatch.setattr(sparks, "shift", fake_shift)
    monkeypatch.setattr(sparks, "TICK_SECONDS", 0.05)
    keeper = threading.Thread(target=sparks._keep, kwargs={
        "always": True, "stop": stop,
        "announce": lambda spark, report: heard.append(spark.name),
    })
    keeper.start()
    try:
        deadline = time.time() + 5
        while not (ran and sparks.keeper()) and time.time() < deadline:
            time.sleep(0.02)
        beat = sparks.keeper()
        assert ran == [made.id]
        assert heard == ["Scout"]
        assert beat["always"] and beat["pid"] == os.getpid()
    finally:
        stop.set()
        sparks.wake()
        keeper.join(5)

    assert sparks.keeper() is None
    assert sparks._floor is None


def test_a_quiet_report_is_not_announced(monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    heard = []
    monkeypatch.setattr(sparks, "shift", lambda spark_id: sparks.Report(
        at=time.time(), text="Nothing new.", quiet=True, read=True,
    ))

    sparks._safe_shift(made.id, announce=lambda s, r: heard.append(s))

    assert heard == []


def test_a_keeper_elsewhere_changing_a_spark_is_news_here(monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    before = sparks._signature()
    time.sleep(0.01)

    sparks.teach(made.id, "Be brief.")

    assert sparks._signature() != before


def test_a_stale_heartbeat_is_no_keeper():
    sparks.sparks_dir().mkdir(parents=True)
    (sparks.sparks_dir() / sparks.HEARTBEAT).write_text(json.dumps({
        "pid": 1, "always": True, "since": 0, "beat": time.time() - 3600,
    }))

    assert sparks.keeper() is None


def test_prepare_runs_before_each_shift(monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    order = []
    monkeypatch.setattr(
        sparks, "shift", lambda spark_id: order.append("shift")
    )

    sparks._safe_shift(made.id, prepare=lambda: order.append("prepare"))

    assert order == ["prepare", "shift"]


def test_a_keeper_no_longer_wanted_stops(monkeypatch):
    asked = []
    monkeypatch.setattr(sparks, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(sparks, "CHECK_EVERY_TICKS", 2)

    sparks._keep(always=True, wanted=lambda: asked.append(1) and False)

    assert asked == [1]
    assert sparks._floor is None


# --- Chat ----------------------------------------------------------------


def test_a_spark_answers_in_its_chat(model):
    made = sparks.create("Scout", "Watch the issues.", "Never push.")
    sparks.shift(made.id, client=FakeClient([_reply("Issue 12 is a bug.")]))
    client = FakeClient([_reply("Issue 12 crashes on start.")])

    reply = sparks.say("scout", "What did you find?", client=client)

    assert reply.who == "spark" and reply.text == "Issue 12 crashes on start."
    kept = sparks.find(made.id)
    assert [(m.who, m.text) for m in kept.chat] == [
        ("you", "What did you find?"),
        ("spark", "Issue 12 crashes on start."),
    ]
    assert not kept.answering
    system = client.calls[0]["messages"][0]["content"]
    assert "Watch the issues." in system and "Never push." in system
    assert "Issue 12 is a bug." in system
    assert client.calls[0]["messages"][-1] == {
        "role": "user", "content": "What did you find?",
    }
    names = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert {"learn", "set_goal", "set_schedule", "keep_notes"} <= names


def test_the_chat_so_far_goes_back_to_the_model(model):
    sparks.create("Scout", "Watch the issues.")
    sparks.say("scout", "Hi", client=FakeClient([_reply("Hello!")]))
    client = FakeClient([_reply("Still here.")])

    sparks.say("scout", "Still there?", client=client)

    said = [(m["role"], m["content"]) for m in client.calls[0]["messages"][1:]]
    assert said == [
        ("user", "Hi"), ("assistant", "Hello!"), ("user", "Still there?"),
    ]


def test_a_spark_learns_and_changes_what_it_is_told_to(model):
    made = sparks.create("Scout", "Watch the issues.", every="daily")
    client = FakeClient([
        _reply(
            "",
            ("learn", {"lesson": "Only report crashes."}),
            ("set_goal", {"goal": "Watch the crash reports."}),
            ("set_schedule", {"every": "2h"}),
            ("keep_notes", {"notes": "Switched to crashes."}),
        ),
        _reply("Done: crashes only, every 2 hours."),
    ])

    reply = sparks.say(made.id, "Only crashes, every two hours.", client)

    kept = sparks.find(made.id)
    assert kept.lessons == ["Only report crashes."]
    assert kept.goal == "Watch the crash reports."
    assert kept.every == 120
    assert kept.notes == "Switched to crashes."
    assert "Learned something" in reply.steps
    assert "Changed its goal" in reply.steps


def test_a_schedule_too_tight_is_refused_in_chat(model):
    made = sparks.create("Scout", "Watch the issues.", every="daily")
    client = FakeClient([
        _reply("", ("set_schedule", {"every": "1m"})),
        _reply("I can only go every 15 minutes."),
    ])

    sparks.say(made.id, "Every minute please.", client)

    assert sparks.find(made.id).every == 1440
    assert "at most every 15" in client.calls[1]["messages"][-1]["content"]


def test_one_message_at_a_time():
    made = sparks.create("Scout", "Watch the issues.")
    sparks.ask(made.id, "First")

    with pytest.raises(sparks.SparkError, match="still answering"):
        sparks.ask(made.id, "Second")
    with pytest.raises(sparks.SparkError):
        sparks.ask(made.id, "   ")


def test_an_answer_left_by_a_quit_flash_does_not_block_the_chat():
    made = sparks.create("Scout", "Watch the issues.")
    sparks.ask(made.id, "First")
    sparks._edit(made.id, lambda s: setattr(
        s, "replying", time.time() - sparks.REPLY_STALE_SECONDS - 1
    ))

    assert not sparks.find(made.id).answering
    sparks.ask(made.id, "Again")


def test_an_answer_without_a_model_says_why(monkeypatch):
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    made = sparks.create("Scout", "Watch the issues.")

    reply = sparks.say(made.id, "Hello?")

    assert reply.failed and "no model" in reply.text
    kept = sparks.find(made.id)
    assert not kept.answering and len(kept.chat) == 2


def test_a_failed_answer_is_not_sent_back_as_history(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks._edit(made.id, lambda s: s.chat.append(sparks.Message(
        at=time.time(), who="spark", text="I could not answer", failed=True,
    )))
    client = FakeClient([_reply("Here now.")])

    sparks.say(made.id, "Hi", client)

    assert all(
        "could not" not in m["content"]
        for m in client.calls[0]["messages"][1:]
    )


def test_chat_is_kept_to_its_length(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks._edit(made.id, lambda s: s.chat.extend(
        sparks.Message(at=i, who="you", text=str(i))
        for i in range(sparks.MAX_CHAT)
    ))

    sparks.say(made.id, "Hi", FakeClient([_reply("Hey.")]))

    assert len(sparks.find(made.id).chat) == sparks.MAX_CHAT


def test_say_later_answers_on_its_own_thread(model, monkeypatch):
    made = sparks.create("Scout", "Watch the issues.")
    monkeypatch.setattr(
        sparks.ollama, "Client", lambda host: FakeClient([_reply("Later.")])
    )

    spark = sparks.say_later(made.id, "Hi")

    assert spark.answering
    deadline = time.time() + 5
    while sparks.find(made.id).answering and time.time() < deadline:
        time.sleep(0.02)
    assert sparks.find(made.id).chat[-1].text == "Later."


def test_the_keepers_heartbeat_is_not_a_spark_nor_a_change():
    sparks.create("Scout", "Watch the issues.")
    before = sparks._signature()
    (sparks.sparks_dir() / sparks.HEARTBEAT).write_text("{}")

    assert sparks._signature() == before
    assert [s.name for s in sparks.all_sparks()] == ["Scout"]


def test_writes_from_two_threads_do_not_lose_each_other():
    made = sparks.create("Scout", "Watch the issues.")

    def teach(n):
        for i in range(10):
            sparks.teach(made.id, f"lesson {n}-{i}")

    threads = [threading.Thread(target=teach, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(sparks.find(made.id).lessons) == 20


class TestChatElsewhere:
    def test_the_page_says_something_to_a_spark(self, model, monkeypatch):
        made = sparks.create("Scout", "Watch the issues.")
        monkeypatch.setattr(sparks, "say_later", lambda key, text: (
            sparks.ask(key, text)
        ))

        got = web.command(web.Session(), {
            "name": "spark-say", "arg": made.id, "text": "Hi",
        })["spark"]

        assert got["answering"] and got["chat"][-1]["text"] == "Hi"

    def test_the_page_gets_the_emoji_list(self):
        from flash.emojis import EMOJIS

        state = web.Session().state()

        assert state["emojis"] == EMOJIS
        assert "emojis" not in web.Session().state(lite=True) or (
            web.Session().state(lite=True)["emojis"] is None
        )

    def test_the_terminal_sends_one_message(self, model, monkeypatch):
        from flash import ai

        made = sparks.create("Scout", "Watch the issues.")
        monkeypatch.setattr(
            sparks.ollama, "Client",
            lambda host: FakeClient([_reply("Found two.")]),
        )
        shown = []
        monkeypatch.setattr(ai, "_spark_says", lambda s, m: shown.append(m))

        ai._sparks_command("chat scout what did you find?")

        assert [m.text for m in shown] == ["Found two."]
        assert sparks.find(made.id).chat[0].text == "what did you find?"
