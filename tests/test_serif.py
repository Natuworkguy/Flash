# pylint: disable=C0114,C0116

from flash import serif, voice

REPLY = """Done.

```serif
The point of autonomous mode is that you **stop waiting**.

Nothing else changed.
```

The word <serif>veto</serif> stays."""


def test_terminal_shows_the_block_as_a_quote():
    shown = serif.for_terminal(REPLY)

    assert "```" not in shown  # nosec B101
    assert "> The point of autonomous mode is that you **stop waiting**." \
        in shown  # nosec B101
    assert ">\n> Nothing else changed." in shown  # nosec B101
    assert "The word *veto* stays." in shown  # nosec B101


def test_plain_keeps_the_words():
    text = serif.plain(REPLY)

    assert "```" not in text and "<serif>" not in text  # nosec B101
    assert "Nothing else changed." in text  # nosec B101
    assert "The word veto stays." in text  # nosec B101


def test_a_block_still_streaming_in():
    shown = serif.for_terminal("```serif\nHalf a thought")

    assert shown == "> Half a thought"  # nosec B101


def test_code_blocks_are_left_alone():
    code = "```python\nprint('hi')\n```"

    assert serif.for_terminal(code) == code  # nosec B101
    assert serif.plain(code) == code  # nosec B101


def test_speech_reads_the_passage():
    spoken = voice.for_speech(REPLY, limit=1000)

    assert "stop waiting" in spoken  # nosec B101
    assert "Nothing else changed." in spoken  # nosec B101
    assert "<serif>" not in spoken and "veto" in spoken  # nosec B101
