"""Tests for the copy of the screen the console keeps for a redraw."""

import io
import time

from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from flash.theme import ScreenConsole


def console(width=60):
    return ScreenConsole(
        file=io.StringIO(),
        force_terminal=True,
        width=width,
        color_system=None,
    )


def replayed(kept, width):
    """What `kept` prints as, on a terminal `width` wide."""

    c = console(width)
    c.transcript = kept.transcript
    c.replay()
    return c.file.getvalue()


class TestWhatIsKept:
    def test_a_print_is_kept(self):
        c = console()
        c.print(Text("hello"))

        assert len(c.transcript) == 1

    def test_a_bare_print_is_a_blank_line_and_kept(self):
        c = console()
        c.print()

        assert len(c.transcript) == 1

    def test_a_transient_live_leaves_nothing(self):
        c = console()

        with Live(Text("thinking"), console=c, transient=True):
            time.sleep(0.02)

        assert c.transcript == []

    def test_a_status_spinner_leaves_nothing(self):
        c = console()

        with c.status("working"):
            time.sleep(0.02)

        assert c.transcript == []

    def test_a_capture_is_not_on_screen_so_is_not_kept(self):
        c = console()

        with c.capture():
            c.print("off screen")

        assert c.transcript == []

    def test_echoed_text_is_kept_once(self):
        c = console()
        c.echo("raw output\n")

        assert c.transcript == ["raw output\n"]
        assert c.file.getvalue() == "raw output\n"

    def test_forgetting_empties_it(self):
        c = console()
        c.print("a")
        c.forget()

        assert c.transcript == []


class TestReplay:
    def test_it_comes_back_in_order(self):
        c = console()
        c.print(Text("you: hi"))
        c.echo("raw\n")
        c.print(Text("  Run it? y/n "), end="")
        c.keep("y\n")
        c.print(Text("done"))

        assert replayed(c, 60).splitlines() == [
            "you: hi", "raw", "  Run it? y/n y", "done",
        ]

    def test_it_wraps_for_the_new_width(self):
        c = console(80)
        c.print(Markdown("word " * 20))

        narrow = replayed(c, 30)

        assert all(len(line) <= 30 for line in narrow.splitlines())
        assert len(narrow.splitlines()) > 1

    def test_replaying_keeps_nothing_new(self):
        c = console()
        c.print("a")
        c.replay()

        assert len(c.transcript) == 1
