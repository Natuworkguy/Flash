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


@pytest.fixture(autouse=True)
def flash_model(monkeypatch):
    """Flash has a model, the one a spark runs on when it names none."""

    monkeypatch.setattr(tools, "MODEL_NAME", "flash-model")


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
    assert "KeepNotes()" in report.steps
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


# --- Set times ----------------------------------------------------------


def test_a_spark_at_set_times_waits_for_the_first_of_them():
    before = time.time()
    made = sparks.create("Brief", "The morning news.", every="9am weekdays")

    assert made.at == "0 9 * * 1-5"
    assert made.every == 1440
    assert made.next_run == sparks.cron.next_after(made.at, before)
    assert sparks.schedule_words(made) == "at 9am on weekdays"
    assert made.to_dict()["schedule"] == "at 9am on weekdays"
    assert sparks.due(now=made.next_run - 60) == []
    assert [s.id for s in sparks.due(now=made.next_run)] == [made.id]


def test_every_so_often_still_reads_as_it_did():
    made = sparks.create("Scout", "Look around.", every="2h")

    assert (made.every, made.at) == (120, "")
    assert sparks.schedule_words(made) == "every 2 hours"


def test_set_times_too_close_together_are_refused():
    with pytest.raises(sparks.SparkError, match="at most every 15"):
        sparks.create("Busy", "Goal.", every="*/5 * * * *")


def test_set_times_that_cannot_be_read_say_so():
    with pytest.raises(sparks.SparkError, match="set times"):
        sparks.create("Odd", "Goal.", every="whenever it rains")


def test_after_a_shift_the_next_is_at_the_next_set_time(model):
    made = sparks.create("Brief", "The morning news.", every="noon daily")

    report = sparks.shift(made.id, client=FakeClient([_reply("News.")]))

    kept = sparks.find(made.id)
    assert kept.next_run == sparks.cron.next_after("0 12 * * *", report.at)
    assert "This shift runs at noon every day" in sparks._prompt(
        kept, "", "m", "",
    )


def test_moving_to_set_times_and_back(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))

    timed = sparks.update(made.id, every="mon 8:30")
    assert timed.at == "30 8 * * 1"
    assert timed.next_run == pytest.approx(
        sparks.cron.next_after(timed.at, time.time()), abs=60,
    )

    back = sparks.update(made.id, every="2h")
    assert back.at == ""
    assert back.next_run == pytest.approx(back.last_run + 7200)


def test_a_spark_sets_its_own_times_in_a_chat(model):
    made = sparks.create("Scout", "Watch the issues.")
    kit = sparks.ChatKit(made)

    said = kit.tools["set_schedule"]({"every": "weekdays at 9am"})
    kit.apply()

    assert said == "(now at 9am on weekdays)"
    assert sparks.find(made.id).at == "0 9 * * 1-5"


def test_set_times_travel_in_a_share_code():
    made = sparks.create("Brief", "The morning news.", every="9am weekdays")

    seen = sparks.read_code(sparks.share_code(made.id))
    copy = sparks.add_from(sparks.share_code(made.id))

    assert seen["at"] == "0 9 * * 1-5"
    assert seen["schedule"] == "at 9am on weekdays"
    assert copy.at == "0 9 * * 1-5"


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


def test_make_spark_can_make_one_paused():
    asked = []
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: asked.append(question) or "y"):
            result = tools.make_spark(
                "Scout", "Watch the issues.", "daily", paused=True,
            )

    assert "paused until you resume it" in asked[0]
    assert "It is paused" in result and "starts now" not in result
    assert sparks.find("scout").paused


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


