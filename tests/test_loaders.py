"""Tests for the loading animations and the colour themes, built in and
from extensions, in the terminal and the web UI."""

import json

import pytest
from prompt_toolkit.document import Document

from flash import ai, extensions, loaders, theme, themes, web
from flash.extensions import ExtensionError
from flash.repl_input import SlashCommandCompleter


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    # Set, so what a test saves is put back after it.
    monkeypatch.setenv(loaders.SETTING, "")
    extensions.reload()
    yield
    extensions.reload()


def extension(tmp_path, manifest, files=None):
    """An installed extension with MANIFEST."""

    folder = tmp_path / "src"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / extensions.MANIFEST).write_text(
        json.dumps({"name": "looks", **manifest}), encoding="utf-8",
    )
    for name, text in (files or {}).items():
        (folder / name).write_text(text, encoding="utf-8")
    checkout = extensions.fetch(f"path@{folder}")
    try:
        installed = extensions.install(checkout, f"path@{folder}")
    finally:
        extensions.discard(checkout)
    extensions.reload()
    return installed


ROCKET = {"name": "rocket", "label": "Rocket", "interval": 0.2,
          "frames": ["🚀  ", " 🚀 ", "  🚀"]}
NORD = {"name": "nord", "label": "Nord",
        "dark": {"bg": "#2e3440", "accent": "#88c0d0"},
        "light": {"bg": "rgb(236, 239, 244)"}}


class TestBuiltIn:
    def test_every_loader_has_ascii_frames_and_a_steady_width(self):
        assert len(loaders.BUILTIN) >= 10
        for loader in loaders.BUILTIN:
            assert loader.ascii and all(
                c.isascii() for f in loader.ascii for c in f
            ), loader.id
            # Every frame drawn is as wide as the widest, so the words
            # beside it hold still.
            for fancy in (True, False):
                widths = {
                    len(loader.frame(n * loader.seconds, fancy))
                    for n in range(len(loader.frames) * 2)
                }
                assert len(widths) == 1, loader.id

    def test_the_setting_picks_one_and_unknown_falls_back(self, monkeypatch):
        assert loaders.chosen() == loaders.DEFAULT
        monkeypatch.setenv(loaders.SETTING, "wave")
        assert loaders.chosen() == "wave"
        # Gone, or never there: the default.
        for gone in ("nope", "shuffle", "spark"):
            monkeypatch.setenv(loaders.SETTING, gone)
            assert loaders.chosen() == loaders.DEFAULT

    def test_the_terminal_draws_ascii_where_it_must(self, monkeypatch):
        pulse = loaders.find("pulse")
        assert theme.loader_frame(0, pulse) == "·"
        monkeypatch.setattr(theme, "can_encode", lambda text: False)
        assert theme.loader_frame(0, pulse) == "."


def shows(text):
    return True


def plain(text):
    return text.isascii()


class TestMorph:
    def test_off_it_stays_on_the_one_chosen(self, monkeypatch):
        monkeypatch.setenv(loaders.SETTING, "wave")
        run = loaders.Run(morph=False)
        wave = loaders.find("wave")
        for t in (0, 9.9, 10.5, 45):
            assert run.frame(t, shows) == wave.frame(t)

    def test_on_it_turns_into_another_every_stretch(self):
        run = loaders.Run(morph=True, rng=__import__("random").Random(4))
        chain = [run.loader(k).id for k in range(30)]
        assert chain[0] == loaders.DEFAULT
        assert all(a != b for a, b in zip(chain, chain[1:]))
        assert len(set(chain)) > 5
        # Kept: asked again, the same ones.
        assert [run.loader(k).id for k in range(30)] == chain
        middle = loaders.MORPH_SECONDS * 2.5
        assert run.frame(middle, shows) == run.loader(2).frame(middle)

    def test_the_morph_dissolves_into_dots_and_out_again(self):
        wave, bounce = loaders.find("wave"), loaders.find("bounce")
        run = loaders.Run(first=wave, morph=True)
        run._every = [wave, bounce]
        end = loaders.MORPH_SECONDS
        seen = [
            run.frame(end - loaders.BLEND_SECONDS * (1 - f), shows)
            for f in (0.05, 0.25, 0.45, 0.55, 0.75, 0.95)
        ]
        assert {len(f) for f in seen} == {4}
        dots = [f.count("·") for f in seen]
        # Into dots from the left, then the next one out of them.
        assert dots[0] < dots[2] and dots[3] > dots[5]
        assert seen[2].startswith("··")
        assert seen[5][0] in "•∙"
        assert run.frame(end + 0.01, shows) == bounce.frame(end + 0.01)

    def test_the_ascii_morph_is_ascii(self):
        run = loaders.Run(morph=True, rng=__import__("random").Random(2))
        for n in range(400):
            assert run.frame(n * 0.1, plain).isascii()

    def test_the_terminal_command(self, monkeypatch):
        lines = []
        monkeypatch.setattr(ai.console, "print",
                            lambda *a, **k: lines.append(str(a[0])))
        monkeypatch.setenv(loaders.MORPH_SETTING, "")
        ai._loader_command("morph on")
        assert loaders.morphing()
        ai._loader_command("morph")
        assert not loaders.morphing()
        assert "LOADER_MORPH=0" in open(ai.ENV_PATH, encoding="utf-8").read()


