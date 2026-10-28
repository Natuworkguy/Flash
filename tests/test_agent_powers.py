# pylint: disable=C0114,C0116

import json

from flash import connectors, extensions, theme, tools, web, workspace


def test_make_project_moves_the_chat(tmp_path, monkeypatch):
    session = web.Session()
    session.hub = web.Hub()
    chat = session.new_chat()
    sink = web._sink(session, chat)
    monkeypatch.setattr(theme, "_sink", lambda: sink)
    said = tools.make_project("Dash", str(tmp_path), "Use pytest.")
    assert "This chat is in it now" in said
    made = next(p for p in workspace.projects() if p.name == "Dash")
    assert chat.project == made.id
    assert made.instructions == "Use pytest."


def test_make_project_in_the_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(theme, "_sink", lambda: None)
    said = tools.make_project("Solo", str(tmp_path))
    assert "Projects" in said
    assert any(p.name == "Solo" for p in workspace.projects())


def test_request_connector_asks_first(monkeypatch):
    asked = []
    monkeypatch.setattr(tools, "ultra_auto", lambda: False)
    monkeypatch.setattr(tools, "_user_allows",
                        lambda q, **_: asked.append(q) or False)
    said = tools.request_connector("gh", "npx -y server-github",
                                   "to read the issues")
    assert said.startswith("The user said no")
    assert "to read the issues" in asked[0]
    assert "gh" not in connectors.read_config()


def test_request_connector_in_ultra_adds_it(monkeypatch):
    monkeypatch.setattr(tools, "ultra_auto", lambda: True)
    monkeypatch.setattr(connectors, "refresh", lambda *a, **k: [])
    said = tools.request_connector("docs", "https://example.com/mcp")
    assert said.startswith("Added docs")
    assert connectors.read_config()["docs"] == {
        "url": "https://example.com/mcp"}
    assert "already" in tools.request_connector(
        "docs", "https://example.com/mcp")


def test_request_extension_installs_once_allowed(tmp_path, monkeypatch):
    folder = tmp_path / "ext"
    folder.mkdir()
    (folder / extensions.MANIFEST).write_text(json.dumps(
        {"name": "helper", "prompt": "p.md"}))
    (folder / "p.md").write_text("Be kind.")
    monkeypatch.setattr(tools, "ultra_auto", lambda: False)
    monkeypatch.setattr(tools, "_user_allows", lambda q, **_: True)
    said = tools.request_extension(f"path@{folder}", "for tone")
    assert said.startswith("Installed helper")
    assert extensions.find("helper") is not None


def test_auto_takeover_is_off_until_asked(monkeypatch):
    from flash import keepalive, sparks

    took = []
    monkeypatch.setattr(sparks, "keeper", lambda: {"pid": 1, "code": "/x"})
    monkeypatch.setattr(keepalive, "take_over", lambda: took.append(1))
    monkeypatch.delenv("SPARKS_AUTO_TAKEOVER", raising=False)
    assert keepalive.take_over_if_wanted() == ""
    keepalive.set_auto_takeover(True)
    said = []
    assert "Took over" in keepalive.take_over_if_wanted(said.append)
    assert took and said

    def refuse():
        raise keepalive.KeepAliveError("it is open")

    monkeypatch.setattr(keepalive, "take_over", refuse)
    assert "it is open" in keepalive.take_over_if_wanted()
    keepalive.set_auto_takeover(False)
    assert not keepalive.auto_takeover()
