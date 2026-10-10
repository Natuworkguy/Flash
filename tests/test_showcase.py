# pylint: disable=C0114,C0116

import io
import time

import pytest
from rich.console import Console

from flash import ai, showcase, web


@pytest.mark.parametrize("host, model, private", [
    ("http://localhost:11434", "alpha3.1", True),
    ("127.0.0.1:11434", "alpha3.1", True),
    ("http://192.168.1.20:11434", "gamma4", True),
    ("http://10.0.0.5:11434", "gamma4", True),
    ("http://studio.local:11434", "gamma4", True),
    ("", "alpha3.1", True),
    ("https://ollama.example.com", "alpha3.1", False),
    ("http://8.8.8.8:11434", "alpha3.1", False),
    # Ollama's cloud models run on Ollama's servers, whatever the host.
    ("http://localhost:11434", "big:120b-cloud", False),
    ("http://localhost:11434", "kimi-k2:cloud", False),
])
def test_private_means_it_stayed_on_your_machines(host, model, private):
    assert showcase.is_private(host, model) is private


def test_replies_add_up():
    showcase.record("localhost", "alpha3.1", 1000, tools=2)
    showcase.record("https://ollama.example.com", "alpha3.1", 500)

    found = showcase.totals()
    assert found["turns"] == 2 and found["tokens"] == 1500
    assert found["tools"] == 2
    assert found["private_turns"] == 1 and found["private_tokens"] == 1000
    assert found["private_share"] == 50
    # Only what stayed private counts towards what it would have cost.
    assert found["saved"] == round(1000 / 1e6 * showcase.PRICE_PER_MILLION, 2)
    assert found["since"] > 0


def test_records_say_nothing():
    assert showcase.record("localhost", "m", 10) is None


def test_each_tip_is_up_for_its_time_then_the_next():
    start = showcase.TIP_SECONDS * len(showcase.TIPS) * 7

    assert showcase.tip_now(start) == showcase.tip_now(start + 29.9)
    shown = [showcase.tip_now(start + n * showcase.TIP_SECONDS)
             for n in range(len(showcase.TIPS))]
    # Every one in turn before any comes round again.
    assert len(set(shown)) == len(showcase.TIPS)


def test_tips_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SHOW_TIPS", "0")

    assert showcase.tip_now() is None
    assert showcase.web_tips() == []


def test_the_spinner_shows_a_tip_while_it_waits(monkeypatch):
    frames = []

    class Live:
        def update(self, frame):
            frames.append(frame)

    def chat(*_):
        time.sleep(0.3)
        return None, None

    monkeypatch.setattr(ai, "_chat_with_retries", chat)
    ai._try_chat(None, [], Live())

    out = Console(width=200, record=True, file=io.StringIO())
    out.print(frames[-1])
    shown = out.export_text()
    assert "Tip: " in shown
    assert any(what in shown for what, _, _ in showcase.TIPS)


def test_the_spinner_keeps_quiet_without_tips(monkeypatch):
    monkeypatch.setenv("SHOW_TIPS", "0")
    frames = []

    class Live:
        def update(self, frame):
            frames.append(frame)

    def chat(*_):
        time.sleep(0.3)
        return None, None

    monkeypatch.setattr(ai, "_chat_with_retries", chat)
    ai._try_chat(None, [], Live())

    out = Console(width=200, record=True, file=io.StringIO())
    out.print(frames[-1])
    assert "Tip" not in out.export_text()


def test_web_tips_carry_a_prompt_or_none():
    for what, prompt in showcase.web_tips():
        assert what and not prompt.startswith("/")


def test_the_terminal_says_it(monkeypatch):
    printed = []
    monkeypatch.setattr(ai.console, "print",
                        lambda *a, **k: printed.append(str(a[0]) if a else ""))

    ai._stats_command()
    assert "No replies counted yet" in printed[-1]

    showcase.record("localhost", "m", 2_000_000)
    ai._stats_command()
    shown = printed[-1]
    assert "replies:" in shown and "100%" in shown
    assert "about $10.00" in shown and "per million tokens" in shown
    assert "Tell a friend" not in shown


def test_the_page_gets_the_numbers_and_the_tips(monkeypatch):
    monkeypatch.setattr(ai.Config, "host", "http://localhost:11434")
    monkeypatch.setattr(ai.Config, "model", "alpha3.1")

    status = web.status(ai)
    assert status["tips"] == showcase.web_tips()

    found = web.command(web.Session(), {"name": "showcase"})
    assert found["turns"] == 0 and found["private_share"] == 100
