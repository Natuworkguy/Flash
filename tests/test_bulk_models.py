# pylint: disable=C0114,C0116

import pytest

from flash import ai, sparks, web


@pytest.fixture
def desk():
    """Desk: Lead, with Scout and Writer, and Loner on no team."""

    team = sparks.create_team("Desk")
    for name in ("Lead", "Scout", "Writer", "Loner"):
        sparks.create(name, f"{name}'s job.", model="old:1b")
    for name in ("lead", "scout", "writer"):
        sparks.set_team(name, "desk")
    return team


def _models():
    return {s.name: s.model for s in sparks.all_sparks()}


def test_a_team_moves_together(desk):
    team, changed = sparks.set_team_model("desk", "new:8b")

    assert team.id == desk.id
    assert sorted(s.name for s in changed) == ["Lead", "Scout", "Writer"]
    assert _models() == {"Lead": "new:8b", "Scout": "new:8b",
                         "Writer": "new:8b", "Loner": "old:1b"}


def test_flash_puts_them_back_on_flashs_model(desk):
    sparks.set_models(["scout", "loner"], "flash")

    assert _models()["Scout"] == "" and _models()["Loner"] == ""


def test_one_unknown_spark_changes_none(desk):
    with pytest.raises(sparks.SparkError):
        sparks.set_models(["scout", "nobody"], "new:8b")

    assert set(_models().values()) == {"old:1b"}


def test_an_empty_team_says_so():
    sparks.create_team("Empty")

    with pytest.raises(sparks.SparkError, match="No sparks on Empty"):
        sparks.set_team_model("empty", "new:8b")


def test_the_page_moves_a_team_and_a_pick(desk):
    session = web.Session()

    said = web.command(session, {"name": "team-model", "arg": desk.id,
                                 "model": "new:8b"})
    assert sorted(said["changed"]) == ["Lead", "Scout", "Writer"]

    ids = [s.id for s in sparks.all_sparks() if s.name in ("Lead", "Loner")]
    said = web.command(session, {"name": "spark-models", "ids": ids,
                                 "model": "mid:4b"})
    assert sorted(said["changed"]) == ["Lead", "Loner"]
    assert _models() == {"Lead": "mid:4b", "Scout": "new:8b",
                         "Writer": "new:8b", "Loner": "mid:4b"}

    with pytest.raises(ValueError, match="Pick at least one"):
        web.command(session, {"name": "spark-models", "ids": [],
                              "model": "x"})


@pytest.mark.parametrize("typed, moved", [
    ("model all new:8b", {"Lead", "Scout", "Writer", "Loner"}),
    ("model scout, writer new:8b", {"Scout", "Writer"}),
    ("model lead,loner new:8b", {"Lead", "Loner"}),
    ("teams model Desk new:8b", {"Lead", "Scout", "Writer"}),
])
def test_the_terminal_moves_many(desk, monkeypatch, typed, moved):
    monkeypatch.setattr(ai.console, "print", lambda *a, **k: None)

    ai._sparks_command(typed)

    assert {n for n, m in _models().items() if m == "new:8b"} == moved


def test_without_a_model_the_terminal_asks(desk, monkeypatch):
    monkeypatch.setattr(ai.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(ai, "_ask_spark_model", lambda: "picked:2b")

    ai._sparks_command("model scout, writer")

    assert _models()["Scout"] == _models()["Writer"] == "picked:2b"


def test_models_lists_them_by_team(desk, monkeypatch):
    shown = []
    monkeypatch.setattr(ai.console, "print",
                        lambda *a, **k: shown.append(str(a[0]) if a else ""))
    sparks.set_models(["loner"], "flash")

    ai._sparks_command("models")

    text = shown[-1]
    assert text.index("Desk") < text.index("Scout") < text.index("No team")
    assert "Loner" in text and "Flash's model" in text
