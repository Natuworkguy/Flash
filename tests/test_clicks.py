# pylint: disable=C0114,C0115,C0116

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from flash import models, theme


def click(x, y):
    """A left click at column X, row Y (from 1), as a terminal sends it."""

    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m"


def wheel(down, x=5, y=2):
    return f"\x1b[<{65 if down else 64};{x};{y}M"


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(theme, "mouse_on", lambda: True)
    monkeypatch.setattr(models, "mouse_on", lambda: True)

    def run(keys, work):
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            with create_app_session(input=pipe, output=DummyOutput()):
                return work()

    return run


ROWS = [models.Model("alpha"), models.Model("beta"), models.Model("gamma")]


def test_a_click_picks_a_row(terminal):
    # The title is the first row; the models follow.
    picked = terminal(click(6, 3), lambda: models.choose(ROWS, title="Pick"))
    assert picked == "beta"


def test_the_wheel_moves_then_enter_picks(terminal):
    picked = terminal(wheel(True) + wheel(True) + "\r",
                      lambda: models.choose(ROWS, title="Pick"))
    assert picked == "gamma"


def test_yes_and_no_by_click_or_key(terminal, monkeypatch):
    monkeypatch.setattr(theme.console, "print", lambda *a, **kw: None)
    monkeypatch.setattr(theme.console.file, "write", lambda text: None)

    assert terminal("y", theme.yes_no) == "y"
    assert terminal("\x1b", theme.yes_no) == "n"
    assert terminal("\t\r", theme.yes_no) == "n"
    # "     [ Yes ]  [ No ]": No starts at column 15.
    assert terminal(click(17, 1), theme.yes_no) == "n"
    assert terminal(click(8, 1), theme.yes_no) == "y"


def test_without_mouse_it_is_a_typed_line(monkeypatch):
    monkeypatch.setenv("MOUSE", "0")
    monkeypatch.setattr(theme, "typed", lambda: "y")
    assert theme.yes_no() == "y"
