# pylint: disable=C0114,C0115,C0116

import time

import pytest


def test_another_chromium_is_used_when_playwrights_own_is_missing(
        monkeypatch, tmp_path):
    from playwright.sync_api import Error as PlaywrightError

    from flash import browser

    # A Mac's cache, with an older build than this Playwright wants.
    shell = (tmp_path / "chromium_headless_shell-1234"
             / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    shell.parent.mkdir(parents=True)
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
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
                    "height:80px'>Hi</button><input id=i>", encoding="utf-8")
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


def _dash():
    from pathlib import Path

    from flash import browser

    pytest.importorskip("playwright")
    game = Path(__file__).parent.parent / "flash" / "games" / "dash.html"
    why = browser.open_page(game.as_uri(), width=900, height=420, wait_ms=100)
    if why:
        pytest.skip(why)
    return browser


def test_a_reflex_runs_every_frame_and_sees_the_canvas():
    browser = _dash()
    try:
        # Doing nothing, the square runs into the first spike.
        said, why = browser.play(
            "api.memory.red = api.memory.red || "
            "api.find('#ff3b5c', 236, 0, api.width) >= 0;",
            seconds=5, until="window.dash.over && document.title",
            start_keys=["r"])
        assert not why
        assert "crashed at" in said
        assert "actions" in said
    finally:
        browser.close_session()


def test_attempts_retry_after_a_death_and_keep_memory():
    browser = _dash()
    try:
        # Doing nothing dies at the first spike, twice; what it saw and
        # how many tries it has had are kept between them.
        said, why = browser.play(
            "const m = api.memory; m.tries = api.attempt; "
            "if (api.frame % 10 === 1) m.spikes = "
            "api.blobs('#ff3b5c', 0, 200, api.width, 241).length;"
            "if (m.logged !== api.attempt && m.spikes !== undefined) { "
            "m.logged = api.attempt; api.log('spikes', m.spikes, 'try', "
            "m.tries); }",
            seconds=12, start_keys=["r"], attempts=2,
            died="window.dash.over && !window.dash.won",
            restart_keys=["r"], progress="window.dash.progress()")
        assert not why
        assert "#1: died at" in said and "#2: died at" in said
        assert "Best: attempt" in said
        assert "spikes 1 try 1" in said and "try 2" in said
    finally:
        browser.close_session()


@pytest.mark.skipif(
    not __import__("os").environ.get("FLASH_SLOW_TESTS"),
    reason="twenty seconds of real time: FLASH_SLOW_TESTS=1 runs it")
def test_the_agents_reflex_beats_flash_dash():
    from pathlib import Path

    policy = (Path(__file__).parent.parent / "flash" / "games"
              / "dash-policy.js").read_text(encoding="utf-8")
    browser = _dash()
    try:
        said, why = browser.play(policy, seconds=40, start_keys=["r"],
                                 until="window.dash.over && document.title")
        assert not why
        assert "Flash Dash: won" in said
    finally:
        browser.close_session()


def _game_page(tmp_path):
    """A tiny game made for playing with Flash: it says how, gives a
    reflex, and shows what Flash says."""
    from flash import browser

    pytest.importorskip("playwright")
    page = tmp_path / "game.html"
    page.write_text(
        '<!doctype html><title>start</title>'
        '<meta name="flash-game" content="Press keys to score.">'
        '<meta name="flash-invite" content="join.html">'
        '<script type="text/flash-reflex">api.press(api.plan.key || "a");'
        '</script><canvas width="50" height="50"></canvas><script>'
        'window.heard = []; window.flashSay = t => heard.push(t);'
        'window.down = new Set(); addEventListener("keydown", e => '
        '{ down.add(e.key); document.title = [...down].join(""); });'
        'addEventListener("keyup", e => down.delete(e.key));</script>')
    why = browser.open_page(page.as_uri(), width=400, height=300, wait_ms=50)
    if why:
        pytest.skip(why)
    return browser, page


def test_a_game_page_says_how_to_play_with_flash(tmp_path):
    browser, page = _game_page(tmp_path)
    try:
        card = browser.game_card()
        assert card["about"] == "Press keys to score."
        assert card["invite"] == (tmp_path / "join.html").as_uri()
        assert card["reflex"] == 'api.press(api.plan.key || "a");'
    finally:
        browser.close_session()


def test_a_live_run_plays_on_between_looks_and_takes_a_plan(tmp_path):
    browser, _ = _game_page(tmp_path)
    try:
        reflex = browser.game_card()["reflex"]
        said, why = browser.play_live(reflex, seconds=60, look_every=0.3,
                                      say="hi")
        assert not why and "Started a live run" in said
        assert "Said in the game: hi" in said
        assert browser.live_running()
        # it keeps playing while nobody is looking
        time.sleep(0.3)
        said, why = browser.play_live(plan={"key": "b"}, look_every=0.3)
        assert not why and "Still playing" in said
        assert 'api.plan: {"key": "b"}' in said
        assert "title now: ab" in said
        # a new reflex swaps in without stopping, and stop ends it
        said, why = browser.play_live('api.log("new one")', look_every=0.2)
        assert "new one" in said
        said, why = browser.play_live(stop=True)
        assert "The live run is over" in said and "stopped by Flash" in said
        assert not browser.live_running()
        # keys it held are let go
        said, _ = browser.run_script("down.size")
        assert said.splitlines()[1] == "0"
    finally:
        browser.close_session()
