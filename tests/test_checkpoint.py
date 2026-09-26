"""Tests for the file snapshots behind /undo."""

import pytest

from flash import checkpoint


@pytest.fixture(autouse=True)
def clean():
    checkpoint.clear()
    yield
    checkpoint.clear()


def test_nothing_to_undo_on_a_fresh_session():
    assert checkpoint.depth() == 0
    assert checkpoint.undo() == "Nothing to undo."
    assert checkpoint.describe() == "Nothing to undo."


def test_records_a_file_before_it_changes(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("before", encoding="utf-8")

    checkpoint.start_turn()
    checkpoint.record(path)
    path.write_text("after", encoding="utf-8")

    checkpoint.undo()

    assert path.read_text(encoding="utf-8") == "before"


def test_the_first_snapshot_of_a_turn_is_the_one_kept(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("one", encoding="utf-8")

    checkpoint.start_turn()
    checkpoint.record(path)
    path.write_text("two", encoding="utf-8")
    checkpoint.record(path)
    path.write_text("three", encoding="utf-8")

    checkpoint.undo()

    assert path.read_text(encoding="utf-8") == "one"


def test_an_empty_turn_is_reused_rather_than_stacked(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("before", encoding="utf-8")

    checkpoint.start_turn("first")
    checkpoint.start_turn("second")
    checkpoint.start_turn("third")
    checkpoint.record(path)

    assert checkpoint.depth() == 1
    assert "third" in checkpoint.describe()


def test_describe_counts_the_files(tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    checkpoint.start_turn("a turn")
    checkpoint.record(tmp_path / "a.txt")
    checkpoint.record(tmp_path / "b.txt")

    assert "2 files" in checkpoint.describe()
    assert "a turn" in checkpoint.describe()


def test_a_file_too_large_to_snapshot_is_reported_not_skipped(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(checkpoint, "MAX_SNAPSHOT_BYTES", 4)
    path = tmp_path / "big.txt"
    path.write_text("far more than four bytes", encoding="utf-8")

    checkpoint.start_turn()
    checkpoint.record(path)
    path.write_text("clobbered", encoding="utf-8")

    summary = checkpoint.undo()

    assert "too large" in summary
    assert "big.txt" in summary
    # Honest: the file really was left as the edit made it.
    assert path.read_text(encoding="utf-8") == "clobbered"


def test_undo_deletes_a_file_that_did_not_exist_before(tmp_path):
    path = tmp_path / "new.txt"

    checkpoint.start_turn()
    checkpoint.record(path)
    path.write_text("created", encoding="utf-8")

    summary = checkpoint.undo()

    assert not path.exists()
    assert "Deleted 1 new file" in summary


def test_undo_restores_bytes_exactly(tmp_path):
    path = tmp_path / "dos.txt"
    path.write_bytes(b"one\r\ntwo\r\n")

    checkpoint.start_turn()
    checkpoint.record(path)
    path.write_bytes(b"clobbered")

    checkpoint.undo()

    assert path.read_bytes() == b"one\r\ntwo\r\n"


def test_the_oldest_turns_fall_off_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "MAX_GROUPS", 3)

    for index in range(6):
        path = tmp_path / f"{index}.txt"
        path.write_text("x", encoding="utf-8")
        checkpoint.start_turn(f"turn {index}")
        checkpoint.record(path)

    assert checkpoint.depth() == 3
    assert "turn 5" in checkpoint.describe()


def test_undo_works_without_start_turn(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("before", encoding="utf-8")

    checkpoint.record(path)
    path.write_text("after", encoding="utf-8")

    checkpoint.undo()

    assert path.read_text(encoding="utf-8") == "before"


def test_clear_drops_the_history(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("before", encoding="utf-8")

    checkpoint.start_turn()
    checkpoint.record(path)
    checkpoint.clear()

    assert checkpoint.depth() == 0
    assert checkpoint.undo() == "Nothing to undo."