def test_a_keeper_started_again_after_flash_is_updated(monkeypatch):
    monkeypatch.setattr(sparks, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(sparks, "CHECK_EVERY_TICKS", 1)
    stamps = iter([sparks._RUNNING_CODE, ("updated",), ("updated",)])
    monkeypatch.setattr(sparks, "_code_stamp", lambda: next(stamps))

    renewed = sparks._keep(always=True, renew=True)

    assert renewed is True
    # It gave up the lock, for another keeper to run shifts meanwhile.
    assert sparks._floor is None


def test_an_update_still_being_written_is_waited_for(monkeypatch):
    monkeypatch.setattr(sparks, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(sparks, "CHECK_EVERY_TICKS", 1)
    stamps = iter([("half",), ("whole",), ("whole",)])
    looks = []

    def stamp():
        looks.append(1)
        return next(stamps)

    monkeypatch.setattr(sparks, "_code_stamp", stamp)

    assert sparks._keep(renew=True) is True
    assert len(looks) == 3


def test_a_keeper_in_an_open_flash_is_not_renewed(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(sparks, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(sparks, "CHECK_EVERY_TICKS", 1)
    monkeypatch.setattr(sparks, "_code_stamp", lambda: ("updated",))
    threading.Timer(0.1, stop.set).start()

    assert sparks._keep(stop=stop) is False


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
    assert "Learn(Only report crashes.)" in reply.steps
    assert "SetGoal(Watch the crash reports.)" in reply.steps
    assert "SetSchedule(every 2 hours)" in reply.steps


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


def test_a_lesson_told_twice_is_kept_once(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.teach(made.id, "Be brief.")
    sparks.teach(made.id, "be brief.")
    sparks.say(made.id, "Keep it brief", FakeClient([
        _reply("", ("learn", {"lesson": "Be brief."})),
        _reply("Will do."),
    ]))

    assert sparks.find(made.id).lessons == ["Be brief."]


# --- Projects ------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    from flash import workspace

    folder = tmp_path / "app"
    folder.mkdir()
    return workspace.create_project("App", str(folder), "Run make test.")


def test_a_spark_can_work_on_a_project(model, project):
    made = sparks.create("Scout", "Watch the build.", project="app")

    assert made.project == project.id
    assert sparks.find(made.id).to_dict()["project_name"] == "App"
    client = FakeClient([_reply(sparks.NOTHING_NEW)])
    sparks.shift(made.id, client=client)
    system = client.calls[0]["messages"][0]["content"]
    assert "=== Your project: App ===" in system
    assert project.path in system and "Run make test." in system


def test_a_project_is_named_by_name_or_id_or_none(project):
    made = sparks.create("Scout", "Watch the build.", project=project.id)

    assert sparks.update(made.id, project="none").project == ""
    assert sparks.update(made.id, project="APP").project == project.id
    with pytest.raises(sparks.SparkError, match="No project"):
        sparks.update(made.id, project="Nope")
    with pytest.raises(sparks.SparkError, match="No project"):
        sparks.create("Other", "Goal.", project="Nope")


def test_a_spark_whose_project_went_has_none(model, project):
    from flash import workspace

    made = sparks.create("Scout", "Watch the build.", project="App")
    workspace.delete_project(project.id)

    kept = sparks.find(made.id)
    assert sparks.project_of(kept) is None
    assert kept.to_dict()["project"] == ""
    assert sparks.project_block(kept) == ""


def test_a_sparks_project_names_its_other_folders(tmp_path):
    from flash import workspace

    (tmp_path / "web").mkdir()
    (tmp_path / "api").mkdir()
    found = workspace.create_project(
        "App", str(tmp_path / "web"), folders=[str(tmp_path / "api")],
    )
    spark = sparks.create("Scout", "Look.", project=found.id)

    block = sparks.project_block(spark)

    assert str((tmp_path / "api").resolve()) in block
    assert "also takes in these folders" in block


def test_a_chat_prompt_can_leave_the_project_to_the_web(project):
    made = sparks.create("Scout", "Watch the build.", project="App")

    assert "Your project" in sparks.chat_prompt(made, "h", "m", "")
    assert "Your project" not in sparks.chat_prompt(
        made, "h", "m", "", project=False,
    )


def test_the_terminal_gives_a_spark_a_project(project):
    from flash import ai

    made = sparks.create("Scout", "Watch the build.")

    ai._sparks_command("project scout App")
    assert sparks.find(made.id).project == project.id
    ai._sparks_command("project scout none")
    assert sparks.find(made.id).project == ""


def test_flash_can_make_a_spark_for_a_project(project):
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: "y"):
            tools.make_spark("Scout", "Watch the build.", "daily", "", "App")

    assert sparks.find("scout").project == project.id


# --- Mentions ------------------------------------------------------------


def test_a_spark_is_mentioned_by_its_name_or_handle():
    scout = sparks.create("Scout", "Watch the issues.")
    watch = sparks.create("Price Watch", "Watch the price.")

    got = sparks.mentioned(
        "@price-watch and @scout-spark, then @Scout again, and @nobody."
    )

    assert [s.id for s in got] == [watch.id, scout.id]


@pytest.mark.parametrize("text", [
    "mail me@scout", "see @scout/notes.md", "open @scout.py", "scout",
])
def test_an_address_or_a_path_is_not_a_mention(text):
    sparks.create("Scout", "Watch the issues.")

    assert sparks.mentioned(text) == []


def test_a_spark_answer_is_kept_as_a_note_of_who_did_what():
    made = sparks.create("Scout", "Watch the issues.")

    note = sparks.called_note(
        made, "Two new bugs.", ["fetch", "fetch", "grep"],
    )

    assert note == (
        "[Spark called] The user @mentioned Scout (@scout-spark), one of "
        "their sparks, in this chat. It answered, using fetch, grep:\n"
        "Two new bugs."
    )


def test_a_mentioned_spark_sees_the_latest_of_the_chat():
    history = [
        {"role": "user", "content": f"old question {i}"} for i in range(20)
    ] + [
        {"role": "assistant", "content": "", "tool_calls": [{}]},
        {"role": "tool", "content": "a long tool output", "tool_name": "x"},
        {"role": "assistant", "content": "word " * 1000},
        {"role": "user", "content": "@scout what do you think?"},
    ]

    shown = sparks.guest_view(history, "Flash")

    assert "latest 12 messages" in shown
    # The latest 12: questions 9 to 19, and Flash's reply.
    assert "old question 19" in shown and "old question 9" in shown
    assert "old question 8" not in shown
    assert "tool output" not in shown
    assert "Flash: word word" in shown and "[...]" in shown
    assert shown.endswith(
        "The user now says, to you:\n@scout what do you think?"
    )


def test_a_second_spark_sees_what_the_first_said():
    first = sparks.create("Scout", "Watch the issues.")
    history = [
        {"role": "user", "content": "@scout @fixer status?"},
        {"role": "system",
         "content": sparks.called_note(first, "Issue 12 is open.", [])},
    ]

    shown = sparks.guest_view(history, "Flash")

    assert "The user now says, to you:\n@scout @fixer status?" in shown
    assert "Already answered by others it named" in shown
    assert "Issue 12 is open." in shown


# --- Approvals, stopping, hand-offs, watching, sharing ---------------------


@pytest.fixture
def fake_shell(monkeypatch):
    ran = []

    def shell(command, timeout=None):
        ran.append(command)
        return f"(ran {command})"

    monkeypatch.setitem(tools.FUNCTIONS, "shell", shell)
    return ran


def test_a_step_that_asks_first_waits_for_the_user(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    client = FakeClient([
        _reply("", ("shell", {"command": "git pull"}),
               ("keep_notes", {"notes": "pulled"})),
    ])

    report = sparks.shift(made.id, client=client)

    assert report.approval and "Run a command" in report.text
    kept = sparks.find(made.id)
    assert kept.waiting and kept.status == sparks.WAITING
    assert kept.pending["detail"] == "git pull"
    assert fake_shell == []
    assert sparks.due() == [] and sparks.unread_total() == 2
    names = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert "shell" in names and "hand_off" in names
    # Waiting, it does not start again, even when it is its time.
    assert sparks.shift(made.id) is None


def test_a_yes_carries_the_shift_on_from_that_step(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"}),
               ("keep_notes", {"notes": "pulled"})),
    ]))

    sparks.answer_step("scout", True)
    assert [s.id for s in sparks.due()] == [made.id]
    client = FakeClient([_reply("Pulled two commits.")])
    report = sparks.shift(made.id, client=client)

    assert fake_shell == ["git pull"]
    assert report.text == "Pulled two commits."
    said = client.calls[0]["messages"]
    assert said[-2] == {
        "role": "tool", "tool_name": "shell", "content": "(ran git pull)",
    }
    assert said[-1]["content"] == sparks.NOT_RUN
    kept = sparks.find(made.id)
    assert kept.status == sparks.IDLE and kept.pending == {}
    assert kept.runs == 1 and sparks.unread_total() == 1


def test_a_no_carries_it_on_without_the_step(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "rm -rf build"})),
    ]))

    sparks.answer_step(made.id, False, "too risky")
    client = FakeClient([_reply("Left the build alone.")])
    sparks.shift(made.id, client=client)

    assert fake_shell == []
    told = client.calls[0]["messages"][-1]["content"]
    assert "said no" in told and "too risky" in told
    assert "(you said no)" in sparks.find(made.id).reports[-1].steps[-1]


def test_in_autonomous_mode_nothing_waits(model, fake_shell, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    made = sparks.create("Scout", "Keep the repo current.")

    report = sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git status"})),
        _reply("Clean."),
    ]))

    assert fake_shell == ["git status"] and report.text == "Clean."


def test_there_is_nothing_to_answer_when_nothing_waits():
    made = sparks.create("Scout", "Watch the issues.")

    with pytest.raises(sparks.SparkError, match="not waiting"):
        sparks.answer_step(made.id, True)


def test_a_shift_stops_at_its_next_step_when_asked(model):
    made = sparks.create("Scout", "Watch the issues.")

    class Stopping(FakeClient):
        def chat(self, model, messages, tools=None, options=None):
            sparks.stop(made.id)
            return super().chat(model, messages, tools, options)

    report = sparks.shift(made.id, client=Stopping([
        _reply("", ("keep_notes", {"notes": "x"})),
        _reply("never reached"),
    ]))

    assert report.text.startswith("Stopped")
    kept = sparks.find(made.id)
    assert kept.status == sparks.IDLE and not kept.stop_asked
    with pytest.raises(sparks.SparkError, match="not working"):
        sparks.stop(made.id)


def test_a_waiting_shift_can_be_called_off(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"})),
    ]))

    sparks.stop(made.id)

    kept = sparks.find(made.id)
    assert kept.status == sparks.IDLE and kept.pending == {}
    assert "Called off" in kept.reports[-1].text
    assert sparks.unread_total() == 0


