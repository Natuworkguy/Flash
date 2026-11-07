"""Tests for Tab completion of the flash command (argcomplete)."""

# pylint: disable=C0116

import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flash import cli

ROOT = Path(cli.__file__).resolve().parent.parent


def offered(prefix, command):
    return cli._targets(prefix, SimpleNamespace(command=command))


def test_the_shells_are_offered_for_completion():
    assert offered("", "completion") == list(cli.SHELLS)
    assert offered("z", "completion") == ["zsh"]


def test_where_an_extension_comes_from_is_offered():
    assert offered("", "install") == ["github@", "git@", "path@"]
    assert offered("gi", "install") == ["github@", "git@"]


def test_folders_are_offered_after_path(tmp_path, monkeypatch):
    (tmp_path / "my-ext").mkdir()
    (tmp_path / "my-notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    folder = tmp_path.as_posix()

    assert offered(f"path@{folder}/my", "install") == [
        f"path@{folder}/my-ext/", f"path@{folder}/my-notes.txt"]
    assert offered(f"path@{folder}/.h", "install") == [
        f"path@{folder}/.hidden/"]
    monkeypatch.chdir(tmp_path)
    assert offered("path@my-e", "install") == ["path@my-ext/"]
    assert offered("path@/no/such/place/", "install") == []


def test_installed_extensions_are_offered_to_update_or_remove(tmp_path):
    from tests.test_extensions import install, make

    install(tmp_path, make(tmp_path / "src", {
        "name": "standup", "prompt": "p.md"}, {"p.md": "Say hi."}))

    assert offered("st", "update") == ["standup"]
    assert offered("", "remove") == ["standup"]
    assert offered("x", "remove") == []


def test_completion_takes_a_shell():
    args = cli.parse_args(["completion", "zsh"])

    assert args.command == "completion" and args.target == "zsh"
    assert "unexpected" not in str(args)


def test_the_hook_for_each_shell_names_flash():
    pytest.importorskip("argcomplete")

    for shell in ("bash", "zsh", "fish"):
        assert "flash" in cli.completion_script(shell)
    with pytest.raises(SystemExit, match="One of: bash"):
        cli.completion_script("nope")


def _tab(line, tmp_path):
    """What the shell is offered for LINE, asked as the shell's hook
    asks: argcomplete's answer in a file."""

    out = tmp_path / "offered"
    env = {**os.environ, "_ARGCOMPLETE": "1", "COMP_LINE": line,
           "COMP_POINT": str(len(line)), "_ARGCOMPLETE_IFS": "\v",
           "COMP_TYPE": "9", "PYTHONPATH": str(ROOT),
           "_ARGCOMPLETE_STDOUT_FILENAME": str(out)}
    subprocess.run(  # nosec B603
        [sys.executable, "-m", "flash"], env=env, cwd=ROOT, timeout=60,
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Each offer as the shell takes it, without the space after a
    # whole word.
    return [w.rstrip() for w in out.read_text(encoding="utf-8")
            .split("\v")] if out.exists() else []


@pytest.mark.skipif(os.name == "nt", reason="bash and zsh ask this way")
def test_a_tab_in_the_shell_is_answered(tmp_path):
    pytest.importorskip("argcomplete")
    from importlib.metadata import version

    if tuple(int(p) for p in version("argcomplete").split(".")[:2]) < (3, 5):
        pytest.skip("this argcomplete predates this Python's argparse")

    assert _tab("flash ", tmp_path)[:4] == [
        "install", "update", "remove", "list"]
    assert _tab("flash --w", tmp_path) == ["--web"]
    assert _tab("flash completion f", tmp_path) == ["fish"]


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh is not here")
def test_the_zsh_hook_works_without_a_completion_system(tmp_path):
    """A .zshrc with no compinit (no oh-my-zsh or the like): the hook
    starts zsh's completion system itself, so compdef is there."""

    pytest.importorskip("argcomplete")
    hook = tmp_path / "hook.zsh"
    hook.write_text(cli.completion_script("zsh"), encoding="utf-8")

    done = subprocess.run(  # nosec B603 B607
        ["zsh", "-f", "-c", f"source {hook} && print -r -- $_comps[flash]"],
        capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "HOME": str(tmp_path)})

    assert "compdef" not in done.stderr
    assert done.stdout.strip() == "_python_argcomplete"
