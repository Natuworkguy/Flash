# pylint: disable=C0114,C0115,C0116

import pytest

from flash import computeruse, tools


@pytest.fixture(autouse=True)
def use_on(monkeypatch, tmp_path):
    monkeypatch.setattr("flash.notify.ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv(computeruse.SETTING, "1")
    monkeypatch.setattr(computeruse, "system", lambda: "linux")
    monkeypatch.setattr(computeruse, "missing", lambda: "")
    monkeypatch.setattr(computeruse, "_watch", lambda: None)
    monkeypatch.setattr(computeruse, "SETTLE", 0)
    monkeypatch.setattr(tools, "model_sees_images", lambda *a: True)
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    monkeypatch.setattr(tools, "ULTRA_AUTO", False)
    tools._pending_images.clear()
    computeruse.end()
    yield
    computeruse.end()
    computeruse._shot.clear()


@pytest.fixture
def screen(monkeypatch):
    """A 2560x1440 screen, shot at 1280x720, and what was sent to it."""

    sent = []

    def shoot():
        computeruse._shot.update(scale=2.0, size=(1280, 720))
        return b"png", (1280, 720)

    monkeypatch.setattr(computeruse, "screenshot", shoot)
    monkeypatch.setattr(computeruse, "_send",
                        lambda kind, command: sent.append(command) or {})
    monkeypatch.setattr(computeruse, "pointer", lambda: (0.0, 0.0))
    return sent


def test_it_asks_even_in_autonomous_mode_and_once_a_reply(
        monkeypatch, screen):
    asked = []
    answers = [False, True]
    monkeypatch.setattr(tools, "_user_allows",
                        lambda q: asked.append(q) or answers.pop(0))

    said = tools.computer_use_tool("screenshot")
    assert "said no" in said and screen == []  # nosec B101
    assert "Ctrl+Alt+Esc" in asked[0]  # nosec B101

    said = tools.computer_use_tool("screenshot")
    assert "The screen now, 1280x720" in said  # nosec B101
    assert tools._pending_images == [b"png"]  # nosec B101
    tools.computer_use_tool("click", x=100, y=50)
    # One yes for the reply, and the screenshot's points made the
    # screen's own.
    assert len(asked) == 2  # nosec B101
    assert screen == [{"do": "click", "x": 200.0, "y": 100.0,  # nosec
                       "button": "left", "count": 1}]

    # The reply ends: the next one asks again.
    computeruse.end("terminal")
    answers.append(False)
    assert "said no" in tools.computer_use_tool("screenshot")  # nosec B101
    assert len(asked) == 3  # nosec B101


def test_ultra_auto_does_not_ask(monkeypatch, screen):
    monkeypatch.setattr(tools, "ULTRA_AUTO", True)
    monkeypatch.setattr(tools, "_user_allows",
                        lambda q: pytest.fail("asked in UltraAuto"))

    assert "The screen now" in tools.computer_use_tool("screenshot")  # nosec


def test_moving_the_mouse_takes_it_back(monkeypatch, screen):
    stopped = []
    computeruse.take("c1", lambda: stopped.append(True))
    computeruse.do("screenshot", {}, "c1")
    computeruse.do("move", {"x": 10, "y": 10}, "c1")
    assert computeruse._holder["pointer"] == (20.0, 20.0)  # nosec B101

    monkeypatch.setattr(computeruse, "pointer", lambda: (400.0, 300.0))
    with pytest.raises(computeruse.TakenBack, match="moved the mouse"):
        computeruse.do("click", {"x": 10, "y": 10}, "c1")
    assert stopped == [True] and not computeruse.holds("c1")  # nosec B101
    assert len(screen) == 1  # nosec B101  (the click never happened)


def test_the_stop_keys_stop_the_reply_that_has_it(screen):
    stopped = []
    computeruse.take("c1", lambda: stopped.append("c1"))

    computeruse.taken_back()

    assert stopped == ["c1"] and not computeruse.holds("c1")  # nosec B101


def test_one_chat_at_a_time(monkeypatch, screen):
    computeruse.take("other", lambda: None)
    monkeypatch.setattr(tools, "_user_allows",
                        lambda q: pytest.fail("asked while busy"))

    with tools.computer_user("c1", lambda: None):
        said = tools.computer_use_tool("screenshot")
    assert "another chat" in said  # nosec B101


def test_points_off_the_screenshot_and_before_one_are_refused(screen):
    computeruse.take("c1")
    with pytest.raises(computeruse.UseError, match="screenshot first"):
        computeruse.do("click", {"x": 1, "y": 1}, "c1")
    computeruse.do("screenshot", {}, "c1")
    with pytest.raises(computeruse.UseError, match="off the screenshot"):
        computeruse.do("click", {"x": 5000, "y": 1}, "c1")


def test_keys_are_read_and_the_stop_keys_never_pressed():
    assert computeruse.parse_keys("Cmd+Shift+4") == (  # nosec B101
        ["cmd", "shift"], "4")
    assert computeruse.parse_keys("enter") == ([], "return")  # nosec B101
    assert computeruse.parse_keys("option+left") == (  # nosec B101
        ["alt"], "left")
    with pytest.raises(computeruse.UseError, match="stop keys"):
        computeruse.parse_keys("ctrl+alt+esc")
    with pytest.raises(computeruse.UseError, match="not a key"):
        computeruse.parse_keys("ctrl+banana")
    with pytest.raises(computeruse.UseError, match="not a modifier"):
        computeruse.parse_keys("hyper+a")


def test_linux_goes_through_xdotool(monkeypatch):
    ran = []
    monkeypatch.setattr(computeruse, "_run",
                        lambda argv, env=None: ran.append(argv) or "")

    computeruse._linux({"do": "click", "x": 20.4, "y": 10, "button": "right",
                        "count": 1})
    computeruse._linux({"do": "key", "mods": ["cmd", "shift"], "key": "f5"})
    computeruse._linux({"do": "type", "text": "--help"})

    assert ran == [  # nosec B101
        ["xdotool", "mousemove", "20", "10", "click", "--repeat", "1", "3"],
        ["xdotool", "key", "--", "super+shift+F5"],
        ["xdotool", "type", "--delay", "8", "--", "--help"],
    ]


def test_offered_only_while_on(monkeypatch):
    names = {t["function"]["name"] for t in tools.available_tools()}
    assert "computer_use" in names  # nosec B101
    assert "computer_use" not in tools.SPARK_TOOL_NAMES  # nosec B101

    monkeypatch.setenv(computeruse.SETTING, "0")
    names = {t["function"]["name"] for t in tools.available_tools()}
    assert "computer_use" not in names  # nosec B101
    said = tools.computer_use_tool("screenshot")
    assert said.startswith("Error: Computer Use is off")  # nosec B101


def test_the_web_turns_it_on_and_says_the_stop_keys(monkeypatch):
    from flash import web

    monkeypatch.setattr(computeruse, "set_enabled", lambda on: monkeypatch
                        .setenv(computeruse.SETTING, "1" if on else "0"))

    class Hub:
        def publish(self, event):
            pass

    class Session:
        hub = Hub()

    said = web.command(Session(), {"name": "computer-use-setting",
                                   "arg": "off"})
    assert said["computer_use"] is False  # nosec B101
    assert said["computer_use_keys"] == "Ctrl+Alt+Esc"  # nosec B101
