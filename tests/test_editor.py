# pylint: disable=C0114,C0115,C0116

import pytest

from flash import editor, tools


@pytest.fixture
def vscode(monkeypatch):
    """Pretend Flash runs in VS Code's terminal; record every launch."""

    launched = []
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/bin/code")
    monkeypatch.setattr(
        editor.subprocess, "Popen",
        lambda argv, **kwargs: launched.append(argv),
    )
    return launched


def test_only_inside_vscodes_terminal(monkeypatch):
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/bin/code")
    assert not editor.available()  # nosec B101
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert not editor.available()  # nosec B101
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    assert editor.available()  # nosec B101
    monkeypatch.setattr(editor.shutil, "which", lambda name: None)
    assert not editor.available()  # nosec B101


def test_nothing_launches_outside_vscode(monkeypatch, tmp_path):
    monkeypatch.setattr(editor.subprocess, "Popen", lambda *a, **k: 1 / 0)
    assert not editor.open_at("x.py", 3)  # nosec B101
    assert not editor.show_diff("a", "b", "x.py", str(tmp_path))  # nosec


def test_open_at_a_line(vscode, tmp_path):
    target = tmp_path / "app.py"

    assert editor.open_at(str(target), 42)  # nosec B101
    assert editor.open_at(str(target))  # nosec B101

    assert vscode == [  # nosec B101
        ["/bin/code", "--reuse-window", "--goto", f"{target}:42"],
        ["/bin/code", "--reuse-window", str(target)],
    ]


def test_show_diff_writes_both_sides(vscode, tmp_path):
    assert editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))

    before = tmp_path / "app (before).py"
    after = tmp_path / "app (Flash's edit).py"
    assert (before.read_text(), after.read_text()) == ("old\n", "new\n")
    assert vscode == [[  # nosec B101
        "/bin/code", "--reuse-window", "--diff", str(before), str(after),
    ]]


def test_a_failed_launch_is_just_false(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/bin/code")

    def broken(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(editor.subprocess, "Popen", broken)
    assert not editor.open_at("x.py")  # nosec B101


# --- in the write tool -----------------------------------------------------


def test_a_confirmed_write_opens_the_diff_first(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "keep.txt"
    target.write_text("original\n")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(tools, "notify_needs_input", lambda: None)
    monkeypatch.setattr("builtins.input", lambda: "n")

    result = tools.write_tool(str(target), "replaced\n")

    assert "blocked by user" in result  # nosec B101
    assert target.read_text() == "original\n"  # nosec B101
    (argv,) = vscode
    assert argv[2] == "--diff"  # nosec B101
    assert argv[3].endswith("keep (before).txt")  # nosec B101


def test_no_diff_without_a_question_or_a_change(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "same.txt"
    target.write_text("same\n")
    monkeypatch.setattr(tools, "notify_needs_input", lambda: None)
    monkeypatch.setattr("builtins.input", lambda: "y")

    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    tools.write_tool(str(target), "same\n")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    tools.write_tool(str(target), "changed\n")

    assert vscode == []  # nosec B101


# --- the open_in_editor tool -----------------------------------------------


def test_open_in_editor_tool(vscode, tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n")

    assert tools.open_in_editor(str(target), 1) == (  # nosec B101
        f"Opened {target} at line 1."
    )
    assert tools.open_in_editor(str(tmp_path / "missing.py")).startswith(
        "Error"
    )  # nosec B101
    assert len(vscode) == 1  # nosec B101


def test_open_in_editor_outside_vscode(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n")
    assert "not available" in tools.open_in_editor(str(target), "7")


def test_the_tool_is_only_offered_inside_vscode(monkeypatch):
    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert "open_in_editor" not in names  # nosec B101

    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/bin/code")
    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert "open_in_editor" in names  # nosec B101
    assert "shell" in names  # nosec B101


def test_no_temp_files_outside_vscode(tmp_path):
    assert not editor.show_diff("a", "b", "x.py", str(tmp_path))  # nosec
    assert list(tmp_path.iterdir()) == []  # nosec B101
