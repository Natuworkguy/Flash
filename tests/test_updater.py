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


def _record_commands(monkeypatch):
    """Capture every command perform_update runs, all of them passing."""

    commands = []

    def stream(command, on_output=None):
        commands.append(command[:2])
        if on_output is not None:
            on_output(f"{command[0]} says something")

        return 0, "fine"

    monkeypatch.setattr(updater, "_stream", stream)

    return commands


def _fake_posix(monkeypatch):
    """A machine that can replace flash's files while flash is running.

    Windows cannot, so it defers the whole install to another process
    and never reaches these steps. Without this the test asserts the
    POSIX path while running the Windows one.
    """

    _fake_tools(monkeypatch)
    monkeypatch.setattr(updater.os, "name", "posix")


def test_perform_update_streams_every_step(monkeypatch):
    _fake_posix(monkeypatch)
    commands = _record_commands(monkeypatch)
    steps = []
    logged = []

    ok, message = perform_update(on_step=steps.append, on_output=logged.append)

    assert ok  # nosec B101
    assert "Restart flash" in message  # nosec B101
    assert commands == [  # nosec B101
        ["git", "clone"],
        ["pipx", "install"],
        ["pipx", "inject"],
        ["pipx", "runpip"],
    ]
    assert len(steps) == 3  # nosec B101
    # The log is what the user watches; every command has to reach it.
    assert len(logged) == 4  # nosec B101


def test_perform_update_never_uninstalls_the_running_flash(monkeypatch):
    # Uninstalling first deletes the executable that is running the
    # uninstall, which Windows refuses outright. `install --force`
    # already reinstalls over the old version.
    _fake_posix(monkeypatch)
    commands = _record_commands(monkeypatch)

    perform_update()

    assert ["pipx", "uninstall"] not in commands  # nosec B101


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


def _fake_windows(monkeypatch):
    """A machine where flash cannot replace its own running files."""

    _fake_tools(monkeypatch)
    monkeypatch.setattr(updater.os, "name", "nt")
    scheduled = []
    monkeypatch.setattr(updater, "_detached", scheduled.append)

    return scheduled


def test_perform_update_on_windows_defers_the_install(monkeypatch):
    scheduled = _fake_windows(monkeypatch)
    commands = _record_commands(monkeypatch)

    ok, message = perform_update()

    assert ok  # nosec B101
    # The clone happens now; the install cannot, so it is handed off.
    assert commands == [["git", "clone"]]  # nosec B101
    assert len(scheduled) == 1  # nosec B101
    assert "quit" in message  # nosec B101


def test_windows_handoff_waits_for_flash_then_installs(monkeypatch):
    scheduled = _fake_windows(monkeypatch)
    _record_commands(monkeypatch)

    perform_update()

    script = scheduled[0][-1]

    assert str(updater.os.getpid()) in script  # nosec B101
    assert "Wait-Process" in script  # nosec B101
    # The launcher that started this python outlives it and holds the
    # same files open, so waiting on this pid alone is not enough.
    assert "Get-Process -Name flash" in script  # nosec B101
    assert "install --force" in script  # nosec B101
    # The clone is the deferred install's input, so it has to survive
    # this process and be cleaned up by the handoff instead.
    assert "Remove-Item" in script  # nosec B101


def test_perform_update_on_windows_keeps_the_clone(monkeypatch):
    _fake_windows(monkeypatch)
    _record_commands(monkeypatch)
    removed = []
    monkeypatch.setattr(
        updater.shutil, "rmtree", lambda path, **_kw: removed.append(path)
    )

    perform_update()

    assert removed == []  # nosec B101


def test_perform_update_on_windows_needs_powershell(monkeypatch):
    _fake_windows(monkeypatch)
    _record_commands(monkeypatch)
    monkeypatch.setattr(
        updater.shutil,
        "which",
        lambda name: (
            None if name in ("powershell", "pwsh") else f"/bin/{name}"
        ),
    )

    ok, message = perform_update()

    assert not ok  # nosec B101
    assert "PowerShell" in message  # nosec B101
    assert "install.ps1" in message  # nosec B101


