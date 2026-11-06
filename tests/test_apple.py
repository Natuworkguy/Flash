# pylint: disable=C0114,C0116
"""Apple Calendar, Apple Reminders and iMessage attachments, with the Mac
side (osascript, Messages' database) faked."""

import os
import sqlite3

import pytest

from flash import calendars, computer, reminders, tools, web
from flash.theme import answer_from

FS, RS = "\x1f", "\x1e"


@pytest.fixture
def mac(monkeypatch):
    """A Mac whose osascript answers from ANSWERS by which app the
    script tells; every call kept, with its argv."""

    class Calls(list):
        answers: dict

    calls = Calls()
    calls.answers = {}

    def run(argv, given="", timeout=None):
        calls.append(list(argv))
        script = " ".join(argv)
        for key, answer in calls.answers.items():
            if key in script:
                if isinstance(answer, Exception):
                    raise answer
                return answer(argv) if callable(answer) else answer
        return ""

    monkeypatch.setenv(computer.SETTING, "1")
    monkeypatch.setattr(computer, "system", lambda: "mac")
    monkeypatch.setattr(computer, "_run", run)
    return calls


def seconds(text):
    return str(calendars._apple_seconds(calendars.parse_time(text)))


# --- Apple Calendar ---------------------------------------------------------


def test_apple_calendar_is_off_until_switched_on(mac):
    assert not calendars.apple_connected()  # nosec B101
    mac.answers["writable of c"] = f"Home{FS}true{RS}"
    said = web.command(web.Session(), {"name": "calendar-apple",
                                       "arg": "on"})
    assert said == {"apple": True} and calendars.apple_connected()  # nosec
    got = web.command(web.Session(), {"name": "calendar", "arg": ""})
    assert got["apple"] and got["apple_available"]  # nosec B101
    web.command(web.Session(), {"name": "calendar-apple", "arg": "off"})
    assert not calendars.apple_connected()  # nosec B101


def test_apple_calendar_is_only_on_a_mac(monkeypatch):
    monkeypatch.setattr(computer, "system", lambda: "linux")
    with pytest.raises(calendars.CalendarError, match="Mac"):
        calendars.apple_connect(True)


def test_apple_events_are_read_with_the_rest(mac):
    calendars.apple_connect(True)
    row = FS.join(["Work", "ABC-1", "Dentist",
                   seconds("2026-11-03T09:00"), seconds("2026-11-03T10:00"),
                   "false", "Main St", "missing nothing"])
    mac.answers["every event of c"] = row + RS
    found = calendars.events("2026-11-02", "2026-11-09")
    assert [e.title for e in found] == ["Dentist"]  # nosec B101
    event = found[0]
    assert event.id == "apple:Work:ABC-1"  # nosec B101
    assert event.start.startswith("2026-11-03T09:00")  # nosec B101
    assert event.end.startswith("2026-11-03T10:00")  # nosec B101
    assert event.where == "Main St"  # nosec B101
    # The window crosses as seconds, never as date text.
    argv = mac[-1]
    assert argv[-2:] == [seconds("2026-11-02"), seconds("2026-11-09")]  # nosec


def test_a_failing_apple_calendar_is_a_problem_not_a_crash(mac):
    calendars.apple_connect(True)
    mac.answers["every event of c"] = computer.ComputerError(
        "osascript failed: Not authorized to send Apple events to "
        "Calendar. (-1743)")
    problems = []
    assert calendars.events(problems=problems) == []  # nosec B101
    assert "Automation" in problems[0]  # nosec B101


