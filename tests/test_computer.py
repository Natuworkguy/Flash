# pylint: disable=C0114,C0115,C0116

import json
import os
import sys

import pytest

from flash import ai, computer, tools, web
from flash.theme import answer_from


@pytest.fixture
def ran(monkeypatch):
    """Every command run, with what it was given; each answers from
    ANSWERS by its program, or with nothing."""

    class Calls(list):
        answers: dict

    calls = Calls()
    answers: dict = {}

    def run(argv, given="", timeout=None):
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
    assert got["computer"] is False and not computer.enabled()  # nosec


def test_the_terminal_switches_one_and_lists_them(monkeypatch):
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: monkeypatch.setenv(name, value))
    monkeypatch.setattr(computer, "system", lambda: "mac")
    monkeypatch.setenv(computer.SETTING, "0")
    monkeypatch.delenv(computer.OFF_SETTING, raising=False)

    ai._computer_command("off imessage")
    assert computer.switched_off() == {"imessage"}  # nosec B101

    # Switching one on lets Flash act at all.
    ai._computer_command("on volume")
    assert computer.enabled()  # nosec B101
    assert not computer.allowed("imessage")  # nosec B101


# --- iMessage -------------------------------------------------------------


SAM = {"name": "Sam Lee",
       "phones": [{"label": "_$!<Home>!$_", "value": "+1 555 0100"},
                  {"label": "_$!<Mobile>!$_", "value": "+1 (555) 010-2000"}],
       "emails": [{"label": "", "value": "sam@example.com"}]}


def mac_messages(monkeypatch, ran, contacts=(SAM,)):
    on(monkeypatch, "mac")
    ran.answers["osascript"] = (
        lambda argv: json.dumps(list(contacts))
        if "Contacts" in " ".join(argv) else "")


def sent(ran):
    return [argv for argv, _ in ran if "Messages" in " ".join(argv)]


def test_send_imessage_is_offered_only_on_a_mac(monkeypatch, ran):
    def names():
        return {t["function"]["name"] for t in tools.turn_tools()}

    on(monkeypatch, "linux")
    assert "send_imessage" not in names()  # nosec B101
    on(monkeypatch, "mac")
    assert "send_imessage" in names()  # nosec B101
    monkeypatch.setenv(computer.SETTING, "0")
    assert "send_imessage" not in names()  # nosec B101


def test_an_imessage_goes_to_a_contacts_mobile_once_agreed(monkeypatch,
                                                           ran):
    mac_messages(monkeypatch, ran)
    asked = []
    with answer_from(lambda question: asked.append(question) or "y"):
        said = tools.send_imessage("sam", "Running late, there by 7")

    assert said == "Sent to Sam Lee, +15550102000."  # nosec B101
    assert "To: Sam Lee, +15550102000" in asked[0]  # nosec B101
    assert "Running late, there by 7" in asked[0]  # nosec B101
    # Who to and what it says go in as arguments, never as script.
    argv = sent(ran)[0]
    assert argv[-2:] == ["+15550102000", "Running late, there by 7"]  # nosec
    assert not any("Running late" in part for part in argv[:-1])  # nosec


def test_an_imessage_asks_even_in_autonomous_mode(monkeypatch, ran):
    mac_messages(monkeypatch, ran)
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    with answer_from(lambda question: "n"):
        said = tools.send_imessage("+1 555 0100", "On my way")

    assert said.startswith("Not sent")  # nosec B101
    assert sent(ran) == []  # nosec B101


def test_a_number_or_address_is_not_looked_up(monkeypatch, ran):
    mac_messages(monkeypatch, ran)
    assert computer.recipient("(555) 010-2000") == (  # nosec B101
        "(555) 010-2000", "5550102000")
    assert computer.recipient("bo@example.com") == (  # nosec B101
        "bo@example.com", "bo@example.com")
    assert ran == []  # nosec B101


def test_two_contacts_with_the_name_are_asked_about_not_guessed(
        monkeypatch, ran):
    other = dict(SAM, name="Sam Ortiz")
    mac_messages(monkeypatch, ran, (SAM, other))
    asked = []
    with answer_from(lambda question: asked.append(question) or "y"):
        said = tools.send_imessage("sam", "Hi")

    assert "Sam Lee, Sam Ortiz" in said and "which one" in said  # nosec
    assert asked == [] and sent(ran) == []  # nosec B101

    # The whole name settles it.
    assert computer.recipient("sam lee")[0] == "Sam Lee"  # nosec B101