def test_a_spark_hands_work_to_another(model):
    scout = sparks.create("Scout", "Watch the issues.")
    fixer = sparks.create("Fixer", "Fix the bugs you are handed.")
    first = FakeClient([
        _reply("", ("hand_off", {"spark": "fixer", "note": "Issue 12."})),
        _reply("Handed issue 12 to Fixer."),
    ])

    sparks.shift(scout.id, client=first)

    assert "Fixer (@fixer-spark)" in first.calls[0]["messages"][0]["content"]
    kept = sparks.find(fixer.id)
    assert kept.asked and kept.inbox[0]["from"] == "Scout"
    second = FakeClient([_reply("On it.")])
    sparks.shift(fixer.id, client=second)
    opening = second.calls[0]["messages"][1]["content"]
    assert "From Scout: Issue 12." in opening
    assert sparks.find(fixer.id).inbox == []


def test_a_spark_cannot_hand_work_to_itself(model):
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient([
        _reply("", ("hand_off", {"spark": "scout", "note": "Me."})),
        _reply("Done."),
    ])

    sparks.shift(made.id, client=client)

    assert "no other spark" in client.calls[1]["messages"][-1]["content"]


def test_a_watched_folder_that_changes_starts_a_shift(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "a.txt").write_text("one")
    made = sparks.create("Scout", "Sort the inbox.", watch=str(folder))
    sparks._watched.clear()

    assert sparks.watch_tick() == []  # the first look only learns it
    (folder / "b.txt").write_text("two")
    (folder / "a.txt").write_text("changed")
    os.utime(folder / "a.txt", (1, 1))
    started = sparks.watch_tick(now=time.time() + 3600)

    assert [s.id for s in started] == [made.id]
    kept = sparks.find(made.id)
    assert kept.asked and "a.txt, b.txt" in kept.why
    # Its opening tells the shift why it started.
    assert "files changed" in sparks._opening(kept.why, [])


