# pylint: disable=C0114,C0115,C0116

import plistlib
import sys
from pathlib import Path

import pytest

from flash import cli, launcher


def test_the_shortcut_opens_flash_in_the_browser_from_this_flash():
    argv = launcher.command()

    assert argv[-2:] == ["--uri", "flash://open"]
    assert Path(argv[0]).name.startswith(Path(sys.executable).name[:6])


def test_a_mac_gets_an_app_that_opens_flash_and_flash_links(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_host", lambda: "mac")
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launcher, "FLASH_DIR", tmp_path / ".flash")
    ran = []

    def run(argv, **_kw):
        # osacompile, standing in: an applet bundle, as a Mac makes one
        ran.append(argv)
        if argv[0] == "osacompile":
            app = Path(argv[2]) / "Contents"
            (app / "Resources").mkdir(parents=True)
            (app / "MacOS").mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps(
                {"CFBundleExecutable": "applet", "CFBundleName": "launcher"}))
            ran.append(Path(argv[3]).read_text(encoding="utf-8"))
        return launcher.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    said = launcher.install()

    app = tmp_path / "Applications" / "Flash.app" / "Contents"
    info = plistlib.loads((app / "Info.plist").read_bytes())
    script = ran[1]
    assert "~/Applications" in said and "flash:// links" in said
    assert info["CFBundleName"] == "Flash" and info["LSUIElement"]
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["flash"]
    # opened: the web UI; a link: the web UI with its prompt, both in the background
    assert "on run" in script and "'--uri' 'flash://open' >/dev/null 2>&1 &" in script
    assert "on open location theURL" in script and "'--web' '--uri'" in script
    assert "quoted form of theURL" in script
    assert (app / "Resources" / "applet.icns").read_bytes()[:4] == b"icns"
    assert any(argv[:3] == ["codesign", "--force", "--sign"] for argv in ran if isinstance(argv, list))
    assert launcher.remove() == "Removed the Flash shortcut."
    assert not (tmp_path / "Applications" / "Flash.app").exists()


def test_linux_gets_an_applications_menu_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_host", lambda: "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    launcher.install()

    entry = (tmp_path / "applications" / "flash.desktop").read_text(
        encoding="utf-8")
    assert "Name=Flash" in entry and "flash://open" in entry
    assert "Terminal=false" in entry and "flash-256.png" in entry
    assert launcher.remove().startswith("Removed")
    assert launcher.remove().startswith("There was no")


def test_windows_makes_a_start_menu_shortcut(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_host", lambda: "windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ran = {}

    def run(argv, env, **_kw):
        ran.update(env)
        Path(env["FLASH_LNK"]).write_bytes(b"lnk")
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.install()

    assert ran["FLASH_LNK"].endswith(
        str(Path("Start Menu") / "Programs" / "Flash.lnk"))
    assert "flash://open" in ran["FLASH_ARGS"]
    assert ran["FLASH_ICON"].endswith("flash.ico")


def test_the_flag_adds_and_removes():
    assert cli.parse_args(["--shortcut"]).shortcut == "add"
    assert cli.parse_args(["--shortcut", "remove"]).shortcut == "remove"
    with pytest.raises(SystemExit):
        cli.parse_args(["--shortcut", "sideways"])


def test_with_no_console_flash_writes_to_a_log(monkeypatch, tmp_path):
    from flash import __main__ as entry

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    entry._no_console()  # pylint: disable=protected-access
    print("hello from the background", flush=True)
    sys.stdout.close()

    log = tmp_path / ".flash" / "background.log"
    assert "hello from the background" in log.read_text(encoding="utf-8")
