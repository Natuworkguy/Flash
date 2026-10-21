# pylint: disable=C0114,C0115,C0116

import json
import os
import sys

import pytest

from flash import ai, computer, tools, web


@pytest.fixture
def ran(monkeypatch):
    """Every command run, with what it was given; each answers from
    ANSWERS by its program, or with nothing."""

    class Calls(list):
        answers: dict

    calls = Calls()
    answers: dict = {}

    def run(argv, given=""):
        calls.append((list(argv), given))
        answer = answers.get(argv[0], "")
        if isinstance(answer, Exception):
            raise answer
        return answer(argv) if callable(answer) else answer

    monkeypatch.setattr(computer, "_run", run)
    calls.answers = answers
    return calls


def on(monkeypatch, kind="linux", have=("playerctl", "wpctl")):
    monkeypatch.setenv(computer.SETTING, "1")
    monkeypatch.setattr(computer, "system", lambda: kind)
    monkeypatch.setattr(computer.shutil, "which",
                        lambda name: f"/usr/bin/{name}"
                        if name in have else None)


def test_off_until_the_user_turns_it_on(monkeypatch, ran):
    names = [t["function"]["name"] for t in tools.turn_tools()]
    assert "computer" not in names  # nosec B101

    said = tools.computer_tool("pause")

    assert said.startswith("Error:") and "/computer on" in said  # nosec
    assert ran == []  # nosec B101

    monkeypatch.setenv(computer.SETTING, "1")
    offered = [t for t in tools.turn_tools()
               if t["function"]["name"] == "computer"]
    actions = offered[0]["function"]["parameters"]["properties"]["action"]
    assert actions["enum"] == list(computer.ACTIONS)  # nosec B101


def test_an_action_that_is_not_one_says_which_are(monkeypatch, ran):
    on(monkeypatch)

    said = tools.computer_tool("format_disk")

    assert "not an action" in said and "play_pause" in said  # nosec
    assert ran == []  # nosec B101


def test_mac_pauses_what_is_playing(monkeypatch, ran):
    on(monkeypatch, "mac")
    ran.answers["osascript"] = json.dumps(
        {"did": "paused", "told": ["Spotify"]},
    )

    said = tools.computer_tool("pause")

    assert said == "Paused Spotify."  # nosec B101
    argv, _ = ran[0]
    assert argv[:3] == ["osascript", "-l", "JavaScript"]  # nosec B101
    # The action and the players go in as data, not as script.
    assert '"pause"' in argv[-1] and '"Spotify"' in argv[-1]  # nosec
    # A player that is not installed is asked carefully: not asking
    # would fail the whole script.
    assert "catch" in argv[-1]  # nosec B101


CHROME = {"known": True, "title": "Lofi beats", "artist": "A channel",
          "app": "Google Chrome"}


def mac_now(ran, monkeypatch, before, after=None, did="press"):
    """The Mac's Now Playing: BEFORE when Flash looks first, AFTER once
    a key has been pressed."""

    monkeypatch.setattr(computer, "SETTLE", 0)

    def osascript(argv):
        if "const action" in argv[-1]:
            return json.dumps({"did": did, "told": [], "now": before})
        return json.dumps(after if after is not None else before)

    ran.answers["osascript"] = osascript
    ran.answers[sys.executable] = "pressed"


def test_mac_presses_the_media_key_for_any_other_app(monkeypatch, ran):
    on(monkeypatch, "mac")
    mac_now(ran, monkeypatch, {"known": False},
            {**CHROME, "playing": True})

    said = tools.computer_tool("play")

    # Then it looks again, so the model knows it worked.
    assert said == (  # nosec B101
        "Pressed the play/pause media key. Now playing: Lofi beats by A "
        "channel in Google Chrome."
    )
    # Pressed by macOS's own calls, in a Python of its own, with only the
    # key's number given to it.
    argv = [a for a, _ in ran if a[0] == sys.executable][0]
    assert argv == [sys.executable, "-c", computer._MAC_KEY, "16"]  # nosec
    assert "CGPreflightPostEventAccess" in argv[2]  # nosec B101
    assert "next track" in tools.computer_tool("next_track")  # nosec B101
    assert [a for a, _ in ran if a[0] == sys.executable][-1][-1] == (  # nosec
        "17"
    )

    ran.answers[sys.executable] = "not allowed"
    assert "Accessibility" in tools.computer_tool("play_pause")  # nosec
    ran.answers[sys.executable] = "no key"
    assert "would not make" in tools.computer_tool("play_pause")  # nosec


