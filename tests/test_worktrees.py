# pylint: disable=C0114,C0115,C0116

import os
import subprocess  # nosec B404

import pytest

from flash import tools, web, worktrees


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout  # nosec


@pytest.fixture
def repo(tmp_path, monkeypatch):
    main = tmp_path / "app"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "me@example.com")
    git(main, "config", "user.name", "Me")
    (main / "readme.md").write_text("hello\n", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "First")
    monkeypatch.setattr(worktrees, "home", lambda: tmp_path / "flash-worktrees")
    return main


def test_a_worktree_is_its_own_local_checkout(repo):
    made = worktrees.create(str(repo), "Fix the login page")
    assert made["branch"] == "fix-the-login-page"  # nosec B101
    assert os.path.isfile(os.path.join(made["path"], "readme.md"))  # nosec B101
    assert worktrees.is_private(made["path"]) and not worktrees.is_private(str(repo))  # nosec B101
    # No upstream, and a bare push goes nowhere.
    assert git(repo, "config", f"branch.{made['branch']}.pushRemote").strip() == worktrees.NOWHERE  # nosec B101
    listed = worktrees.listing(str(repo))
    assert [w["main"] for w in listed] == [True, False]  # nosec B101
    assert listed[1]["branch"] == "fix-the-login-page" and listed[1]["private"]  # nosec B101
    # A second with the same name gets a branch of its own.
    assert worktrees.create(str(repo), "Fix the login page")["branch"] == "fix-the-login-page-2"  # nosec B101


def test_work_merges_back_locally_and_is_removed(repo):
    made = worktrees.create(str(repo), "notes")
    with open(os.path.join(made["path"], "notes.md"), "w", encoding="utf-8") as f:
        f.write("new\n")
    assert worktrees.changed(made["path"]) == 1  # nosec B101
    with pytest.raises(worktrees.WorktreeError, match="changed files"):
        worktrees.remove(str(repo), made["path"])
    said = worktrees.merge(str(repo), made["path"])
    assert "Merged 1 commit from notes into main" in said and "Nothing was pushed" in said  # nosec B101
    assert (repo / "notes.md").read_text(encoding="utf-8") == "new\n"  # nosec B101
    assert "Removed" in worktrees.remove(str(repo), made["path"])  # nosec B101
    assert len(worktrees.listing(str(repo))) == 1  # nosec B101
    assert "notes" not in git(repo, "branch")  # nosec B101  (merged, so gone)


def test_flash_does_not_publish_from_a_private_worktree(repo, monkeypatch):
    made = worktrees.create(str(repo), "secret")
    monkeypatch.chdir(made["path"])
    for command in ("git push", "git push -u origin secret", "gh pr create --fill", "cd . && git  push"):
        assert "not run" in tools.shell_tool(command)  # nosec B101
    assert not worktrees.publishes("git status && git commit -m 'push button'")  # nosec B101


def test_a_chat_works_in_its_worktree(repo, monkeypatch):
    monkeypatch.chdir(repo)
    session = web.Session()
    chat = session.new_chat()
    got = web.command(session, {"name": "worktree-new", "chat": chat.id, "label": "Try the new layout"})
    assert chat.worktree == got["worktree"] and chat.worktree.endswith("try-the-new-layout")  # nosec B101
    assert web.where(chat, None) == chat.worktree  # nosec B101
    assert "never push it" in worktrees.describe(chat.worktree)  # nosec B101
    listed = web.command(session, {"name": "worktrees", "chat": chat.id})["worktrees"]
    assert [w["current"] for w in listed] == [False, True]  # nosec B101
    # Saved with the chat.
    assert web.Chat.restore(chat.saved()).worktree == chat.worktree  # nosec B101
    web.command(session, {"name": "worktree-use", "chat": chat.id, "arg": ""})
    assert chat.worktree == "" and web.where(chat, None) is None  # nosec B101
