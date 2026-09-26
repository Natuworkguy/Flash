"""Tests for skills and the learning loop that writes them."""

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from flash import ai, learning, memory, skills, tools
from flash.skills import SkillError


def create(name="deploy-app", body="1. Build.\n2. Ship.", **kwargs):
    return skills.manage(
        "create", name, description="Ship the app", content=body, **kwargs
    )


class TestSkills:
    def test_create_then_view(self):
        assert create() == "Created skill deploy-app."

        text = skills.view("deploy-app")

        assert text.startswith("Skill: deploy-app\nShip the app")
        assert "1. Build." in text

    def test_the_file_round_trips(self):
        create()

        skill = skills.find("deploy-app")

        assert skill.name == "deploy-app"
        assert skill.description == "Ship the app"
        assert skill.body == "1. Build.\n2. Ship."
        assert not skill.managed

    def test_patch(self):
        create()

        skills.manage(
            "patch", "deploy-app",
            old_string="2. Ship.", new_string="2. Test.\n3. Ship.",
        )

        assert skills.find("deploy-app").body.endswith("2. Test.\n3. Ship.")

    @pytest.mark.parametrize("old, message", [
        ("nowhere", "not found"),
        (". ", "found 2 times"),
        ("", "not found"),
    ])
    def test_patch_needs_one_exact_match(self, old, message):
        create()

        with pytest.raises(SkillError, match=message):
            skills.manage("patch", "deploy-app", old_string=old, new_string="")

    def test_rewrite_and_describe(self):
        create()

        skills.manage(
            "rewrite", "deploy-app", content="Just run make.",
            description="Ship it with make",
        )

        skill = skills.find("deploy-app")
        assert (skill.description, skill.body) == (
            "Ship it with make", "Just run make."
        )

    def test_delete(self):
        create()

        skills.manage("delete", "deploy-app")

        assert skills.find("deploy-app") is None

    @pytest.mark.parametrize("call, message", [
        (lambda: create("Bad Name"), "not a skill name"),
        (lambda: create("../escape"), "not a skill name"),
        (lambda: skills.manage("create", "x", content="y"), "description"),
        (lambda: skills.manage("create", "x", description="d"), "content"),
        (lambda: skills.manage(
            "create", "x", description="d" * 200, content="y"),
         "limit"),
        (lambda: create(body="x" * (skills.MAX_SKILL_CHARS + 1)), "under"),
        (lambda: skills.manage("explode", "x"), "unknown action"),
        (lambda: skills.manage("patch", "nope"), "No skill"),
    ])
    def test_refusals(self, call, message):
        with pytest.raises(SkillError, match=message):
            call()

    def test_creating_twice_says_to_patch(self):
        create()

        with pytest.raises(SkillError, match="patch it"):
            create()

    def test_other_files_are_listed_and_readable(self):
        create()
        extra = skills.find("deploy-app").path / "references" / "flags.md"
        extra.parent.mkdir()
        extra.write_text("--prod", encoding="utf-8")

        assert "references/flags.md" in skills.view("deploy-app")
        assert skills.view("deploy-app", "references/flags.md") == "--prod"

    def test_no_reading_outside_the_skill(self):
        create()

        with pytest.raises(SkillError, match="no file"):
            skills.view("deploy-app", "../../.flash_memory.md")


class TestReviewOwnership:
    def test_the_review_marks_what_it_writes(self):
        create(managed_only=True)

        assert skills.find("deploy-app").managed

    def test_the_review_can_change_its_own(self):
        create(managed_only=True)

        skills.manage(
            "patch", "deploy-app", old_string="Ship.", new_string="Deploy.",
            managed_only=True,
        )

        assert skills.find("deploy-app").managed

    @pytest.mark.parametrize("action", ["patch", "rewrite", "delete"])
    def test_the_review_leaves_the_users_alone(self, action):
        create()

        with pytest.raises(SkillError, match="written by the user"):
            skills.manage(
                action, "deploy-app", content="x", old_string="Ship.",
                new_string="", managed_only=True,
            )

        assert skills.find("deploy-app").body == "1. Build.\n2. Ship."


class TestPrompt:
    def test_nothing_saved_adds_nothing(self):
        assert learning.prompt_block() == ""
        assert tools.build_system_prompt() == tools.SYSTEM_PROMPT

    def test_skills_and_memory_go_inside_the_prompt(self):
        create()
        memory.add_memory("The user deploys on Fridays")

        prompt = tools.build_system_prompt()

        assert "- deploy-app: Ship the app" in prompt
        assert "- The user deploys on Fridays" in prompt
        assert prompt.index("=== Skills ===") < prompt.index(
            "=== END OF SYSTEM PROMPT ==="
        )

    def test_it_is_frozen_until_refreshed(self):
        before = learning.prompt_block()
        create()

        assert learning.prompt_block() == before

        learning.refresh()

        assert "deploy-app" in learning.prompt_block()

    def test_memory_keeps_the_newest_that_fit(self, monkeypatch):
        monkeypatch.setattr(learning, "MEMORY_PROMPT_CHARS", 30)
        for fact in ("oldest fact here", "middle fact here", "newest one"):
            memory.add_memory(fact)

        block = learning.memory_block()

        assert "newest one" in block
        assert "oldest fact here" not in block
        assert "older entries are not shown" in block

    def test_the_listing_is_capped(self, monkeypatch):
        monkeypatch.setattr(skills, "MAX_LISTED", 2)
        for name in ("a-one", "b-two", "c-three"):
            create(name)

        listing = skills.listing()

        assert "a-one" in listing and "b-two" in listing
        assert "c-three" not in listing
        assert "1 more" in listing


