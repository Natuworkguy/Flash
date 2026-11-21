import io
import re
import time

from flash import startup

PLAIN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def test_a_frame_fills_the_screen_and_says_what_is_going_on():
    out = startup.frame(80, 24, 0.0, 0.0, 1.0, "Waking up", 0)
    rows = out[len("\x1b[H"):].split("\n")
    assert len(rows) == 24
    # Every row is the full width, so the screen is filled edge to edge.
    for row in rows:
        assert len(PLAIN.sub("", row)) == 80
    text = PLAIN.sub("", out)
    assert "Waking up" in text
    # Two eyes, drawn in half blocks.
    assert text.count("█") > 20


def test_a_closed_eye_is_smaller_than_an_open_one():
    shut = PLAIN.sub("", startup.frame(80, 24, 0, 0, 0.1, "", 0))
    open_ = PLAIN.sub("", startup.frame(80, 24, 0, 0, 1.0, "", 0))
    assert shut.count("█") < open_.count("█")


def test_the_face_blinks_and_looks_within_bounds():
    face = startup.Face()
    seen_blink = False
    now = time.monotonic()
    for i in range(600):
        x, y, open_ = face.step(now + i / 30)
        assert -1 <= x <= 1 and -1 <= y <= 1 and 0 < open_ <= 1
        seen_blink = seen_blink or open_ < 0.5
    assert seen_blink


def test_only_the_terminal_chat_gets_it(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(startup.sys, "stdout", Tty())
    monkeypatch.setattr(startup.sys, "stdin", Tty())
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("FLASH_NO_LOADER", raising=False)
    assert startup.wanted([])
    assert not startup.wanted(["--web"])
    monkeypatch.setenv("FLASH_NO_LOADER", "1")
    assert not startup.wanted([])


def test_a_quick_start_shows_nothing(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    out = Tty()
    monkeypatch.setattr(startup.sys, "stdout", out)
    loader = startup.Loader().start()
    assert loader.stop() is False
    assert out.getvalue() == ""


def test_a_slow_start_shows_the_face_then_puts_the_screen_back(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    out = Tty()
    monkeypatch.setattr(startup.sys, "stdout", out)
    loader = startup.Loader().start()
    loader.say("Waking the sparks")
    time.sleep(startup.QUIET_FOR + 0.15)
    assert loader.stop() is True
    said = out.getvalue()
    assert said.startswith("\x1b[?1049h")
    assert said.endswith("\x1b[?1049l")
    assert "Waking the sparks" in PLAIN.sub("", said)
