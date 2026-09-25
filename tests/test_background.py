"""Tests for the pixel-art scenes drawn behind the prompt."""

import io
import re

import pytest
from rich.console import Console

from flash import ai, background
from flash.background import Scene, SceneError

GOOD = """\
# a scene
name: Two Tone
palette:
  . #112233
  o #ffaa00
pixels:
..oo
oo..
..oo
oo..
"""


class TestParsing:
    def test_a_scene_reads_back(self):
        scene = background.parse(GOOD)

        assert scene.name == "Two Tone"
        assert scene.width == 4
        assert scene.height == 4
        assert scene.rows[0] == ["#112233", "#112233", "#ffaa00", "#ffaa00"]

    def test_comments_and_blanks_are_skipped(self):
        assert background.parse(GOOD).height == 4

    def test_colours_are_lowercased(self):
        scene = background.parse(
            "palette:\n  a #AABBCC\npixels:\na\n"
        )

        assert scene.rows[0] == ["#aabbcc"]

    def test_a_pixel_outside_the_palette_is_named(self):
        with pytest.raises(SceneError, match="palette does not define"):
            background.parse("palette:\n  . #000000\npixels:\n.x\n")

    def test_a_ragged_scene_is_refused(self):
        with pytest.raises(SceneError, match="same width"):
            background.parse("palette:\n  . #000000\npixels:\n..\n...\n")

    def test_a_bad_colour_is_named(self):
        with pytest.raises(SceneError, match="not a #rrggbb"):
            background.parse("palette:\n  . red\npixels:\n.\n")

    def test_a_bad_palette_line_is_named(self):
        with pytest.raises(SceneError, match="one character then one"):
            background.parse("palette:\n  ab #000000\npixels:\na\n")

    def test_a_scene_with_no_pixels_is_refused(self):
        with pytest.raises(SceneError, match="no pixels"):
            background.parse("palette:\n  . #000000\npixels:\n")

    def test_stray_prose_is_refused(self):
        with pytest.raises(SceneError, match="not part of a scene"):
            background.parse("hello there\npalette:\n  . #000000\n")

    def test_the_file_name_is_in_the_error(self):
        with pytest.raises(SceneError, match="mine.scene:2"):
            background.parse("palette:\n  . nope\n", where="mine.scene")


# Read from the bundled folder alone. background.names() also lists the
# developer's own scenes and their extensions', and is called here at
# collection, before any fixture has swapped the home folder out.
BUNDLED = sorted(
    path.stem for path in background.bundled_dir().glob("*.scene")
)


class TestBundled:
    def test_there_are_scenes_to_choose_from(self):
        assert len(background.names()) >= 4

    @pytest.mark.parametrize("name", BUNDLED)
    def test_every_bundled_scene_loads(self, name):
        scene = background.load(background.find(name))

        assert scene.width > 0
        assert scene.height > 0
        assert scene.name

    @pytest.mark.parametrize("name", BUNDLED)
    def test_every_bundled_scene_is_even_height(self, name):
        # Two pixels to a cell: an odd row would be half drawn.
        assert background.load(background.find(name)).height % 2 == 0

    def test_a_missing_name_is_none(self):
        assert background.find("no-such-scene") is None

    def test_an_empty_name_is_none(self):
        assert background.find("   ") is None

    def test_the_user_directory_comes_first(self):
        assert background.search_paths()[0] == background.user_dir()


class TestRendering:
    def scene(self):
        return background.parse(GOOD)

    def test_two_pixels_to_a_cell(self):
        drawn = background.render(self.scene(), 40, 8)

        assert len(drawn) == 8
        assert len(drawn[0].plain) == 40

    def test_every_cell_is_a_half_block(self):
        drawn = background.render(self.scene(), 30, 6)

        assert set(drawn[0].plain) == {background.UPPER_HALF}

    def test_it_stretches_to_any_size(self):
        for columns, rows in ((20, 4), (80, 10), (200, 30)):
            drawn = background.render(self.scene(), columns, rows)

            assert len(drawn) == rows
            assert len(drawn[0].plain) == columns

    def test_too_few_rows_draws_nothing(self):
        assert background.render(self.scene(), 80, 1) == []

    def test_too_narrow_draws_nothing(self):
        assert background.render(self.scene(), 4, 10) == []

    def test_an_empty_scene_draws_nothing(self):
        assert background.render(Scene(), 80, 10) == []

    def test_the_colours_come_from_the_scene(self):
        drawn = background.render(self.scene(), 40, 8)
        styles = {str(span.style) for span in drawn[0].spans}

        assert any("#ffaa00" in style for style in styles)

    def test_it_never_wraps(self):
        # A wrapped row would push the prompt down a line each redraw.
        console = Console(file=io.StringIO(), width=20,
                          force_terminal=False)
        for line in background.render(self.scene(), 20, 4):
            console.print(line)

        assert len(console.file.getvalue().splitlines()) == 4