def test_mac_says_when_a_key_did_not_do_it(monkeypatch, ran):
    on(monkeypatch, "mac")
    mac_now(ran, monkeypatch, {**CHROME, "playing": True},
            {**CHROME, "playing": True})

    said = tools.computer_tool("play_pause")

    assert said.endswith("So the key did not do it.")  # nosec B101


def test_mac_never_presses_a_key_the_wrong_way(monkeypatch, ran):
    on(monkeypatch, "mac")
    mac_now(ran, monkeypatch, {**CHROME, "playing": False})

    assert tools.computer_tool("pause") == (  # nosec B101
        "Nothing to pause: Lofi beats by A channel in Google Chrome is "
        "already paused."
    )
    mac_now(ran, monkeypatch, {**CHROME, "playing": True})
    assert tools.computer_tool("play").startswith("Already playing")  # nosec
    assert not [a for a, _ in ran if a[0] == sys.executable]  # nosec B101


def test_mac_says_what_is_playing_in_any_app(monkeypatch, ran):
    on(monkeypatch, "mac")
    mac_now(ran, monkeypatch, {**CHROME, "playing": False}, did="")

    assert tools.computer_tool("now_playing") == (  # nosec B101
        "Paused: Lofi beats by A channel in Google Chrome."
    )


def test_mac_without_now_playing_says_it_cannot_check(monkeypatch, ran):
    on(monkeypatch, "mac")
    mac_now(ran, monkeypatch, {"known": False})

    assert "cannot see what is playing" in tools.computer_tool(  # nosec
        "play_pause"
    )
    mac_now(ran, monkeypatch, {"known": False}, did="")
    assert "cannot see what is playing" in tools.computer_tool(  # nosec
        "now_playing"
    )


def test_the_mac_key_presser_is_python(monkeypatch):
    compile(computer._MAC_KEY, "mac_key", "exec")


def test_mac_volume_is_held_between_0_and_100(monkeypatch, ran):
    on(monkeypatch, "mac")
    ran.answers["osascript"] = "100"

    assert tools.computer_tool("set_volume", "150") == (  # nosec B101
        "Volume is at 100%."
    )
    assert "set volume output volume 100" in ran[0][0]  # nosec B101
    assert "level" in tools.computer_tool("set_volume", "loud")  # nosec


def test_linux_plays_through_playerctl(monkeypatch, ran):
    on(monkeypatch)
    ran.answers["playerctl"] = "Song by Band (Playing)"

    tools.computer_tool("pause")
    said = tools.computer_tool("now_playing")

    assert ran[0][0] == ["playerctl", "--all-players", "pause"]  # nosec
    assert said == "Playing: Song by Band (Playing)"  # nosec B101


def test_linux_without_playerctl_says_how_to_get_it(monkeypatch, ran):
    on(monkeypatch, have=())

    assert "install" in tools.computer_tool("next_track")  # nosec B101
    assert ran == []  # nosec B101


def test_linux_volume_through_the_sound_server(monkeypatch, ran):
    on(monkeypatch)
    ran.answers["wpctl"] = "Volume: 0.40"

    assert tools.computer_tool("volume_up") == (  # nosec B101
        "Volume is at 40%."
    )
    assert ran[0][0][:2] == ["wpctl", "set-volume"]  # nosec B101

    on(monkeypatch, have=("pactl",))
    ran.answers["pactl"] = "Volume: front-left: 26214 /  25% / -36 dB"
    assert tools.computer_tool("set_volume", "25") == (  # nosec B101
        "Volume is at 25%."
    )
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "25%"] in [
        argv for argv, _ in ran
    ]  # nosec B101


def test_windows_presses_the_media_keys(monkeypatch, ran):
    on(monkeypatch, "windows")
    pressed = []
    monkeypatch.setattr(computer, "_press",
                        lambda key, times=1: pressed.append((key, times)))

    tools.computer_tool("pause")
    tools.computer_tool("set_volume", "37")

    assert pressed == [(0xB3, 1), (0xAE, 50), (0xAF, 18)]  # nosec B101
    assert "cannot see" in tools.computer_tool("now_playing")  # nosec