def call(tool, **arguments):
    return SimpleNamespace(
        function=SimpleNamespace(name=tool, arguments=arguments)
    )


def chunk(content="", calls=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=calls)
    )


class FakeClient:
    """An Ollama client that answers each chat with the next script."""

    scripts: list = []
    seen: list = []

    def __init__(self, host=None):
        pass

    def chat(self, **kwargs):
        FakeClient.seen.append(kwargs)
        yield from FakeClient.scripts.pop(0)


@pytest.fixture
def fake_model(monkeypatch):
    FakeClient.scripts = []
    FakeClient.seen = []
    monkeypatch.setattr(learning.ollama, "Client", FakeClient)
    return FakeClient


class Inline:
    """threading.Thread, run on the spot so tests need not wait."""

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


TURN = [
    {"role": "user", "content": "ship it"},
    {"role": "assistant", "content": "Shipped after fixing the build."},
]


def review_now(monkeypatch, *, skills_every="1", memory_every="0"):
    monkeypatch.setenv("SKILL_REVIEW_AFTER", skills_every)
    monkeypatch.setenv("MEMORY_REVIEW_EVERY", memory_every)
    return learning.after_turn(
        TURN, 1, host="h", model="m", start=Inline
    )


class TestWhenReviewsRun:
    def test_off_by_default_in_tests(self):
        assert learning.after_turn(TURN, 99, host="h", model="m") is None

    def test_skills_after_enough_tool_calls(self, monkeypatch):
        monkeypatch.setenv("SKILL_REVIEW_AFTER", "5")
        started = []

        def start(**kwargs):
            started.append(kwargs["args"][0])
            return SimpleNamespace(start=lambda: None)

        assert learning.after_turn(TURN, 3, host="h", model="m",
                                   start=start) is None
        review = learning.after_turn(TURN, 2, host="h", model="m",
                                     start=start)

        assert (review.skills, review.memory) == (True, False)
        assert started == [review]
        assert learning.running()

    def test_memory_every_few_messages(self, monkeypatch):
        monkeypatch.setenv("MEMORY_REVIEW_EVERY", "2")
        start = lambda **kwargs: SimpleNamespace(start=lambda: None)  # noqa

        assert learning.after_turn(TURN, 0, host="h", model="m",
                                   start=start) is None
        review = learning.after_turn(TURN, 0, host="h", model="m",
                                     start=start)

        assert (review.skills, review.memory) == (False, True)

    def test_one_at_a_time(self, monkeypatch):
        monkeypatch.setenv("SKILL_REVIEW_AFTER", "1")
        start = lambda **kwargs: SimpleNamespace(start=lambda: None)  # noqa

        assert learning.after_turn(TURN, 1, host="h", model="m", start=start)
        assert learning.after_turn(
            TURN, 1, host="h", model="m", start=start
        ) is None

    def test_no_model_no_review(self, monkeypatch):
        monkeypatch.setenv("SKILL_REVIEW_AFTER", "1")

        assert learning.after_turn(TURN, 5, host="h", model="") is None


