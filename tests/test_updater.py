# pylint: disable=C0114,C0115,C0116

import sys

from flash import updater
from flash.updater import (
    check_for_update,
    fetch_latest_version,
    is_newer,
    perform_update,
)
from flash.version import __version__


def test_is_newer():
    assert is_newer("9.9.9", current="0.1.0")  # nosec B101
    assert not is_newer("0.1.0", current="0.1.0")  # nosec B101
    assert not is_newer("0.0.9", current="0.1.0")  # nosec B101


def test_is_newer_handles_non_semver():
    assert is_newer("weird-tag", current="0.1.0")  # nosec B101
    assert not is_newer(__version__, current=__version__)  # nosec B101


def test_fetch_latest_version_network_failure(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise OSError("no network")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert fetch_latest_version() is None  # nosec B101


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "flash.updater.fetch_latest_version", lambda: __version__
    )
    assert check_for_update() is None  # nosec B101


def test_check_for_update_available(monkeypatch):
    monkeypatch.setattr(
        "flash.updater.fetch_latest_version", lambda: "99.0.0"
    )
    assert check_for_update() == "99.0.0"  # nosec B101


def _fake_tools(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        updater.tempfile, "mkdtemp", lambda **_kw: "/tmp/flash"  # nosec B108
    )
    monkeypatch.setattr(updater.shutil, "rmtree", lambda *_a, **_kw: None)


def test_perform_update_streams_every_step(monkeypatch):
    _fake_tools(monkeypatch)
    commands = []

    def stream(command, on_output=None):
        commands.append(command[:2])
        if on_output is not None:
            on_output(f"{command[0]} says something")

        return 0, "fine"

    monkeypatch.setattr(updater, "_stream", stream)
    steps = []
    logged = []

    ok, message = perform_update(on_step=steps.append, on_output=logged.append)

    assert ok  # nosec B101
    assert "Restart flash" in message  # nosec B101
    assert commands == [  # nosec B101
        ["pipx", "uninstall"], ["git", "clone"], ["pipx", "install"],
    ]
    assert len(steps) == 3  # nosec B101
    # The log is what the user watches; every command has to reach it.
    assert len(logged) == 3  # nosec B101


def test_perform_update_survives_a_missing_old_install(monkeypatch):
    _fake_tools(monkeypatch)

    def stream(command, on_output=None):
        if command[:2] == ["pipx", "uninstall"]:
            return 1, "Nothing to uninstall for flash"

        return 0, ""

    monkeypatch.setattr(updater, "_stream", stream)

    ok, _message = perform_update()

    assert ok  # nosec B101


def test_perform_update_reports_what_the_failing_command_said(monkeypatch):
    _fake_tools(monkeypatch)

    def stream(command, on_output=None):
        if command[0] == "git":
            return 128, "Cloning into ...\nfatal: repository not found"

        return 0, ""

    monkeypatch.setattr(updater, "_stream", stream)

    ok, message = perform_update()

    assert not ok  # nosec B101
    assert "Could not download the update" in message  # nosec B101
    assert "exit code 128" in message  # nosec B101
    assert "fatal: repository not found" in message  # nosec B101


def test_perform_update_needs_git(monkeypatch):
    monkeypatch.setattr(
        updater.shutil, "which", lambda name: None if name == "git" else "/bin"
    )

    ok, message = perform_update()

    assert not ok  # nosec B101
    assert "git is required" in message  # nosec B101


def test_stream_collects_output_and_the_exit_code():
    script = "import sys; print('hello'); sys.exit(2)"
    lines = []

    code, output = updater._stream(
        [sys.executable, "-c", script], lines.append
    )

    assert code == 2  # nosec B101
    assert lines == ["hello"]  # nosec B101
    assert output == "hello"  # nosec B101
