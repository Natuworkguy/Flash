# pylint: disable=C0114,C0115,C0116

from flash import memory, tools, web, workspace


def a_project(tmp_path):
    return workspace.create_project("Shop", str(tmp_path))


def test_a_project_remembers_apart_from_the_user(tmp_path):
    project = a_project(tmp_path)

    with memory.in_project(project.id):
        said = tools.remember("Tests run with pnpm test", project=True)
        tools.remember("Nathan likes short replies")
        found = tools.recall("e")

    assert said == "Remembered for this project: Tests run with pnpm test"
    assert memory.list_memory() == ["Nathan likes short replies"]
    assert memory.list_memory(memory.project_path(project.id)) == [
        "Tests run with pnpm test"]
    assert "1. Nathan likes short replies" in found  # nosec B101
    assert "project 1. Tests run with pnpm test" in found  # nosec B101

    with memory.in_project(project.id):
        assert "Deleted" in tools.forget(1, project=True)  # nosec B101
    assert memory.list_memory(memory.project_path(project.id)) == []


def test_outside_a_project_there_is_none_to_reach():
    said = tools.remember("Uses tabs", project=True)

    assert said.startswith("Error: this chat is not in a project")
    assert memory.list_memory() == []  # nosec B101
    assert "project" not in tools.recall("tabs")  # nosec B101


def test_every_chat_in_the_project_is_told_what_it_remembers(tmp_path):
    project = a_project(tmp_path)
    assert "(nothing yet)" in web.project_prompt(project)  # nosec B101

    memory.add_memory("Deploys go through make ship",
                      memory.project_path(project.id))

    assert "- Deploys go through make ship" in web.project_prompt(  # nosec
        project)


def test_the_page_edits_a_projects_memory_and_deleting_it_forgets(
        tmp_path):
    project = a_project(tmp_path)
    session = web.Session()

    def say(name, arg="", **body):
        return web.command(session, {"name": name, "arg": arg,
                                     "project": project.id, **body})

    assert say("memory-add", text="Uses Postgres 16")["entries"] == [
        "Uses Postgres 16"]
    assert say("memory-edit", "1", text="Uses Postgres 17")["entries"] == [
        "Uses Postgres 17"]
    assert say("memory")["entries"] == ["Uses Postgres 17"]
    # The user's own memory is left alone.
    assert web.command(session, {"name": "memory"})["entries"] == []

    web.command(session, {"name": "project-delete", "arg": project.id})
    assert not memory.project_path(project.id).exists()  # nosec B101