@pytest.mark.parametrize("to, text, said", [
    ("Sam", "placeholder", "placeholder"),
    ("[recipient name]", "Hi", "placeholder"),
    ("Nobody", "Hi", "No one called Nobody"),
])
def test_a_message_not_ready_is_never_put_to_the_user(monkeypatch, ran, to,
                                                      text, said):
    mac_messages(monkeypatch, ran, () if to == "Nobody" else (SAM,))
    asked = []
    with answer_from(lambda question: asked.append(question) or "y"):
        result = tools.send_imessage(to, text)

    assert result.startswith("Error:") and said in result  # nosec B101
    assert asked == [] and sent(ran) == []  # nosec B101


def test_messages_not_allowed_says_where_to_allow_it(monkeypatch, ran):
    on(monkeypatch, "mac")
    ran.answers["osascript"] = computer.ComputerError(
        "osascript failed: execution error: Not authorized to send Apple "
        "events to Messages. (-1743)")

    with pytest.raises(computer.ComputerError, match="Automation"):
        computer.send_imessage("+15550100", "Hi")


def test_a_nickname_finds_the_contact(monkeypatch, ran):
    mom = {"name": "Linda Lee", "nickname": "Mom",
           "phones": [{"label": "_$!<Mobile>!$_", "value": "555 010 3000"}],
           "emails": []}
    momo = {"name": "Momo Park", "nickname": "",
            "phones": [{"label": "", "value": "555 010 4000"}], "emails": []}
    mac_messages(monkeypatch, ran, (mom, momo))
    with answer_from(lambda question: "y"):
        said = tools.send_imessage("mom", "Home soon")

    # Both have "mom" in them; the whole nickname settles it.
    assert said == "Sent to Linda Lee (Mom), 5550103000."  # nosec B101
    looked = [argv for argv, _ in ran if "Contacts" in " ".join(argv)]
    assert "nickname" in " ".join(looked[0])  # nosec B101


# --- Switching one thing off ----------------------------------------------


def test_what_is_switched_off_is_not_offered_or_done(monkeypatch, ran):
    on(monkeypatch, "mac")
    monkeypatch.setenv(computer.OFF_SETTING, "imessage,music")

    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert "send_imessage" not in names  # nosec B101
    offered = [t for t in tools.turn_tools()
               if t["function"]["name"] == "computer"]
    actions = offered[0]["function"]["parameters"]["properties"]["action"]
    assert "pause" not in actions["enum"]  # nosec B101
    assert "set_volume" in actions["enum"]  # nosec B101

    said = tools.computer_tool("pause")
    assert "switched pause off" in said and "on music" in said  # nosec
    said = tools.send_imessage("+1 555 0100", "Hi")
    assert "/computer on imessage" in said  # nosec B101
    assert ran == []  # nosec B101


def test_switching_one_back_on_keeps_the_rest_off(monkeypatch):
    monkeypatch.setenv(computer.OFF_SETTING, "music,imessage")
    assert computer.off_after("imessage", True) == "music"  # nosec B101
    assert computer.off_after("volume", False) == (  # nosec B101
        "music,volume,imessage")
    with pytest.raises(computer.ComputerError):
        computer.off_after("printer", False)


def test_imessages_are_only_a_switch_on_a_mac(monkeypatch):
    on(monkeypatch, "linux")
    assert "imessage" not in computer.kinds()  # nosec B101
    on(monkeypatch, "mac")
    assert "imessage" in computer.kinds()  # nosec B101


def test_the_web_page_switches_one_off(monkeypatch):
    on(monkeypatch, "mac")
    monkeypatch.delenv(computer.OFF_SETTING, raising=False)
    monkeypatch.setattr(ai, "set_config_var",
                        lambda name, value: monkeypatch.setenv(name, value))

    said = web.command(web.Session(), {"name": "computer-setting",
                                       "arg": "off imessage"})

    assert said["computer"] is True  # nosec B101
    assert said["computer_off"] == ["imessage"]  # nosec B101
    assert said["computer_kinds"][-1]["name"] == "imessage"  # nosec B101
    assert "send_imessage" not in {  # nosec B101
        t["function"]["name"] for t in tools.turn_tools()}


