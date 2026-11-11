"""Flash Icons: the set, its style, and the tool."""

import re

import pytest

from flash import icons, tools


def test_every_icon_has_a_motion_with_keyframes():
    sheet = icons.css(icons.DEFAULT_STYLE)
    for name, (_cat, motion, paths) in icons.ICONS.items():
        assert paths, name
        assert motion == "draw" or f"@keyframes fi-{motion}{{" in sheet, (name, motion)
        for d in paths:
            assert re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9.,\s-]+", d), (name, d)


def test_an_icon_is_inline_svg_hidden_or_labelled():
    plain = icons.svg("bell")
    assert plain.startswith('<svg class="fi fi-bell" data-motion="ring"')
    assert 'aria-hidden="true"' in plain and plain.count('pathLength="1"') == 2
    named = icons.svg("trash-can", "Delete")
    assert 'class="fi fi-trash"' in named and 'aria-label="Delete"' in named
    with pytest.raises(icons.IconError, match="no icon called"):
        icons.svg("unicorn")


@pytest.mark.parametrize("said, found", [("X", "close"), ("cog", "gear"), ("Arrow Right", "arrow-right"),
                                         ("shopping_cart", "cart"), ("nope", None)])
def test_common_names_find_the_icon(said, found):
    assert icons.find(said) == found


def test_the_style_is_kept_and_followed():
    assert icons.style() == icons.DEFAULT_STYLE
    now = icons.set_style({"stroke": "2.5", "corners": "Square", "variant": "duotone", "motion": "subtle",
                           "trigger": "loop", "speed": 900, "color": "#0a84ff"})
    assert now["stroke"] == 2.5 and now["corners"] == "square" and now["speed"] == 900
    sheet = icons.css()
    assert "--fi-stroke:2.5" in sheet and "--fi-cap:square" in sheet and "--fi-amp:0.5" in sheet
    assert "color-mix(in srgb, currentColor 16%" in sheet and "infinite" in sheet and "--fi-color:#0a84ff" in sheet
    # a later change keeps the earlier ones
    assert icons.set_style({"size": "24px"})["stroke"] == 2.5
    assert icons.reset_style() == icons.DEFAULT_STYLE


@pytest.mark.parametrize("bad", [{"stroke": 9}, {"corners": "wavy"}, {"speed": "fast"}, {"color": "red;}<script>"},
                                 {"glow": 1}])
def test_a_style_it_cannot_take_says_why(bad):
    with pytest.raises(icons.IconError):
        icons.set_style(bad)
    assert icons.style() == icons.DEFAULT_STYLE


def test_no_motion_means_no_animation():
    icons.set_style({"motion": "none"})
    sheet = icons.css()
    assert "@keyframes" not in sheet and "animation:" not in sheet
    icons.reset_style()


def test_hover_motion_plays_on_the_button_or_link_it_is_in():
    sheet = icons.css(icons.DEFAULT_STYLE)
    assert ":is(a,button,[role=button],label,summary,.fi-host):is(:hover,:focus-visible) .fi[data-motion][data-motion=ring]" in sheet
    assert "prefers-reduced-motion" in sheet


def test_packs_for_a_page_and_for_react():
    page = icons.pack(["bell", "cog", "bell"])
    assert page.startswith("<style>") and page.count("<svg") == 2
    module = icons.pack(["arrow-right"], "react")
    assert "export const flashIconsCss" in module and "export function ArrowRightIcon" in module
    assert "pathLength={1}" in module
    with pytest.raises(icons.IconError):
        icons.pack(["bell"], "vue")


def test_the_tool():
    run = tools.FUNCTIONS["flash_icons"]
    assert "navigation: home, menu" in run(action="list")
    assert run(action="get", names="search, heart").count("<svg") == 2
    assert run(action="get").startswith("Error")
    said = run(action="style", stroke=2, motion="lively", trigger="load")
    assert said.startswith("Saved.") and "2.0 px" in said and "as they appear" in said
    assert "Error" in run(action="style", corners="pointy")
    assert run(action="preview", names="bell").endswith("Show it with send_html.")
    page = (icons.FLASH_DIR / "icons-preview.html").read_text()
    assert "fi-bell" in page and "fi-heart" not in page
    assert run(action="reset").startswith("Back to Flash's own icon style")


def test_designs_are_told_to_use_them():
    from flash import design

    assert "flash_icons" in design.prompt_block()
