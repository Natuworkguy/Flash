"""Tests for the completion dropdown and the prompt it draws over."""

import inspect
from types import SimpleNamespace

import pytest
from prompt_toolkit.document import Document

from flash import repl_input
from flash.repl_input import SlashCommandCompleter


def complete(text, tmp_path=None):
    """Every completion offered for `text`, as a list."""

    return list(
        SlashCommandCompleter().get_completions(
            Document(text, cursor_position=len(text)),
            SimpleNamespace(completion_requested=True),
        )
    )


class TestImagePaths:
    """The /image branch lost the two lines that parsed its argument
    when the emoji completions were added, so asking for a path raised
    NameError instead of offering one."""

    def test_asking_for_an_image_path_does_not_raise(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cat.png").write_bytes(b"")

        complete("/image ")

    def test_it_offers_the_images_in_the_directory(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cat.png").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("x")

        offered = {c.text for c in complete("/image ")}

        assert "cat.png" in offered
        assert "notes.txt" not in offered

    def test_past_the_path_it_offers_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cat.png").write_bytes(b"")

        # The optional prompt after the path is prose, not a path.
        assert complete("/image cat.png describe ") == []


class TestEmoji:
    def test_a_colon_word_offers_emoji(self):
        offered = complete(":")

        assert offered
        assert all(c.display_text for c in offered)

    def test_a_path_with_a_colon_is_not_treated_as_emoji(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cat.png").write_bytes(b"")

        # Checked after the path branches, so this is still a path.
        offered = {c.text for c in complete("/image ")}

        assert "cat.png" in offered


class TestSlashCommands:
    def test_a_slash_offers_commands(self):
        offered = {c.text for c in complete("/")}

        assert "/help" in offered
        assert "/background" in offered

    def test_background_offers_its_scenes(self):
        offered = {c.text for c in complete("/background ")}

        assert "forest" in offered


class TestMenuHeadroom:
    """prompt_toolkit draws the menu over the rows above the cursor and
    no further, so the prompt has to be that tall while one is open."""

    def app(self, monkeypatch, completions):
        state = (
            None if completions is None
            else SimpleNamespace(completions=completions)
        )
        monkeypatch.setattr(
            repl_input,
            "get_app_or_none",
            lambda: SimpleNamespace(
                current_buffer=SimpleNamespace(complete_state=state)
            ),
        )

    def test_no_application_asks_for_nothing(self, monkeypatch):
        monkeypatch.setattr(repl_input, "get_app_or_none", lambda: None)

        assert repl_input.menu_headroom() == 0

    def test_nothing_completing_asks_for_nothing(self, monkeypatch):
        self.app(monkeypatch, None)

        assert repl_input.menu_headroom() == 0

    def test_an_empty_completion_list_asks_for_nothing(self, monkeypatch):
        self.app(monkeypatch, [])

        assert repl_input.menu_headroom() == 0

    def test_it_asks_for_a_row_per_completion(self, monkeypatch):
        self.app(monkeypatch, ["a", "b", "c"])

        assert repl_input.menu_headroom() == 3

    def test_it_stops_at_the_cap(self, monkeypatch):
        self.app(monkeypatch, ["x"] * 40)

        assert repl_input.menu_headroom() == repl_input.MAX_MENU_ROWS

    @pytest.mark.parametrize("count", [1, 5, 12, 13, 50])
    def test_it_never_asks_for_more_than_the_cap(self, monkeypatch, count):
        self.app(monkeypatch, ["x"] * count)

        assert 0 < repl_input.menu_headroom() <= repl_input.MAX_MENU_ROWS


class TestSurvivingAResize:
    """The prompt is rebuilt on every frame. Built once, it carried the
    width of the terminal it was built in, so a resize left the rules
    short and the status line padded to a margin that had moved."""

    def test_the_prompt_is_a_callable_not_a_string(self):
        source = inspect.getsource(repl_input.read_line)

        assert "def message()" in source
        assert "_session.prompt(\n        message," in source

    def test_the_rule_follows_the_terminal(self, monkeypatch):
        widths = iter([80, 120])
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: __import__("os").terminal_size((next(widths), 24)),
        )

        assert len(repl_input.input_rule()) == 80
        assert len(repl_input.input_rule()) == 120

    def test_the_closing_rule_follows_it_too(self, monkeypatch):
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: __import__("os").terminal_size((64, 24)),
        )

        assert len(repl_input.closing_rule()[0][1]) == 64


class TestCarriedText:
    """A resize stands the prompt down, so whatever was half typed has
    to come back on the next call."""

    def test_it_hands_the_text_back_once(self, monkeypatch):
        monkeypatch.setattr(repl_input, "_carried", "half a sentence")

        assert repl_input._take_carried() == "half a sentence"
        assert repl_input._take_carried() == ""

    def test_nothing_carried_is_an_empty_default(self, monkeypatch):
        monkeypatch.setattr(repl_input, "_carried", "")

        assert repl_input._take_carried() == ""

    def test_the_prompt_starts_from_what_was_carried(self):
        source = inspect.getsource(repl_input.read_line)

        assert "default=_take_carried()" in source


class TestResizeSentinel:
    def test_it_cannot_be_typed(self):
        # A NUL never arrives from a keyboard, so no real line collides.
        assert "\x00" in repl_input.RESIZE

    def test_it_is_not_the_wake_sentinel(self):
        assert repl_input.RESIZE != repl_input.WAKE

    def test_the_prompt_watches_for_a_resize(self):
        source = inspect.getsource(repl_input.read_line)

        assert "watch_for_resize" in source
        assert "pre_run=pre_run" in source

class TestSnugRenderer:
    """A resize used to leave stale copies of the frame behind and
    stretch the prompt to the foot of the screen."""

    def renderer(self, size):
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.styles import Style

        box = {"size": Size(rows=size[1], columns=size[0])}
        output = DummyOutput()
        output.get_size = lambda: box["size"]

        renderer = repl_input.Renderer(Style([]), output)
        rows_below = renderer.__dict__.pop("_min_available_height", 0)
        renderer.__class__ = repl_input.SnugRenderer
        renderer._min_available_height = rows_below

        def resize(columns, rows):
            box["size"] = Size(rows=rows, columns=columns)

        return renderer, resize

    def test_it_never_asks_for_more_than_a_row(self):
        renderer, _ = self.renderer((80, 40))
        renderer._min_available_height = 30

        assert renderer._min_available_height == 1

    def test_a_rewrapped_rule_counts_twice(self):
        from prompt_toolkit.layout.screen import Char, Screen

        screen = Screen()
        for x in range(120):
            screen.data_buffer[0][x] = Char("-", "")
        screen.data_buffer[1][0] = Char(">", "")

        assert repl_input.reflowed_rows(screen, 2, 80) == 3
        assert repl_input.reflowed_rows(screen, 2, 120) == 2

    def test_unstyled_trailing_space_does_not_wrap(self):
        from prompt_toolkit.layout.screen import Char, Screen

        screen = Screen()
        screen.data_buffer[0][0] = Char("x", "")
        screen.data_buffer[0][100] = Char(" ", "")

        assert repl_input.reflowed_rows(screen, 1, 80) == 1

    def test_a_resize_tells_the_prompt(self, monkeypatch):
        from types import SimpleNamespace
        from prompt_toolkit.data_structures import Point, Size
        from prompt_toolkit.layout.screen import Char, Screen

        renderer, resize = self.renderer((120, 40))
        screen = Screen()
        for x in range(120):
            screen.data_buffer[0][x] = Char("-", "")
        renderer._last_screen = screen
        renderer._last_size = Size(rows=40, columns=120)
        renderer._cursor_pos = Point(x=2, y=1)

        told = []
        renderer.on_resize = lambda: told.append(True)
        erased_from = []
        renderer.erase = lambda **_: erased_from.append(renderer._cursor_pos)
        renderer.request_absolute_cursor_position = lambda: None

        resize(80, 40)
        monkeypatch.setattr(
            repl_input.Renderer, "render", lambda *a, **k: None
        )
        renderer.render(SimpleNamespace(future=None), SimpleNamespace())

        assert told == [True]
        assert erased_from == [Point(x=2, y=2)]

    def test_a_shorter_frame_is_drawn_fresh(self, monkeypatch):
        from types import SimpleNamespace
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.layout.screen import Screen

        renderer, _ = self.renderer((80, 40))
        renderer._last_size = Size(rows=40, columns=80)
        renderer._last_screen = Screen(initial_height=10)
        renderer._min_available_height = 30

        erased = []
        renderer.erase = lambda **_: erased.append(True)
        monkeypatch.setattr(
            repl_input.Renderer, "render", lambda *a, **k: None
        )
        layout = SimpleNamespace(container=SimpleNamespace(
            preferred_height=lambda w, h: SimpleNamespace(preferred=3)
        ))

        renderer.render(SimpleNamespace(), layout)

        assert erased == [True]
        # Still at the same spot, so what is below it has not changed.
        assert renderer.rows_below == 30

    def test_a_frame_the_same_height_is_left_to_diff(self, monkeypatch):
        from types import SimpleNamespace
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.layout.screen import Screen

        renderer, _ = self.renderer((80, 40))
        renderer._last_size = Size(rows=40, columns=80)
        renderer._last_screen = Screen(initial_height=3)

        erased = []
        renderer.erase = lambda **_: erased.append(True)
        monkeypatch.setattr(
            repl_input.Renderer, "render", lambda *a, **k: None
        )
        layout = SimpleNamespace(container=SimpleNamespace(
            preferred_height=lambda w, h: SimpleNamespace(preferred=3)
        ))

        renderer.render(SimpleNamespace(), layout)

        assert erased == []



class TestScreenRedrawn:
    """After a full redraw the next prompt must not see a resize that
    has already been dealt with, or it asks for another, forever."""

    def test_it_measures_the_way_the_renderer_does(self, monkeypatch):
        from types import SimpleNamespace
        from prompt_toolkit.data_structures import Size

        # prompt_toolkit's Windows output is a column narrower than
        # shutil's figure; the settled size has to be prompt_toolkit's.
        ptk = Size(rows=30, columns=99)
        monkeypatch.setattr(
            repl_input,
            "_session",
            SimpleNamespace(app=SimpleNamespace(
                output=SimpleNamespace(get_size=lambda: ptk)
            )),
        )
        monkeypatch.setattr(
            repl_input.shutil,
            "get_terminal_size",
            lambda: __import__("os").terminal_size((100, 30)),
        )
        monkeypatch.setattr(repl_input.SnugRenderer, "settled", None)

        repl_input.screen_redrawn()

        assert repl_input.SnugRenderer.settled == ptk

    def test_before_any_prompt_there_is_nothing_to_compare(
        self, monkeypatch
    ):
        monkeypatch.setattr(repl_input, "_session", None)
        monkeypatch.setattr(repl_input.SnugRenderer, "settled", "stale")

        repl_input.screen_redrawn()

        assert repl_input.SnugRenderer.settled is None
