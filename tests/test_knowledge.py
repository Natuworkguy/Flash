"""The knowledge base: Flash's own guides, and extensions'."""

import json
import re
from pathlib import Path

import pytest

from flash import extensions, skills, tools

EXAMPLE = re.compile(r"```\n([a-z_0-9]+) (\{.*?\})\n```", re.S)


def _schemas() -> dict:
    offered = (tools.tools + tools.EMAIL_TOOLS + tools.EDITOR_TOOLS
               + tools.SYSTEM_ONE_TOOLS)
    found = {t["function"]["name"]: t["function"] for t in offered}
    # Tools offered only once allowed: their schemas are built on demand.
    for build in ("computer_tools", "computer_use_tools", "imessage_tools"):
        try:
            for t in getattr(tools, build, lambda: [])():
                found[t["function"]["name"]] = t["function"]
        except Exception:  # noqa: BLE001  (not on this platform)
            continue
    return found


def test_every_guide_reads_and_is_listed():
    guides = skills.knowledge()
    assert len(guides) >= 30
    for guide in guides:
        assert guide.builtin == "flash"
        assert guide.description and len(guide.description) <= 120
        assert len(guide.body) <= skills.MAX_SKILL_CHARS
        assert "—" not in guide.body
        assert guide.body.startswith(guide.body.lstrip())
    listed = skills.listing()
    assert listed.startswith("=== Knowledge base ===")
    assert "- teach-a-course: " in listed
    assert "Guide: teach-a-course (from Flash)" in skills.view(
        "teach-a-course")


def test_every_example_calls_a_real_tool_with_real_arguments():
    schemas = _schemas()
    seen = 0
    for guide in skills.knowledge():
        for tool, args in EXAMPLE.findall(guide.body):
            seen += 1
            where = f"{guide.name}: {tool}"
            assert tool in tools.FUNCTIONS, where
            parsed = json.loads(args)
            if tool not in schemas:
                continue
            params = schemas[tool].get("parameters", {})
            known = set(params.get("properties", {}))
            assert set(parsed) <= known, f"{where}: {set(parsed) - known}"
            for need in params.get("required", []):
                assert need in parsed, f"{where} lacks {need}"
            for name, value in parsed.items():
                allowed = params["properties"][name].get("enum")
                if allowed:
                    assert value in allowed, f"{where}: {name}={value}"
    assert seen > 80


def test_a_skill_of_the_same_name_stands_in_for_a_guide():
    skills.manage("create", "teach-a-course", description="Mine",
                  content="My way.")
    assert skills.view("teach-a-course").startswith("Skill: teach-a-course")
    assert "- teach-a-course" not in skills.knowledge_listing()
    skills.remove("teach-a-course")
    assert "Guide: teach-a-course" in skills.view("teach-a-course")


def test_guides_cannot_be_changed():
    with pytest.raises(skills.SkillError, match="cannot be changed"):
        skills.manage("patch", "use-git", old_string="git", new_string="x")
    with pytest.raises(skills.SkillError, match="cannot be changed"):
        skills.manage("delete", "use-git")
    assert not skills.remove("use-git")


def _extension(root: Path, guides: dict) -> Path:
    root.mkdir()
    (root / "flash-extension.json").write_text(json.dumps(
        {"name": "acme", "knowledge": "knowledge"}))
    (root / "knowledge").mkdir()
    for name, text in guides.items():
        (root / "knowledge" / name).write_text(text)
    return root


def test_an_extension_brings_guides(tmp_path, monkeypatch):
    root = _extension(tmp_path / "acme", {
        "deploy.md": "---\ndescription: Ship with Acme\n---\n1. Run it.\n"})
    ext = extensions.load(root)
    assert ext.knowledge == root / "knowledge"
    assert "knowledge base: deploy" in ext.contents()
    monkeypatch.setattr(extensions, "installed", lambda: [ext])
    names = {g.name: g for g in skills.knowledge()}
    assert names["acme-deploy"].builtin == "acme"
    assert "- acme-deploy: Ship with Acme (from acme)" in skills.listing()
    assert "from the acme extension" in skills.view("acme-deploy")


@pytest.mark.parametrize("guides,why", [
    ({}, "no .md guides"),
    ({"x.md": "no description here"}, "needs a description"),
    ({"Bad Name.md": "---\ndescription: d\n---\nx"}, "not a name"),
])
def test_an_extensions_guides_are_checked(tmp_path, guides, why):
    root = _extension(tmp_path / "acme", guides)
    with pytest.raises(extensions.ExtensionError, match=why):
        extensions.load(root)
