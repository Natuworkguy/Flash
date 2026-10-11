"""Tests for documents kept up to date as the agents edit them, and the
gaps in them left to fill in."""

import threading
import time
from types import SimpleNamespace

import ollama
import pytest

from flash import ai, livedocs, theme, tools, web, workspace


@pytest.fixture
def shown(tmp_path):
    """A document on disk, shown in the web UI, and what that shows."""

    path = tmp_path / "report.md"
    path.write_text("# Q3\n\nRevenue: [insert the Q3 revenue here]\n",
                    encoding="utf-8")
    return path, workspace.keep_file(str(path))


def events_of(session):
    queue = session.hub.subscribe()

    def drain(kind):
        seen = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        return [e for e in seen if e["type"] == kind]

    return drain


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


class TestPlaceholders:
    @pytest.mark.parametrize("text, found", [
        ("Total: [insert the total here].", ["[insert the total here]"]),
        ("[Add a summary] and [fill in the date]",
         ["[Add a summary]", "[fill in the date]"]),
        ("[Add to calendar](https://example.com)", []),
        ("- [x] done, [see above], [1]", []),
        ("[insert\nacross lines]", []),
        # Typed in the page's editor, saved with its brackets escaped.
        ('Hi \\[fill in "hi"\\] there', ['\\[fill in "hi"\\]']),
    ])
    def test_what_counts(self, text, found):
        assert livedocs.placeholders(text) == found


class TestLive:
    def test_an_edit_reaches_the_page_as_it_lands(self, shown,
                                                  monkeypatch):
        path, info = shown
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
        session = web.Session()
        drain = events_of(session)

        tools.edit_tool(str(path), "[insert the Q3 revenue here]",
                        "$4.2M, up 12%")

        changed = drain("doc-changed")
        assert [e["id"] for e in changed] == [info["id"]]
        assert "$4.2M, up 12%" in changed[0]["text"]
        kept = workspace.kept_file(info["id"])[0]
        assert kept.read_text(encoding="utf-8") == changed[0]["text"]

    def test_every_copy_shown_is_kept_up(self, shown, monkeypatch):
        path, first = shown
        second = workspace.keep_file(str(path))
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
        session = web.Session()
        drain = events_of(session)

        tools.write_tool(str(path), "All new.\n")

        assert {e["id"] for e in drain("doc-changed")} == {
            first["id"], second["id"],
        }

    def test_a_file_not_shown_says_nothing(self, tmp_path, monkeypatch):
        other = tmp_path / "notes.md"
        other.write_text("hi\n", encoding="utf-8")
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
        session = web.Session()
        drain = events_of(session)

        tools.edit_tool(str(other), "hi", "hello")
        assert drain("doc-changed") == []


class TestAskingFirst:
    def run_asked(self, session, chat, call):
        """CALL, on a thread of its own, asking the page as a turn does."""

        result = {}

        def turn():
            with theme.answer_from(session.answerer(chat)):
                result["said"] = call()

        worker = threading.Thread(target=turn)
        worker.start()
        return worker, result

    def test_the_change_is_shown_to_allow_in_place(self, shown):
        path, info = shown
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        worker, result = self.run_asked(session, chat, lambda: (
            tools.edit_tool(str(path), "[insert the Q3 revenue here]",
                            "$4.2M")
        ))
        wait_for(lambda: session.asks)
        proposed = drain("doc-proposal")
        ask_id = next(iter(session.asks))
        assert proposed[0]["ask"] == ask_id
        assert proposed[0]["ids"] == [info["id"]]
        assert "Revenue: $4.2M" in proposed[0]["text"]
        # Nothing written yet.
        assert "[insert" in path.read_text(encoding="utf-8")

        session.answer(ask_id, "y")
        worker.join(5)
        assert "Made 1 replacement" in result["said"]
        assert "Revenue: $4.2M" in path.read_text(encoding="utf-8")
        assert drain("doc-changed")

    def test_denied_leaves_it(self, shown):
        path, _ = shown
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        worker, result = self.run_asked(session, chat, lambda: (
            tools.edit_tool(str(path), "[insert the Q3 revenue here]", "x")
        ))
        wait_for(lambda: session.asks)
        session.answer(next(iter(session.asks)), "n")
        worker.join(5)

        assert result["said"] == "Edit blocked by user"
        assert "[insert" in path.read_text(encoding="utf-8")
        assert drain("doc-changed") == []

    def test_the_write_tool_asks_the_page_too(self, shown):
        path, info = shown
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        worker, result = self.run_asked(
            session, chat, lambda: tools.write_tool(str(path), "Gone.\n"),
        )
        wait_for(lambda: session.asks)
        ask_id = next(iter(session.asks))
        assert drain("doc-proposal")[0]["ids"] == [info["id"]]
        session.answer(ask_id, "n")
        worker.join(5)

        assert result["said"] == "Write blocked by user"
        assert path.read_text(encoding="utf-8").startswith("# Q3")

    def test_a_question_about_something_else_carries_none(self, shown):
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        def question():
            return theme.remote_answer("Run this command?\nls")

        worker, _ = self.run_asked(session, chat, question)
        wait_for(lambda: session.asks)
        session.answer(next(iter(session.asks)), "n")
        worker.join(5)
        assert drain("doc-proposal") == []


