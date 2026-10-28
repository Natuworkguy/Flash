# pylint: disable=C0114,C0115,C0116

from types import SimpleNamespace

from flash import debate


class Client:
    """Says, for each call, who it was asked as, and remembers it."""

    def __init__(self):
        self.asked = []

    def chat(self, model, messages, options=None, stream=True):
        self.asked.append((model, messages))
        system = messages[0]["content"]
        said = "summary" if "watched a debate" in system else \
            f"{model} turn {len(self.asked)}"
        return [SimpleNamespace(message=SimpleNamespace(content=said))]


def test_the_question_and_two_models_are_read():
    assert debate.parse("/debate tabs or spaces?") == (  # nosec B101
        "tabs or spaces?", [])
    assert debate.parse("/debate alpha:8b vs beta:8b: tabs?") == (  # nosec
        "tabs?", ["alpha:8b", "beta:8b"])


def test_two_sides_take_turns_then_a_summary():
    client = Client()
    heard = []
    done = debate.run("tabs or spaces?", client, ["alpha", "beta"], "judge",
                      history=[{"role": "user", "content": "hi"}],
                      say=heard.append)

    assert [m for m, _ in client.asked] == [  # nosec B101
        "alpha", "beta", "alpha", "beta", "judge"]
    assert [t["side"] for t in done["turns"]] == [0, 1, 0, 1]  # nosec B101
    assert done["sides"] == ["For (alpha)", "Against (beta)"]  # nosec B101
    assert done["summary"] == "summary"  # nosec B101
    # The second side hears the first, and the chat came along.
    second = client.asked[1][1]
    assert "alpha turn 1" in second[-1]["content"]  # nosec B101
    assert second[1] == {"role": "user", "content": "hi"}  # nosec B101
    assert {"turn": 0, "side": 0, "text": "alpha turn 1"} in heard  # nosec
    note = debate.note(done)
    assert note[1]["content"].endswith("summary")  # nosec B101


def test_one_model_argues_both_sides_and_a_stop_ends_it():
    client = Client()
    done = debate.run("q", client, [], "m", stopped=lambda: True)
    assert done["turns"] == [] and done["summary"] == ""  # nosec B101
    done = debate.run("q", client, [], "m", rounds=1)
    assert done["sides"] == ["For", "Against"]  # nosec B101
    assert len(done["turns"]) == 2  # nosec B101


def test_the_web_argues_it_out_in_the_chat(monkeypatch):
    import time

    import pytest

    from flash import ai, web

    monkeypatch.setattr(ai, "_client", Client)
    monkeypatch.setattr(ai.Config, "model", "m")
    session = web.Session()
    chat = session.new_chat()

    session.debate(chat, "/debate tabs or spaces?")
    for _ in range(100):
        if any(e["type"] == "debate" for e in chat.log):
            break
        time.sleep(0.02)
    [kept] = [e for e in chat.log if e["type"] == "debate"]
    assert kept["question"] == "tabs or spaces?"  # nosec B101
    assert len(kept["turns"]) == 4  # nosec B101
    assert chat.messages[-1]["content"].endswith("summary")  # nosec B101

    with pytest.raises(ValueError, match="after /debate"):
        session.debate(chat, "/debate")


def test_steering_and_going_on_from_where_it_got_to():
    client = Client()
    first = debate.run("q", client, [], "m", rounds=1)
    asked = iter([["think about cost"], []])
    more = debate.run("q", client, [], "m", rounds=1, turns=first["turns"],
                      steers=lambda: next(asked, []))

    assert [t["side"] for t in more["turns"]] == [0, 1, -1, 0, 1]  # nosec
    assert more["turns"][2]["text"] == "think about cost"  # nosec B101
    # The side after the steer hears it, and is told to take it in.
    model, messages = client.asked[-3]
    assert "The user, steering the debate:\nthink about cost" in (  # nosec
        messages[-1]["content"])
    assert "just steered" in messages[0]["content"]  # nosec B101
    note = debate.note(more)[1]["content"]
    assert note.endswith("summary")  # nosec B101


def test_a_stop_mid_turn_keeps_what_was_said():
    calls = []

    class Slow(Client):
        def chat(self, model, messages, options=None, stream=True):
            calls.append(model)
            return [SimpleNamespace(message=SimpleNamespace(content=w))
                    for w in ("one ", "two ", "three")]

    seen = []
    stop = []

    def say(event):
        seen.append(event)
        if event.get("text"):
            stop.append(True)

    done = debate.run("q", Slow(), [], "m", say=say,
                      stopped=lambda: bool(stop))
    assert done["stopped"] and done["summary"] == ""  # nosec B101
    [turn] = done["turns"]
    assert turn["side"] == 0 and "three" not in turn["text"]  # nosec B101
    assert "stopped by the user" in debate.note(done)[1]["content"]  # nosec