def test_a_watch_waits_for_a_shift_just_run(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    made = sparks.create("Scout", "Sort the inbox.", watch=str(folder))
    sparks._edit(made.id, lambda s: setattr(s, "last_run", time.time()))
    sparks._watched.clear()
    sparks.watch_tick()
    (folder / "new.txt").write_text("x")

    assert sparks.watch_tick() == []
    assert sparks.watch_tick(now=time.time() + 3600) != []


def test_a_watch_must_be_a_folder(tmp_path):
    with pytest.raises(sparks.SparkError, match="not a folder"):
        sparks.create("Scout", "Goal.", watch=str(tmp_path / "missing"))


def test_a_shared_spark_is_added_as_a_copy():
    made = sparks.create("Scout", "Watch the issues.", "Only read.", "2h")
    sparks.teach(made.id, "Skip docs.")

    code = sparks.share_code("scout")
    seen = sparks.read_code(code)
    copy = sparks.add_from(code)

    assert code.startswith(sparks.SHARE_PREFIX)
    assert seen == {
        "name": "Scout", "title": "", "goal": "Watch the issues.",
        "boundaries": "Only read.", "every": 120, "at": "",
        "lessons": ["Skip docs."], "schedule": "every 2 hours",
    }
    assert copy.name == "Scout 2" and copy.id != made.id
    assert sparks.find(copy.id).lessons == ["Skip docs."]


def test_a_spark_has_a_title_it_knows_and_shares():
    made = sparks.create(
        "Scout", "Watch the issues.", title="  Repo   watcher ",
    )

    assert made.title == "Repo watcher"
    assert "Scout (@scout-spark), the user's Repo watcher, a spark" in (
        sparks.chat_prompt(made, "h", "m", "")
    )
    assert sparks.read_code(sparks.share_code(made.id))["title"] == (
        "Repo watcher"
    )
    assert sparks.add_from(sparks.share_code(made.id)).title == "Repo watcher"

    changed = sparks.update(made.id, title="x" * 99)
    assert changed.title == "x" * sparks.TITLE_CHARS
    assert sparks.update(made.id, title="").title == ""
    assert "You are Scout (@scout-spark), a spark" in sparks.chat_prompt(
        sparks.find(made.id), "h", "m", "",
    )


@pytest.mark.parametrize("code", [
    "hello", "flash-spark:%%%", "flash-spark:" + "e30",
])
def test_a_bad_share_code_is_refused(code):
    with pytest.raises(sparks.SparkError):
        sparks.read_code(code)


def test_a_spark_made_paused_waits_until_resumed():
    made = sparks.create("Scout", "Watch the issues.", paused=True)

    assert made.paused
    assert sparks.due(now=time.time() + 10 ** 6) == []

    sparks.set_paused(made.id, False)
    assert [s.id for s in sparks.due()] == [made.id]


def test_a_template_or_shared_spark_can_be_added_paused():
    made = sparks.add_from("repo watch", paused=True)
    copy = sparks.add_from(sparks.share_code(made.id))

    assert made.paused and not copy.paused


def _with_reports(*texts):
    made = sparks.create("Scout", "Watch the issues.")

    def fill(spark):
        spark.reports = [
            sparks.Report(at=float(n + 1), text=text)
            for n, text in enumerate(texts)
        ] + [sparks.Report(at=99.0, text="Nothing new.", quiet=True)]

    sparks._edit(made.id, fill)
    return made


def test_a_report_is_liked_or_disliked_and_its_shifts_are_shown_which():
    made = _with_reports("Three bugs, with links.", "A wall of text.")

    sparks.rate(made.id, 1.0, 1)
    sparks.teach(made.id, "Shorter, please.", 2.0)
    spark = sparks.rate(made.id, 2.0, -1)
    prompt = sparks._prompt(spark, "h", "m", "")

    assert [r.rating for r in spark.reports] == [1, -1, 0]
    assert "- Liked: Three bugs, with links." in prompt
    assert "- Disliked: A wall of text.\n  They said: Shorter, please." in (
        prompt
    )

    spark = sparks.rate(made.id, 1.0, 0)
    assert "Three bugs" not in sparks.rated_block(spark)


def test_nothing_rated_says_so():
    made = _with_reports("One.")

    assert sparks.rated_block(made) == "(none rated yet)"


@pytest.mark.parametrize("at, rating", [(1.0, 5), (1.0, "x"), (99.0, 1),
                                        (7.0, 1)])
def test_a_rating_must_be_a_like_or_dislike_of_a_real_report(at, rating):
    made = _with_reports("One.")

    with pytest.raises(sparks.SparkError):
        sparks.rate(made.id, at, rating)


def test_the_latest_report_is_rated_from_the_terminal():
    made = _with_reports("Old.", "New.")

    spark, report = sparks.rate_latest("scout", -1)

    assert report.text == "New." and report.rating == -1
    with pytest.raises(sparks.SparkError, match="no report"):
        sparks.rate_latest(sparks.create("Quiet", "Goal.").id, 1)
    assert made.id == spark.id


def test_a_shift_gets_as_many_tool_rounds_as_the_user_set(monkeypatch):
    monkeypatch.delenv("SPARK_SHIFT_ROUNDS", raising=False)
    assert sparks.shift_rounds() == sparks.SHIFT_ROUNDS_DEFAULT

    assert sparks.set_shift_rounds("3") == 3
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient(
        [_reply("", ("keep_notes", {"notes": "n"}))] * 3
        + [_reply("Found nothing worth saying.")]
    )

    sparks.shift(made.id, client=client)

    # Three rounds of tools, then the last word with none offered.
    assert len(client.calls) == 4
    assert client.calls[-1]["tools"] is None
    assert sparks.find(made.id).reports[-1].text.startswith("Found nothing")


def test_the_round_limit_is_read_from_the_settings_file(monkeypatch):
    sparks.set_shift_rounds(30)
    monkeypatch.setenv("SPARK_SHIFT_ROUNDS", "5")

    # Another Flash set 30: the file says so, whatever this one holds.
    assert sparks.shift_rounds() == 30


def test_with_no_limit_a_shift_calls_tools_until_it_is_done():
    sparks.set_shift_rounds(3)
    assert sparks.set_shift_rounds("unlimited") is None
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient(
        [_reply("", ("keep_notes", {"notes": "n"}))] * 25
        + [_reply("Went through all of it.")]
    )

    sparks.shift(made.id, client=client)

    # Every round it asked for, and no last word forced on it.
    assert len(client.calls) == 26
    assert all(call["tools"] is not None for call in client.calls)
    assert sparks.find(made.id).reports[-1].text == "Went through all of it."


def test_the_limit_comes_back_at_the_number_it_had():
    sparks.set_shift_rounds(30)
    sparks.set_shift_rounds_unlimited(True)
    assert sparks.shift_rounds() is None
    assert sparks.shift_rounds_number() == 30

    assert sparks.set_shift_rounds_unlimited(False) == 30
    # Setting a number puts the limit back on too.
    sparks.set_shift_rounds("off")
    assert sparks.set_shift_rounds(8) == 8 and sparks.shift_rounds() == 8


def test_a_shift_with_no_limit_still_stops_when_asked():
    sparks.set_shift_rounds("unlimited")
    made = sparks.create("Scout", "Watch the issues.")

    class Endless:
        calls = 0

        def chat(self, **kwargs):
            Endless.calls += 1
            if Endless.calls == 5:
                sparks.stop(made.id)
            return _reply("", ("keep_notes", {"notes": "n"}))

    report = sparks.shift(made.id, client=Endless())

    assert report.text.startswith("Stopped")
    assert Endless.calls == 5


@pytest.mark.parametrize("value", ["0", "101", "lots", ""])
def test_the_round_limit_must_be_a_sensible_number(value):
    with pytest.raises(sparks.SparkError):
        sparks.set_shift_rounds(value)


def test_a_template_makes_a_spark():
    made = sparks.add_from("repo watch")

    assert made.name == "Repo Watch" and made.every == 120
    assert made.title == "Repo watcher"
    assert "Never commit" in made.boundaries
    assert len(sparks.TEMPLATES) >= 5


class TestTheRestOfIt:
    def test_the_page_answers_stops_shares_and_adds(self, model, fake_shell):
        session = web.Session()
        made = sparks.create("Scout", "Keep the repo current.")
        sparks.shift(made.id, client=FakeClient([
            _reply("", ("shell", {"command": "git pull"})),
        ]))

        got = web.command(session, {
            "name": "spark-deny", "arg": made.id, "why": "not now",
        })["spark"]
        assert got["pending"] == {} or got["status"] == sparks.WAITING
        assert sparks.find(made.id).pending["why"] == "not now"
        code = web.command(session, {"name": "spark-share", "arg": "scout"})
        preview = web.command(session, {
            "name": "spark-code", "arg": code["code"],
        })["template"]
        assert preview["name"] == "Scout"
        added = web.command(
            session, {"name": "spark-add", "arg": code["code"]},
        )
        assert added["spark"]["name"] == "Scout 2"
        listed = web.command(session, {"name": "spark-templates"})
        assert listed["templates"] == sparks.TEMPLATES

    def test_the_page_sees_what_waits_but_not_the_conversation(
        self, model, fake_shell
    ):
        made = sparks.create("Scout", "Keep the repo current.")
        sparks.shift(made.id, client=FakeClient([
            _reply("", ("shell", {"command": "git pull"})),
        ]))

        shown = sparks.find(made.id).to_dict()

        assert shown["waiting"] is True
        assert shown["pending"]["label"] == "Run a command"
        assert "messages" not in shown["pending"]

    def test_the_terminal_approves(self, model, fake_shell):
        from flash import ai

        made = sparks.create("Scout", "Keep the repo current.")
        sparks.shift(made.id, client=FakeClient([
            _reply("", ("shell", {"command": "git pull"})),
        ]))

        ai._sparks_command("approve scout")

        assert sparks.find(made.id).pending["answer"] == "yes"

    def test_a_notification_says_it_asks(self, monkeypatch):
        from flash import notify

        calls = []
        monkeypatch.setattr(notify, "_Notification", None)
        monkeypatch.setattr(notify.sys, "platform", "linux")
        monkeypatch.setattr(notify.shutil, "which", lambda name: name)
        monkeypatch.setattr(
            notify.subprocess, "run", lambda args, **kw: calls.append(args)
        )

        notify.notify_spark("Scout", "Waiting.", asking=True)

        assert calls[0][2] == "Scout needs your approval"


# --- Its own model --------------------------------------------------------


class RecordingClient(FakeClient):
    def chat(self, model, messages, tools=None, options=None):
        self.calls.append({"model": model, "messages": list(messages),
                           "tools": tools})
        return self.responses.pop(0)


def test_a_spark_runs_on_its_own_model(model):
    made = sparks.create("Scout", "Watch the issues.", model="qwen3:8b")
    client = RecordingClient([_reply("Found one.")])

    sparks.shift(made.id, client=client)

    assert client.calls[0]["model"] == "qwen3:8b"
    assert sparks.find(made.id).to_dict()["model_used"] == "qwen3:8b"


def test_a_spark_without_one_runs_on_flashs(model):
    made = sparks.create("Scout", "Watch the issues.")
    client = RecordingClient([_reply("Found one.")])

    sparks.shift(made.id, client=client)

    assert client.calls[0]["model"] == "test-model"
    assert sparks.model_of(made, "web-model") == "web-model"


def test_a_spark_gets_a_new_model(model):
    made = sparks.create("Scout", "Watch the issues.", model="a")

    assert sparks.update(made.id, model="  b  ").model == "b"


def test_a_spark_with_a_model_runs_when_flash_has_none(monkeypatch):
    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "")
    made = sparks.create("Scout", "Watch the issues.", model="qwen3:8b")

    report = sparks.shift(made.id, client=RecordingClient([_reply("Hi.")]))

    assert not report.failed and report.text == "Hi."


def test_make_spark_names_the_model_it_asks_about():
    asked = []
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: asked.append(question) or "y"):
            result = tools.make_spark(
                "Scout", "Watch the issues.", "daily", model="qwen3:8b",
            )

    assert "on qwen3:8b" in asked[0] and "on qwen3:8b" in result
    assert sparks.find("scout").model == "qwen3:8b"


def test_make_spark_takes_flashs_model_by_default():
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: "y"):
            tools.make_spark("Scout", "Watch the issues.")

    assert sparks.find("scout").model == "flash-model"


def test_make_spark_without_any_model_asks_for_one(monkeypatch):
    monkeypatch.setattr(tools, "MODEL_NAME", "")

    with capture_tool_output(lambda kind, text, style: None):
        result = tools.make_spark("Scout", "Watch the issues.")

    assert "no model" in result and sparks.all_sparks() == []


def test_the_page_makes_and_changes_a_sparks_model():
    session = web.Session()
    made = web.command(session, {
        "name": "spark-create", "arg": "Scout", "goal": "Goal.",
        "model": "qwen3:8b",
    })["spark"]
    assert made["model"] == "qwen3:8b"

    web.command(session, {
        "name": "spark-update", "arg": made["id"], "model": "llama3.1",
    })
    added = web.command(session, {
        "name": "spark-add", "arg": "Disk Guard", "model": "phi4",
    })["spark"]

    assert sparks.find(made["id"]).model == "llama3.1"
    assert added["model"] == "phi4"


