# pylint: disable=C0114,C0115,C0116

import random
from types import SimpleNamespace

import pytest

from flash import ai
from flash.dashes import DashGuard, undash

EN, EM, BAR = "\u2013", "\u2014", "\u2015"


@pytest.mark.parametrize("text, expected", [
    (f"It works {EM} mostly.", "It works, mostly."),
    (f"and then {EM} well {EM} it broke", "and then, well, it broke"),
    (f"word{EM}word", "word, word"),
    (f"then {EN} as some write it {EN} on", "then, as some write it, on"),
    (f"pages 10{EN}20, 9:00 {EN} 17:00", "pages 10-20, 9:00 - 17:00"),
    (f"Jan{EN}Mar", "Jan-Mar"),
    (f"{EM} first\n  {EN} second", "- first\n  - second"),
    (f"Wait,{EM} no", "Wait, no"),
    (f"Done {EM}.", "Done."),
    (f"trailing off {EM}\nnext", "trailing off\nnext"),
    (f"({EM} an aside)", "(an aside)"),
    (f"**Speed** {EM} it is fast", "**Speed**, it is fast"),
    (f"two {EM}{EM} of them", "two, of them"),
    (f"a bar {BAR} like this", "a bar, like this"),
    (f"{BAR} a quote", "- a quote"),
    (f"1{BAR}5 and see{BAR}saw", "1-5 and see, saw"),
    ("nothing to do here - really", "nothing to do here - really"),
    ("", ""),
])
def test_prose(text, expected):
    assert undash(text) == expected
    assert not any(d in undash(text) for d in (EN, EM, BAR))


@pytest.mark.parametrize("text", [
    f"run `a {EM} b` first",
    f"```\nx = '{EM}'\n```",
    f"~~~text\n9 {EN} 5\n~~~",
    f"```py\nstill open {EM} here",
])
def test_code_keeps_its_dashes(text):
    assert undash(text) == text


def test_prose_around_code_is_still_fixed():
    text = f"Use `a {EM} b` {EM} then\n```\nq = '{EN}'\n```\nafter {EM} that"

    assert undash(text) == (
        f"Use `a {EM} b`, then\n```\nq = '{EN}'\n```\nafter, that"
    )


REPLIES = [
    f"It works {EM} mostly. Pages 10{EN}20.\n\n{EM} one\n{EM} two",
    f"Run `a {EM} b` {EM} then\n```sh\necho '{EN}'\n```\nDone {EM}.",
    f"Jan{EN}Mar, 9:00 {EN} 17:00, and ({EM} aside) {EM}",
    "no dashes at all, just `code` and text",
]


@pytest.mark.parametrize("reply", REPLIES)
def test_a_stream_shows_what_the_whole_reply_becomes(reply):
    chance = random.Random(7)  # nosec B311 -- test data, not secrets
    for _ in range(50):
        guard = DashGuard()
        shown, at = "", 0
        while at < len(reply):
            size = chance.randint(1, 5)
            shown += guard.feed(reply[at:at + size])
            at += size
        shown += guard.flush()

        assert shown == undash(reply) == guard.text()


def test_a_stream_holds_a_dash_until_it_knows_what_follows():
    guard = DashGuard()

    assert guard.feed("works ") == "works"
    assert guard.feed(EM) == ""
    assert guard.feed(" mostly") == ", mostly"


def test_the_terminal_reply_comes_without_dashes():
    response = SimpleNamespace(message=SimpleNamespace(
        content=f"Fixed {EM} the test passes.", thinking="", tool_calls=[],
    ))

    text, _, _ = ai._response_parts(response)

    assert text == "Fixed, the test passes."
