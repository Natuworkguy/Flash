# pylint: disable=C0114,C0115,C0116

import pytest

from flash import ai, learning, web


class Started:
    def __init__(self):
        self.runs = []

    def __call__(self, target, args, daemon):
        self.runs.append(args)
        return self

    def start(self):
        pass


def test_a_rating_starts_a_review_of_that_reply():
    started = Started()
    review = learning.rated(
        [{"role": "user", "content": "hi"}], "Hello there", False,
        "too long", host="h", model="m", start=started)

    [(kept, _messages, host, model)] = started.runs
    assert kept is review and (host, model) == ("h", "m")  # nosec B101
    task = learning.review_messages(review, _messages)[-1]["content"]
    assert "a bad one" in task and "> Hello there" in task  # nosec B101
    assert "too long" in task  # nosec B101
    assert learning.rated([], "x", True, host="h", model="",  # nosec B101
                          start=started) is None


def test_the_page_rates_a_reply_and_takes_it_back(monkeypatch):
    rated = []
    monkeypatch.setattr(learning, "rated",
                        lambda *a, **kw: rated.append((a, kw)))
    monkeypatch.setattr(ai.Config, "model", "m")
    session = web.Session()
    chat = session.new_chat()
    chat.log += [{"type": "user", "text": "hi"},
                 {"type": "assistant", "text": "Hello"}]

    said = web.command(session, {"name": "rate", "chat": chat.id,
                                 "arg": "bad", "at": 1, "why": "rude"})
    assert said["rating"] == "bad" and chat.log[1]["rating"] == "bad"  # nosec
    (args, kw), = rated
    assert args[1:] == ("Hello", False, "rude")  # nosec B101

    said = web.command(session, {"name": "rate", "chat": chat.id,
                                 "arg": "clear", "at": 1})
    assert said["rating"] == "" and "rating" not in chat.log[1]  # nosec
    with pytest.raises(ValueError):
        web.command(session, {"name": "rate", "chat": chat.id,
                              "arg": "good", "at": 0})


def test_the_terminal_rates_the_last_reply(monkeypatch):
    rated = []
    monkeypatch.setattr(learning, "rated",
                        lambda *a, **kw: rated.append(a) or True)
    ai._rate_command("/good short and right",
                     [{"role": "assistant", "content": "Done."}])
    assert rated == [([{"role": "assistant", "content": "Done."}],  # nosec
                      "Done.", True, "short and right")]


# --- A thumbs-up that answers a question ---------------------------------


@pytest.mark.parametrize("text,asks", [
    ("I drafted the fix. Should I open a PR to dev?", True),
    ("Done.\n\n**Want me to push it?**", True),
    ("Do you want the short version or the long one?", False),
    ("Which file should I start with?", False),
    ("Should I push it? Tests pass.", False),
    ("", False),
])
def test_a_yes_or_no_question_is_told_from_others(text, asks):
    assert web.asks_yes_or_no(text) is asks  # nosec B101


def _chat_with(session, reply):
    chat = session.new_chat()
    chat.log += [{"type": "user", "text": "fix it"},
                 {"type": "assistant", "text": reply}]
    return chat


def test_a_thumbs_up_on_the_last_question_is_a_yes(monkeypatch):
    rated = []
    monkeypatch.setattr(learning, "rated",
                        lambda *a, **kw: rated.append(a))
    session = web.Session()
    chat = _chat_with(session, "It's fixed. Should I push it?")

    said = web.command(session, {"name": "rate", "chat": chat.id,
                                 "arg": "good", "at": 1})

    assert said == {"rating": "", "answer": "Yes"}  # nosec B101
    assert rated == [] and "rating" not in chat.log[1]  # nosec B101


def test_a_thumbs_up_is_a_rating_once_the_user_has_moved_on(monkeypatch):
    rated = []
    monkeypatch.setattr(learning, "rated",
                        lambda *a, **kw: rated.append(a))
    monkeypatch.setattr(ai.Config, "model", "m")
    session = web.Session()
    chat = _chat_with(session, "It's fixed. Should I push it?")
    chat.log.append({"type": "user", "text": "not yet"})

    said = web.command(session, {"name": "rate", "chat": chat.id,
                                 "arg": "good", "at": 1})

    assert said == {"rating": "good"} and len(rated) == 1  # nosec B101
    # A reply that asks nothing is rated as before.
    chat = _chat_with(session, "It's fixed and pushed.")
    assert web.command(session, {"name": "rate", "chat": chat.id,
                                 "arg": "good", "at": 1}) == {  # nosec
        "rating": "good"}
