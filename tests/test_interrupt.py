# pylint: disable=C0114,C0116

import io
import signal

import pytest

from flash import interrupt, repl_input


@pytest.fixture(autouse=True)
def restore_sigint():
    before = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, before)


def test_the_first_press_hints_and_the_second_leaves(monkeypatch):
    said = io.StringIO()
    monkeypatch.setattr("sys.stderr", said)
    interrupt.guard("Press it again")

    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    assert "Press it again" in said.getvalue()  # nosec B101

    with pytest.raises(KeyboardInterrupt):
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)


def test_a_slow_second_press_only_hints_again(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(interrupt.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("sys.stderr", io.StringIO())
    interrupt.guard()

    interrupt._pressed(signal.SIGINT, None)
    clock[0] += interrupt.EXIT_PRESS_SECONDS + 0.1
    interrupt._pressed(signal.SIGINT, None)  # armed again, not out

    with pytest.raises(KeyboardInterrupt):
        interrupt._pressed(signal.SIGINT, None)


def test_release_hands_a_waiting_press_to_the_prompt(monkeypatch):
    monkeypatch.setattr("sys.stderr", io.StringIO())
    monkeypatch.setattr(repl_input, "_exit_armed_until", 0.0)
    interrupt.guard()
    interrupt._pressed(signal.SIGINT, None)

    until = interrupt.release()

    handler = signal.getsignal(signal.SIGINT)
    assert handler is signal.default_int_handler  # nosec B101
    assert until > 0  # nosec B101
    repl_input.arm_exit(until)
    assert repl_input._exit_armed_until == until  # nosec B101


def test_nothing_waiting_hands_over_nothing():
    interrupt.guard()
    assert interrupt.release() == 0.0  # nosec B101


@pytest.mark.parametrize(
    "argv, cli",
    [
        (["/usr/local/bin/flash"], True),
        (["run.py"], True),
        (["/home/me/Flash/flash/__main__.py"], True),
        (["/usr/lib/python3/site-packages/pytest/__main__.py"], False),
        (["/usr/bin/pytest"], False),
        ([], False),
    ],
)
def test_only_flash_itself_guards_from_the_start(monkeypatch, argv, cli):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Tty())
    assert interrupt.started_as_cli(argv) is cli  # nosec B101
