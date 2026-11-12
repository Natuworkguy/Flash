# pylint: disable=C0114,C0116

import random
from types import SimpleNamespace

import pytest

from flash import compare


class Client:
    def chat(self, model, messages, options=None, stream=False, **_):
        text = f"{model} says hi"
        return iter([SimpleNamespace(message=SimpleNamespace(content=text))])


def test_parse():
    assert compare.parse("/compare a:8b vs b:8b: why?") == (
        ["a:8b", "b:8b"], "why?")
    with pytest.raises(ValueError):
        compare.parse("/compare just a question")


def test_blind_then_revealed_and_tallied():
    done = compare.run(Client(), ["alpha", "beta"], "hi",
                       rng=random.Random(1))
    assert sorted(done["answers"]) == ["alpha says hi", "beta says hi"]
    assert "alpha" not in str({k: v for k, v in done.items()
                               if k != "answers"})
    first = done["answers"][0].split()[0]
    told = compare.pick(done["id"], "a")
    assert told["a"] == first
    assert compare.tally()[first]["wins"] == 1
    assert compare.standings()[0].startswith(first)
    with pytest.raises(ValueError, match="gone"):
        compare.pick(done["id"], "b")


def test_a_tie_counts_for_both():
    done = compare.run(Client(), ["x", "y"], "q")
    compare.pick(done["id"], "tie")
    assert compare.tally()["x"]["ties"] == compare.tally()["y"]["ties"] == 1


def test_both_answer_in_the_middle_of_the_chat_and_the_winner_carries_on():
    seen = []

    class Client:
        def chat(self, model, messages, **_):
            seen.append(messages)
            return iter([SimpleNamespace(message=SimpleNamespace(content=f"from {model}"))])

    history = [{"role": "user", "content": "we are planning a trip to Rome"},
               {"role": "assistant", "content": "Rome it is."},
               {"role": "tool", "content": "never shown"}]
    done = compare.run(Client(), ["x", "y"], "where should we eat?", history=history,
                       rng=random.Random(0))
    assert all(m[0]["content"] == "we are planning a trip to Rome" and len(m) == 3 for m in seen)
    told = compare.pick(done["id"], "a")
    prompt, answer, model = compare.keep(done["id"], "a")
    assert (prompt, model) == ("where should we eat?", told["a"]) and answer == f"from {told['a']}"
