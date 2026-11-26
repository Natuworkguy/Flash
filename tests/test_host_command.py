"""Tests for /host: switching the terminal's Ollama server among the
hosts the web UI keeps."""

from types import SimpleNamespace

import pytest
from prompt_toolkit.document import Document

from flash import ai, models, workspace
from flash.repl_input import SlashCommandCompleter


@pytest.fixture(autouse=True)
def terminal(monkeypatch):
    # Whatever a test switches to is put back after it.
    monkeypatch.setenv("OLLAMA_HOST", ai.OLLAMA_HOST_DEFAULT)
    monkeypatch.setenv("MODEL", "flash-test")
    ai.Config.refresh()
    said = []
    monkeypatch.setattr(
        ai.console, "print",
        lambda *a, **k: said.append(str(a[0]) if a else ""),
    )
    monkeypatch.setattr(ai, "warn", lambda text: said.append(str(text)))
    monkeypatch.setattr(models, "can_pick", lambda: False)
    monkeypatch.setattr(models, "installed_models", lambda client, cur: [
        models.Model(cur, note="active"),
    ])
    yield said
    ai.Config.refresh()


def state(monkeypatch, answer):
    monkeypatch.setattr(workspace, "host_state", lambda url, *a: answer)


class TestSwitching:
    def test_an_address_switches_and_is_saved(self, monkeypatch, terminal):
        state(monkeypatch, "up")
        forgot = []
        monkeypatch.setattr(ai, "forget_model_facts",
                            lambda: forgot.append(True))

        ai._host_command("10.0.0.5")

        assert ai.Config.host == "http://10.0.0.5:11434"
        assert "OLLAMA_HOST=http://10.0.0.5:11434" in \
            open(ai.ENV_PATH, encoding="utf-8").read()
        assert forgot == [True]
        assert any("Switched to" in line for line in terminal)

    def test_a_saved_host_is_found_by_its_name(self, monkeypatch):
        state(monkeypatch, "up")
        workspace.add_host("Studio", "10.0.0.7")

        ai._host_command("studio")

        assert ai.Config.host == "http://10.0.0.7:11434"

    def test_a_host_that_is_down_still_switches_and_says_so(
        self, monkeypatch, terminal,
    ):
        state(monkeypatch, "down")

        ai._host_command("10.0.0.9")

        assert ai.Config.host == "http://10.0.0.9:11434"
        assert any("nothing answers" in line for line in terminal)

    def test_a_host_that_wants_a_key_says_how_to_give_one(
        self, monkeypatch, terminal,
    ):
        state(monkeypatch, "refused")

        ai._host_command("10.0.0.9")

        assert any("/host add http://10.0.0.9:11434" in line
                   for line in terminal)

    def test_a_model_missing_on_the_new_host_is_pointed_out(
        self, monkeypatch, terminal,
    ):
        state(monkeypatch, "up")
        monkeypatch.setattr(models, "installed_models",
                            lambda client, cur: [models.Model("other")])

        ai._host_command("10.0.0.5")

        assert any("not on this host" in line for line in terminal)

    def test_nonsense_is_a_warning_not_a_switch(self, monkeypatch, terminal):
        state(monkeypatch, "up")

        ai._host_command("ftp://x/y")

        assert ai.Config.host == ai.OLLAMA_HOST_DEFAULT
        assert any("Usage: /host" in line for line in terminal)


class TestPicker:
    def test_the_picker_starts_on_the_host_in_use(self, monkeypatch):
        state(monkeypatch, "up")
        workspace.add_host("Studio", "10.0.0.7")
        workspace.add_host("Laptop", "10.0.0.8")
        monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.8:11434")
        ai.Config.refresh()
        monkeypatch.setattr(models, "can_pick", lambda: True)
        offered = {}

        def choose(rows, **kwargs):
            offered.update(rows=rows, **kwargs)
            return "Studio"

        monkeypatch.setattr(models, "choose", choose)

        ai._host_command("")

        names = [r.name for r in offered["rows"]]
        assert names == ["This computer", "Studio", "Laptop"]
        assert offered["start"] == 2
        assert offered["rows"][2].note == "up, in use"
        assert ai.Config.host == "http://10.0.0.7:11434"


class TestSavedHosts:
    def test_add_saves_the_key_and_switches(self, monkeypatch):
        state(monkeypatch, "up")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-1")

        ai._host_command("add 10.0.0.5 Big GPU")

        listed = workspace.hosts()
        assert listed[-1] == {"name": "Big GPU",
                              "url": "http://10.0.0.5:11434",
                              "saved": True, "locked": True}
        assert workspace.api_key("http://10.0.0.5:11434") == "sk-1"
        assert ai.Config.host == "http://10.0.0.5:11434"

    def test_remove_forgets_one(self, monkeypatch):
        workspace.add_host("Studio", "10.0.0.7")

        ai._host_command("remove Studio")

        assert [h["name"] for h in workspace.hosts()] == ["This computer"]

    def test_the_host_in_use_is_not_removed(self, monkeypatch, terminal):
        state(monkeypatch, "up")
        workspace.add_host("Studio", "10.0.0.7")
        ai._host_command("Studio")

        ai._host_command("remove Studio")

        assert len(workspace.hosts()) == 2
        assert any("in use" in line for line in terminal)

    def test_list_prints_each_with_how_it_answers(
        self, monkeypatch, terminal,
    ):
        state(monkeypatch, "up")
        workspace.add_host("Studio", "10.0.0.7")

        ai._host_command("list")

        assert any("Studio" in line and "up" in line for line in terminal)


class TestCompletion:
    def complete(self, text):
        return [
            c.text for c in SlashCommandCompleter().get_completions(
                Document(text, cursor_position=len(text)),
                SimpleNamespace(completion_requested=True),
            )
        ]

    def test_actions_and_names(self):
        workspace.add_host("Studio", "10.0.0.7")

        assert self.complete("/host ") == [
            "list", "add", "remove", "This computer", "Studio",
        ]
        assert self.complete("/host st") == ["Studio"]

    def test_remove_offers_only_saved_hosts(self):
        workspace.add_host("Studio", "10.0.0.7")

        assert self.complete("/host remove ") == ["Studio"]