class TestDrawable:
    def test_no_color_turns_it_off(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")

        assert not background.drawable()

    def test_a_terminal_that_cannot_encode_it_turns_it_off(
        self, monkeypatch
    ):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(background, "can_encode", lambda text: False)

        assert not background.drawable()


class TestChoosing:
    @pytest.fixture(autouse=True)
    def quiet(self, monkeypatch):
        monkeypatch.setattr(ai, "_background_notices", set())

    def test_nothing_chosen_means_no_scene(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "background", "")

        assert ai.current_scene() is None

    def test_a_bundled_scene_is_found(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "background", "sunset")

        assert ai.current_scene() is not None

    def test_an_unknown_scene_says_so_once(self, monkeypatch):
        said = []
        monkeypatch.setattr(ai.Config, "background", "nope")
        monkeypatch.setattr(ai, "warn", lambda text: said.append(text))

        assert ai.current_scene() is None
        assert ai.current_scene() is None
        assert len(said) == 1
        assert "nope" in said[0]

    def test_a_terminal_that_cannot_draw_says_so(self, monkeypatch):
        said = []
        monkeypatch.setattr(ai.Config, "background", "sunset")
        monkeypatch.setattr(ai, "warn", lambda text: said.append(text))
        monkeypatch.setattr(background, "drawable", lambda: False)

        assert ai.current_scene() is None
        assert "half block" in said[0]


class TestOverlay:
    """Text drawn on the picture rather than punching a hole in it."""

    def scene(self):
        return background.parse(GOOD)

    def cells(self, text, width=40):
        row = [(char, None) for char in text]
        row += [(" ", None)] * (width - len(row))
        return [row]

    def test_a_glyph_replaces_the_half_block(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=self.cells("Hi")
        )

        assert drawn[0].plain.startswith("Hi")

    def test_the_rest_of_the_row_is_still_scene(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=self.cells("Hi")
        )

        assert set(drawn[0].plain[2:]) == {background.UPPER_HALF}

    def test_rows_past_the_overlay_are_pure_scene(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=self.cells("Hi")
        )

        assert set(drawn[3].plain) == {background.UPPER_HALF}

    def test_a_short_row_leaves_the_rest_showing(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=[[("X", None)]]
        )

        assert drawn[0].plain[0] == "X"
        assert set(drawn[0].plain[1:]) == {background.UPPER_HALF}

    def test_a_blank_cell_leaves_the_scene_showing(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=self.cells("  X")
        )

        assert drawn[0].plain[:2] == background.UPPER_HALF * 2
        assert drawn[0].plain[2] == "X"

    def test_a_glyph_takes_the_scene_as_its_background(self):
        drawn = background.render(
            self.scene(), 40, 6, overlay=self.cells("X")
        )
        style = drawn[0].spans[0].style

        assert style.bgcolor is not None

    def test_no_overlay_behaves_as_before(self):
        plain = background.render(self.scene(), 40, 6)
        empty = background.render(self.scene(), 40, 6, overlay=[])

        assert [line.plain for line in plain] == [
            line.plain for line in empty
        ]


class TestBlend:
    def test_it_meets_in_the_middle(self):
        assert background._blend("#000000", "#ffffff") == "#7f7f7f"

    def test_one_colour_blends_to_itself(self):
        assert background._blend("#123456", "#123456") == "#123456"


class TestPaintLaunch:
    def console(self, width=76, height=22):
        return Console(file=io.StringIO(), width=width, height=height,
                       force_terminal=True, color_system="truecolor")

    @pytest.fixture(autouse=True)
    def forest(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "background", "forest")
        monkeypatch.setattr(ai.Config, "model", "a-model")
        monkeypatch.setattr(ai.Config, "host", ai.OLLAMA_HOST_DEFAULT)
        monkeypatch.setattr(ai.Config, "no_command_confirmation", False)
        monkeypatch.setattr(ai.Config, "voice", False)

    def test_it_fills_everything_but_the_prompt(self):
        console = self.console(height=22)

        assert ai.paint_launch(console, None)

        drawn = console.file.getvalue().splitlines()
        assert len(drawn) == 22 - ai.FIRST_PROMPT_ROWS

    def test_the_banner_lands_on_the_picture(self):
        console = self.console()
        ai.paint_launch(console, None)

        # Every cell carries its own escape, so the text is only
        # contiguous once the styling is stripped off. The spaces
        # inside the banner are scene, not blanks: that is the point,
        # the picture shows through the box rather than stopping at it.
        bare = re.sub(r"\x1b\[[0-9;]*m", "", console.file.getvalue())
        text = bare.replace(background.UPPER_HALF, " ")

        assert "Flash CLI" in text
        assert background.UPPER_HALF in bare

    def test_the_picture_shows_through_the_banner(self):
        console = self.console()
        ai.paint_launch(console, None)
        bare = re.sub(r"\x1b\[[0-9;]*m", "", console.file.getvalue())

        inside = bare.splitlines()[1]

        assert inside.startswith("│")
        assert background.UPPER_HALF in inside

    def test_no_background_means_it_does_not_paint(self, monkeypatch):
        monkeypatch.setattr(ai.Config, "background", "")

        assert not ai.paint_launch(self.console(), None)

    def test_a_pipe_is_left_alone(self):
        console = Console(file=io.StringIO(), width=76, height=22,
                          force_terminal=False)

        assert not ai.paint_launch(console, None)

    def test_a_terminal_too_short_falls_back(self):
        # Below background.MIN_ROWS there is nothing worth drawing.
        assert not ai.paint_launch(self.console(height=5), None)


class TestOverlayCells:
    def test_a_short_renderable_is_padded_with_blank_rows(self):
        from rich.text import Text as RichText

        console = Console(file=io.StringIO(), width=20, height=10,
                          force_terminal=False)
        rows = ai._overlay_cells(console, RichText("hi"), 20, 8)

        assert len(rows) == 8
        assert rows[-1] == []

    def test_it_never_returns_more_rows_than_asked_for(self):
        from rich.text import Text as RichText

        console = Console(file=io.StringIO(), width=20, height=40,
                          force_terminal=False)
        tall = RichText("\n".join(str(n) for n in range(30)))

        assert len(ai._overlay_cells(console, tall, 20, 6)) == 6