def test_autonomous_mode_turned_on_in_another_flash_is_heeded(
    model, fake_shell, monkeypatch, tmp_path,
):
    """This Flash still has it off in memory; the one the user switched
    it on in wrote it to the env file. A shift goes by the file."""

    env = tmp_path / ".flash.env"
    env.write_text("NO_COMMAND_CONFIRMATION=1\n")
    monkeypatch.setattr(sparks, "ENV_PATH", str(env))
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    made = sparks.create("Scout", "Keep the repo current.")

    report = sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git status"})),
        _reply("Clean."),
    ]))

    assert not report.approval and report.text == "Clean."
    assert fake_shell == ["git status"]


def test_a_tool_that_asks_is_told_yes_in_autonomous_mode(
    model, monkeypatch, tmp_path,
):
    """The real shell tool, which asks when this process thinks
    autonomous mode is off. Asking the terminal from a keeper's thread
    would wait forever."""

    env = tmp_path / ".flash.env"
    env.write_text("NO_COMMAND_CONFIRMATION=1\n")
    monkeypatch.setattr(sparks, "ENV_PATH", str(env))
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)

    def no_terminal():
        raise AssertionError("asked the terminal")

    monkeypatch.setattr(tools, "typed", no_terminal)
    made = sparks.create("Scout", "Say hi.")
    client = FakeClient([
        _reply("", ("shell", {"command": "echo hi-from-spark"})),
        _reply("Said hi."),
    ])

    sparks.shift(made.id, client=client)

    assert "hi-from-spark" in client.calls[1]["messages"][-1]["content"]


def test_autonomous_mode_off_in_the_file_waits_even_if_on_here(
    model, fake_shell, monkeypatch, tmp_path,
):
    env = tmp_path / ".flash.env"
    env.write_text("NO_COMMAND_CONFIRMATION=0\n")
    monkeypatch.setattr(sparks, "ENV_PATH", str(env))
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    made = sparks.create("Scout", "Keep the repo current.")

    report = sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"})),
    ]))

    assert report.approval and fake_shell == []


def test_with_nothing_in_the_file_this_flash_decides(
    model, fake_shell, monkeypatch, tmp_path,
):
    monkeypatch.setattr(sparks, "ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)

    assert sparks.autonomous() is True
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    assert sparks.autonomous() is False


# --- Knowing its own status -------------------------------------------------


def test_a_spark_talked_to_knows_it_is_idle_and_when_it_works_next(model):
    made = sparks.create("Scout", "Watch the issues.", every="2h")
    sparks.shift(made.id, client=FakeClient([_reply("Found one.")]))

    status = sparks.status_block(sparks.find(made.id))

    assert "Idle, between shifts" in status
    assert "Last shift: 0 minutes ago" in status
    assert "Next shift: in 119 minutes" in status or \
        "Next shift: in about 2 hours" in status
    assert "Shifts so far: 1" in status and "not read: 1" in status
    assert "You run on the model test-model." in status


def test_a_spark_talked_to_knows_what_it_waits_on(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"})),
    ]))

    status = sparks.status_block(sparks.find(made.id))

    assert "waiting for the user to approve a step: Run a command" in status
    assert "(git pull)" in status
    assert "/sparks approve scout" in status
    assert "Next shift" not in status


def test_a_spark_talked_to_knows_it_is_working_or_paused():
    made = sparks.create("Scout", "Watch the issues.")
    sparks._edit(made.id, lambda s: (
        setattr(s, "status", sparks.WORKING),
        setattr(s, "activity", "Running Fetch(x)"),
    ))
    assert "In the middle of a shift (Running Fetch(x))" in \
        sparks.status_block(sparks.find(made.id))

    sparks._edit(made.id, lambda s: setattr(s, "status", sparks.IDLE))
    sparks.set_paused(made.id, True)
    assert "Paused" in sparks.status_block(sparks.find(made.id))


def test_its_status_is_in_what_it_is_told_in_a_chat(model):
    made = sparks.create("Scout", "Watch the issues.", every="daily")
    client = FakeClient([_reply("Nothing yet.")])

    sparks.say(made.id, "How are you doing?", client)

    system = client.calls[0]["messages"][0]["content"]
    assert "=== Your status right now ===" in system
    assert "You have not run a shift yet" in system


# --- Jobs taken on in a chat -------------------------------------------------


def test_a_job_taken_on_runs_next_and_reports_back_to_its_chat(model):
    made = sparks.create("Scout", "Watch the issues.", every="daily")
    sparks._edit(made.id, lambda s: setattr(s, "next_run", time.time() + 9e5))

    sparks.take_on("scout", "Check issue 12 on the tracker.", chat="c1")

    assert [s.id for s in sparks.due()] == [made.id]
    client = FakeClient([_reply("Issue 12 is fixed upstream.")])
    report = sparks.shift(made.id, client=client)
    opening = client.calls[0]["messages"][1]["content"]
    assert "The user asked you, in a chat, to do this" in opening
    assert "Check issue 12 on the tracker." in opening
    assert report.chats == ["c1"] and not report.quiet
    assert [(s.id, r.at, c) for s, r, c in sparks.to_post()] == [
        (made.id, report.at, "c1"),
    ]
    sparks.posted(made.id, report.at, "c1")
    assert sparks.to_post() == []


def test_a_job_with_nothing_to_say_still_answers(model):
    made = sparks.create("Scout", "Watch the issues.")
    sparks.take_on(made.id, "Look for anything about login.", chat="c1")

    report = sparks.shift(made.id, client=FakeClient([
        _reply(sparks.NOTHING_NEW),
    ]))

    assert not report.quiet
    assert report.text == "Done, with nothing to report on it."


def test_a_job_that_needs_approval_still_reports_back(model, fake_shell):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.take_on(made.id, "Pull the latest.", chat="c1")
    asking = sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"})),
    ]))
    assert asking.approval and asking.chats == ["c1"]

    sparks.answer_step(made.id, True)
    done = sparks.shift(made.id, client=FakeClient([_reply("Pulled.")]))

    assert done.chats == ["c1"] and done.text == "Pulled."


def test_what_is_handed_over_while_it_waits_is_kept_for_later(
    model, fake_shell,
):
    made = sparks.create("Scout", "Keep the repo current.")
    sparks.shift(made.id, client=FakeClient([
        _reply("", ("shell", {"command": "git pull"})),
    ]))
    sparks.take_on(made.id, "Also check the tags.", chat="c2")
    sparks.answer_step(made.id, True)

    resumed = sparks.shift(made.id, client=FakeClient([_reply("Pulled.")]))

    assert resumed.chats == []
    kept = sparks.find(made.id)
    assert kept.inbox and kept.inbox[0]["text"] == "Also check the tags."
    assert kept.asked
    later = FakeClient([_reply("Tags are fine.")])
    report = sparks.shift(made.id, client=later)
    assert "Also check the tags." in later.calls[0]["messages"][1]["content"]
    assert report.chats == ["c2"]


