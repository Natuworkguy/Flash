# pylint: disable=C0114,C0116

import pytest

from flash import design, tools, web, workspace

PAGE = """<!doctype html><html><head><style>
:root { --accent: #14b8a6; --radius: 14px; }
.cta { border-radius: var(--radius); }
</style></head><body>
<h1>Calm water</h1><p class="lead">Fish &amp; chips</p>
<a class="cta" href="#">Start free</a>
<script>const label = "Start free";</script>
</body></html>"""


@pytest.fixture
def shown(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "FLASH_DIR", tmp_path / ".flash")
    source = tmp_path / "landing.html"
    source.write_text(PAGE, encoding="utf-8")
    return source, workspace.keep_file(str(source))


def test_a_shown_page_knows_where_it_came_from(shown):
    source, info = shown

    assert info["kind"] == "html"  # nosec B101
    assert info["path"] == str(source.resolve())  # nosec B101
    came = workspace.file_source(info["id"])
    assert came == str(source.resolve())  # nosec B101


def test_words_and_sliders_go_into_the_file(shown):
    source, info = shown

    saved = design.save(
        info["id"],
        [
            {"old": "Calm water", "new": "Still water"},
            {"old": "Fish & chips", "new": "Salt & vinegar"},
        ],
        {"--radius": "28px", "--accent": "#ff7a59"},
    )

    text = source.read_text(encoding="utf-8")
    assert saved == {"path": str(source.resolve()), "missed": []}  # nosec B101
    assert "<h1>Still water</h1>" in text  # nosec B101
    assert "Salt &amp; vinegar" in text  # nosec B101
    assert "--radius: 28px;" in text  # nosec B101
    assert "--accent: #ff7a59;" in text  # nosec B101
    kept = workspace.kept_file(info["id"])[0].read_text(encoding="utf-8")
    assert kept == text  # nosec B101


def test_only_words_between_tags_change(shown):
    source, info = shown

    design.save(info["id"], [{"old": "Start free", "new": "Try it"}], {})

    text = source.read_text(encoding="utf-8")
    assert ">Try it</a>" in text  # nosec B101
    assert 'const label = "Start free"' in text  # nosec B101


def test_words_it_cannot_find_come_back(shown):
    source, info = shown

    saved = design.save(
        info["id"],
        [{"old": "Not on the page", "new": "Anything"}],
        {},
    )

    assert saved["missed"] == [  # nosec B101
        {"old": "Not on the page", "new": "Anything"}
    ]
    assert source.read_text(encoding="utf-8") == PAGE  # nosec B101


@pytest.mark.parametrize(
    "name, value",
    [
        ("--radius", "1px; } body { display: none"),
        ("--radius", "</style><script>alert(1)</script>"),
        ("radius", "28px"),
        ("--missing", "28px"),
    ],
)
def test_slider_values_that_are_not_values_are_ignored(shown, name, value):
    source, info = shown

    design.save(info["id"], [], {name: value})

    assert source.read_text(encoding="utf-8") == PAGE  # nosec B101


def test_only_shown_pages_save(shown):
    with pytest.raises(design.DesignError):
        design.save("0123456789abcdef", [], {})


def test_the_kit_goes_in_before_the_body_ends():
    page = web.with_design_kit(b"<html><body><p>Hi</p></body></html>")

    assert page.startswith(b"<html><body><p>Hi</p><script id=")  # nosec B101
    assert page.endswith(b"</script></body></html>")  # nosec B101
    bare = web.with_design_kit(b"<p>bare</p>")
    assert bare.startswith(b"<p>bare</p><script")  # nosec B101


def test_the_prompt_lists_the_design_systems(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "SYSTEMS_DIR", tmp_path)
    assert "== Designs ==" in tools.build_system_prompt()  # nosec B101
    assert "saved so far" not in design.prompt_block()  # nosec B101

    (tmp_path / "shop.md").write_text(
        "# Shop design system\n\nColours...", encoding="utf-8"
    )

    block = design.prompt_block()
    assert "- shop: Shop design system (" in block  # nosec B101
    assert block in tools.build_system_prompt()  # nosec B101


def test_unknown_exports_are_refused(shown, tmp_path):
    with pytest.raises(design.DesignError):
        design.export(shown[1]["id"], "gif", tmp_path)
