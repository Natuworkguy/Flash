"""The user's presence, as sparks see it: each platform read mocked."""

import pytest

from flash import presence


@pytest.mark.parametrize("idle, locked, word", [
    (5, False, "online"),
    (119, None, "online"),
    (120, False, "idle"),
    (None, False, "unknown"),
    (None, None, "unknown"),
    (3, True, "offline"),
    (None, True, "offline"),
])
def test_the_word_for_idle_time_and_the_lock(idle, locked, word):
    assert presence.status_from(idle, locked) == word


def _outputs(monkeypatch, outputs):
    def run(*args):
        for start, out in outputs.items():
            if " ".join(args).startswith(start):
                return out
        return ""
    monkeypatch.setattr(presence, "_run", run)


def test_macos_reads_ioreg(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "darwin")
    _outputs(monkeypatch, {
        "ioreg -c IOHIDSystem": '  |   "HIDIdleTime" = 150000000000\n',
        "ioreg -n Root": (
            '"IOConsoleUsers" = ({"CGSSessionScreenIsLocked"=No})'
        ),
    })
    monkeypatch.setitem(__import__("sys").modules, "Quartz", None)
    assert presence.user_status() == {"status": "idle", "idle_seconds": 150}


def test_macos_locked_is_offline(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "darwin")
    _outputs(monkeypatch, {
        "ioreg -n Root": (
            '"IOConsoleUsers" = ({"CGSSessionScreenIsLocked"=Yes})'
        ),
    })
    monkeypatch.setitem(__import__("sys").modules, "Quartz", None)
    assert presence.user_status()["status"] == "offline"


def test_linux_reads_loginctl_and_xprintidle(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_ID", "2")
    _outputs(monkeypatch, {"loginctl": "no\n", "xprintidle": "4200\n"})
    assert presence.user_status() == {"status": "online", "idle_seconds": 4}

    _outputs(monkeypatch, {"loginctl": "yes\n"})
    assert presence.user_status()["status"] == "offline"


def test_linux_falls_back_to_dbus_then_unknown(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "linux")
    _outputs(monkeypatch, {
        "gdbus call --session --dest org.freedesktop.ScreenSaver": "(false,)",
        "gdbus call --session --dest org.gnome.Mutter": "(uint64 300000,)",
    })
    assert presence.user_status()["status"] == "idle"

    _outputs(monkeypatch, {})
    assert presence.user_status() == {
        "status": "unknown", "idle_seconds": None,
    }


def test_windows_uses_its_readers(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "win32")
    monkeypatch.setattr(presence, "_win_idle", lambda: 30.0)
    monkeypatch.setattr(presence, "_win_locked", lambda: False)
    assert presence.user_status()["status"] == "online"
    monkeypatch.setattr(presence, "_win_locked", lambda: True)
    assert presence.user_status()["status"] == "offline"


def test_a_reader_that_breaks_is_unknown(monkeypatch):
    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(presence, "_readers", lambda: (boom, boom))
    assert presence.user_status()["status"] == "unknown"
    assert "unknown" in presence.presence_line()
