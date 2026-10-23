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
    (folder / extensions.MANIFEST).write_text(json.dumps(manifest))
    for name, text in (files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
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
    # A button only for the terminal is not the page's to draw.
    assert [b["id"] for b in page["buttons"]] == ["tldr", "now", "fc"]


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