def test_a_spark_takes_on_a_job_when_asked_in_a_chat(model):
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient([
        _reply("", ("take_on", {"job": "Summarise this week's issues."})),
        _reply("On it: I will report back."),
    ])

    reply = sparks.say(made.id, "Actually, can you sum up this week?", client)

    assert "TakeOn(Summarise this week's issues.)" in reply.steps
    kept = sparks.find(made.id)
    # From the terminal, there is no web chat: it goes to its reports.
    assert kept.inbox[0]["job"] and kept.inbox[0]["chat"] == ""
    assert kept.asked
    names = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert "take_on" in names


def test_the_page_reads_set_times_back_before_saving():
    session = web.Session()

    read = web.command(session, {"name": "spark-schedule", "arg": "mon 8:30"})

    assert read["at"] == "30 8 * * 1"
    assert read["schedule"] == "at 8:30am on Mondays"
    assert read["next"] == sparks.cron.next_after("30 8 * * 1", time.time())
    with pytest.raises(ValueError, match="at most every 15"):
        web.command(session, {"name": "spark-schedule", "arg": "*/5 * * * *"})


# --- Asking each other ----------------------------------------------------


@pytest.fixture
def one_client(monkeypatch):
    """One fake model for every call, the ones made inside a shift too."""

    client = FakeClient([])
    monkeypatch.setattr(sparks.ollama, "Client", lambda host=None: client)
    return client


def test_a_spark_answers_from_what_it_knows_with_no_tools(model, one_client):
    sparks.create("Scout", "Watch the issues.")
    sparks._edit("scout", lambda s: setattr(s, "notes", "Issue 12 is a bug."))
    one_client.responses = [_reply("Issue 12 is a bug; I reported it.")]

    found, answer = sparks.consult("scout", "Any bugs?", asker="Flash")

    assert found.name == "Scout"
    assert answer == "Issue 12 is a bug; I reported it."
    asked = one_client.calls[0]
    assert asked["tools"] == []
    system = asked["messages"][0]["content"]
    assert "=== Flash is asking you ===" in system
    assert "Issue 12 is a bug." in system
    assert asked["messages"][1] == {"role": "user", "content": "Any bugs?"}


def test_asking_nobody_names_who_there_is():
    sparks.create("Scout", "Watch the issues.")

    with pytest.raises(sparks.SparkError, match="sparks: Scout"):
        sparks.consult("ghost", "Hello?")
    with pytest.raises(sparks.SparkError, match="Ask it something"):
        sparks.consult("scout", "  ")


def test_a_spark_asks_another_mid_shift(model, one_client):
    sparks.create("Scout", "Watch the issues.")
    tester = sparks.create("Tester", "Run the tests.")
    one_client.responses = [
        _reply("", ("ask_spark", {"spark": "scout", "question": "Bugs?"})),
        _reply("Issue 12 crashes on login."),  # Scout's answer
        _reply("", ("keep_notes", {"notes": "Check login."})),
        _reply("Wrote a test for the login crash."),
    ]

    report = sparks.shift(tester.id)

    assert report.text == "Wrote a test for the login crash."
    # Asking in the middle did not cost the shift its record of steps.
    assert report.steps == ["AskSpark(Scout)", "KeepNotes()"]
    told = one_client.calls[2]["messages"]
    assert told[-1]["content"] == "Scout says: Issue 12 crashes on login."
    asked = one_client.calls[1]["messages"][0]["content"]
    assert "Tester (@tester-spark), another spark" in asked
    offered = [t["function"]["name"] for t in one_client.calls[0]["tools"]]
    assert "ask_spark" in offered


def test_a_spark_cannot_ask_itself(model):
    made = sparks.create("Scout", "Watch the issues.")

    said = sparks._asking(made)({"spark": "scout", "question": "Me?"})

    assert said.startswith("Error: that is you")


def test_a_spark_can_ask_another_in_a_chat(model):
    made = sparks.create("Scout", "Watch the issues.")

    assert "ask_spark" in sparks.ChatKit(made).tools
    assert sparks.ASK_SPARK_TOOL in sparks.ChatKit.schemas


def test_flash_asks_a_spark(model, one_client):
    sparks.create("Scout", "Watch the issues.")
    one_client.responses = [_reply("Two new issues today.")]

    said = tools.ask_spark("scout", "What did you find?")

    assert said == "Scout (@scout-spark) says: Two new issues today."
    system = one_client.calls[0]["messages"][0]["content"]
    assert "Flash, the assistant the user talks to" in system


def test_flash_asking_nobody_is_told_so():
    assert tools.ask_spark("ghost", "Hello?").startswith(
        "Error: There is no spark called 'ghost'"
    )


def test_flash_gives_a_spark_a_job():
    made = sparks.create("Scout", "Watch the issues.")

    said = tools.give_spark("scout", "Check the login page.")

    assert "given to Scout" in said and "in its reports" in said
    kept = sparks.find(made.id)
    assert kept.inbox[0]["text"] == "Check the login page."
    assert kept.inbox[0]["job"] is True
    assert kept.asked


def test_flash_knows_the_users_sparks():
    assert sparks.roster_block() == ""
    assert "The user's sparks" not in tools.build_system_prompt()

    sparks.create("Scout", "Watch the issues.\nAnd more.")
    sparks.set_paused("scout", True)

    block = sparks.roster_block()
    assert "- Scout (@scout-spark) (paused): Watch the issues." in block
    assert block in tools.build_system_prompt()


# --- A busy model ---------------------------------------------------------


class RefusingOnce(FakeClient):
    """Says it is busy, then answers: as Ollama's cloud does when a chat
    with the spark has the one request it allows at a time."""

    def __init__(self, responses, error):
        super().__init__(responses)
        self.error = error

    def chat(self, model, messages, tools=None, options=None):
        if self.error is not None:
            error, self.error = self.error, None
            self.calls.append({"refused": True})
            raise error
        return super().chat(model, messages, tools, options)


@pytest.fixture
def no_waiting(monkeypatch):
    waited = []
    monkeypatch.setattr(
        sparks, "_pause", lambda seconds, stopping: waited.append(seconds),
    )
    return waited


@pytest.mark.parametrize("error", [
    sparks.ollama.ResponseError("too many concurrent requests", 429),
    sparks.ollama.ResponseError("server overloaded, try again", 500),
    sparks.ollama.ResponseError("upstream unavailable", 503),
    ConnectionError("Failed to connect to Ollama"),
])
def test_a_busy_model_is_waited_out_mid_shift(model, no_waiting, error):
    made = sparks.create("Brief", "Write the brief.")
    client = RefusingOnce([_reply("The brief.")], error)

    report = sparks.shift(made.id, client=client)

    assert report.text == "The brief." and not report.failed
    assert no_waiting == [sparks.RETRY_WAITS[0]]


def test_a_model_that_stays_busy_fails_the_shift_in_the_end(
    model, no_waiting,
):
    made = sparks.create("Brief", "Write the brief.")

    class Busy:
        def chat(self, **kwargs):
            raise sparks.ollama.ResponseError("too many requests", 429)

    report = sparks.shift(made.id, client=Busy())

    assert report.failed and "too many requests" in report.text
    assert no_waiting == list(sparks.RETRY_WAITS)


def test_a_real_error_is_not_retried(model, no_waiting):
    made = sparks.create("Brief", "Write the brief.")
    client = RefusingOnce(
        [_reply("Never asked.")],
        sparks.ollama.ResponseError("model 'nope' not found", 404),
    )

    report = sparks.shift(made.id, client=client)

    assert report.failed and "not found" in report.text
    assert no_waiting == []


