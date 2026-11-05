# pylint: disable=C0114,C0115,C0116

import shutil
import sys
from pathlib import Path

import pytest

from flash import sandbox, tools


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr("flash.notify.ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv(sandbox.SETTING, raising=False)
    monkeypatch.delenv(sandbox.AUTO_ALLOW_SETTING, raising=False)
    monkeypatch.setattr(sandbox, "set_setting",
                        lambda name, on: monkeypatch.setenv(
                            name, "1" if on else "0"))
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)


def test_both_switches_are_off_until_turned_on():
    assert not sandbox.enabled() and not sandbox.auto_allow()  # nosec B101


def test_writes_only_where_it_works_and_never_at_home(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    assert work.resolve() in sandbox.writable(str(work))  # nosec B101
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert tmp_path.resolve() not in sandbox.writable(  # nosec B101
        str(tmp_path))
    assert work.resolve() not in sandbox.writable("/")  # nosec B101


def test_secrets_are_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".flash.env").write_text("KEY=1")
    args = sandbox.bwrap_args(str(tmp_path))
    assert ["--tmpfs", str((tmp_path / ".ssh").resolve())] == args[  # nosec
        args.index("--tmpfs"):args.index("--tmpfs") + 2]
    assert str((tmp_path / ".flash.env").resolve()) in args  # nosec B101
    profile = sandbox.mac_profile(str(tmp_path))
    assert "(deny file-write*)" in profile  # nosec B101
    assert ".ssh" in profile and "deny file-read*" in profile  # nosec B101


def test_with_no_sandbox_here_nothing_runs(monkeypatch):
    monkeypatch.setattr(sandbox, "missing", lambda: "No sandbox here.")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    said = tools.shell_tool("echo hi", sandbox=True)
    assert said.startswith("Error: the command was not run")  # nosec B101
    monkeypatch.setenv(sandbox.SETTING, "1")
    said = tools.shell_tool("echo hi")
    assert "none run until" in said  # nosec B101


def test_ask_first_asks_unless_sandboxed_commands_may_run(monkeypatch):
    asked = []
    monkeypatch.setattr(tools, "remote_answer",
                        lambda q: asked.append(q) or "n")
    monkeypatch.setattr(sandbox, "missing", lambda: "")
    monkeypatch.setattr(sandbox, "wrap",
                        lambda command, folder=None: [
                            "/bin/sh", "-c", command])
    assert tools.shell_tool("echo hi", sandbox=True) == \
        "Command blocked by user"  # nosec B101
    monkeypatch.setenv(sandbox.AUTO_ALLOW_SETTING, "1")
    assert tools.shell_tool("echo hi", sandbox=True) == "hi"  # nosec B101
    # A command outside the sandbox still asks.
    assert tools.shell_tool("echo hi") == "Command blocked by user"  # nosec
    assert len(asked) == 2  # nosec B101
    # With every command in the sandbox, none asks.
    monkeypatch.setenv(sandbox.SETTING, "1")
    assert tools.shell_tool("echo hi") == "hi"  # nosec B101


@pytest.mark.skipif(not shutil.which("bwrap") or sys.platform == "darwin",
                    reason="needs bubblewrap")
def test_a_real_sandbox_walls_the_command_in(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    (tmp_path / "home" / ".ssh").mkdir(parents=True)
    (tmp_path / "home" / ".ssh" / "id_key").write_text("SECRET")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    # Not the folder it works in, nor a temp folder it may write in.
    var_tmp = Path("/var/tmp")  # nosec B108
    outside = var_tmp / f"flash-sandbox-{tmp_path.name}.txt"
    outside.unlink(missing_ok=True)

    said = tools.shell_tool(
        f"echo ok > inside.txt; echo no > {outside}; "
        f"cat {tmp_path}/home/.ssh/id_key; echo done", sandbox=True)
    assert (work / "inside.txt").read_text() == "ok\n"  # nosec B101
    try:
        assert not outside.exists()  # nosec B101
    finally:
        outside.unlink(missing_ok=True)
    assert "SECRET" not in said and "done" in said  # nosec B101
