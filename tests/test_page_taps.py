# pylint: disable=C0114,C0115,C0116

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

PAGE = Path(__file__).parent.parent / "flash" / "web" / "index.html"
THUMBS = "\U0001f44d"
PARTY = "\U0001f389"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="needs node")


def run(script: str, info: dict | None = None):
    """What SCRIPT prints as JSON, run in node after the page's own code
    for double tap to react, with INFO as the page's facts."""

    page = PAGE.read_text(encoding="utf-8")
    start = page.index("const TC_QUICK = ")
    end = page.index("function tcDoubleTapToReact(")
    code = f"let info = {json.dumps(info or {})};\n" + page[start:end] + script
    done = subprocess.run(  # nosec B603
        [node, "-e", code], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )
    return json.loads(done.stdout)


def taps(*events) -> list:
    """For each lift among EVENTS, whether it ended a double tap. An
    event is (type, ms, x, y), from a finger unless a fifth item says
    which pointer, and the first finger unless a sixth says not."""

    fed = [
        {"type": e[0], "timeStamp": e[1], "clientX": e[2], "clientY": e[3],
         "pointerType": e[4] if len(e) > 4 else "touch",
         "isPrimary": e[5] if len(e) > 5 else True}
        for e in events
    ]
    return run(
        f"const feed = tcTaps();\n"
        f"const said = [];\n"
        f"for (const e of {json.dumps(fed)}) {{\n"
        f"  const r = feed(e);\n"
        f"  if (e.type === 'pointerup') said.push(r);\n"
        f"}}\n"
        f"console.log(JSON.stringify(said));\n"
    )


def tap(at, x=100, y=100, held=60, kind="touch"):
    return [("pointerdown", at, x, y, kind), ("pointerup", at + held, x, y,
                                              kind)]


def test_two_quick_taps_are_a_double_tap():
    assert taps(*tap(0), *tap(180)) == [False, True]  # nosec B101


def test_a_pen_double_taps_too():
    assert taps(*tap(0, kind="pen"), *tap(180, kind="pen")) == [  # nosec B101
        False, True,
    ]


def test_a_mouse_double_click_is_left_to_pick_a_word():
    assert taps(*tap(0, kind="mouse"), *tap(150, kind="mouse")) == [  # nosec
        False, False,
    ]


def test_taps_too_far_apart_in_time_are_two_taps():
    assert taps(*tap(0), *tap(700)) == [False, False]  # nosec B101


def test_taps_on_different_spots_are_two_taps():
    assert taps(*tap(0), *tap(180, x=180)) == [False, False]  # nosec B101


def test_a_long_press_is_no_tap():
    # Holding to select or copy, then a quick tap: not a double tap.
    assert taps(*tap(0, held=600), *tap(700)) == [False, False]  # nosec
    assert taps(*tap(0), *tap(150, held=600)) == [False, False]  # nosec


def test_a_scroll_is_no_tap():
    moved = [("pointerdown", 0, 100, 100), ("pointerup", 80, 100, 160)]

    assert taps(*moved, *tap(150)) == [False, False]  # nosec B101


def test_a_cancelled_touch_starts_over():
    cancelled = [("pointerdown", 150, 100, 100),
                 ("pointercancel", 170, 100, 100)]

    assert taps(*tap(0), *cancelled, *tap(250)) == [False, False]  # nosec


def test_a_third_tap_does_not_undo_the_double_tap():
    assert taps(*tap(0), *tap(150), *tap(300)) == [  # nosec B101
        False, True, False,
    ]
    # Two more make a second double tap.
    assert taps(*tap(0), *tap(150), *tap(300), *tap(450)) == [  # nosec
        False, True, False, True,
    ]


def test_a_second_finger_is_no_double_tap():
    pinch = [("pointerdown", 140, 140, 100, "touch", False)]

    assert taps(*tap(0), *pinch, *tap(180)) == [False, False]  # nosec B101


def test_a_double_tap_reacts_with_the_picked_emoji():
    script = "console.log(JSON.stringify(tcQuickEmoji()));\n"

    assert run(script) == THUMBS  # nosec B101
    assert run(script, {"team_chat_quick_react": PARTY}) == PARTY  # nosec
    assert run(script, {"team_chat_quick_react": ""}) == THUMBS  # nosec
    assert run(script, {"team_chat_quick_react": 5}) == THUMBS  # nosec
