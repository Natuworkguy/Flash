# pylint: disable=C0114,C0116

import os

import pytest

from flash import (
    ai,
    extensions,
    learning,
    memory,
    repl_input,
    skills,
    sparks,
    terminal,
    tools,
    voice,
    workspace,
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
    # Sparks, and what this session has already pointed out about them.
    monkeypatch.setattr(sparks, "FLASH_DIR", home / ".flash")
    monkeypatch.setattr(sparks, "_told", set())
    # The web UI's saved hosts, projects, and chats.
    monkeypatch.setattr(workspace, "FLASH_DIR", home / ".flash")
    # Voice settings and models too: the developer's own picks, loaded
    # from ~/.flash.env when ai was imported, would otherwise decide
    # which model a test expects. And a setting a test saves lands in
    # the temp home, never in the developer's real env file.
    for name in [n for n in os.environ if n.startswith("VOICE_")]:
        monkeypatch.delenv(name)
    monkeypatch.setattr(voice, "MODELS_DIR", home / ".flash" / "models")
    monkeypatch.setattr(ai, "ENV_PATH", str(home / ".flash.env"))
    # A shift reads autonomous mode from the env file: the temp one.
    monkeypatch.setattr(sparks, "ENV_PATH", str(home / ".flash.env"))
    learning.refresh()
    learning.reset()
    yield home
    extensions.reload()
    learning.refresh()
    learning.reset()


@pytest.fixture(autouse=True)
def fresh_shift_rounds(monkeypatch):
    """Rounds a test sets for a shift go with it: setting them writes the
    process's environment too."""

    monkeypatch.delenv("SPARK_SHIFT_ROUNDS", raising=False)
    monkeypatch.delenv("SPARK_SHIFT_UNLIMITED", raising=False)


@pytest.fixture(autouse=True)
def no_spark_keeper(monkeypatch):
    """No test starts the thread that runs sparks' shifts.

    ai.main() and the web server start it, and once running it would
    outlive the test and work on sparks a later test makes. A test runs
    a shift itself, with sparks.shift.
    """

    monkeypatch.setattr(sparks, "start", lambda: None)
    # Nor does a web session an earlier test made hear its sparks change.
    monkeypatch.setattr(sparks, "_listeners", [])
    yield
    # A test that ran the keeper lets its lock go with it.
    sparks._give_floor()


@pytest.fixture(autouse=True)
def no_login_service(monkeypatch):
    """No test registers a real service or starts a real keeper.

    Every system command keepalive runs goes through _ok, and the one
    process it starts itself through _spawn: both do nothing here. A
    test that wants to see what they were asked to do patches them.
    """

    from flash import keepalive

    monkeypatch.setattr(keepalive, "_ok", lambda args: False)
    monkeypatch.setattr(keepalive, "_spawn", lambda: None)


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
