from pathlib import Path

import pytest

from flash import cli, uninstall


def test_uninstall_is_a_command():
    args = cli.parse_args(["uninstall", "--purge"])
    assert args.command == "uninstall" and args.purge


def test_purge_needs_uninstall():
    with pytest.raises(SystemExit):
        cli.parse_args(["--purge"])
    with pytest.raises(SystemExit):
        cli.parse_args(["uninstall", "--purge", "--keep-data"])


def test_package_command():
    assert uninstall.package_command("pipx")[1:] == ["uninstall", "flash"]
    assert uninstall.package_command("pip")[-2:] == ["-y", "flash"]
    assert uninstall.package_command("checkout") is None


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    data = tmp_path / ".flash"
    data.mkdir()
    (data / "chats.json").write_text("[]")
    monkeypatch.setattr(uninstall, "DATA", data)
    monkeypatch.setattr(uninstall, "_services", lambda say: None)
    monkeypatch.setattr(uninstall, "_scheme", lambda: "")
    monkeypatch.setattr(uninstall, "_shortcut", lambda: "")
    return data


def test_keeps_data_unless_asked(quiet):
    said = []
    uninstall.run(purge=None, say=said.append, ask=lambda q: False,
                  package=False)
    assert quiet.exists()
    assert any("Kept" in line for line in said)


def test_purge_deletes_data(quiet):
    said = []
    uninstall.run(purge=True, say=said.append, ask=lambda q: False,
                  package=False)
    assert not quiet.exists()


def test_a_failed_step_does_not_stop_the_rest(quiet, monkeypatch):
    def broken():
        raise OSError("nope")
    monkeypatch.setattr(uninstall, "_scheme", broken)
    said = []
    uninstall.run(purge=True, say=said.append, ask=lambda q: True,
                  package=False)
    assert any("could not" in line for line in said)
    assert not quiet.exists()
