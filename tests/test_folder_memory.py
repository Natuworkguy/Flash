# pylint: disable=C0114,C0115,C0116

import pytest

from flash import learning, memory, tools, workspace


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder with a repository in it, and a plain folder."""

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    repo = tmp_path / "code" / "shop"
    (repo / ".git").mkdir(parents=True)
    (repo / "web" / "src").mkdir(parents=True)
    (repo / "web" / "package.json").write_text("{}")
    (tmp_path / "notes").mkdir()
    return tmp_path


def test_a_repository_is_one_project_from_any_folder_in_it(home):
    repo = home / "code" / "shop"
    assert memory.folder_root(repo / "web" / "src") == repo  # nosec B101
    same = memory.folder_project(repo / "web" / "src")
    assert same == memory.folder_project(repo)  # nosec B101
    assert same.startswith("dir-")  # nosec B101


def test_a_folder_with_a_project_file_is_one_too(home):
    app = home / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text("")
    (app / "pkg").mkdir()
    assert memory.folder_root(app / "pkg") == app  # nosec B101


def test_a_plain_folder_and_the_home_folder_are_not(home):
    assert memory.folder_project(home / "notes") == ""  # nosec B101
    assert memory.folder_project(home) == ""  # nosec B101
    (home / ".git").mkdir()
    # Never the home folder, even when it is a repository.
    assert memory.folder_root(home / "notes") is None  # nosec B101


def test_a_web_project_that_takes_the_folder_in_shares_its_memory(home):
    repo = home / "code" / "shop"
    project = workspace.create_project("Shop", str(repo))
    assert memory.folder_project(repo / "web") == project.id  # nosec B101


def test_the_terminal_remembers_per_folder_and_is_told_it(
        home, monkeypatch):
    repo = home / "code" / "shop"
    monkeypatch.chdir(repo / "web")
    assert memory.folder_block() == ""  # off the terminal  # nosec B101
    monkeypatch.setattr(memory, "FOLDER_MEMORY", True)
    assert memory.enter_folder().startswith("dir-")  # nosec B101
    said = tools.remember("Tests run with make test", project=True)
    assert said.startswith("Remembered for this project")  # nosec B101
    block = memory.folder_block()
    assert f"({repo})" in block  # nosec B101
    assert "- Tests run with make test" in block  # nosec B101
    assert "Tests run" in tools.build_system_prompt()  # nosec B101

    # Another folder, another memory; a plain one, none.
    monkeypatch.chdir(home / "notes")
    assert memory.enter_folder() == ""  # nosec B101
    assert memory.folder_block() == ""  # nosec B101
    assert tools.remember("x", project=True).startswith("Error")  # nosec


def test_a_review_of_project_work_saves_to_the_project(monkeypatch):
    started = []

    class Start:
        def __init__(self, target, args, daemon):
            started.append(args[0])

        def start(self):
            pass

    monkeypatch.setattr(learning, "skill_review_after", lambda: 2)
    monkeypatch.setattr(learning, "memory_review_every", lambda: 50)
    learning.reset()
    review = learning.after_turn([], 3, host="h", model="m", start=Start,
                                 project="dir-abc")
    assert review is not None and review.memory  # nosec B101
    assert review.project == "dir-abc"  # nosec B101
    sent = learning.review_messages(review, [])
    assert "remember with project" in sent[0]["content"]  # nosec B101
    assert "project's memory" in sent[1]["content"]  # nosec B101
    learning.reset()

    # Outside a project, the review is as it was.
    review = learning.after_turn([], 3, host="h", model="m", start=Start,
                                 project="")
    assert review is not None and not review.memory  # nosec B101
    assert "PROJECT" not in str(learning.review_messages(review, []))  # nosec
    learning.reset()
