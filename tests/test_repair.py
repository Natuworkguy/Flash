# pylint: disable=C0114,C0116

import sys

import pytest

from flash import __main__ as entry
from flash import installer


def missing(name):
    error = ModuleNotFoundError(f"No module named '{name}'")
    error.name = name
    return error


def test_a_missing_requirement_is_installed_and_flash_starts_again(
        monkeypatch):
    ran, started = [], []
    monkeypatch.setattr(installer, "_pipx", lambda: None)
    monkeypatch.delenv(entry.REPAIRED, raising=False)

    # The same subprocess.call installs, then starts Flash again.
    def call(command, env=None):
        (started.append((command, env)) if env else ran.append(command))
        return 0

    monkeypatch.setattr(installer.subprocess, "call", call)
    monkeypatch.setattr(sys, "argv", ["flash", "--web", "--lan"])

    with pytest.raises(SystemExit):
        entry._repair(missing("segno"))

    assert ran[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert "segno" in ran[0] and "rich" in ran[0]
    command, env = started[0]
    assert command == [sys.executable, "-m", "flash", "--web", "--lan"]
    assert env[entry.REPAIRED] == "1"


def test_not_flashs_or_already_tried_is_raised(monkeypatch):
    monkeypatch.setattr(installer.subprocess, "call",
                        lambda command: pytest.fail("installed"))
    with pytest.raises(ModuleNotFoundError):
        entry._repair(missing("numpy"))
    monkeypatch.setenv(entry.REPAIRED, "1")
    with pytest.raises(ModuleNotFoundError):
        entry._repair(missing("segno"))


def test_names_that_differ_from_their_package():
    assert installer.requirement_for("dotenv.main") == "python-dotenv"
    assert installer.requirement_for("docx") == "python-docx"