def test_adding_to_apple_calendar_asks_first(mac, monkeypatch):
    calendars.apple_connect(True)
    mac.answers["make new event"] = f"Home{FS}NEW-1"
    # Even in autonomous mode.
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    asked = []
    with answer_from(lambda q: asked.append(q) or "n"):
        said = tools.calendar_tool("add", start="2026-11-04T15:00",
                                   title="Call Mum")
    assert "said no" in said and "Apple Calendar" in asked[0]  # nosec B101
    assert not any("make new event" in " ".join(a) for a in mac)  # nosec

    with answer_from(lambda q: "y"):
        said = tools.calendar_tool("add", start="2026-11-04T15:00",
                                   title="Call Mum", where="Home")
    assert said.startswith("Added:") and "[apple:Home:NEW-1]" in said  # nosec
    argv = mac[-1]
    # Calendar left out: the first writable one, picked by the script.
    assert argv[-7:] == ["", "Call Mum", seconds("2026-11-04T15:00"),  # nosec
                         seconds("2026-11-04T16:00"), "false", "Home", ""]


def test_apple_calendar_named_but_off_says_so(mac):
    said = tools.calendar_tool("add", start="2026-11-04T15:00", title="X",
                               calendar="apple")
    assert "not switched on" in said  # nosec B101


def test_an_apple_event_is_deleted_by_its_calendar_and_uid(mac):
    calendars.apple_connect(True)
    with answer_from(lambda q: "y"):
        said = tools.calendar_tool("delete", id="apple:Work: Team:UID-9")
    assert said == "Deleted apple:Work: Team:UID-9."  # nosec B101
    assert mac[-1][-2:] == ["Work: Team", "UID-9"]  # nosec B101
    with pytest.raises(calendars.CalendarError, match="cannot change"):
        calendars.update("apple:Work:UID-9", title="New")


def test_new_events_go_to_apple_calendar_when_it_is_the_only_one(mac):
    calendars.apple_connect(True)
    assert calendars._where_to("") == "apple:"  # nosec B101
    assert calendars._where_to("apple:Home") == "apple:Home"  # nosec B101


# --- Reminders --------------------------------------------------------------


def test_reminders_are_offered_only_on_a_mac(monkeypatch, mac):
    def names():
        return {t["function"]["name"] for t in tools.turn_tools()}

    assert "reminders" in names()  # nosec B101
    monkeypatch.setattr(computer, "system", lambda: "linux")
    assert "reminders" not in names()  # nosec B101


def test_reminders_are_listed(mac):
    due = str(reminders._seconds(calendars.parse_time("2026-11-05T09:00")))
    mac.answers["every reminder"] = (
        FS.join(["Groceries", "x-apple-reminder://R1", "Milk", "", "false",
                 ""]) + RS
        + FS.join(["Work", "x-apple-reminder://R2", "Send invoice", due,
                   "false", "to Acme"]) + RS)
    said = tools.reminders_tool("list")
    lines = said.splitlines()
    assert lines[0] == "Milk in Groceries [x-apple-reminder://R1]"  # nosec
    assert lines[1].startswith("Send invoice due 2026-11-05T09:00")  # nosec
    assert mac[-1][-2:] == ["", "false"]  # nosec B101

    tools.reminders_tool("list", done=True, **{"list": "Work"})
    assert mac[-1][-2:] == ["Work", "true"]  # nosec B101


def test_adding_a_reminder_asks_first(mac, monkeypatch):
    mac.answers["make new reminder"] = f"Reminders{FS}x-apple-reminder://N"
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    with answer_from(lambda q: "n"):
        said = tools.reminders_tool("add", title="Water plants")
    assert "said no" in said and len(mac) == 0  # nosec B101

    asked = []
    with answer_from(lambda q: asked.append(q) or "y"):
        said = tools.reminders_tool("add", title="Water plants",
                                    due="2026-11-03T18:00")
    assert "Water plants" in asked[0]  # nosec
    assert "2026-11-03T18:00" in asked[0]  # nosec
    assert said.startswith("Added: Water plants due 2026-11-03T18:00")  # nosec
    due = str(reminders._seconds(calendars.parse_time("2026-11-03T18:00")))
    assert mac[-1][-4:] == ["", "Water plants", "", due]  # nosec B101


