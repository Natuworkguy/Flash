# pylint: disable=C0114,C0116

import types

import pytest

from flash import ai, repl_input, theme


class FakeDock:
    def __init__(self):
        self.held = 0
        self.calls = []

    def pause(self):
        self.held += 1
        self.calls.append("pause")

    def resume(self):
        self.held -= 1
        self.calls.append("resume")


@pytest.fixture
def dock(monkeypatch):
    found = FakeDock()
    theme.set_dock(found)
    yield found
    theme.set_dock(None)


@pytest.mark.parametrize("result, sent", [
    ("look at the logs too", ("steer", "look at the logs too")),
    (("queue", "then write a test"), ("queue", "then write a test")),
    ("/stats", ("queue", "/stats")),
    ("!ls", ("queue", "!ls")),
    ("   ", None),
    (None, None),
    (repl_input._ASIDE, None),
])
def test_enter_steers_tab_queues_and_commands_wait(result, sent):
    assert repl_input.route(result) == sent


def test_the_dock_lists_what_waits_and_hands_it_over():
    dock = repl_input.Dock("❯ ", lambda: "", lambda: "ok")
    dock.sent = [("steer", "also the weather"), ("queue", "/stats")]
    dock.show("LOADER\n")

    rows = dock._rows()
    assert rows.startswith("LOADER\n")
    assert "steering: " in rows and "also the weather" in rows
    assert "queued: " in rows and "/stats" in rows

    assert dock.take("steer") == ["also the weather"]
    assert dock.sent == [("queue", "/stats")]


def test_a_question_steps_the_dock_aside_until_answered(dock, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda: "y")
    shown = []
    monkeypatch.setattr(theme.Console, "print",
                        lambda self, *a, **k: shown.append(dock.held))

    assert theme.confirm("Run it?") is True
    # Aside while the question showed, back once it was answered.
    assert shown[0] == 1
    assert dock.held == 0


def test_a_live_and_a_status_step_it_aside(dock):
    with theme.Live(theme.Text("x"), console=theme.Console(
            file=open("/dev/null", "w")), transient=True):
        assert dock.held == 1
    assert dock.held == 0

    with theme.console.status("working"):
        assert dock.held == 1
    assert dock.held == 0


def test_without_a_dock_nothing_steps_anywhere():
    theme.set_dock(None)

    with theme.screen_to_itself():
        pass
    assert not theme.dock_active()


def test_the_loader_runs_on_braille_or_ascii(monkeypatch):
    frames = [theme.loader_frame(n * theme.LOADER_SECONDS)
              for n in range(len(theme.LOADER_FRAMES))]
    assert frames == list(theme.LOADER_FRAMES)

    monkeypatch.setattr(theme, "can_encode", lambda text: False)
    assert theme.loader_frame(0) in theme.LOADER_FRAMES_ASCII


def test_carry_adds_to_what_is_already_in_the_box():
    repl_input._carried = ""
    repl_input.carry("first")
    repl_input.carry("second")

    assert repl_input._take_carried() == "first\nsecond"


def test_the_dock_is_for_typed_turns_in_a_terminal(monkeypatch):
    monkeypatch.setattr(ai.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(ai.sys.stdout, "isatty", lambda: True, raising=False)

    assert ai._dock_wanted(heard=False)
    assert not ai._dock_wanted(heard=True)
    monkeypatch.setenv("FLASH_NO_DOCK", "1")
    assert not ai._dock_wanted(heard=False)


def test_a_bare_bang_says_how_to_leave_shell_mode():
    def shown(text):
        ti = types.SimpleNamespace(
            lineno=0, document=types.SimpleNamespace(text=text),
            fragments=[("", ch) for ch in text],
        )
        done = repl_input.HideShellMark().apply_transformation(ti)
        return "".join(t for _, t in done.fragments)

    assert shown("!") == repl_input.SHELL_HINT
    assert shown("!ls") == "ls"
    assert shown("hello") == "hello"


def test_the_loader_is_drawn_in_the_dock(monkeypatch):
    seen = []

    class Dock:
        def show(self, rows):
            seen.append(rows)

    theme.set_dock(Dock())
    try:
        monkeypatch.setattr(ai, "_try_chat", lambda c, m, live, *a, **k: (
            live.update(theme.Text("⣾ Thinking… (1s)")), (None, None))[1])
        ai._chat_with_status(ai.console, None, [])
    finally:
        theme.set_dock(None)

    assert "Thinking" in seen[0]
    assert seen[-1] == ""
