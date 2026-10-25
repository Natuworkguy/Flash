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
