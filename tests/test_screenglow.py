# pylint: disable=C0114,C0115,C0116

import ast
import shutil
import subprocess  # nosec B404

from flash import computeruse, screenglow

# The real one: every test has a stand-in (see conftest).
LAUNCH = screenglow._launch


class Glow:
    def __init__(self):
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True


def test_it_glows_while_flash_has_the_computer(monkeypatch):
    started = []

    def launch(kind, folder):
        started.append((kind, folder))
        return Glow()

    monkeypatch.setattr(screenglow, "_launch", launch)
    monkeypatch.setattr(computeruse, "_watch", lambda: None)
    monkeypatch.setattr(computeruse, "system", lambda: "mac")
    computeruse.take("c1")
    computeruse.take("c1")
    assert screenglow.shown() and len(started) == 1  # nosec B101
    assert started[0][0] == "mac"  # nosec B101
    folder = started[0][1]

    computeruse.end("c1")
    assert not screenglow.shown()  # nosec B101
    assert not folder.exists()  # nosec B101


def test_it_steps_out_of_the_screenshot(monkeypatch):
    monkeypatch.setattr(screenglow, "_launch", lambda kind, folder: Glow())
    monkeypatch.setattr(screenglow, "HIDE_WAIT", 0)
    screenglow.show("linux")
    flag = screenglow._glow["folder"] / "hide"
    with screenglow.out_of_shot():
        assert flag.exists()  # nosec B101
    assert not flag.exists()  # nosec B101
    screenglow.hide()
    with screenglow.out_of_shot():
        pass


def test_a_glow_that_cannot_start_is_nothing(monkeypatch):
    def fails(kind, folder):
        raise OSError("no")

    monkeypatch.setattr(screenglow, "_launch", fails)
    screenglow.show("windows")
    assert not screenglow.shown()  # nosec B101


def test_each_draws_with_its_own_program(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(screenglow.shutil, "which", lambda name: name)
    monkeypatch.setattr(screenglow.subprocess, "Popen",
                        lambda argv, **kw: ran.append(argv) or Glow())
    monkeypatch.setenv("DISPLAY", ":0")
    for kind in ("mac", "windows", "linux"):
        assert LAUNCH(kind, tmp_path) is not None  # nosec B101
    assert ran[0][:3] == ["osascript", "-l", "JavaScript"]  # nosec B101
    assert ran[1][0] == "powershell" and "-STA" in ran[1]  # nosec B101
    assert ran[0][-1] == ran[1][-1] == "217,119,87"  # nosec B101
    assert ran[2][-1] == screenglow.ACCENT  # nosec B101
    assert (tmp_path / "glow.js").exists()  # nosec B101

    monkeypatch.delenv("DISPLAY")
    assert LAUNCH("linux", tmp_path) is None  # nosec B101


def test_the_scripts_parse():
    ast.parse(screenglow._LINUX_GLOW)
    if shutil.which("node"):
        node = subprocess.run(  # nosec B603 B607
            ["node", "--check", "-"], input=screenglow._MAC_GLOW,
            text=True, capture_output=True, check=False)
        assert node.returncode == 0, node.stderr  # nosec B101
