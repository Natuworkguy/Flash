"""Digests: big things read in full, part by part, and mapped with where everything is."""

import pytest

from flash import digest, tools


@pytest.fixture
def summer(monkeypatch, tmp_path):
    monkeypatch.setattr(digest, "_cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    asked = []

    def sum_up(prompt):
        asked.append(prompt)
        if prompt.startswith("Below are notes"):
            return "Overview: a long thing.\n## Where things are\n" + "\n".join(
                line for line in prompt.splitlines() if line.startswith("### "))
        where = prompt.split("This part is ", 1)[1].split(".\n", 1)[0]
        return f"- the gist of {where}"

    return asked, sum_up


def test_a_long_file_is_read_in_parts_and_mapped_by_line(summer, tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "PART_CHARS", 1000)
    asked, sum_up = summer
    f = tmp_path / "log.txt"
    f.write_text("\n".join(f"line {i} " + "x" * 40 for i in range(200)))
    out = digest.digest(str(f), summer=sum_up)
    parts = [p for p in asked if p.startswith("You are reading")]
    assert len(parts) > 5
    assert "### lines 1-" in out and "Overview: a long thing." in out
    assert digest.digest(str(f), summer=lambda p: 1 / 0) == out    # kept: nothing read again


def test_a_pdf_is_cut_at_pages(summer):
    text = "\n\n".join(f"--- page {i} ---\n" + "word " * 900 for i in range(1, 9))
    parts = digest._cut_text(text, "pages")
    assert parts[0][0] == "pages 1-2" and parts[-1][0].endswith("8")
    assert "[page 3]" in parts[1][1]


def test_a_transcript_is_cut_at_times():
    lines = "\n".join(f"[{m}:{s:02d}] something said here at length " * 1 for m in range(0, 90) for s in (0, 30))
    parts = digest._cut_timed(lines * 20)
    assert parts[0][0].startswith("0:00 to ")


def test_a_folder_is_read_file_by_file(summer, tmp_path):
    asked, sum_up = summer
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    (repo / "src" / "app.py").write_text("def main():\n    return 1\n")
    (repo / "README.md").write_text("# The app\n")
    (repo / "node_modules" / "junk.js").write_text("x")
    out = digest.digest(str(repo), "where is main?", summer=sum_up)
    assert "src/app.py" in out and "junk.js" not in out
    assert "The user wants to know: where is main?" in asked[0]


def test_missing_things_are_said_plainly():
    assert tools.digest_tool("/no/such/file").startswith("Error: no such file or folder")
    assert tools.digest_tool("https://example.com/page").startswith("Error: only YouTube links")
