# pylint: disable=C0114,C0115,C0116

import json
import os
import subprocess  # nosec B404

import pytest

from flash import fleet, fleet_worker, worktrees


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout  # nosec


@pytest.fixture
def repo(tmp_path, monkeypatch):
    main = tmp_path / "app"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "me@example.com")
    git(main, "config", "user.name", "Me")
    (main / "app.py").write_text("print('hi')\n", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "First")
    monkeypatch.setattr(worktrees, "home", lambda: tmp_path / "worktrees")
    monkeypatch.setattr(fleet, "home", lambda: tmp_path / "fleet")
    return main


class Done:
    """A chat's process that has already finished."""

    pid = 0

    def poll(self):
        return 0


def finishing(work):
    """A launch that does the work at once, and says it is done."""

    def launch(member, log):
        spec = json.loads(open(member.spec, encoding="utf-8").read())
        work(member)
        with open(spec["status"], "w", encoding="utf-8") as f:
            json.dump({"state": "done", "activity": "", "steps": ["write(app.py)"],
                       "result": f"## Did it\nChat {member.n} changed app.py.", "rounds": 2,
                       "finished": 1.0}, f)
        return Done()
    return launch


def test_the_task_and_how_many():
    assert fleet.parse("/fleet fix the login bug", "m") == (["m", "m", "m"], "fix the login bug")  # nosec B101
    assert fleet.parse("4: make it fast", "m") == (["m"] * 4, "make it fast")  # nosec B101
    assert fleet.parse("qwen3, gpt-oss: speed it up", "m") == (["qwen3", "gpt-oss"], "speed it up")  # nosec B101
    # A colon in the task is the task's.
    assert fleet.parse("fix this: it crashes", "m") == (["m"] * 3, "fix this: it crashes")  # nosec B101
    for bad in ("", "1: alone", "9: too many"):
        with pytest.raises(ValueError):
            fleet.parse(bad, "m")
    with pytest.raises(ValueError, match="model"):
        fleet.parse("do it", "")


def test_each_works_in_its_own_worktree_and_one_is_merged(repo):
    def work(member):
        with open(os.path.join(member.path, "app.py"), "a", encoding="utf-8") as f:
            f.write(f"print({member.n})\n" * member.n)
        with open(os.path.join(member.path, f"new{member.n}.txt"), "w", encoding="utf-8") as f:
            f.write("new\n")

    made = fleet.Fleet.start(str(repo), "print more", ["a", "b", "c"], launch=finishing(work))
    assert len({m.path for m in made.members}) == 3  # nosec B101
    assert all(worktrees.is_private(m.path) for m in made.members)  # nosec B101
    # The main checkout is untouched while they work.
    assert worktrees.changed(str(repo)) == 0  # nosec B101

    made.refresh()
    assert not made.running()  # nosec B101
    assert [m.state for m in made.members] == ["done"] * 3  # nosec B101
    assert made.members[0].summary == "Did it"  # nosec B101

    made.tally()
    second = made.member(2)
    assert sorted(second.files) == ["app.py", "new2.txt"] and second.added == 3  # nosec B101
    assert "print(2)" in made.diff(2)  # nosec B101

    made.run_check("grep -q 'print(3)' app.py")
    assert [m.check for m in made.members] == [False, False, True]  # nosec B101

    said = made.merge(3)
    assert "Merged" in said  # nosec B101
    assert (repo / "new3.txt").exists() and "print(3)" in (repo / "app.py").read_text()  # nosec B101
    # The others are gone, worktrees and branches.
    assert [w["main"] for w in worktrees.listing(str(repo))] == [True]  # nosec B101
    assert git(repo, "branch", "--format=%(refname:short)").split() == ["main"]  # nosec B101
    assert not os.path.exists(made.place)  # nosec B101


def test_dropping_them_all_leaves_the_repository_as_it_was(repo):
    made = fleet.Fleet.start(str(repo), "x", ["a", "b"], launch=finishing(lambda m: None))
    made.discard()
    assert [w["main"] for w in worktrees.listing(str(repo))] == [True]  # nosec B101
    assert git(repo, "branch", "--format=%(refname:short)").split() == ["main"]  # nosec B101


def test_one_that_dies_without_a_word_is_said_to_have_failed(repo):
    def launch(member, log):
        log.write_text("Traceback\nRuntimeError: no model\n", encoding="utf-8")
        return Done()

    made = fleet.Fleet.start(str(repo), "x", ["a", "b"], launch=launch)
    made.refresh()
    assert [m.state for m in made.members] == ["error", "error"]  # nosec B101
    assert "no model" in made.members[0].result  # nosec B101
    with pytest.raises(ValueError, match="no chat 5"):
        made.member(5)
    made.discard()


def test_a_running_one_cannot_be_merged_and_stopping_marks_it(repo):
    class Working:
        pid = 0

        def poll(self):
            return None

    made = fleet.Fleet.start(str(repo), "x", ["a", "b"], launch=lambda m, log: Working())
    made.refresh()
    assert made.running()  # nosec B101
    with pytest.raises(ValueError, match="still working"):
        made.merge(1)
    made.members[0].proc = None
    made.members[1].proc = None
    made.stop()
    assert [m.state for m in made.members] == ["stopped", "stopped"]  # nosec B101
    made.discard()


def test_not_a_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "home", lambda: tmp_path / "fleet")
    with pytest.raises(worktrees.WorktreeError, match="git repository"):
        fleet.Fleet.start(str(tmp_path), "x", ["a", "b"], launch=lambda m, log: Done())


def test_the_worker_runs_the_task_in_its_worktree_and_says_how_it_went(repo, tmp_path, monkeypatch):
    from flash import agent, ai
    from flash import tools as flash_tools

    made = worktrees.create(str(repo), "fleet w 1")
    status = tmp_path / "1.status.json"
    spec = tmp_path / "1.json"
    spec.write_text(json.dumps({"task": "add a file", "prompt": "Fleet note", "model": "some-model",
                                "path": made["path"], "status": str(status)}), encoding="utf-8")
    seen = {}

    def run(entry):
        seen.update(cwd=os.getcwd(), task=entry.task, model=flash_tools.MODEL_NAME,
                    asks=flash_tools.NO_COMMAND_CONFIRMATION)
        entry.steps.append(agent.Step(label="write(x)"))
        entry.status, entry.result = agent.DONE, "Added x."

    monkeypatch.setattr(ai, "refresh_config", lambda: None)
    monkeypatch.setattr(agent, "_run", run)
    monkeypatch.setattr(flash_tools, "NO_COMMAND_CONFIRMATION", False)
    monkeypatch.setattr(flash_tools, "MODEL_NAME", "")
    monkeypatch.setattr(ai.Config, "model", None, raising=False)
    here = os.getcwd()
    try:
        assert fleet_worker.main(str(spec)) == 0  # nosec B101
    finally:
        os.chdir(here)
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(made["path"])  # nosec B101
    assert seen["model"] == "some-model" and seen["asks"] is True  # nosec B101
    assert "add a file" in seen["task"] and "Worktree" in seen["task"] and "Fleet note" in seen["task"]  # nosec B101
    said = json.loads(status.read_text(encoding="utf-8"))
    assert said["state"] == "done" and said["result"] == "Added x." and said["steps"] == ["write(x)"]  # nosec B101