# --- Saving wakes Flash -------------------------------------------------


def part(content="", calls=None, done=False):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, thinking="",
                                tool_calls=calls),
        done=done, eval_count=1, eval_duration=1,
    )


def call(name, **arguments):
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments)
    )


class FakeClient:
    scripts: list = []
    requests: list = []

    def __init__(self, host=None, **_):
        pass

    def chat(self, **kwargs):
        FakeClient.requests.append(
            {**kwargs, "messages": list(kwargs["messages"])}
        )
        script = FakeClient.scripts.pop(0)
        return iter(script() if callable(script) else script)


class TestSavingWakesFlash:
    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        FakeClient.scripts = []
        FakeClient.requests = []
        monkeypatch.setattr(ollama, "Client", FakeClient)
        monkeypatch.setattr(ai.Config, "model", "flash-test")
        monkeypatch.setattr(ai, "_session_system_prompt",
                            lambda heard=False: "")
        monkeypatch.setattr(ai, "_history_budget", lambda: 100_000)
        monkeypatch.setattr(web.checkpoint, "start_turn", lambda label: None)
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)

    def save(self, session, chat, info, text, **more):
        return web.command(session, {
            "name": "document-save", "arg": info["id"], "chat": chat.id,
            "text": text, **more,
        })

    def settled(self, chat):
        wait_for(lambda: not chat.busy and not chat.queued)

    def test_a_save_wakes_flash_to_do_what_was_left(self, shown):
        path, info = shown
        saved = path.read_text(encoding="utf-8") + (
            "\nFlash, add a line on what drove the growth.\n"
        )
        FakeClient.scripts = [
            [part(calls=[call(
                "edit", path=str(path),
                old_string="Flash, add a line on what drove the growth.",
                new_string="Growth came from the new billing page.",
            )]), part(done=True)],
            [part("Added the line on growth."), part(done=True)],
        ]
        session = web.Session()
        drain = events_of(session)
        chat = session.new_chat()

        self.save(session, chat, info, saved)
        self.settled(chat)

        asked = FakeClient.requests[0]["messages"][-1]["content"]
        assert f"saved {path.resolve()}" in asked
        assert "+Flash, add a line on what drove the growth." in asked
        assert "[insert the Q3 revenue here]" in asked  # a gap still in it
        assert "Growth came from the new billing page." in path.read_text(
            encoding="utf-8")
        notes = [e["text"] for e in drain("note")]
        assert notes == ["You saved report.md."]
        assert not [e for e in chat.log if e["type"] == "user"]
        said = [e["text"] for e in chat.log if e["type"] == "assistant"]
        assert said[-1] == "Added the line on growth."

    def test_nothing_to_do_shows_nothing(self, shown):
        path, info = shown
        FakeClient.scripts = [[part("NOTH"), part("ING"), part(done=True)]]
        session = web.Session()
        queue = session.hub.subscribe()
        chat = session.new_chat()

        self.save(session, chat, info, "# Q3, fixed a typo\n")
        self.settled(chat)

        seen = []
        while not queue.empty():
            seen.append(queue.get_nowait())
        kinds = [e["type"] for e in seen]
        assert "token" not in kinds and "assistant" not in kinds
        assert "stats" not in kinds
        # Kept for the next message as a file that changed, not as a turn.
        assert chat.messages == []
        assert session.edited[chat.id] == [str(path.resolve())]

    def test_saves_during_a_turn_are_read_once_it_ends(self, shown):
        path, info = shown
        gate = threading.Event()

        def held():
            gate.wait(5)
            yield from [part("Working."), part(done=True)]

        FakeClient.scripts = [held, [part("NOTHING"), part(done=True)]]
        session = web.Session()
        chat = session.new_chat()
        session.send(chat, "hello")
        wait_for(lambda: FakeClient.requests)

        self.save(session, chat, info, "first\n")
        self.save(session, chat, info, "second\n")
        assert len(FakeClient.requests) == 1
        gate.set()
        wait_for(lambda: len(FakeClient.requests) == 2)
        self.settled(chat)

        # One turn for both, from before the first to after the second.
        asked = FakeClient.requests[1]["messages"][-1]["content"]
        assert "-# Q3" in asked and "+second" in asked
        assert "+first" not in asked

    def test_comments_save_without_waking(self, shown):
        path, info = shown
        session = web.Session()
        chat = session.new_chat()

        self.save(session, chat, info, "quiet\n", quiet=True)

        time.sleep(0.1)
        assert FakeClient.requests == [] and not chat.busy
        assert session.edited[chat.id] == [str(path.resolve())]