def test_quoted_escapes_a_path_with_a_quote_in_it():
    assert updater._quoted(r"C:\it's here") == r"'C:\it''s here'"  # nosec B101


def test_runpip_passes_install_straight_to_pip():
    # `pipx runpip <venv> ...` forwards its arguments to pip, so a `pip`
    # in front of `install` reaches pip as a subcommand it does not
    # have: `ERROR: unknown command "pip"`. install.sh shipped that for
    # a while and printed a voice failure on every single install.
    inject, runpip = updater._voice_commands()

    assert runpip[:4] == ["pipx", "runpip", "flash", "install"]  # nosec B101
    assert "pip" not in runpip[3:]  # nosec B101
    assert inject[:3] == ["pipx", "inject", "flash"]  # nosec B101


def test_voice_commands_cover_every_voice_package():
    for command in updater._voice_commands():
        for package in updater.VOICE_PACKAGES:
            assert package in command  # nosec B101


def test_voice_reinstall_is_forced_not_just_injected():
    # `inject` leaves a package that is already in the venv at whatever
    # version it was, so without this the update never moves one.
    _inject, runpip = updater._voice_commands()

    assert "--upgrade" in runpip  # nosec B101
    assert "--force-reinstall" in runpip  # nosec B101


def test_perform_update_puts_the_voice_packages_back(monkeypatch):
    # `pipx install --force` rebuilds the venv, so every injected
    # package is gone by the time the update finishes.
    _fake_posix(monkeypatch)
    ran = []

    def stream(command, on_output=None):
        ran.append(command)
        return 0, ""

    monkeypatch.setattr(updater, "_stream", stream)

    ok, message = perform_update()

    assert ok  # nosec B101
    assert ran[-2:] == updater._voice_commands()  # nosec B101
    assert "voice" not in message.lower()  # nosec B101


def test_perform_update_survives_a_voice_failure(monkeypatch):
    # Voice is optional. A working update never gets reported as a
    # failure because a microphone package would not build.
    _fake_posix(monkeypatch)

    def stream(command, on_output=None):
        if command[:2] == ["pipx", "inject"]:
            return 1, "could not build wheel for piper-tts"

        return 0, ""

    monkeypatch.setattr(updater, "_stream", stream)

    ok, message = perform_update()

    assert ok  # nosec B101
    assert "Restart flash" in message  # nosec B101
    assert "voice mode stays unavailable" in message.lower()  # nosec B101


def test_perform_update_stops_at_the_inject_that_failed(monkeypatch):
    _fake_posix(monkeypatch)
    ran = []

    def stream(command, on_output=None):
        ran.append(command[:2])
        return (1, "boom") if command[:2] == ["pipx", "inject"] else (0, "")

    monkeypatch.setattr(updater, "_stream", stream)

    perform_update()

    assert ["pipx", "runpip"] not in ran  # nosec B101


def test_windows_handoff_puts_the_voice_packages_back(monkeypatch):
    scheduled = _fake_windows(monkeypatch)
    _record_commands(monkeypatch)

    perform_update()

    script = scheduled[0][-1]

    forced = "runpip flash install --upgrade --force-reinstall"

    assert "inject flash" in script  # nosec B101
    assert forced in script  # nosec B101
    for package in updater.VOICE_PACKAGES:
        assert package in script  # nosec B101


def test_windows_handoff_only_restores_voice_after_a_good_install(
    monkeypatch,
):
    # A failed install leaves no venv to inject into, and the voice
    # step's own exit code must not overwrite the one the user is shown.
    scheduled = _fake_windows(monkeypatch)
    _record_commands(monkeypatch)

    perform_update()

    script = scheduled[0][-1]

    assert script.index("$code = $LASTEXITCODE") < script.index(  # nosec B101
        "inject flash"
    )
    assert "if ($code -eq 0) {" in script  # nosec B101
