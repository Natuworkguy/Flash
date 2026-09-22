# pylint: disable=C0114,C0116

import pytest

from flash import terminal


@pytest.fixture(autouse=True)
def outside_vscode(monkeypatch):
    """Tests never drive the developer's real VS Code.

    Run from VS Code's terminal, TERM_PROGRAM=vscode would let any test
    that reaches a write confirmation open a real diff tab.
    """

    monkeypatch.delenv("TERM_PROGRAM", raising=False)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """The terminal log, hook scripts, and shell rc files live in a temp
    home, so no test reads or edits the developer's real ones. It sits
    apart from tmp_path, which some tests list as a working directory."""

    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(terminal, "FLASH_DIR", home / ".flash")
    monkeypatch.setattr(terminal, "LOG_PATH", home / ".flash" / "terminal.log")
    return home
