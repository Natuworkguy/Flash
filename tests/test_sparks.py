# pylint: disable=C0114,C0115,C0116

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
