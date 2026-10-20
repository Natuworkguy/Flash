"""Tests for installing a package Flash needs (installer)."""

import sys
import threading
import time

import pytest

from flash import installer, web


class FakeRun:
    """Stands in for subprocess.Popen: prints LINES, exits with CODE,
    holding at GATE when given one."""

    def __init__(self, lines=("Collecting it", "Installed it"), code=0,
                 gate=None):
        self.lines, self.code, self.gate = list(lines), code, gate
        self.ran = []

    def __call__(self, command, **_):
        self.ran.append(command)
        run = self

        class Running:
            @property
            def stdout(self):
                if run.gate is not None:
                    run.gate.wait(5)
                return iter(line + "\n" for line in run.lines)

            def wait(self):
                return run.code

        return Running()


def until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"  # nosec B101
        time.sleep(0.01)


def test_it_installs_into_this_flash_and_says_every_line(monkeypatch):
    monkeypatch.setattr(installer, "_pipx", lambda: None)
    told, run = [], FakeRun()
    found = installer.Installer(told.append, run=run)

    found.start(["kokoro-onnx"])
    until(lambda: found.ok is not None)

    assert run.ran == [[sys.executable, "-m", "pip", "install",  # nosec
                        "kokoro-onnx"]]
    assert found.ok is True and not found.running  # nosec B101
    lines = [t["line"] for t in told if "line" in t]
    assert lines[1:3] == ["Collecting it", "Installed it"]  # nosec B101
    assert told[-1] == {"state": "done", "ok": True,  # nosec B101
                        "packages": ["kokoro-onnx"]}


def test_a_pipx_flash_has_it_injected_and_playwright_its_browser(
        monkeypatch):
    monkeypatch.setattr(installer, "_pipx", lambda: "/usr/bin/pipx")

    steps = installer.commands(["playwright"])

    assert steps[0] == ["/usr/bin/pipx", "inject", "flash",  # nosec B101
                        "playwright"]
    assert steps[1][-2:] == ["install", "chromium"]  # nosec B101


def test_a_failure_is_said_and_its_logs_kept(monkeypatch):
    monkeypatch.setattr(installer, "_pipx", lambda: None)
    told = []
    found = installer.Installer(
        told.append, run=FakeRun(["error: no matching distribution"], 1))

    found.start(["pypdf"])
    until(lambda: found.ok is not None)

    assert found.ok is False  # nosec B101
    assert "error: no matching distribution" in found.lines  # nosec B101
    assert any("exit code 1" in line for line in found.lines)  # nosec B101
    assert told[-1]["ok"] is False  # nosec B101


@pytest.mark.parametrize("asked", [["requests"], ["vosk; rm -rf /"], []])
def test_only_what_flash_needs_can_be_installed(asked):
    found = installer.Installer(lambda said: None, run=FakeRun())

    with pytest.raises(installer.InstallError):
        found.start(asked)


def test_one_at_a_time(monkeypatch):
    monkeypatch.setattr(installer, "_pipx", lambda: None)
    gate = threading.Event()
    found = installer.Installer(lambda said: None, run=FakeRun(gate=gate))
    found.start(["vosk"])

    with pytest.raises(installer.InstallError, match="already"):
        found.start(["pypdf"])
    gate.set()
    until(lambda: found.ok is not None)


def test_the_page_starts_one_and_hears_how_it_goes(monkeypatch):
    monkeypatch.setattr(installer, "_pipx", lambda: None)
    session = web.Session()
    seen = []
    session.hub.publish = lambda event: seen.append(event) or event
    session.installer._run = FakeRun()

    started = web.command(session, {"name": "install",
                                    "packages": ["sounddevice"]})
    assert started["running"] is True  # nosec B101
    until(lambda: session.installer.ok is not None)

    assert web.command(session, {"name": "install-status"})["ok"] is True
    assert {"type": "install", "state": "done", "ok": True,  # nosec B101
            "packages": ["sounddevice"]} in seen
    with pytest.raises(ValueError):
        web.command(session, {"name": "install", "arg": "requests"})
