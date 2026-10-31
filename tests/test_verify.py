"""Tests for checking a change: finding, running and reading checks."""

import json

from flash import tools, verify
from tests.test_web import (  # noqa: F401  (offline is a fixture)
    FakeClient,
    call,
    offline,
    part,
    run,
)


def project(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


class TestFinding:
    def test_python(self, tmp_path):
        root = project(tmp_path, {
            "pyproject.toml": "[tool.ruff]\nline-length = 79\n",
            "tests/test_a.py": "def test_a():\n    pass\n",
            "pkg/a.py": "",
        })
        found_root, checks = verify.detect(str(root / "pkg"))
        assert found_root == root
        assert checks == ["ruff check .", "python -m pytest -q"]

    def test_node_by_its_lockfile_and_scripts(self, tmp_path):
        root = project(tmp_path, {"package.json": json.dumps({"scripts": {
            "test": "vitest run", "lint": "eslint .", "dev": "vite"}}),
            "pnpm-lock.yaml": ""})
        assert verify.detect(str(root))[1] == ["pnpm run lint", "pnpm test"]
        (root / "package.json").write_text(json.dumps({"scripts": {
            "test": 'echo "Error: no test specified" && exit 1'}}))
        assert verify.detect(str(root))[1] == []

    def test_a_makefile_comes_first(self, tmp_path):
        root = project(tmp_path, {
            "Makefile": "lint:\n\truff .\ntest:\n\tpytest\n",
            "go.mod": "module x\n"})
        assert verify.detect(str(root))[1] == ["make lint", "make test"]
        (root / "Makefile").unlink()
        assert verify.detect(str(root))[1] == ["go vet ./...",
                                               "go test ./..."]


class TestReading:
    def test_failures_point_at_tests_and_lines(self):
        ran = verify.Ran("python -m pytest -q", False, 1.0, (
            "tests/test_dates.py:12: AssertionError\n"
            "/usr/lib/python3/site-packages/x.py:3: in f\n"
            "FAILED tests/test_dates.py::test_parse - AssertionError\n"
            "src/dates.py:40: in parse\n"), 1)
        tests, places = verify.failures(ran)
        assert tests == ["tests/test_dates.py::test_parse"]
        assert places == ["tests/test_dates.py:12", "src/dates.py:40"]
        said = verify.report(verify.Path("/p"), [
            verify.Ran("ruff check .", True, 0.2, ""), ran])
        assert said.startswith("Checks in /p: 1 of 2 passed.")
        assert "Look at: tests/test_dates.py:12, src/dates.py:40" in said
        assert "never by skipping" in said


class TestTheTool:
    def test_runs_the_projects_checks(self, tmp_path, monkeypatch):
        root = project(tmp_path, {
            "Makefile": "test:\n\t@echo 'FAILED t.py::test_x' && exit 1\n"
                        "lint:\n\t@echo clean\n"})
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
        said = tools.verify(folder=str(root))
        assert "1 of 2 passed" in said
        assert "PASS  make lint" in said and "FAIL  make test" in said
        assert "Failing: t.py::test_x" in said
        said = tools.verify(commands=["true"], folder=str(root))
        assert "1 of 1 passed" in said and "All passed" in said

    def test_asks_first_in_ask_first_mode(self, tmp_path, monkeypatch):
        root = project(tmp_path, {"Makefile": "test:\n\ttouch ran\n"})
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
        monkeypatch.setattr(tools, "_user_allows",
                            lambda q, ask_why=False: False)
        assert tools.verify(folder=str(root)).startswith("The user said no")
        assert not (root / "ran").exists()

    def test_no_checks_says_so(self, tmp_path):
        assert tools.verify(folder=str(tmp_path)).startswith("No checks found")


class TestTheNudge:
    def test_changed_code_unchecked_is_sent_back_once(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.delenv(verify.SETTING, raising=False)
        root = project(tmp_path, {"Makefile": "test:\n\ttrue\n"})
        watch = verify.Watch()
        watch.saw("read", {"path": str(root / "a.py")}, "x")
        assert watch.nudge() == ""
        watch.saw("edit", {"path": str(root / "a.py")}, "Edit blocked by user")
        assert watch.nudge() == ""
        watch.saw("edit", {"path": str(root / "a.py")}, "Edited")
        said = watch.nudge()
        assert "you changed code (a.py)" in said and "Call verify" in said
        assert watch.nudge() == ""  # once a turn

    def test_checked_since_or_turned_off_is_let_end(self, tmp_path,
                                                    monkeypatch):
        root = project(tmp_path, {"Makefile": "test:\n\ttrue\n"})
        watch = verify.Watch()
        watch.saw("write", {"path": str(root / "a.py")}, "Wrote")
        watch.saw("shell", {"command": "make test"}, "ok")
        assert watch.nudge() == ""
        watch.saw("edit", {"path": str(root / "a.py")}, "Edited")
        monkeypatch.setenv(verify.SETTING, "0")
        assert watch.nudge() == ""
        monkeypatch.setenv(verify.SETTING, "1")
        watch.saw("verify", {}, "Checks")
        assert watch.nudge() == ""
        # Prose is not code.
        notes = verify.Watch()
        notes.saw("write", {"path": str(root / "notes.md")}, "Wrote")
        assert notes.nudge() == ""


def test_a_web_turn_that_changed_code_checks_it_before_ending(
        tmp_path, monkeypatch):
    from flash import web

    monkeypatch.delenv(verify.SETTING, raising=False)
    root = project(tmp_path, {"Makefile": "test:\n\ttrue\n"})
    ran = []

    def fake_tool(called):
        ran.append(called[0])
        return "Edited." if called[0] == "edit" else "Checks: 1 of 1 passed."

    monkeypatch.setattr(tools, "run_tool", fake_tool)
    FakeClient.scripts = [
        [part(calls=[call("edit", path=str(root / "a.py"), old_string="a",
                          new_string="b")]), part(done=True)],
        [part("Done."), part(done=True)],
        [part(calls=[call("verify")]), part(done=True)],
        [part("Done: make test passed."), part(done=True)],
    ]
    session = web.Session()
    chat = session.new_chat()
    run(session, chat, "fix it")
    assert ran == ["edit", "verify"]
    said = [str(m.get("content")) for m in FakeClient.requests[-1]["messages"]]
    nudged = next(n for n, m in enumerate(said) if "Call verify now" in m)
    assert said[nudged - 1] == "Done." and said[nudged + 2].startswith(
        "Checks: 1 of 1")
    assert chat.messages[-1]["content"] == "Done: make test passed."