def test_completing_a_reminder_asks_first(mac, monkeypatch):
    mac.answers["set completed"] = "Milk"
    with answer_from(lambda q: "n"):
        assert "said no" in tools.reminders_tool(  # nosec B101
            "complete", id="x-apple-reminder://R1")
    assert mac == []  # nosec B101
    monkeypatch.setattr(tools, "ULTRA_AUTO", True)
    with answer_from(lambda q: pytest.fail("asked in UltraAuto")):
        said = tools.reminders_tool("complete", id="x-apple-reminder://R1")
    assert said == "Done: Milk"  # nosec B101
    assert mac[-1][-1] == "x-apple-reminder://R1"  # nosec B101


def test_reminders_not_allowed_says_where(mac):
    mac.answers["every reminder"] = computer.ComputerError(
        "osascript failed: Not authorized to send Apple events to "
        "Reminders. (-1743)")
    assert "Automation" in tools.reminders_tool("list")  # nosec B101


# --- iMessage attachments ---------------------------------------------------


def test_an_imessage_sends_a_file_after_its_text(mac, tmp_path):
    photo = tmp_path / "beach.jpg"
    photo.write_bytes(b"\xff\xd8")
    asked = []
    with answer_from(lambda q: asked.append(q) or "y"):
        said = tools.send_imessage("+1 555 0100", "Look!", str(photo))
    assert said.startswith("Sent to")  # nosec B101
    assert f"Attachment: {photo.resolve()}" in asked[0]  # nosec B101
    sends = [a for a in mac if "Messages" in " ".join(a)]
    assert sends[0][-1] == "Look!"  # nosec B101
    assert "POSIX file" in " ".join(sends[1])  # nosec B101
    assert sends[1][-1] == str(photo.resolve())  # nosec B101


def test_a_file_alone_is_enough(mac, tmp_path):
    photo = tmp_path / "beach.jpg"
    photo.write_bytes(b"\xff\xd8")
    with answer_from(lambda q: "y"):
        said = tools.send_imessage("+1 555 0100", "", str(photo))
    assert said.startswith("Sent to")  # nosec B101
    assert len(mac) == 1 and "POSIX file" in " ".join(mac[0])  # nosec B101


def test_a_missing_or_hidden_file_is_never_asked_about(mac, tmp_path):
    asked = []
    with answer_from(lambda q: asked.append(q) or "y"):
        gone = tools.send_imessage("+1 555 0100", "Hi",
                                   str(tmp_path / "nope.jpg"))
        hidden = tools.send_imessage("+1 555 0100", "Hi", "~/.ssh/id_rsa")
    assert gone.startswith("Error:") and hidden.startswith("Error:")  # nosec
    assert asked == [] and mac == []  # nosec B101


def test_read_imessages_names_the_attachments(mac, monkeypatch, tmp_path):
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
            CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY,
                filename TEXT, mime_type TEXT, transfer_name TEXT);
            CREATE TABLE message_attachment_join (message_id INTEGER,
                                                  attachment_id INTEGER);
            INSERT INTO handle VALUES (1, '+15550102000');
            INSERT INTO chat VALUES (1, '', '+15550102000');
            INSERT INTO chat_handle_join VALUES (1, 1);
            INSERT INTO message VALUES (1, 750000000000000000, 0, 0,
                'Here it is', NULL, 1, 1, 0);
            INSERT INTO chat_message_join VALUES (1, 1);
            INSERT INTO attachment VALUES (1,
                '~/Library/Messages/Attachments/ab/IMG_1.heic',
                'image/heic', 'IMG_1.heic');
            INSERT INTO message_attachment_join VALUES (1, 1);
        """)
    monkeypatch.setattr(computer, "MESSAGES_DB", db)
    monkeypatch.setattr(computer, "_address_book",
                        lambda: {"5550102000": "Sam Lee"})
    said = tools.read_imessages()
    assert "Sam Lee (unread): Here it is [attachment: IMG_1.heic " \
        "(image/heic) at " in said  # nosec B101
    # where the Mac keeps it, said in this system's own path form
    kept = os.path.join("Library", "Messages", "Attachments", "ab",
                        "IMG_1.heic")
    assert f"{os.sep}{kept}]" in said  # nosec
    assert "~" not in said  # nosec B101
