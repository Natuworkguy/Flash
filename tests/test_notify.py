# pylint: disable=C0114,C0116

import pytest

from flash import notify, tools
from flash.theme import capture_tool_output


@pytest.fixture
def shown(monkeypatch):
    """The desktop notifications the tool would have shown."""

    seen = []
    monkeypatch.setattr(
        tools, "desktop_notify", lambda title, text: seen.append((title, text))
    )
    return seen


def test_the_limit_spaces_notifications_out():
    start = 1_000_000.0

    gap = notify.NOTIFY_GAP

    assert notify.take_notify_turn(start) == 0  # nosec B101
    assert notify.take_notify_turn(start + 1) == gap - 1  # nosec B101
    assert notify.take_notify_turn(start + gap) == 0  # nosec B101


def test_the_limit_caps_a_window():
    start = 1_000_000.0
    gap = notify.NOTIFY_GAP
    for turn in range(notify.NOTIFY_LIMIT):
        assert notify.take_notify_turn(start + turn * gap) == 0  # nosec B101

    later = start + notify.NOTIFY_LIMIT * gap
    wait = notify.take_notify_turn(later)

    assert wait == start + notify.NOTIFY_WINDOW - later  # nosec B101
    # Once the first has aged out of the window, one more may go.
    aged = start + notify.NOTIFY_WINDOW
    assert notify.take_notify_turn(aged) == 0  # nosec B101


def test_the_limit_is_kept_on_disk():
    notify.take_notify_turn(1_000_000.0)

    assert (notify.FLASH_DIR / "notified.json").is_file()  # nosec B101
    assert notify.wait_to_notify(1_000_001.0) > 0  # nosec B101


def test_a_spoiled_count_does_not_stop_notifications():
    path = notify.FLASH_DIR / "notified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    assert notify.take_notify_turn() == 0  # nosec B101


def test_notify_user_shows_it_on_the_desktop(shown):
    result = tools.notify_user("The build  finished.", title="Build")

    assert result.startswith("Sent")  # nosec B101
    assert shown == [("Build", "The build finished.")]  # nosec B101


def test_notify_user_is_rate_limited(shown):
    tools.notify_user("One")
    result = tools.notify_user("Two")

    assert result.startswith("Error: not sent")  # nosec B101
    assert "30 seconds" in result  # nosec B101
    assert shown == [("Flash", "One")]  # nosec B101


def test_notify_user_needs_a_message(shown):
    assert "needs a message" in tools.notify_user("  ")  # nosec B101
    assert shown == []  # nosec B101
    # Refusing it used up nothing.
    assert notify.wait_to_notify() == 0  # nosec B101


def test_notify_user_keeps_it_short(shown):
    tools.notify_user("x" * 1000)

    assert len(shown[0][1]) == tools.MAX_NOTIFY_MESSAGE  # nosec B101


def test_a_page_that_takes_it_keeps_it_off_the_desktop(shown):
    taken = []

    def page(kind, text, style):
        if kind == "notify":
            taken.append(text)
            return True
        return None

    with capture_tool_output(page):
        tools.notify_user("Done", title="Job")

    assert taken and '"Job"' in taken[0]  # nosec B101
    assert shown == []  # nosec B101


def test_a_spark_notifies_on_the_desktop(shown):
    # A spark's shift records its steps, and shows nothing itself.
    with capture_tool_output(lambda kind, text, style: None):
        tools.notify_user("Found it", title="Scout")

    assert shown == [("Scout", "Found it")]  # nosec B101


def test_sparks_and_sub_agents_can_notify():
    assert "notify_user" in tools.SUBAGENT_TOOL_NAMES  # nosec B101
    assert "notify_user" in tools.SPARK_TOOL_NAMES  # nosec B101
    assert "notify_user" in tools.FUNCTIONS  # nosec B101
