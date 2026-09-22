"""Tests for how a turn's tool traffic and compaction reach the history."""

import pytest

from flash import ai, context
from flash.ai import Config, _compact, _fit_and_compact, _worth_keeping


def user(text):
    return {"role": "user", "content": text}


def assistant(text, calls=None):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"function": {"name": name, "arguments": {}}} for name in calls
        ]
    return message


def tool(name, text):
    return {"role": "tool", "content": text, "tool_name": name}


class FakeClient:
    """Stands in for ollama.Client, answering with a fixed reply."""

    def __init__(self, reply="a summary of earlier work"):
        self.reply = reply
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Response",
            (),
            {
                "message": type(
                    "Message",
                    (),
                    {
                        "content": self.reply,
                        "thinking": "",
                        "tool_calls": [],
                    },
                )()
            },
        )()


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setattr(ai, "_history_budget", lambda: 200)


class TestWorthKeeping:
    def test_tool_traffic_survives_the_turn(self):
        messages = [
            user("earlier"),
            assistant("", ["read"]),
            tool("read", "the file said this"),
        ]

        kept = _worth_keeping(messages, 1)

        assert len(kept) == 2
        assert kept[1]["content"] == "the file said this"

    def test_the_mid_loop_system_nudge_is_dropped(self):
        messages = [
            assistant("", ["read"]),
            tool("read", "output"),
            {"role": "system", "content": "wrap it up"},
        ]

        kept = _worth_keeping(messages, 0)

        assert not any(m.get("role") == "system" for m in kept)

    def test_an_image_a_tool_opened_does_not_persist(self):
        messages = [
            assistant("", ["view_image"]),
            tool("view_image", "Opened cat.png"),
            {"role": "user", "content": "look", "images": [b"bytes"]},
        ]

        kept = _worth_keeping(messages, 0)

        assert not any(m.get("images") for m in kept)
        # The tool result still says an image was opened, so the
        # exchange reads correctly without the bytes.
        assert kept[-1]["content"] == "Opened cat.png"

    def test_a_kept_tool_result_still_follows_its_call(self):
        messages = [
            assistant("", ["view_image"]),
            tool("view_image", "Opened cat.png"),
            {"role": "user", "content": "look", "images": [b"bytes"]},
        ]

        kept = _worth_keeping(messages, 0)

        assert kept[0].get("tool_calls")
        assert kept[1]["role"] == "tool"

    def test_nothing_before_the_start_index_is_taken(self):
        messages = [user("old"), assistant("new")]

        assert _worth_keeping(messages, 1) == [assistant("new")]


class TestCompaction:
    def test_a_summary_replaces_what_was_dropped(self):
        client = FakeClient("we renamed the parser")
        messages = [user("current question")]
        dropped = [user("old question"), assistant("old answer")]

        assert _compact(ai.console, client, messages, dropped)
        assert context.is_summary(messages[0])
        assert "we renamed the parser" in messages[0]["content"]
        assert messages[1] == user("current question")

    def test_nothing_dropped_means_no_model_call(self):
        client = FakeClient()

        assert not _compact(ai.console, client, [], [])
        assert client.calls == []

    def test_a_failed_summary_leaves_the_history_alone(self):
        client = FakeClient("")
        messages = [user("current")]

        assert not _compact(ai.console, client, messages, [user("old")])
        assert messages == [user("current")]

    def test_an_earlier_summary_is_not_summarized_twice(self):
        client = FakeClient("second summary")
        messages = [user("current")]
        dropped = [context.summary_message("first summary"), user("old")]

        _compact(ai.console, client, messages, dropped)

        sent = client.calls[0]["messages"][1]["content"]
        assert "first summary" not in sent
        assert "old" in sent


class TestFitAndCompact:
    def test_an_overflowing_history_is_summarized(self, budget, monkeypatch):
        monkeypatch.setattr(Config, "auto_compact", True)
        client = FakeClient("the gist of it")
        messages = [
            user(f"question {i} " + "x" * 400) for i in range(10)
        ]

        _fit_and_compact(ai.console, client, messages)

        assert any(context.is_summary(m) for m in messages)
        assert context.total_tokens(messages) <= 200

    def test_auto_compact_off_just_drops(self, budget, monkeypatch):
        monkeypatch.setattr(Config, "auto_compact", False)
        client = FakeClient()
        messages = [
            user(f"question {i} " + "x" * 400) for i in range(10)
        ]

        _fit_and_compact(ai.console, client, messages)

        assert client.calls == []
        assert not any(context.is_summary(m) for m in messages)

    def test_a_history_inside_the_budget_is_untouched(
        self, budget, monkeypatch
    ):
        monkeypatch.setattr(Config, "auto_compact", True)
        client = FakeClient()
        messages = [user("short"), assistant("also short")]

        _fit_and_compact(ai.console, client, messages)

        assert client.calls == []
        assert messages == [user("short"), assistant("also short")]
