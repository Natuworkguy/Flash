# pylint: disable=C0114,C0115,C0116

import pytest


def test_another_chromium_is_used_when_playwrights_own_is_missing(
        monkeypatch, tmp_path):
    from playwright.sync_api import Error as PlaywrightError

    from flash import browser

    # A Mac's cache, with an older build than this Playwright wants.
    shell = (tmp_path / "chromium_headless_shell-1234"
             / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    shell.parent.mkdir(parents=True)
    shell.write_text("#!/bin/sh\n")
    shell.chmod(0o755)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(browser.Path, "home", lambda: tmp_path / "nobody")
    tried = []

    class Chromium:
        def launch(self, **given):
            tried.append(given)
            if not given:
                raise PlaywrightError(
                    "Executable doesn't exist at /x/chromium-1300/chrome\n"
                    "Looks like Playwright was just installed or updated. "
                    "Please run the following command to download new "
                    "browsers: playwright install")
            return "a browser"

    class Driver:
        chromium = Chromium()

    assert browser.installed_chromiums() == [shell]  # nosec B101
    assert browser.launch_chromium(Driver()) == "a browser"  # nosec B101
    assert tried == [{}, {"executable_path": str(shell)}]  # nosec B101


def test_another_fault_is_not_hidden_by_trying_other_chromiums():
    from playwright.sync_api import Error as PlaywrightError

    from flash import browser

    class Chromium:
        def launch(self, **given):
            raise PlaywrightError("Target page, context or browser closed")

    class Driver:
        chromium = Chromium()

    with pytest.raises(PlaywrightError, match="closed"):
        browser.launch_chromium(Driver())


def test_the_user_can_take_the_browser_from_another_thread(tmp_path):
    import threading

    from flash import browser

    pytest.importorskip("playwright")
    page = tmp_path / "p.html"
    page.write_text("<button id=b onclick=\"document.title='clicked'\" "
                    "style='position:absolute;left:0;top:0;width:200px;"
                    "height:80px'>Hi</button><input id=i>")
    why = browser.open_page(page.as_uri(), width=400, height=300, wait_ms=0)
    if why:
        pytest.skip(why)
    seen = {}

    def server_thread():
        seen["take"] = browser.take_control(True)
        seen["agent"] = browser.interact("click", selector="#b")
        browser.user_act("click", 50, 40)
        seen["title"] = browser.where()[1]
        seen["frame"] = browser.live_frame()[:3]
        browser.take_control(False)

    try:
        thread = threading.Thread(target=server_thread)
        thread.start()
        thread.join(60)
        assert seen["take"]["width"] == 400
        assert seen["agent"] == ("", browser.TAKEN)
        assert seen["title"] == "clicked"
        assert seen["frame"] == b"\xff\xd8\xff"
        assert browser.interact("fill", selector="#i", value="x")[1] == ""
    finally:
        browser.take_control(False)
        browser.close_session()
