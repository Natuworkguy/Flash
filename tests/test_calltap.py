# pylint: disable=C0114,C0115,C0116

import sys

import pytest

from flash import calls, calltap


@pytest.fixture
def helper(tmp_path, monkeypatch):
    """A stand-in for the Swift helper: plays a known tone until let go."""

    script = tmp_path / "fake-tap"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, array, threading, os\n"
        "tone = array.array('h', [1000, -1000] * 800).tobytes()\n"
        "def play():\n"
        "    try:\n"
        "        while True:\n"
        "            sys.stdout.buffer.write(tone); sys.stdout.buffer.flush()\n"
        "            import time; time.sleep(0.05)\n"
        "    except (BrokenPipeError, OSError):\n"
        "        pass\n"
        "threading.Thread(target=play, daemon=True).start()\n"
        "sys.stdin.read()\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(calltap, "build", lambda: script)
    return script


def test_the_tap_reads_like_an_input_stream(helper):
    tap = calltap.Tap(16000)
    block, _ = tap.read(1600)
    assert len(block) == 3200 and tap.device_rate == 16000  # nosec B101
    assert tap.read_available >= 0  # nosec B101
    tap.close()
    assert tap._proc.poll() is not None  # nosec B101  pylint: disable=W0212


def test_a_tap_macos_refuses_says_why(tmp_path, monkeypatch):
    script = tmp_path / "refused"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('allow it under System Audio Recording')\nsys.exit(1)\n",
                      encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(calltap, "build", lambda: script)
    with pytest.raises(calltap.TapError, match="System Audio Recording"):
        calltap.Tap(16000)


def test_only_the_mic_is_switched_when_the_call_is_heard_through_the_tap(helper, monkeypatch):
    switched = []
    monkeypatch.setattr(calltap, "usable", lambda: True)
    monkeypatch.setattr(calls, "_switch", lambda kind, name="": switched.append((kind, name)) or "Mac speakers")
    with calls._routed():  # pylint: disable=W0212
        assert calls._tap is not None  # nosec B101  pylint: disable=W0212
    # In: the mic only. Out: put back, the speakers never touched.
    assert [k for k, _ in switched] == ["input", "input"]  # nosec B101
    assert calls._tap is None  # nosec B101  pylint: disable=W0212


def test_without_the_tap_the_speakers_are_switched_as_before(monkeypatch):
    switched = []
    monkeypatch.setattr(calltap, "usable", lambda: False)
    monkeypatch.setattr(calls, "_switch", lambda kind, name="": switched.append((kind, name)) or "Mac's own")
    with calls._routed():  # pylint: disable=W0212
        pass
    assert sorted({k for k, _ in switched}) == ["input", "output"]  # nosec B101


def test_with_the_tap_only_the_mic_loopback_is_needed(monkeypatch):
    monkeypatch.setattr(calls.computer, "system", lambda: "mac")
    monkeypatch.setattr(calltap, "usable", lambda: True)
    monkeypatch.setattr(calls, "_devices", lambda: {calls.to_call()})
    monkeypatch.setattr(calls.shutil, "which", lambda name: "/usr/local/bin/" + name)
    assert calls.missing() == ""  # nosec B101
    assert calls.joins_by_itself()  # nosec B101


def test_missing_devices_open_the_install_in_terminal(monkeypatch):
    opened = []
    monkeypatch.setattr(calls.computer, "system", lambda: "mac")
    monkeypatch.setattr(calltap, "usable", lambda: True)
    monkeypatch.setattr(calls, "_devices", lambda: set())
    monkeypatch.setattr(calls.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)
    monkeypatch.setattr(calls.computer, "_run", lambda args, timeout=0: opened.append(args))
    monkeypatch.setattr(calls, "_setup_opened", False)
    said = calls.missing()
    assert "opened Terminal" in said  # nosec B101
    assert calls.INSTALL_TAPPED in opened[0][2]  # nosec B101


def test_off_a_new_enough_mac_there_is_no_tap(monkeypatch):
    monkeypatch.setattr(calltap.platform, "system", lambda: "Linux")
    assert not calltap.usable()  # nosec B101
    monkeypatch.setattr(calltap.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(calltap.platform, "mac_ver", lambda: ("13.6", ("", "", ""), ""))
    assert not calltap.usable()  # nosec B101
