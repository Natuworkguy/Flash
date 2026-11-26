# pylint: disable=C0114,C0115,C0116

import pytest

from flash import ai, tools


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv(tools.THINK_AUTO_SETTING, raising=False)
    tools.end_thinking()
    yield
    tools.end_thinking()


def names(offered):
    return [t["function"]["name"] for t in offered]


def test_offered_only_when_it_can_help():
    assert names(tools.think_tools(True)) == ["think_harder"]  # nosec B101
    assert tools.think_tools(False) == []  # nosec B101  (cannot think)
    assert tools.think_tools(True, already=True) == []  # nosec B101

    for off in ("0", "off", "false"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(tools.THINK_AUTO_SETTING, off)
            assert tools.think_tools(True) == []  # nosec B101


def test_the_terminal_thinks_once_it_is_turned_on(monkeypatch):
    asked = []

    class Client:
        def chat(self, **request):
            asked.append(request)
            return {}

    monkeypatch.setattr(ai.Config, "model", "reasoner:8b")
    monkeypatch.setattr(ai, "_think_levels",
                        {(ai.Config.host, "reasoner:8b"): True})

    ai._chat(Client(), [])
    said = tools.run_tool(("think_harder", {"why": "a hard proof"}))
    ai._chat(Client(), [])
    tools.end_thinking()
    ai._chat(Client(), [])

    assert said == tools.THOUGHT_HARDER  # nosec B101
    assert ["think" in a for a in asked] == [False, True, False]  # nosec
    assert asked[1]["think"] is True  # nosec B101


def test_think_command(monkeypatch):
    printed = []
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: monkeypatch.setenv(name, value))
    monkeypatch.setattr(ai.console, "print",
                        lambda text: printed.append(str(text)))
    monkeypatch.setattr(ai, "_think_level", lambda: True)

    ai._think_command("auto off")
    assert tools.think_auto() is False  # nosec B101
    assert printed[-1].startswith("Flash thinks only when")  # nosec B101

    ai._think_command("auto on")
    assert tools.think_auto() is True  # nosec B101
    ai._think_command("banana")
    assert printed[-1].startswith("Use /think auto")  # nosec B101