def test_the_web_steers_stops_and_keeps_debating(monkeypatch):
    import threading
    import time

    import pytest

    from flash import ai, web

    gate = threading.Event()

    class Waits(Client):
        def chat(self, model, messages, options=None, stream=True):
            gate.wait(2)
            return super().chat(model, messages, options, stream)

    monkeypatch.setattr(ai, "_client", Waits)
    monkeypatch.setattr(ai.Config, "model", "m")
    session = web.Session()
    chat = session.new_chat()

    first = session.debate(chat, "/debate tabs?")
    with pytest.raises(ValueError, match="already"):
        session.debate(chat, "/debate again?")
    session.steer_debate(first, "be brief")
    gate.set()
    for _ in range(100):
        if first not in session.debating:
            break
        time.sleep(0.02)
    [kept] = [e for e in chat.log if e["type"] == "debate"]
    assert {"side": -1, "text": "be brief"} in kept["turns"]  # nosec B101

    again = session.debate(chat, continues=first, steer="now cost")
    for _ in range(100):
        if again not in session.debating:
            break
        time.sleep(0.02)
    latest = [e for e in chat.log if e["type"] == "debate"][-1]
    assert latest["continues"] == first  # nosec B101
    assert len(latest["turns"]) > len(kept["turns"])  # nosec B101
    with pytest.raises(ValueError, match="over"):
        session.steer_debate(again, "x")
    assert session.stop_debate(chat=chat.id) == 0  # nosec B101


def test_the_agent_starts_a_debate_and_gets_the_summary(monkeypatch):
    from flash import providers, tools

    client = Client()
    monkeypatch.setattr(providers, "client", lambda host: client)
    monkeypatch.setattr(tools, "MODEL_NAME", "m")
    said = tools.run_tool(("debate", {"question": "monorepo or not?",
                                      "context": "Two teams, one deploy.",
                                      "rounds": 1}))
    assert said.endswith("Its summary:\n\nsummary")  # nosec B101
    # The sides were told what the agent knew.
    assert {"role": "user", "content": "Two teams, one deploy."} in (  # nosec
        client.asked[0][1])
    assert "debate" in tools.SPARK_TOOL_NAMES  # nosec B101
    assert tools.run_tool(("debate", {"question": " "})).startswith(  # nosec
        "Error")


def test_the_page_shows_steers_and_stops_the_agents_debate(monkeypatch):
    from flash import providers, theme, tools, web

    client = Client()
    monkeypatch.setattr(providers, "client", lambda host: client)
    monkeypatch.setattr(tools, "MODEL_NAME", "m")
    session = web.Session()
    chat = session.new_chat()
    sink = web._sink(session, chat)
    live = []
    session.hub.publish = lambda event: live.append(event) or event
    steered = []

    real = sink

    def watching(kind, text, style):
        # The user steers once the first side has spoken, then stops it.
        if kind == "debate-poll" and len(client.asked) == 1 and \
                not steered:
            debate_id = next(iter(session.debating))
            session.steer_debate(debate_id, "think about cost")
            steered.append(debate_id)
        if kind == "debate-poll" and len(client.asked) == 3:
            session.stop_debate(chat=chat.id)
        return real(kind, text, style)

    with theme.capture_tool_output(watching):
        said = tools.debate_tool("tabs?", rounds=2)

    assert said.startswith("The user stopped the debate")  # nosec B101
    [kept] = [e for e in chat.log if e["type"] == "debate"]
    assert kept["by"] == "flash" and kept["stopped"]  # nosec B101
    assert {"side": -1, "text": "think about cost"} in kept["turns"]  # nosec
    assert not session.debating  # nosec B101
    first = next(e for e in live if e["type"] == "debate-live")
    assert first["by"] == "flash" and first["chat"] == chat.id  # nosec


class TestRoundsAndSuggestions:
    def test_parts_between_semicolons(self):
        found = debate.parse_full(
            "/debate a:8b vs b:8b; rounds 4; Should I rewrite this in C?")
        assert found == {"question": "Should I rewrite this in C?",
                         "models": ["a:8b", "b:8b"], "rounds": 4}

    def test_any_order_and_the_old_way(self):
        assert debate.parse_full("/debate Why; rounds 3; a vs b") == {
            "question": "Why", "models": ["a", "b"], "rounds": 3}
        assert debate.parse_full("/debate a vs b: why; not?") == {
            "question": "why; not?", "models": ["a", "b"], "rounds": 0}
        assert debate.parse("/debate just; asking") == ("just; asking", [])

    def test_rounds_are_held_in_bounds(self):
        assert debate.parse_full("rounds 99; q")["rounds"] == \
            debate.MOST_ROUNDS

    def test_suggestions(self):
        names = ["alpha:8b", "beta:8b"]

        def said(typed):
            return [s[0] for s in debate.suggest(typed, names)]

        assert said("") == ["rounds ", "alpha:8b ", "beta:8b "]
        assert said("alpha:8b ") == ["vs "]
        assert said("alpha:8b vs ") == ["beta:8b"]
        assert said("alpha:8b vs beta:8b; ") == ["rounds "]
        assert said("alpha:8b vs beta:8b; rounds 1")[:2] == ["1; ", "10; "]
        assert said("alpha:8b vs beta:8b; rounds 4; Should I") == []
        assert said("mo") == ["more"]
