"""Edits stay inside the files the user said to touch."""

from flash import scope, tools


def test_reads_only_and_never():
    assert scope.parse("only change web.py and cli.py please") == (["web.py", "cli.py"], [])
    assert scope.parse("Fix the bug but don't touch the README.md or tests/") == ([], ["README.md", "tests/"])
    assert scope.parse("leave `flash/ai.py` alone, just edit flash/web/*.html") == (["flash/web/*.html"], ["flash/ai.py"])
    assert scope.parse("make the button blue") == ([], [])


def test_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    scope.set_request("only edit app.py, don't touch tests/")
    assert scope.check("app.py") is None
    assert scope.check(str(tmp_path / "app.py")) is None
    assert "only in app.py" in scope.check("other.py")
    assert "not to touch tests/" in scope.check("tests/test_a.py")
    scope.set_request("rename the variable")
    assert scope.check("anything.py") is None


def test_write_outside_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scope.set_request("just change notes.txt")
    try:
        out = tools.run_tool(("write", {"path": "other.txt", "content": "x"}))
        assert out.startswith("Not written")
        assert not (tmp_path / "other.txt").exists()
        assert tools._out_of_scope("write", {"path": "notes.txt", "content": "hi"}) is None
        assert tools._out_of_scope("read", {"path": "other.txt"}) is None
    finally:
        scope.clear()
