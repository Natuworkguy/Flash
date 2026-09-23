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


# --- closing the diff once it has been answered ----------------------------


@pytest.fixture(autouse=True)
def fresh_diff_state(monkeypatch, tmp_path_factory):
    """No diff carried over, and the hint unsaid, for every test.

    The settings file is pointed somewhere empty rather than stubbing
    `closes_deleted_editors`, so the tests that exercise the real
    reader can just repoint it without having to undo a stub.
    """

    missing = tmp_path_factory.mktemp("novscode") / "settings.json"
    monkeypatch.setattr(editor, "_showing", None)
    monkeypatch.setattr(tools, "_diff_hint_shown", False)
    monkeypatch.setattr(editor, "_settings_path", lambda: missing)


def test_closing_removes_both_sides(vscode, tmp_path):
    editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))
    before = tmp_path / "app (before).py"
    after = tmp_path / "app (Flash's edit).py"
    assert before.exists() and after.exists()  # nosec B101

    assert editor.close_diff()  # nosec B101
    assert not before.exists()  # nosec B101
    assert not after.exists()  # nosec B101


def test_closing_nothing_says_so(vscode):
    assert not editor.close_diff()  # nosec B101


def test_closing_twice_is_harmless(vscode, tmp_path):
    editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))

    assert editor.close_diff()  # nosec B101
    assert not editor.close_diff()  # nosec B101


def test_a_diff_that_never_opened_leaves_nothing_to_close(tmp_path):
    # Outside VS Code show_diff writes no files at all.
    assert not editor.show_diff("a\n", "b\n", "x.py", str(tmp_path))
    assert not editor.close_diff()  # nosec B101


def test_a_failed_launch_is_not_recorded_as_open(monkeypatch, tmp_path):
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(editor.shutil, "which", lambda name: "/bin/code")

    def broken(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(editor.subprocess, "Popen", broken)

    assert not editor.show_diff("a\n", "b\n", "x.py", str(tmp_path))
    assert not editor.close_diff()  # nosec B101


def test_an_unwritable_file_does_not_stop_the_close(vscode, tmp_path):
    editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))
    (tmp_path / "app (before).py").unlink()

    # One side already gone is not a reason to leave the other behind.
    assert editor.close_diff()  # nosec B101
    assert not (tmp_path / "app (Flash's edit).py").exists()  # nosec B101


# --- reading the setting that makes the tab actually close -----------------


def _settings(monkeypatch, tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(editor, "_settings_path", lambda: path)


def test_the_setting_is_read_when_on(monkeypatch, tmp_path):
    _settings(
        monkeypatch, tmp_path, '{"workbench.editor.closeOnFileDelete": true}'
    )

    assert editor.closes_deleted_editors() is True  # nosec B101


def test_the_setting_is_read_when_off(monkeypatch, tmp_path):
    _settings(
        monkeypatch, tmp_path, '{"workbench.editor.closeOnFileDelete": false}'
    )

    assert editor.closes_deleted_editors() is False  # nosec B101


def test_comments_and_trailing_commas_do_not_break_it(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path, """
{
    // VS Code allows comments here, which json.loads does not.
    "editor.fontSize": 13,
    "workbench.editor.closeOnFileDelete": true,
}
""")

    assert editor.closes_deleted_editors() is True  # nosec B101


def test_an_absent_setting_reads_as_the_default(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path, '{"editor.fontSize": 13}')

    assert editor.closes_deleted_editors() is False  # nosec B101


def test_an_unreadable_file_is_unknown_not_off(monkeypatch, tmp_path):
    monkeypatch.setattr(
        editor, "_settings_path", lambda: tmp_path / "nope.json"
    )

    # Unknown is not the same as off, and is not worth a hint.
    assert editor.closes_deleted_editors() is None  # nosec B101


# --- through the write tool ------------------------------------------------


def _answer(monkeypatch, reply):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(tools, "notify_needs_input", lambda: None)
    monkeypatch.setattr("builtins.input", lambda: reply)


def test_approving_a_write_closes_the_diff(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "keep.txt"
    target.write_text("original\n")
    _answer(monkeypatch, "y")

    tools.write_tool(str(target), "replaced\n")

    assert editor._showing is None  # nosec B101
    assert not (tmp_path / "keep (before).txt").exists()  # nosec B101


def test_rejecting_a_write_closes_the_diff_too(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "keep.txt"
    target.write_text("original\n")
    _answer(monkeypatch, "n")

    tools.write_tool(str(target), "replaced\n")

    assert editor._showing is None  # nosec B101
    assert not (tmp_path / "keep (before).txt").exists()  # nosec B101


def test_an_interrupted_answer_still_closes_the_diff(
    vscode, tmp_path, monkeypatch
):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "keep.txt"
    target.write_text("original\n")
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(tools, "notify_needs_input", lambda: None)

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupted)

    with pytest.raises(KeyboardInterrupt):
        tools.write_tool(str(target), "replaced\n")

    assert editor._showing is None  # nosec B101


def test_approving_an_edit_closes_the_diff(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "greet.py"
    target.write_text('print("hello")\n')
    _answer(monkeypatch, "y")

    tools.edit_tool(str(target), '"hello"', '"hi"')

    assert editor._showing is None  # nosec B101
    assert target.read_text() == 'print("hi")\n'  # nosec B101


def test_rejecting_an_edit_closes_the_diff(vscode, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "SCRATCH_DIR", str(tmp_path))
    target = tmp_path / "greet.py"
    target.write_text('print("hello")\n')
    _answer(monkeypatch, "n")

    tools.edit_tool(str(target), '"hello"', '"hi"')

    assert editor._showing is None  # nosec B101
    assert target.read_text() == 'print("hello")\n'  # nosec B101


def test_the_setting_hint_is_said_once_and_only_when_it_is_off(
    vscode, tmp_path, monkeypatch
):
    _settings(
        monkeypatch, tmp_path, '{"workbench.editor.closeOnFileDelete": false}'
    )
    said = []
    monkeypatch.setattr(
        tools, "tool_result", lambda text, **kw: said.append(text)
    )

    for _ in range(3):
        editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))
        tools._close_diff()

    hints = [line for line in said if editor.CLOSE_SETTING in line]

    assert len(hints) == 1  # nosec B101


def test_no_hint_when_vscode_already_closes_them(
    vscode, tmp_path, monkeypatch
):
    _settings(
        monkeypatch, tmp_path, '{"workbench.editor.closeOnFileDelete": true}'
    )
    said = []
    monkeypatch.setattr(
        tools, "tool_result", lambda text, **kw: said.append(text)
    )

    editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))
    tools._close_diff()

    assert not [line for line in said if editor.CLOSE_SETTING in line]


def test_no_hint_when_the_setting_cannot_be_read(
    vscode, tmp_path, monkeypatch
):
    said = []
    monkeypatch.setattr(
        tools, "tool_result", lambda text, **kw: said.append(text)
    )

    editor.show_diff("old\n", "new\n", "app.py", str(tmp_path))
    tools._close_diff()

    # Unknown is not off: nothing to advise.
    assert not [line for line in said if editor.CLOSE_SETTING in line]