def test_stop_still_stops_while_it_waits(model, monkeypatch):
    made = sparks.create("Brief", "Write the brief.")
    monkeypatch.setattr(sparks, "RETRY_STEP_SECONDS", 0.01)

    class Busy:
        def chat(self, **kwargs):
            sparks._edit(made.id, lambda s: setattr(s, "stop_asked", True))
            raise sparks.ollama.ResponseError("too many requests", 429)

    started = time.monotonic()
    report = sparks.shift(made.id, client=Busy())

    assert report.text.startswith("Stopped, as you asked")
    assert time.monotonic() - started < 1


def test_a_chat_with_a_spark_waits_out_a_busy_model_too(model, no_waiting):
    made = sparks.create("Brief", "Write the brief.")
    client = RefusingOnce(
        [_reply("Not yet: I am mid-shift.")],
        sparks.ollama.ResponseError("too many concurrent requests", 429),
    )

    reply = sparks.say(made.id, "Done yet?", client=client)

    assert reply.text == "Not yet: I am mid-shift." and not reply.failed


# --- Email, before it is connected -------------------------------------------


def test_an_email_spark_is_told_email_is_not_connected(model):
    made = sparks.add_from("Inbox")
    client = FakeClient([
        _reply("", ("check_inbox", {"unread_only": True})),
        _reply("Email is not connected: connect it in Settings > Email."),
    ])

    report = sparks.shift(made.id, client=client)

    offered = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert {"check_inbox", "read_email", "send_email"} <= offered
    told = client.calls[1]["messages"][-1]["content"]
    assert "Email is not set up yet" in told and "Settings > Email" in told
    assert report.text.startswith("Email is not connected")
    assert sparks.needs_email(made)


def test_other_sparks_are_not_offered_email_tools(model):
    made = sparks.create("Scout", "Watch the issues.")
    client = FakeClient([_reply("Nothing.")])

    sparks.shift(made.id, client=client)

    offered = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert "check_inbox" not in offered
    assert not sparks.needs_email(made)


# --- The default model for new sparks -------------------------------------


def test_new_sparks_are_on_flashs_model_until_a_default_is_set():
    assert sparks.default_model() == ""  # nosec B101
    made = sparks.model_for_new("mine", look=False)

    assert made == ("mine", "")  # nosec B101


def test_the_default_is_kept_and_flash_takes_it_back():
    assert sparks.set_default_model("qwen3:8b") == "qwen3:8b"  # nosec B101
    assert sparks.default_model() == "qwen3:8b"  # nosec B101
    saved = Path(sparks.ENV_PATH).read_text()
    assert "SPARK_DEFAULT_MODEL=" in saved  # nosec B101

    assert sparks.set_default_model("flash") == ""  # nosec B101
    assert sparks.default_model() == ""  # nosec B101


def test_new_sparks_are_made_on_the_default_while_it_is_here():
    sparks.set_default_model("qwen3")

    here = {"qwen3:latest", "llama3.1:latest"}
    made = sparks.model_for_new("llama3.1", here)
    # Not known whether it is here: it is used, and Ollama has its say.
    unknown = sparks.model_for_new("llama3.1", None, look=False)

    assert made == unknown == ("qwen3", "")  # nosec B101


def test_a_default_not_listed_is_kept_not_switched():
    sparks.set_default_model("qwen3:8b")

    model, note = sparks.model_for_new("llama3.1", {"llama3.1:latest"})

    assert model == "qwen3:8b"  # nosec B101
    assert "not in this computer's model list" in note  # nosec B101


def test_a_cloud_default_is_never_called_missing():
    sparks.set_default_model("gpt-oss:120b-cloud")

    made = sparks.model_for_new("llama3.1", {"llama3.1:latest"})
    cloud = sparks.is_here("deepseek-v3.1:671b-cloud", set())

    assert made == ("gpt-oss:120b-cloud", "")  # nosec B101
    assert cloud is None  # nosec B101
    assert sparks.is_here("qwen3:8b", set()) is False  # nosec B101


