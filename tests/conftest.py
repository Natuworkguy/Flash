# pylint: disable=C0114,C0116

import pytest

from flash import (
    extensions,
    learning,
    memory,
    repl_input,
    skills,
    terminal,
    tools,
)


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
    # Windows finds the home folder through USERPROFILE and ignores HOME.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(terminal, "FLASH_DIR", home / ".flash")
    monkeypatch.setattr(terminal, "LOG_PATH", home / ".flash" / "terminal.log")
    # Every turn's tool list reads the installed extensions, so without
    # this the suite would pick up whatever the developer has installed.
    monkeypatch.setattr(extensions, "FLASH_DIR", home / ".flash")
    extensions.reload()
    monkeypatch.setattr(
        repl_input, "HISTORY_PATH", home / ".flash" / "history"
    )
    # The system prompt carries saved memory and the skill list, so
    # both have to come from the temp home too.
    monkeypatch.setattr(memory, "MEMORY_PATH", home / ".flash_memory.md")
    monkeypatch.setattr(skills, "FLASH_DIR", home / ".flash")
    learning.refresh()
    learning.reset()
    yield home
    extensions.reload()
    learning.refresh()
    learning.reset()


@pytest.fixture(autouse=True)
def no_leftover_bang_commands(monkeypatch):
    """Each test starts with no `!` commands waiting to be attached."""

    monkeypatch.setattr(tools, "_user_runs", [])


@pytest.fixture(autouse=True)
def no_developer_config(monkeypatch):
    """No test inherits the confirmation setting from a real ~/.flash.env.

    `flash.ai` calls load_dotenv at import, which puts the developer's
    own settings into os.environ, and any later Config.refresh() copies
    NO_COMMAND_CONFIRMATION from there onto the module global in
    flash.tools. Nothing puts it back, so whether a test that reaches a
    confirmation prompt blocks on stdin came down to whose machine the
    suite was running on. Pinned to the module default here: a test
    that wants autonomous mode, or a background, asks for it.
    """

    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)

    # The same leak reaches every other setting in that file, so the
    # ones that change what a test does are cleared too.
    for name in ("NO_COMMAND_CONFIRMATION", "BACKGROUND"):
        monkeypatch.delenv(name, raising=False)

    # No test starts a background learning review against a real model
    # by running enough turns; one that wants a review asks for it.
    monkeypatch.setenv("SKILL_REVIEW_AFTER", "0")
    monkeypatch.setenv("MEMORY_REVIEW_EVERY", "0")

    from flash.ai import Config

    monkeypatch.setattr(Config, "background", "")
