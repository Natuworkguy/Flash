"""Tests for the edit and multi_edit tools, against real files."""

import pytest

from flash import checkpoint, tools
from flash.tools import edit_tool, multi_edit_tool

SOURCE = 'def greet(name):\n    return "hello"\n'


@pytest.fixture(autouse=True)
def autonomous(monkeypatch):
    """No y/n prompt: these tests drive the tools, not the terminal."""

    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)


@pytest.fixture(autouse=True)
def clean_checkpoints():
    checkpoint.clear()
    yield
    checkpoint.clear()


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "greet.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_edit_replaces_text_on_disk(source):
    result = edit_tool(str(source), '"hello"', '"hi"')

    assert source.read_text(encoding="utf-8") == SOURCE.replace(
        '"hello"', '"hi"'
    )
    assert "1 replacement" in result


def test_edit_reports_the_count_for_replace_all(tmp_path):
    path = tmp_path / "n.py"
    path.write_text("a = 1\nb = 1\n", encoding="utf-8")

    result = edit_tool(str(path), "= 1", "= 2", replace_all=True)

    assert "2 replacements" in result
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 2\n"


def test_edit_leaves_the_file_alone_when_it_fails(source):
    result = edit_tool(str(source), "not in the file", "x")

    assert source.read_text(encoding="utf-8") == SOURCE
    assert result.startswith("Error: ")


def test_edit_refuses_a_missing_file(tmp_path):
    result = edit_tool(str(tmp_path / "nope.py"), "a", "b")

    assert result.startswith("Error: ")
    assert "write tool" in result


def test_edit_refuses_a_directory(tmp_path):
    result = edit_tool(str(tmp_path), "a", "b")

    assert "is a directory" in result


def test_edit_refuses_a_binary_file(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\xff\xfe\x00binary")

    result = edit_tool(str(path), "binary", "text")

    assert result.startswith("Error: ")
    assert "UTF-8" in result


def test_edit_keeps_crlf_line_endings(tmp_path):
    path = tmp_path / "dos.txt"
    path.write_bytes(b"one\r\ntwo\r\n")

    edit_tool(str(path), "two", "three")

    assert path.read_bytes() == b"one\r\nthree\r\n"


def test_edit_reports_a_no_op_without_writing(source):
    before = source.stat().st_mtime_ns
    result = edit_tool(str(source), '"hello"', '"hello"')

    assert "identical" in result
    assert source.stat().st_mtime_ns == before


def test_edit_notes_an_absorbed_indentation_shift(source):
    result = edit_tool(str(source), 'return "hello"', 'return "hi"')

    assert '    return "hi"' in source.read_text(encoding="utf-8")
    assert "1 replacement" in result


def test_a_blocked_edit_writes_nothing(source, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    result = edit_tool(str(source), '"hello"', '"hi"')

    assert result == "Edit blocked by user"
    assert source.read_text(encoding="utf-8") == SOURCE


def test_a_blocked_edit_is_not_undoable(source, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    edit_tool(str(source), '"hello"', '"hi"')

    assert checkpoint.depth() == 0


class TestMultiEdit:
    def test_applies_every_edit(self, source):
        result = multi_edit_tool(str(source), [
            {"old_string": "greet", "new_string": "hello"},
            {"old_string": '"hello"\n', "new_string": '"hi"\n'},
        ])

        text = source.read_text(encoding="utf-8")
        assert "def hello(name):" in text
        assert '"hi"' in text
        assert "2 replacements" in result

    def test_one_bad_edit_leaves_the_file_untouched(self, source):
        result = multi_edit_tool(str(source), [
            {"old_string": "greet", "new_string": "hello"},
            {"old_string": "missing", "new_string": "x"},
        ])

        assert source.read_text(encoding="utf-8") == SOURCE
        assert "edit 2 of 2" in result

    def test_accepts_a_json_string_of_edits(self, source):
        result = multi_edit_tool(
            str(source),
            '[{"old_string": "greet", "new_string": "hello"}]',
        )

        assert "1 replacement" in result
        assert "def hello" in source.read_text(encoding="utf-8")

    def test_rejects_edits_that_are_not_a_list(self, source):
        result = multi_edit_tool(str(source), {"old_string": "a"})

        assert result.startswith("Error: ")
        assert "list" in result

    def test_rejects_an_edit_missing_a_field(self, source):
        result = multi_edit_tool(str(source), [{"old_string": "greet"}])

        assert "missing old_string or new_string" in result

    def test_rejects_unparseable_json(self, source):
        result = multi_edit_tool(str(source), "[{not json")

        assert result.startswith("Error: ")


class TestUndo:
    def test_an_edit_can_be_taken_back(self, source):
        checkpoint.start_turn("the edit")
        edit_tool(str(source), '"hello"', '"hi"')

        assert source.read_text(encoding="utf-8") != SOURCE

        checkpoint.undo()

        assert source.read_text(encoding="utf-8") == SOURCE

    def test_one_undo_takes_back_every_edit_in_a_turn(self, source):
        checkpoint.start_turn("two edits")
        edit_tool(str(source), "greet", "hello")
        edit_tool(str(source), '"hello"', '"hi"')

        checkpoint.undo()

        assert source.read_text(encoding="utf-8") == SOURCE
        assert checkpoint.depth() == 0

    def test_undo_deletes_a_file_the_turn_created(self, tmp_path):
        from flash.tools import write_tool

        target = tmp_path / "fresh.txt"
        checkpoint.start_turn("a new file")
        write_tool(str(target), "hello\n")

        assert target.exists()

        checkpoint.undo()

        assert not target.exists()

    def test_undo_stops_at_the_previous_turn(self, source):
        checkpoint.start_turn("first")
        edit_tool(str(source), "greet", "hello")
        after_first = source.read_text(encoding="utf-8")

        checkpoint.start_turn("second")
        edit_tool(str(source), '"hello"', '"hi"')

        checkpoint.undo()

        assert source.read_text(encoding="utf-8") == after_first
        assert checkpoint.depth() == 1
