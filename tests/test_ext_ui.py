"""Tests for what extensions change of the web page and the terminal."""

import json
import shutil
import sys

import pytest

from flash import ext_ui, extensions, repl_input, web
from flash.extensions import ExtensionError

NOW_PY = """\
import json, sys
asked = json.load(sys.stdin)
print(json.dumps({"say": "14C in Paris", "insert": asked["text"] + "!"}))
"""


def make(folder, ui, files=None, commands=None):
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "weather", "ui": ui,
                "commands": commands or []}
    (folder / extensions.MANIFEST).write_text(json.dumps(manifest),
                                              encoding="utf-8")
    for name, text in (files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


def everything(folder):
    return make(folder, {
        "web": "web", "css": "style.css", "script": "main.js",
        "panels": [{"id": "radar", "label": "Radar", "icon": "globe",
                    "file": "radar.html"}],
        "buttons": [
            {"id": "tldr", "label": "TL;DR", "where": ["message"],
             "prompt": "Sum up: $TEXT"},
            {"id": "now", "label": "Now", "where": ["header", "terminal"],
             "key": "m-w", "run": [sys.executable, "./now.py"]},
            {"id": "fc", "label": "Forecast", "where": ["sidebar"],
             "command": "forecast"},
        ],
        "status": {"run": [sys.executable, "./status.py"], "every": 5},
        "greeting": "What's the sky doing?",
        "suggestions": ["Forecast for Paris"],
        "placeholder": "Ask about the weather",
        "banner": "banner.txt",
    }, {
        "web/style.css": "body{}", "web/main.js": "Flash.toast('hi')",
        "web/radar.html": "<h1>Radar</h1>", "web/logo.png": "png",
        "now.py": NOW_PY, "status.py": "print('  cloudy, 14C  ')",
        "banner.txt": "~ weather ~", "forecast.md": "Forecast: $ARGUMENTS",
        "secret.txt": "keep out",
    }, commands=[{"name": "forecast", "prompt": "forecast.md"}])


def install(folder):
    dest = extensions.extensions_dir() / "weather"
    shutil.copytree(folder, dest)
    extensions.reload()
    ext_ui.forget_status()
    return extensions.find("weather")


def test_every_part_is_read_and_said_at_install(tmp_path):
    ext = extensions.load(everything(tmp_path / "w"))

    assert ext.ui.css == "style.css" and ext.ui.script == "main.js"
    assert [p.id for p in ext.ui.panels] == ["radar"]
    assert [b.where for b in ext.ui.buttons] == [
        ("message",), ("header", "terminal"), ("sidebar",)]
    said = "\n".join(ext.contents())
    assert "runs its own script in the web page: main.js" in said
    assert "TL;DR (message)" in said and "status line" in said

    page = ext.ui.summary("weather")
    assert page["css"] == "/ext/weather/style.css"
    assert page["panels"][0]["url"] == "/ext/weather/radar.html"
    # Each at an address of its own, with its X unless it says not.
    assert page["panels"][0]["route"] == "/x/weather/radar"
    assert page["panels"][0]["close"] is True
    # A button only for the terminal is not the page's to draw.
    assert [b["id"] for b in page["buttons"]] == ["tldr", "now", "fc"]


def test_a_panel_can_go_without_its_x(tmp_path):
    ext = extensions.load(make(tmp_path / "w", {
        "web": "web",
        "panels": [{"id": "home", "label": "Home", "file": "home.html",
                    "close": False}],
    }, {"web/home.html": "<h1>Home</h1>"}))
    assert ext.ui.summary("w")["panels"][0]["close"] is False


def test_a_panels_close_is_true_or_false(tmp_path):
    with pytest.raises(extensions.ExtensionError, match="true or false"):
        extensions.load(make(tmp_path / "w", {
            "web": "web",
            "panels": [{"id": "p", "label": "P", "file": "p.html",
                        "close": "no"}],
        }, {"web/p.html": "<p>"}))


def test_a_panel_address_opens_the_page():
    from flash import web

    assert web.PAGE_PATHS.match("/x/watch/watch")
    assert web.PAGE_PATHS.match("/x/my-ext_2/board/")
    assert not web.PAGE_PATHS.match("/x/watch")
    assert not web.PAGE_PATHS.match("/x/Watch/../etc")


@pytest.mark.parametrize("ui, why", [
    ({}, "changes nothing"),
    ({"css": "style.css"}, 'needs "web"'),
    ({"web": "web", "css": "../x.css"}, "outside"),
    ({"web": "web", "script": "missing.js"}, "no missing.js"),
    ({"web": "web", "css": "main.js"}, "is not a .css"),
    ({"buttons": [{"id": "a", "label": "A", "prompt": "x",
                   "command": "y"}]}, "one of"),
    ({"buttons": [{"id": "a", "label": "A", "command": "nope"}]},
     "no command /nope"),
    ({"buttons": [{"id": "a", "label": "A", "prompt": "x",
                   "where": ["terminal"]}]}, 'needs a "key"'),
    ({"buttons": [{"id": "a", "label": "A", "prompt": "x", "key": "c-c",
                   "where": ["terminal"]}]}, "not one Flash uses"),
    ({"buttons": [{"id": "a", "label": "A", "prompt": "x",
                   "where": ["roof"]}]}, '"where"'),
    ({"status": {"run": ["x"], "every": 1}}, "5 to 3600"),
    ({"suggestions": ["x"] * 9}, "up to 8"),
])
def test_mistakes_are_refused(tmp_path, ui, why):
    folder = make(tmp_path / "w", ui, {"web/main.js": "", "x.css": ""})
    with pytest.raises(ExtensionError, match=why):
        extensions.load(folder)


def test_only_the_web_folder_is_served(tmp_path):
    install(everything(tmp_path / "w"))

    body, kind = ext_ui.web_file("weather", "radar.html")
    assert body == b"<h1>Radar</h1>" and kind.startswith("text/html")
    assert ext_ui.web_file("weather", "logo.png")[1] == "image/png"
    assert ext_ui.web_file("weather", "../secret.txt") is None
    assert ext_ui.web_file("weather", "../flash-extension.json") is None
    assert ext_ui.web_file("nobody", "radar.html") is None


def test_buttons_do_what_they_say(tmp_path):
    install(everything(tmp_path / "w"))

    assert ext_ui.pressed("weather", "tldr", "a long reply") == {
        "send": "Sum up: a long reply"}
    assert ext_ui.pressed("weather", "fc", "Paris") == {
        "send": "Forecast: Paris"}
    assert ext_ui.pressed("weather", "now", "hi") == {
        "say": "14C in Paris", "insert": "hi!"}
    assert ext_ui.pressed("weather", "gone")["say"] == "That button is gone."


def test_status_greeting_and_the_terminal(tmp_path):
    install(everything(tmp_path / "w"))

    assert ext_ui.status_lines(wait=True) == [("weather", "cloudy, 14C")]
    assert ext_ui.greeting() == "What's the sky doing?"
    assert ext_ui.placeholder() == "Ask about the weather"
    assert repl_input._suggestions_now() == ["Ask about the weather"]
    assert ext_ui.banners() == [("weather", "~ weather ~")]
    assert [b.id for _, b in ext_ui.terminal_buttons()] == ["now"]
    # The key is bound in the box.
    keys = repl_input.key_bindings()
    assert any(tuple(k.value if hasattr(k, "value") else k
                     for k in b.keys) == ("escape", "w")
               for b in keys.bindings)


def test_the_page_gets_it(tmp_path):
    install(everything(tmp_path / "w"))
    from flash import ai

    info = web.status(ai)
    assert info["extension_ui"][0]["greeting"] == "What's the sky doing?"

    class Session:
        hub = None

    said = web.command(Session(), {"name": "ext-button", "arg": "tldr",
                                   "extension": "weather", "text": "x"})
    assert said == {"send": "Sum up: x"}


def test_the_web_folder_is_behind_the_sign_in(tmp_path):
    import http.client
    import threading

    install(everything(tmp_path / "w"))
    srv = web.Server(0)
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        port = srv.server_address[1]

        def get(path, token):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Host": f"127.0.0.1:{port}"}
            if token:
                headers["X-Flash-Token"] = srv.token
            conn.request("GET", path, headers=headers)
            answer = conn.getresponse()
            return answer.status, answer.read()

        assert get("/ext/weather/radar.html", True) == (
            200, b"<h1>Radar</h1>")
        assert get("/ext/weather/radar.html", False)[0] == 403
        assert get("/ext/weather/..%2Fsecret.txt", True)[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_extension_can_ask_for_the_network(tmp_path):
    folder = make(tmp_path / "w", {"web": "web", "css": "x.css"},
                  {"web/x.css": ""})
    file = folder / extensions.MANIFEST
    manifest = json.loads(file.read_text(encoding="utf-8"))
    manifest["network"] = True
    file.write_text(json.dumps(manifest), encoding="utf-8")
    ext = extensions.load(folder)
    assert ext.network is True
    assert any("--lan" in line for line in ext.contents())

    manifest["network"] = "yes"
    file.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(extensions.ExtensionError, match="true or false"):
        extensions.load(folder)


def surfaces(folder, extra=None, name="weather"):
    ui = {
        "web": "web",
        "widgets": [{"id": "today", "label": "Today", "file": "today.html",
                     "height": 140}],
        "docks": [{"id": "radar", "label": "Radar", "icon": "globe",
                   "file": "radar.html", "width": 380}],
        "blocks": [{"lang": "forecast", "file": "card.html",
                    "about": "a forecast card from JSON {city, days}"}],
        "buttons": [
            {"id": "shout", "label": "Shout", "where": ["palette", "input"],
             "shortcut": "Alt+Shift+Y", "prompt": "Shout: $TEXT"},
            {"id": "define", "label": "Define", "where": ["selection"],
             "prompt": "Define: $TEXT"},
        ],
    }
    ui.update(extra or {})
    folder = make(folder, ui, {"web/today.html": "<p>t</p>",
                               "web/radar.html": "<p>r</p>",
                               "web/card.html": "<p>c</p>"})
    manifest = json.loads((folder / extensions.MANIFEST).read_text(
            encoding="utf-8"))
    manifest["name"] = name
    (folder / extensions.MANIFEST).write_text(json.dumps(manifest),
                                              encoding="utf-8")
    return folder


def test_widgets_docks_blocks_and_new_places_are_read(tmp_path):
    ext = extensions.load(surfaces(tmp_path / "w"))
    drawn = ext.ui.summary("weather")
    assert drawn["widgets"] == [{"id": "today", "label": "Today",
                                 "height": 140,
                                 "url": "/ext/weather/today.html"}]
    assert drawn["docks"][0]["width"] == 380
    assert drawn["docks"][0]["url"] == "/ext/weather/radar.html"
    assert drawn["blocks"] == [{"lang": "forecast", "height": 240,
                                "url": "/ext/weather/card.html"}]
    shout = next(b for b in drawn["buttons"] if b["id"] == "shout")
    assert shout["where"] == ["palette", "input"]
    assert shout["shortcut"] == "alt+shift+y"
    said = " ".join(ext.contents())
    assert "beside the chat: Radar" in said
    assert "cards on a new chat: Today" in said
    assert "```forecast" in said


@pytest.mark.parametrize("extra, why", [
    ({"blocks": [{"lang": "python", "file": "card.html"}]}, "block"),
    ({"blocks": [{"lang": "ui", "file": "card.html"}]}, "block"),
    ({"blocks": [{"lang": "x", "file": "card.html"}]}, "block"),
    ({"blocks": [{"lang": "card", "file": "nope.html"}]}, "nope.html"),
    ({"docks": [{"id": "d", "label": "D", "file": "radar.html",
                 "width": 2000}]}, "260 to 720"),
    ({"widgets": [{"id": "w", "label": "W", "file": "today.html",
                   "height": 5}]}, "60 to 480"),
    ({"buttons": [{"id": "b", "label": "B", "where": ["input"],
                   "shortcut": "ctrl+b", "prompt": "x"}]}, "shortcut"),
    ({"buttons": [{"id": "b", "label": "B", "where": ["input"],
                   "shortcut": "w", "prompt": "x"}]}, "shortcut"),
    ({"buttons": [{"id": "b", "label": "B", "where": ["floating"],
                   "prompt": "x"}]}, "where"),
])
def test_bad_surfaces_are_refused_with_why(tmp_path, extra, why):
    with pytest.raises(ExtensionError, match=why):
        extensions.load(surfaces(tmp_path / "w", extra))


def test_blocks_reach_the_system_prompt(tmp_path):
    install(surfaces(tmp_path / "w"))
    extensions.reload()
    said = extensions.system_prompt()
    assert "```forecast (from weather): a forecast card" in said
    assert ext_ui.blocks_taken() == {"forecast": "weather"}


def test_a_block_another_extension_draws_refuses_the_install(tmp_path):
    install(surfaces(tmp_path / "w"))
    extensions.reload()
    other = extensions.load(surfaces(tmp_path / "o", name="other"))
    found = extensions.clashes(other, frozenset(), frozenset())
    assert any("```forecast blocks are drawn by weather" in c for c in found)