# --- Reading iMessages ------------------------------------------------------


def _styled(text: str) -> bytes:
    """TEXT as Messages keeps it when it keeps only styled text."""

    data = text.encode()
    size = bytes([len(data)]) if len(data) < 0x81 else (
        b"\x81" + len(data).to_bytes(2, "little"))
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12"
            b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84"
            b"\x84\x84\x08NSString\x01\x94\x84\x01+" + size + data
            + b"\x86\x84\x02iI\x01")


@pytest.fixture
def messages(monkeypatch, tmp_path):
    """A Messages history with Sam, a group chat, and one sent."""

    import sqlite3

    db = tmp_path / "chat.db"
    with sqlite3.connect(db) as made:
        made.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT,
                               chat_identifier TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER,
                                           handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER,
                                            message_id INTEGER);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER,
                is_from_me INTEGER, is_read INTEGER, text TEXT,
                attributedBody BLOB, cache_has_attachments INTEGER,
                handle_id INTEGER, item_type INTEGER);
            INSERT INTO handle VALUES (1, '+15550102000'),
                                      (2, 'bo@example.com');
            INSERT INTO chat VALUES (1, '', '+15550102000'),
                                    (2, 'Climbing', 'chat123');
            INSERT INTO chat_handle_join VALUES (1, 1), (2, 1), (2, 2);
        """)
        rows = [
            # Seconds since 2001, as nanoseconds: an hour apart.
            (1, 1, 0, 1, "Are we still on for 7?", None, 0, 1),
            (2, 1, 1, 1, "Yes, see you there", None, 0, 1),
            (3, 2, 0, 0, None, _styled("Who's bringing the rope?"), 0, 2),
            (4, 1, 0, 0, "Running late", None, 1, 1),
        ]
        for at, (rowid, chat, mine, read, text, body, attached,
                 handle) in enumerate(rows):
            made.execute(
                "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (rowid, (750_000_000 + at * 3600) * 10**9, mine, read, text,
                 body, attached, handle))
            made.execute("INSERT INTO chat_message_join VALUES (?, ?)",
                         (chat, rowid))
    monkeypatch.setattr(computer, "MESSAGES_DB", db)
    monkeypatch.setattr(computer, "_address_book",
                        lambda: {"5550102000": "Sam Lee"})
    return db


def test_recent_imessages_read_newest_first_with_names(monkeypatch, ran,
                                                       messages):
    on(monkeypatch, "mac")

    said = tools.read_imessages()

    lines = said.splitlines()
    assert lines[0] == "4 messages, newest first:"  # nosec B101
    assert lines[1].endswith(  # nosec B101
        "Sam Lee (unread): Running late [attachment]")
    assert lines[2].endswith(  # nosec B101
        "bo@example.com in Climbing (unread): Who's bringing the rope?")
    assert lines[3].endswith("You to Sam Lee: Yes, see you there")  # nosec
    assert lines[4].endswith("Sam Lee: Are we still on for 7?")  # nosec


def test_unread_and_one_person_only(monkeypatch, ran, messages):
    mac_messages(monkeypatch, ran)

    unread = computer.read_imessages(unread_only=True)
    assert unread.startswith("2 messages") and "Yes, see" not in unread

    with_sam = tools.read_imessages(**{"with": "sam"})
    assert "rope" not in with_sam and "Yes, see you there" in with_sam
    assert with_sam.startswith("3 messages")  # nosec B101


def test_reading_is_its_own_switch(monkeypatch, ran, messages):
    on(monkeypatch, "mac")
    monkeypatch.setenv(computer.OFF_SETTING, "imessage_read")

    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert "read_imessages" not in names  # nosec B101
    assert "send_imessage" in names  # nosec B101
    assert "imessage_read" in tools.read_imessages()  # nosec B101


def test_no_full_disk_access_says_how_to_give_it(monkeypatch, ran,
                                                 messages):
    on(monkeypatch, "mac")

    def denied(*_, **__):
        raise computer.sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(computer.sqlite3, "connect", denied)

    with pytest.raises(computer.ComputerError, match="Full Disk Access"):
        computer.read_imessages()
