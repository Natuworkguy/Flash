"""Extensions in the inbox: notes and questions their code puts there,
and what an inbox program in their manifest lists."""

import json

import pytest

from flash import extensions, inbox, workspace


@pytest.fixture(autouse=True)
def own_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "store", lambda: tmp_path / "web")
    monkeypatch.setattr(extensions, "inbox_items", lambda: [])
    inbox._program_cache.update(at=0.0, items=[])


def ext_items():
    return [i for i in inbox.items() if i["source"] == "extension"]


def test_a_note_lands_in_the_inbox_and_done_puts_it_away():
    inbox.notify("weather", "Rain at 4", "Take a coat.", id="rain", url="https://example.com/radar")
    [item] = ext_items()
    assert item["kind"] == "report" and item["name"] == "weather" and item["url"] == "https://example.com/radar"
    assert "**Rain at 4**" in item["text"] and "Take a coat." in item["text"]
    inbox.seen(item["id"])
    assert ext_items() == []


def test_the_same_id_replaces_and_withdraw_takes_it_back():
    inbox.notify("ci", "Build 1 failed", id="build")
    inbox.notify("ci", "Build 2 failed", id="build")
    assert [i["text"] for i in ext_items()] == ["**Build 2 failed**\n\n"]
    inbox.withdraw("ci", "build")
    assert ext_items() == []


def test_a_question_is_answered_and_the_answer_kept():
    inbox.notify("deploy", "Ship v2 to production?", "3 changes since v1.", id="ship", ask=True)
    [item] = ext_items()
    assert item["kind"] == "approval" and item["ask"] == "Ship v2 to production?"
    assert inbox.answer_of("deploy", "ship") is None
    said = inbox.answer(item["id"], False, "not on a Friday")
    assert "no" in said
    got = inbox.answer_of("deploy", "ship")
    assert got["yes"] is False and got["why"] == "not on a Friday"
    assert ext_items() == []


def test_unsafe_links_and_empty_items_are_refused():
    inbox.notify("x", "Hi", url="javascript:alert(1)")
    assert ext_items()[0]["url"] == ""
    with pytest.raises(ValueError):
        inbox.notify("x", "", "")


def test_an_inbox_program_lists_items_and_hears_answers(monkeypatch):
    class Ext:
        name, inbox = "tickets", object()
    told = []
    monkeypatch.setattr(extensions, "inbox_items", lambda: [(Ext, [
        {"title": "Review PR 12?", "id": "pr12", "ask": True},
        {"title": "Deploy finished", "id": "d1"}])])
    monkeypatch.setattr(extensions, "installed", lambda: [Ext])
    monkeypatch.setattr(extensions, "inbox_answer", lambda ext, item, yes, why: told.append((item, yes, why)))
    asks = [i for i in ext_items() if i["kind"] == "approval"]
    assert [a["ask"] for a in asks] == ["Review PR 12?"]
    inbox.answer(asks[0]["id"], True)
    assert told == [("pr12", True, "")]
    assert [i["text"] for i in ext_items()] == ["**Deploy finished**\n\n"]


def test_the_manifest_takes_an_inbox_program(tmp_path):
    root = tmp_path / "ext"
    root.mkdir()
    (root / "inbox.py").write_text("print('[]')")
    (root / extensions.MANIFEST).write_text(json.dumps({"name": "notes", "inbox": {"run": ["python", "./inbox.py"]}}))
    ext = extensions.load(root)
    assert ext.inbox is not None and ext.inbox.timeout == 10
    assert "notes and questions in the inbox" in ext.contents()