@pytest.mark.parametrize("name", [
    "rm -rf /", "Spotify; reboot", "../../bin/sh", "$(id)", "",
    "/Applications/Evil.app",
])
def test_an_app_is_named_never_a_path_or_command(monkeypatch, ran, name):
    on(monkeypatch, "mac")

    assert "Name the app" in tools.computer_tool("open_app", name)  # nosec
    assert ran == []  # nosec B101


def test_apps_open_and_quit_by_name(monkeypatch, ran):
    on(monkeypatch, "mac")
    ran.answers["osascript"] = "not running"

    assert tools.computer_tool("open_app", "Visual Studio Code") == (  # nosec
        "Opened Visual Studio Code."
    )
    assert ran[0][0] == ["open", "-a", "Visual Studio Code"]  # nosec B101
    assert tools.computer_tool("quit_app", "Spotify.app") == (  # nosec
        "Spotify was not open."
    )
    assert json.dumps("Spotify") in ran[1][0][-1]  # nosec B101

    on(monkeypatch, "windows")
    tools.computer_tool("quit_app", "Spotify")
    # Asked to close, never forced.
    assert ran[-1][0] == ["taskkill", "/IM", "Spotify.exe"]  # nosec B101


def test_open_shows_links_and_documents_never_runs_things(
    monkeypatch, ran, tmp_path,
):
    on(monkeypatch, "mac")
    note = tmp_path / "notes.txt"
    note.write_text("hi")
    script = tmp_path / "go.command"
    script.write_text("echo hi")
    runnable = tmp_path / "tool"
    runnable.write_text("#!/bin/sh\n")
    os.chmod(runnable, 0o755)  # nosec B103

    assert tools.computer_tool("open", "https://example.com") == (  # nosec
        "Opened https://example.com."
    )
    assert tools.computer_tool("open", str(note)).startswith(  # nosec
        "Opened"
    )
    assert tools.computer_tool("open", str(tmp_path)).startswith(  # nosec
        "Opened"
    )
    opened = [argv for argv, _ in ran]
    for refused, why in (
        (str(script), "would run"),
        (str(runnable), "would run"),
        ("javascript:alert(1)", "Only web and mail links"),
        ("file:///etc/passwd", "Only web and mail links"),
        (str(tmp_path / "missing.txt"), "Nothing is at"),
    ):
        assert why in tools.computer_tool("open", refused)  # nosec B101
    assert [argv for argv, _ in ran] == opened  # nosec B101


def test_copy_goes_in_as_input(monkeypatch, ran):
    on(monkeypatch, "mac")

    said = tools.computer_tool("copy", "it's \"quoted\"; $(and) more")

    assert said == "Copied 26 characters to the clipboard."  # nosec B101
    assert ran[0] == (["pbcopy"], "it's \"quoted\"; $(and) more")  # nosec


def test_dark_mode_needs_on_or_off(monkeypatch, ran):
    on(monkeypatch, have=("gsettings",))

    assert tools.computer_tool("dark_mode", "on") == "Dark mode is on."
    assert ran[0][0][-1] == "prefer-dark"  # nosec B101
    assert "on or off" in tools.computer_tool("dark_mode", "maybe")  # nosec


def test_a_failed_command_is_told_to_the_model(monkeypatch, ran):
    on(monkeypatch, "mac")
    ran.answers["pmset"] = computer.ComputerError("pmset failed: denied")

    assert tools.computer_tool("lock_screen") == (  # nosec B101
        "Error: pmset failed: denied"
    )


def test_run_never_uses_a_shell(monkeypatch):
    seen = {}

    def fake(argv, **options):
        seen.update(options, argv=argv)
        raise FileNotFoundError

    monkeypatch.setattr(computer.subprocess, "run", fake)

    with pytest.raises(computer.ComputerError, match="not on this"):
        computer._run(["playerctl", "pause"])
    assert "shell" not in seen and seen["argv"] == [  # nosec B101
        "playerctl", "pause",
    ]


def test_the_terminal_and_the_page_switch_it(monkeypatch):
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: monkeypatch.setenv(name, value))

    ai._computer_command("on")
    assert computer.enabled()  # nosec B101

    got = web.command(web.Session(), {"name": "computer-setting",
                                      "arg": "off"})
    assert got == {"computer": False} and not computer.enabled()  # nosec
