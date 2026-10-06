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


def test_the_limit_starts_at_its_default():
    assert notify.limits() == (  # nosec B101
        notify.NOTIFY_LIMIT, notify.NOTIFY_WINDOW,
    )
    said = notify.limit_words()
    assert "4 notifications every 15 minutes" in said  # nosec B101


def test_the_user_sets_the_limit():
    assert notify.set_limits(10, "60m") == (10, 3600)  # nosec B101
    assert notify.limits() == (10, 3600)  # nosec B101
    assert "every 1 hour" in notify.limit_words()  # nosec B101
    saved = (notify.FLASH_DIR.parent / ".flash.env").read_text()
    assert "NOTIFY_LIMIT=" in saved  # nosec B101


def test_a_limit_out_of_range_is_refused():
    for count, minutes in ((61, 15), (4, 0), ("lots", 15), (4, "soon")):
        with pytest.raises(notify.LimitError):
            notify.set_limits(count, minutes)
    assert notify.limits()[0] == notify.NOTIFY_LIMIT  # nosec B101


def test_a_tighter_limit_brings_the_gap_down():
    notify.set_limits(20, 1)
    start = 1_000_000.0

    assert notify.take_notify_turn(start) == 0  # nosec B101
    # 20 in a minute is one every 3 seconds, not one every 30.
    assert notify.take_notify_turn(start + 3) == 0  # nosec B101


def test_a_bigger_limit_lets_more_through():
    notify.set_limits(6, 15)
    start = 1_000_000.0
    for turn in range(6):
        assert notify.take_notify_turn(start + turn * 30) == 0  # nosec B101

    assert notify.take_notify_turn(start + 6 * 30) > 0  # nosec B101


def test_notifications_turned_off_are_refused(shown):
    notify.set_limits("off", 15)

    result = tools.notify_user("Done")

    assert "turned notifications from you off" in result  # nosec B101
    assert shown == []  # nosec B101
    assert "cannot send notifications" in notify.limit_words()  # nosec B101


def test_the_terminal_sets_the_limit(monkeypatch):
    from flash import ai

    ai._notify_command("8 per 30m")
    assert notify.limits() == (8, 1800)  # nosec B101

    ai._notify_command("off")
    assert notify.limits() == (0, 1800)  # nosec B101

    ai._notify_command("on")
    assert notify.limits() == (notify.NOTIFY_LIMIT, 1800)  # nosec B101