class TestTheReview:
    def test_it_saves_a_skill_and_reports_it(self, monkeypatch, fake_model):
        fake_model.scripts = [
            [chunk(calls=[call(
                "skill_manage", action="create", name="ship-app",
                description="Ship the app", content="1. Fix the build.",
            )])],
            [chunk("Created skill ship-app.")],
        ]

        review = review_now(monkeypatch)

        assert review.done and not review.error
        assert skills.find("ship-app").managed
        assert learning.news() == ["Created skill ship-app."]
        assert learning.news() == []
        assert not learning.running()

    def test_nothing_to_save_says_nothing(self, monkeypatch, fake_model):
        fake_model.scripts = [[chunk(learning.NOTHING)]]

        review_now(monkeypatch)

        assert learning.news() == []

    def test_it_cannot_touch_the_users_skills(self, monkeypatch, fake_model):
        create()
        fake_model.scripts = [
            [chunk(calls=[call("skill_manage", action="delete",
                               name="deploy-app")])],
            [chunk("Could not.")],
        ]

        review_now(monkeypatch)

        assert skills.find("deploy-app") is not None
        assert learning.news() == []

    def test_it_only_has_its_own_tools(self, monkeypatch, fake_model):
        fake_model.scripts = [
            [chunk(calls=[call("shell", command="rm -rf /")])],
            [chunk("done")],
        ]

        review_now(monkeypatch, memory_every="1")

        offered = {
            t["function"]["name"] for t in fake_model.seen[0]["tools"]
        }
        assert offered == set(learning.REVIEW_TOOL_NAMES)
        assert fake_model.seen[1]["messages"][-1]["content"] == (
            "Unknown tool: shell."
        )

    def test_it_remembers_facts(self, monkeypatch, fake_model):
        fake_model.scripts = [
            [chunk(calls=[call("remember", entry="Deploys use fly.io")])],
            [chunk("Saved.")],
        ]

        review_now(monkeypatch, skills_every="0", memory_every="1")

        assert memory.list_memory() == ["Deploys use fly.io"]
        assert learning.news() == ["Remembered: Deploys use fly.io"]

    def test_a_cancel_stops_it_and_it_is_owed_again(
        self, monkeypatch, fake_model
    ):
        def slow():
            yield chunk("thinking")
            learning.cancel()
            yield chunk(" more")

        fake_model.scripts = [slow()]

        review = review_now(monkeypatch, skills_every="3")
        # The first after_turn counted only one call of the three.
        assert review is None

        monkeypatch.setenv("SKILL_REVIEW_AFTER", "1")
        review = learning.after_turn(TURN, 1, host="h", model="m",
                                     start=Inline)

        assert review.error == "cancelled"
        assert learning.news() == []

        # Owed again: the very next turn, even with no tool calls, runs it.
        fake_model.scripts = [[chunk(learning.NOTHING)]]
        assert learning.after_turn(TURN, 0, host="h", model="m",
                                   start=Inline)

    def test_a_failure_is_reported(self, monkeypatch, fake_model):
        fake_model.scripts = []  # pop from an empty list: the chat fails

        review_now(monkeypatch)

        assert learning.news()[0].startswith("Learning review failed")

    def test_it_reads_only_the_end_of_a_long_conversation(self, monkeypatch):
        monkeypatch.setattr(learning, "REVIEW_INPUT_CHARS", 50)
        long = [{"role": "user", "content": "x" * 500 + "THE END"}]

        sent = learning.review_messages(
            learning.Review(skills=True, memory=False), long
        )

        assert "(earlier conversation cut)" in sent[1]["content"]
        assert "THE END" in sent[1]["content"]


class TestInTheApp:
    @pytest.fixture
    def screen(self, monkeypatch):
        out = Console(file=io.StringIO(), width=120, force_terminal=False)
        monkeypatch.setattr(ai, "console", out)
        return out

    def test_skills_lists_them(self, screen):
        create()
        create("tidy-up", managed_only=True)

        ai._skills_command("")

        text = screen.file.getvalue()
        assert "deploy-app" in text and "Ship the app" in text
        assert "tidy-up" in text and "(learned)" in text

    def test_skills_show_and_remove(self, screen):
        create()

        ai._skills_command("show deploy-app")
        ai._skills_command("remove deploy-app")

        assert "Build." in screen.file.getvalue()
        assert skills.find("deploy-app") is None

    def test_the_status_bar_says_learning(self, monkeypatch):
        monkeypatch.setattr(learning, "running", lambda: True)
        monkeypatch.setattr(ai, "_history_budget", lambda: 0)

        assert ai._status_text([]).endswith("learning")

    def test_news_is_shown(self, screen, monkeypatch):
        monkeypatch.setattr(
            learning, "news", lambda: ["Created skill ship-app."]
        )

        ai._note_learning()

        assert "Created skill ship-app." in screen.file.getvalue()


class TestTheLoopCountsTurns:
    def test_a_turn_reports_its_tool_calls(self, monkeypatch):
        from flash import agent as subagents
        from flash.cli import parse_args

        feed = iter(["what day is it"])
        replies = iter([
            ("", "", [call("get_date"), call("get_os")], None),
            ("Thursday.", "", [], None),
        ])
        counted = []
        order = []

        def fake_read_line(*args, **kwargs):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError from None

        def fake_chat(*args, **kwargs):
            order.append("chat")
            return next(replies)

        monkeypatch.setattr(ai, "parse_args", lambda: parse_args([]))
        monkeypatch.setattr(ai, "check_for_update", lambda: None)
        monkeypatch.setattr(ai, "read_line", fake_read_line)
        monkeypatch.setattr(ai, "_chat_retry_until_response", fake_chat)
        monkeypatch.setattr(
            ai, "_session_system_prompt", lambda heard=False: ""
        )
        monkeypatch.setattr(ai, "notify_reply_ready", lambda: None)
        monkeypatch.setattr(ai, "_history_budget", lambda: 1000)
        monkeypatch.setattr(ai.Config, "show_stats", False)
        monkeypatch.setattr(ai.Config, "model", "m")
        monkeypatch.setattr(ai.Config, "voice", False)
        monkeypatch.setattr(subagents, "_agents", {})
        monkeypatch.setattr(
            learning, "cancel", lambda: order.append("cancel")
        )
        monkeypatch.setattr(
            learning, "after_turn",
            lambda messages, calls, **kw: counted.append(
                (messages[-1]["content"], calls)
            ),
        )

        ai.main()

        assert counted == [("Thursday.", 2)]
        # The review is stopped before the user's turn asks the model.
        assert order[:2] == ["cancel", "chat"]
