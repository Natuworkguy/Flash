"""Tests for documents kept up to date as the agents edit them, and the
gaps in them left to fill in."""

import threading
import time

import pytest

from flash import livedocs, theme, tools, web, workspace


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
