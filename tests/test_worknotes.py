# pylint: disable=C0114,C0115,C0116

import pytest

from flash import ai, context, tools, worknotes
from tests.test_ai_context import FakeClient, user


@pytest.fixture(autouse=True)
def _fresh():
    worknotes.clear()
    yield
    worknotes.clear()


def test_lines_are_added_and_kept_in_order():
    tools.notes_tool(add="the bug is in render.py\n- tests run with make test")
    tools.notes_tool(add="decided: keep the old API")
    assert worknotes.lines() == ["the bug is in render.py",
                                 "tests run with make test",
                                 "decided: keep the old API"]


def test_a_line_already_there_is_not_added_twice():
    tools.notes_tool(add="one")
    tools.notes_tool(add="one\ntwo")
    assert worknotes.lines() == ["one", "two"]


def test_rewrite_replaces_them_and_drop_takes_lines_out():
    tools.notes_tool(add="a\nb\nc")
    tools.notes_tool(rewrite="done: a\nleft: b")
    assert worknotes.lines() == ["done: a", "left: b"]
    tools.notes_tool(drop="LEFT")
    assert worknotes.lines() == ["done: a"]
    tools.notes_tool(rewrite="")
    assert worknotes.lines() == []


def test_called_with_nothing_it_reads_them_back():
    assert tools.notes_tool() == "No notes yet."
    tools.notes_tool(add="x")
    assert tools.notes_tool() == "- x"


def test_the_oldest_lines_go_past_the_limit(monkeypatch):
    monkeypatch.setattr(worknotes, "MAX_CHARS", 20)
    tools.notes_tool(add="first line here\nsecond line")
    assert worknotes.lines() == ["second line"]


def test_each_chat_has_its_own_and_a_kept_chat_survives_a_restart():
    with worknotes.in_chat("chat1"):
        tools.notes_tool(add="for chat one")
    with worknotes.in_chat("chat2"):
        tools.notes_tool(add="for chat two")
        assert worknotes.lines() == ["for chat two"]
    # A new process reads them from disk.
    worknotes._held.clear()
    assert worknotes.lines("chat1") == ["for chat one"]
    worknotes.clear("chat1")
    worknotes._held.clear()
    assert worknotes.lines("chat1") == []
    worknotes.clear("chat2")


def test_every_compaction_carries_the_notes_word_for_word(monkeypatch):
    monkeypatch.setattr(ai.Config, "model", "test-model")
    tools.notes_tool(add="port is 8123, not 8080")
    messages = [user("current")]
    ai._compact(ai.console, FakeClient("first summary"), messages,
                [user("old")])
    assert "port is 8123, not 8080" in messages[0]["content"]
    # The next compaction drops the first summary, but not the notes.
    dropped = [messages.pop(0), user("older")]
    ai._compact(ai.console, FakeClient("second summary"), messages, dropped)
    head = messages[0]["content"]
    assert context.is_summary(messages[0])
    assert "first summary" not in head and "second summary" in head
    assert head.count("port is 8123, not 8080") == 1
