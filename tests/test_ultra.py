# pylint: disable=C0114,C0115,C0116

import pytest

from flash import ai, sparks, systemone, teamchat, tools, web


@pytest.fixture(autouse=True)
def fresh_config():
    ai.Config.refresh()
    yield
    ai.Config.refresh()


def test_ultra_is_a_third_mode_only_once_allowed():
    assert ai.mode() == "ask"  # nosec B101
    with pytest.raises(ValueError, match="not allowed yet"):
        ai.set_mode("ultra")
    # Round the two there are.
    assert ai.next_mode() == "auto"  # nosec B101
    ai.set_mode("auto")
    assert ai.next_mode() == "ask"  # nosec B101

    ai.allow_ultra(True)
    assert ai.next_mode() == "ultra"  # nosec B101
    ai.set_mode("ultra")
    assert ai.mode() == "ultra" and ai.Config.no_command_confirmation
    assert tools.ULTRA_AUTO and tools.ultra_auto()  # nosec B101
    assert sparks.ultra() and sparks.autonomous()  # nosec B101
    assert ai.next_mode() == "ask"  # nosec B101

    # No longer offered: out of it at once, autonomous still.
    ai.allow_ultra(False)
    assert ai.mode() == "auto" and not tools.ultra_auto()  # nosec B101


def test_system_one_still_reviews_in_ultra(monkeypatch):
    monkeypatch.setattr(systemone, "enabled", lambda: True)
    ai.allow_ultra(True)
    ai.set_mode("ultra")
    assert systemone.active()  # nosec B101


def test_a_spark_sees_ultra_switched_in_another_flash():
    # Another Flash wrote it; this process never heard.
    from flash.envfile import set_env_var

    set_env_var(sparks.ENV_PATH, "ULTRA_AUTO_ALLOWED", "1")
    set_env_var(sparks.ENV_PATH, "ULTRA_AUTO", "1")
    assert not tools.ULTRA_AUTO and sparks.ultra()  # nosec B101
    set_env_var(sparks.ENV_PATH, "ULTRA_AUTO_ALLOWED", "0")
    assert not sparks.ultra()  # nosec B101


def ultra_on():
    ai.allow_ultra(True)
    ai.set_mode("ultra")


def test_a_hire_is_a_yes_without_asking_in_ultra(monkeypatch):
    lead = sparks.Spark(id="l1", name="Lead", goal="g")
    decided = []
    monkeypatch.setattr(sparks, "propose_hire", lambda *a, **k: {
        "id": "h1", "spark": {"name": "Scout"}})
    monkeypatch.setattr(sparks, "propose_removal", lambda *a, **k: {
        "id": "h2", "target_name": "Old"})
    monkeypatch.setattr(sparks, "decide_hire",
                        lambda hire, yes, why="": decided.append((hire, yes)))
    proposing = sparks._proposing(lead)

    said = proposing["propose_hire"]({"name": "Scout", "goal": "look"})
    assert "waits on the user" in said and decided == []  # nosec B101

    ultra_on()
    said = proposing["propose_hire"]({"name": "Scout", "goal": "look"})
    assert said.startswith("(hired: Scout") and decided == [  # nosec
        ("h1", True)]
    said = proposing["propose_removal"]({"spark": "old"})
    assert said.startswith("(let go: Old") and decided[-1] == (  # nosec
        "h2", True)


def test_team_chat_gets_every_tool_in_ultra():
    names = teamchat._ultra_tools()
    assert "shell" in names and "write" in names  # nosec B101
    assert set(names) <= set(tools.SPARK_TOOL_NAMES)  # nosec B101


def test_a_wait_command_is_not_asked_about_in_ultra(monkeypatch):
    from flash import waits

    ultra_on()
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(tools, "_user_allows",
                        lambda q: pytest.fail("asked in UltraAuto"))
    wait = waits.make("c1", {"what": "CI", "then": "deploy",
                             "command": "true"})
    assert web._command_allowed(wait)  # nosec B101


def test_the_web_goes_round_the_modes(monkeypatch):
    class Hub:
        def publish(self, event):
            pass

    class Session:
        hub = Hub()

    said = web.command(Session(), {"name": "auto", "arg": "toggle"})
    assert said == {"auto": True, "mode": "auto"}  # nosec B101
    with pytest.raises(ValueError, match="not allowed"):
        web.command(Session(), {"name": "auto", "arg": "ultra"})

    said = web.command(Session(), {"name": "ultra-setting", "arg": "on"})
    assert said["ultra_allowed"] is True  # nosec B101
    said = web.command(Session(), {"name": "auto", "arg": "toggle"})
    assert said["mode"] == "ultra"  # nosec B101
    said = web.command(Session(), {"name": "ultra-setting", "arg": "off"})
    assert said == {"ultra_allowed": False, "auto": True,  # nosec B101
                    "mode": "auto"}
