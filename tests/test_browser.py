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
