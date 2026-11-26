# pylint: disable=C0114,C0115,C0116
import pytest

from flash import presets

# --- Which presets a message gets ------------------------------------------


def test_a_yes_or_no_question_gets_yes_no_and_not_now():
    assert presets.for_message("Should I push it to dev?") \
        == ["Yes", "No", "Not now"]


@pytest.mark.parametrize("text", [
    "Pushed it to dev.", "Tea or coffee?", "Which branch should it go on?",
    "",
])
def test_anything_else_gets_none(text):
    assert presets.for_message(text) == []


def test_its_own_replies_come_first_kept_short_and_once():
    own = ["Sounds right", "  Something's\noff ", "sounds right", 7,
           "x" * 80, "Later", "One too many"]

    got = presets.for_message("Should I go on?", own)

    assert got == ["Sounds right", "Something's off",
                   "x" * presets.PRESET_CHARS]
    assert presets.clean("Yes") == [] and presets.clean(None) == []