def test_make_spark_uses_the_default(model, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    monkeypatch.setattr(sparks, "models_here", lambda client=None: None)
    sparks.set_default_model("qwen3:8b")

    result = tools.make_spark("Scout", "Watch the issues.")

    assert "on qwen3:8b" in result  # nosec B101
    assert sparks.all_sparks()[0].model == "qwen3:8b"  # nosec B101


def test_make_spark_keeps_a_default_that_is_not_listed(model, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    monkeypatch.setattr(
        sparks, "models_here", lambda client=None: {"test-model:latest"},
    )
    sparks.set_default_model("qwen3:8b")

    result = tools.make_spark("Scout", "Watch the issues.")

    assert sparks.all_sparks()[0].model == "qwen3:8b"  # nosec B101
    assert "Tell the user: qwen3:8b" in result  # nosec B101


class FailingClient(FakeClient):
    """A host whose model refuses every request."""

    def __init__(self, error):
        super().__init__([])
        self.error = error
        self.used = []

    def chat(self, model, messages, tools=None, options=None):
        self.used.append(model)
        raise self.error


@pytest.fixture
def no_waits(monkeypatch):
    monkeypatch.setattr(sparks, "RETRY_WAITS", ())


def test_a_failing_model_marks_the_spark_unavailable(model, no_waits):
    import ollama

    made = sparks.create("Scout", "Watch.", model="glm-4.6:cloud")
    client = FailingClient(ollama.ResponseError("model not found", 404))

    report = sparks.shift(made.id, client=client)

    found = sparks.find(made.id)
    assert report.failed  # nosec B101
    assert "glm-4.6:cloud is unavailable" in report.text  # nosec B101
    assert "never switched to another model" in report.text  # nosec B101
    assert found.unavailable  # nosec B101
    assert found.unavailable_model == "glm-4.6:cloud"  # nosec B101
    # Only its own model was ever asked.
    assert set(client.used) == {"glm-4.6:cloud"}  # nosec B101
    assert found.model == "glm-4.6:cloud"  # nosec B101


def test_an_unavailable_spark_waits_for_you(model, no_waits):
    made = sparks.create("Scout", "Watch.")
    sparks.shift(made.id, client=FailingClient(ConnectionError("down")))

    assert sparks.due(time.time() + 10 ** 8) == []  # nosec B101

    sparks.run_now(made.id)
    assert [s.id for s in sparks.due()] == [made.id]  # nosec B101
    report = sparks.shift(made.id, client=FakeClient([_reply("Back.")]))

    assert report.text == "Back."  # nosec B101
    assert not sparks.find(made.id).unavailable  # nosec B101


def test_another_model_clears_unavailable(model, no_waits):
    made = sparks.create("Scout", "Watch.", model="a:1b")
    sparks.shift(made.id, client=FailingClient(ConnectionError("down")))

    sparks.update(made.id, model="b:2b")

    assert not sparks.find(made.id).unavailable  # nosec B101


def test_a_chat_that_fails_on_its_model_marks_it_too(model, no_waits):
    made = sparks.create("Scout", "Watch.")

    reply = sparks.say(made.id, "Hi?", FailingClient(ConnectionError("x")))

    assert reply.failed  # nosec B101
    assert sparks.find(made.id).unavailable == "x"  # nosec B101


def test_the_terminal_says_unavailable(model, no_waits):
    from flash import ai

    made = sparks.create("Scout", "Watch.", model="a:1b")
    sparks.shift(made.id, client=FailingClient(ConnectionError("down")))

    assert ai._spark_state(sparks.find(made.id)).startswith(  # nosec B101
        "unavailable: a:1b failed (down)"
    )


# --- On call -----------------------------------------------------------------


@pytest.mark.parametrize("words", [
    "on call", "On Call", "oncall", "on demand", "manual", "never",
    "only when called on", "when mentioned", 0, "0",
])
def test_on_call_reads_as_no_schedule(words):
    assert sparks.parse_schedule(words) == (sparks.ON_CALL, "")  # nosec B101


def test_an_on_call_spark_is_never_due_by_the_clock(model):
    made = sparks.create("Scout", "Look when asked.", every="on call")

    assert sparks.on_call(made)  # nosec B101
    assert sparks.schedule_words(made) == "only when called on"  # nosec B101
    assert sparks.due(time.time() + 10 ** 8) == []  # nosec B101


def test_an_on_call_spark_runs_when_called_on(model):
    made = sparks.create("Scout", "Look when asked.", every="on call")

    sparks.run_now(made.id)

    assert [s.id for s in sparks.due()] == [made.id]  # nosec B101
    report = sparks.shift(made.id, client=FakeClient([_reply("Looked.")]))
    assert report.text == "Looked."  # nosec B101
    # Done, it waits to be called on again.
    assert sparks.due(time.time() + 10 ** 8) == []  # nosec B101


def test_a_hand_off_calls_an_on_call_spark(model):
    sparks.create("Lead", "Lead.")
    helper = sparks.create("Helper", "Help when asked.", every="on call")
    lead = sparks.find("lead")
    hand = sparks._handing_off(lead)

    hand({"spark": "helper", "note": "Check the build."})

    assert helper.id in [s.id for s in sparks.due()]  # nosec B101


def test_a_report_that_mentions_an_on_call_spark_calls_it(model):
    sparks.create("Scout", "Watch the issues.")
    fixer = sparks.create("Fixer", "Fix what is found.", every="on call")
    other = sparks.create("Other", "Watch the docs.")

    sparks.shift(
        sparks.find("scout").id,
        client=FakeClient([
            _reply("Issue 12 is a bug. @fixer can you take it?"),
        ]),
    )

    called = sparks.find(fixer.id)
    assert called.asked  # nosec B101
    assert called.inbox[-1]["mentioned"] is True  # nosec B101
    assert "Issue 12" in called.inbox[-1]["text"]  # nosec B101
    assert sparks.find(other.id).inbox == []  # nosec B101
    opening = sparks._opening("", called.inbox)
    assert "@mentioned you in their reports" in opening  # nosec B101


def test_a_scheduled_spark_mentioned_gets_the_note_but_is_not_woken(model):
    sparks.create("Scout", "Watch the issues.")
    later = sparks.create("Later", "Works hourly.")
    sparks.update(later.id, every="daily")

    sparks.shift(
        sparks.find("scout").id,
        client=FakeClient([_reply("@later this is for you.")]),
    )

    found = sparks.find(later.id)
    assert found.inbox and not found.asked  # nosec B101


def test_an_on_call_spark_knows_it_is_on_call(model):
    made = sparks.create("Scout", "Look when asked.", every="on call")

    block = sparks.status_block(made)

    assert "you are on call" in block  # nosec B101
    assert "Next shift" not in block  # nosec B101


def test_a_spark_can_be_moved_on_and_off_call(model):
    made = sparks.create("Scout", "Look.")

    sparks.update(made.id, every="on call")
    assert sparks.on_call(sparks.find(made.id))  # nosec B101

    sparks.update(made.id, every="2h")
    assert not sparks.on_call(sparks.find(made.id))  # nosec B101
    assert sparks.find(made.id).every == 120  # nosec B101


def test_make_spark_says_an_on_call_spark_waits(model, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)

    result = tools.make_spark("Fixer", "Fix things.", every="on call")

    assert "It is on call" in result  # nosec B101
    assert sparks.on_call(sparks.find("fixer"))  # nosec B101


def test_the_terminal_says_on_call(model):
    from flash import ai

    made = sparks.create("Scout", "Look.", every="on call")

    assert ai._spark_state(made) == "on call"  # nosec B101


# --- Documents ---------------------------------------------------------------


def test_a_spark_writes_a_document_and_a_new_version(model):
    made = sparks.create("Scout", "Write things up.")

    first = sparks.write_document(made.id, "Weekly Brief", "All quiet.")
    again = sparks.write_document(
        made.id, "Weekly Brief", "# Brief\n\nBusy.",
    )

    assert first == again  # nosec B101
    assert first.read_text().startswith("# Brief")  # nosec B101
    found = sparks.documents(made.id)
    assert [d["title"] for d in found] == ["Brief"]  # nosec B101


def test_a_document_gets_its_title_as_a_heading(model):
    made = sparks.create("Scout", "Write things up.")

    path = sparks.write_document(made.id, "Release notes", "Two fixes.")

    assert path.read_text() == "# Release notes\n\nTwo fixes.\n"  # nosec B101
    with pytest.raises(sparks.SparkError):
        sparks.write_document(made.id, "", "x")
    with pytest.raises(sparks.SparkError):
        sparks.write_document(made.id, "Empty", "  ")


def test_only_a_sparks_own_documents_can_be_reached(model, tmp_path):
    scout = sparks.create("Scout", "Write.")
    other = sparks.create("Other", "Write.")
    mine = sparks.write_document(scout.id, "Mine", "Text.")
    secret = tmp_path / "secret.md"
    secret.write_text("no")

    found = sparks.document_of(scout.id, str(mine))
    assert found == mine.resolve()  # nosec B101
    for path in (str(secret), str(mine.parent / ".." / "x.md"), ""):
        with pytest.raises(sparks.SparkError):
            sparks.document_of(scout.id, path)
    with pytest.raises(sparks.SparkError):
        sparks.document_of(other.id, str(mine))


def test_a_shift_brings_its_documents_with_its_report(model):
    made = sparks.create("Scout", "Write a brief.")
    client = FakeClient([
        _reply("", ("make_document", {
            "title": "Morning brief", "content": "Three things.",
        })),
        _reply("NOTHING NEW"),
    ])

    report = sparks.shift(made.id, client=client)

    assert not report.quiet  # nosec B101
    titles = [f["title"] for f in report.files]
    assert titles == ["Morning brief"]  # nosec B101
    assert "MakeDocument(Morning brief)" in report.steps  # nosec B101
    saved = sparks.find(made.id).reports[-1]
    assert saved.files[0]["path"].endswith("morning-brief.md")  # nosec B101


def test_a_chat_answer_can_write_a_document(model):
    made = sparks.create("Scout", "Write.")
    client = FakeClient([
        _reply("", ("make_document", {"title": "Plan", "content": "Do it."})),
        _reply("Here is the plan."),
    ])

    reply = sparks.say(made.id, "Write me a plan?", client)

    assert reply.text == "Here is the plan."  # nosec B101
    titles = [d["title"] for d in sparks.documents(made.id)]
    assert titles == ["Plan"]  # nosec B101


def test_a_document_opening_with_a_section_still_gets_its_title(model):
    made = sparks.create("Scout", "Write.")

    path = sparks.write_document(made.id, "Review", "## Summary\n\nFine.")

    assert path.read_text().startswith("# Review\n\n## Summary")  # nosec B101
    titles = [d["title"] for d in sparks.documents(made.id)]
    assert titles == ["Review"]  # nosec B101