class TestTerminalCommand:
    def said(self, monkeypatch):
        lines = []
        monkeypatch.setattr(ai.console, "print",
                            lambda *a, **k: lines.append(str(a[0])))
        monkeypatch.setattr(ai, "warn", lambda text: lines.append(text))
        return lines

    def test_naming_one_saves_it(self, monkeypatch):
        lines = self.said(monkeypatch)
        ai._loader_command("bloom")
        assert loaders.chosen() == "bloom"
        assert any("Bloom" in line for line in lines)
        assert "LOADER=bloom" in open(ai.ENV_PATH, encoding="utf-8").read()

    def test_unknown_changes_nothing(self, monkeypatch):
        lines = self.said(monkeypatch)
        ai._loader_command("wave")
        ai._loader_command("shuffle")
        assert loaders.chosen() == "wave"
        assert any("No loader" in line for line in lines)

    def test_list_shows_each_with_its_frames(self, monkeypatch):
        lines = self.said(monkeypatch)
        ai._loader_command("list")
        assert any("Wave" in line and "▁▃▅▇" in line for line in lines)

    def test_completion(self):
        offered = [
            c.text for c in SlashCommandCompleter().get_completions(
                Document("/loader s", cursor_position=9), None,
            )
        ]
        assert offered == ["snake"]
        offered = [
            c.text for c in SlashCommandCompleter().get_completions(
                Document("/loader m", cursor_position=9), None,
            )
        ]
        assert offered == ["morph"]


class TestExtensions:
    def test_an_extension_adds_a_loader_and_a_theme(self, tmp_path):
        added = extension(tmp_path, {"loaders": [ROCKET], "themes": [NORD]})

        assert any("loading animations: Rocket" in line
                   for line in added.contents())
        rocket = loaders.find("rocket")
        assert rocket.source == "looks"
        assert rocket.ascii == ("*  ", " * ", "  *")
        nord = themes.extension_themes()[0]
        assert nord.dark == {"bg": "#2e3440", "accent": "#88c0d0"}

    def test_lists_can_live_in_a_file(self, tmp_path):
        extension(
            tmp_path, {"themes": "themes.json"},
            {"themes.json": json.dumps([NORD])},
        )
        assert [t.id for t in themes.extension_themes()] == ["nord"]

    @pytest.mark.parametrize("bad, why", [
        ({"themes": [{**NORD, "dark": {"bg": "url(x)"}}]}, "not a colour"),
        ({"themes": [{**NORD, "dark": {"font": "#fff"}}]},
         "not a colour a theme"),
        ({"themes": [{"name": "empty"}]}, "sets no colours"),
        ({"themes": [NORD, NORD]}, "declared twice"),
        ({"loaders": [{**ROCKET, "frames": []}]}, "frames"),
        ({"loaders": [{**ROCKET, "interval": 9}]}, "interval"),
        ({"loaders": [{**ROCKET, "ascii": ["→"]}]}, "ASCII"),
        ({"loaders": "missing.json"}, "loaders"),
    ])
    def test_bad_ones_are_refused_with_a_reason(self, tmp_path, bad, why):
        with pytest.raises(ExtensionError, match=why):
            extension(tmp_path, bad)

    def test_a_built_in_name_is_not_taken_over(self, tmp_path):
        extension(tmp_path, {"loaders": [{**ROCKET, "name": "dots"}]})
        assert loaders.find("dots").source == ""


class TestWeb:
    def test_status_carries_the_loaders_and_themes(self, tmp_path):
        extension(tmp_path, {"loaders": [ROCKET], "themes": [NORD]})

        shown = web.status(ai)

        assert shown["loader"] == "dots"
        ids = [loader["id"] for loader in shown["loaders"]]
        assert ids[0] == "dots" and ids[-1] == "rocket"
        assert shown["themes"][0]["id"] == "nord"

    def test_the_loader_command(self):
        session = web.Session()
        assert web.command(session, {"name": "loader", "arg": "Wave"}) == {
            "loader": "wave",
        }
        for gone in ("shuffle", "spark", "nope"):
            with pytest.raises(ValueError):
                web.command(session, {"name": "loader", "arg": gone})

    def test_the_morph_switch(self, monkeypatch):
        monkeypatch.setenv(loaders.MORPH_SETTING, "")
        session = web.Session()
        assert web.status(ai)["loader_morph"] is False
        assert web.command(session, {"name": "loader-morph", "arg": "on"}) \
            == {"loader_morph": True}
        assert web.status(ai)["loader_morph"] is True
