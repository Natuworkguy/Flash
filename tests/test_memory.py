

# --- importing with a command ------------------------------------------------


def test_import_takes_a_json_list(tmp_path):
    from flash import memory

    path = tmp_path / "m.md"
    added, known = memory.import_memory('["Likes tea", "Lives in Leeds", "likes tea"]', path)
    assert added == ["Likes tea", "Lives in Leeds"] and known == 0


def test_import_takes_a_memories_object_and_skips_saved_ones(tmp_path):
    from flash import memory

    path = tmp_path / "m.md"
    memory.add_memory("Likes tea", path)
    added, known = memory.import_memory(
        '{"memories": ["Likes tea", {"text": "Birthday is 14 March"}]}', path)
    assert added == ["Birthday is 14 March"] and known == 1


def test_import_from_a_file_and_from_stdin(tmp_path):
    import io

    from flash import memory

    path = tmp_path / "m.md"
    src = tmp_path / "export.txt"
    src.write_text("- Works on Flash\n- Uses UTC-5\n", encoding="utf-8")
    assert memory.import_from(str(src), path)[0] == ["Works on Flash", "Uses UTC-5"]
    assert memory.import_from("-", path, stdin=io.StringIO("Prefers dark mode\n"))[0] == [
        "Prefers dark mode"]
    assert len(memory.list_memory(path)) == 3


def test_the_command_line_takes_import_memories():
    from flash.cli import build_parser

    args = build_parser().parse_args(["--import-memories", "facts.json"])
    assert args.import_memories == "facts.json"
    assert build_parser().parse_args(["--memory-prompt"]).memory_prompt
