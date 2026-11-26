"""Shell commands are watched too: what one changes can be put back, by /undo or the time machine."""

import subprocess
import sys

from flash import checkpoint, tools


def run(cmd, cwd):
    w = checkpoint.ShellWatch(cmd, str(cwd))
    subprocess.run(cmd, shell=True, cwd=cwd, check=False)
    return w.finish()


def test_a_command_s_changes_are_undone(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    (tmp_path / "gone.txt").write_text("keep me")
    checkpoint.clear()
    checkpoint.start_turn("tidy up")
    run(f'{sys.executable} -c "import pathlib; pathlib.Path(\'a.txt\').write_text(\'TWO\'); pathlib.Path(\'gone.txt\').unlink(); pathlib.Path(\'new.txt\').write_text(\'x\')"', tmp_path)
    changes = {f["name"]: f["change"] for f in checkpoint.history()[0]["files"]}
    assert changes == {"a.txt": "changed", "gone.txt": "deleted", "new.txt": "made"}
    checkpoint.undo()
    assert (tmp_path / "a.txt").read_text() == "one"
    assert (tmp_path / "gone.txt").read_text() == "keep me"
    assert not (tmp_path / "new.txt").exists()


def test_ignored_folders_are_not_walked(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "big.js").write_text("x")
    checkpoint.clear()
    checkpoint.start_turn("install")
    run(f'{sys.executable} -c "import pathlib; pathlib.Path(\'node_modules/big.js\').write_text(\'y\')"', tmp_path)
    assert checkpoint.history() == []


def test_going_back_to_before_an_earlier_turn_undoes_the_later_ones(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("v1")
    checkpoint.clear()
    for v in ("v2", "v3"):
        checkpoint.start_turn(f"make {v}")
        run(f'{sys.executable} -c "import pathlib; pathlib.Path(\'notes.txt\').write_text(\'{v}\')"', tmp_path)
    assert [t["label"] for t in checkpoint.history()] == ["make v2", "make v3"]
    checkpoint.undo_to(checkpoint.history()[0]["index"])
    assert f.read_text() == "v1"
    assert checkpoint.history() == []


def test_the_shell_tool_watches_what_it_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    (tmp_path / "x.txt").write_text("before")
    checkpoint.clear()
    checkpoint.start_turn("shell")
    tools.shell_tool(f'{sys.executable} -c "open(\'x.txt\', \'w\').write(\'after\')"')
    checkpoint.undo()
    assert (tmp_path / "x.txt").read_text() == "before"
