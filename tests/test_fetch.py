# pylint: disable=C0114,C0115,C0116

import io
import urllib.error

from flash import tools
from flash.tools import fetch

HTML = b"""<html><head><title>Docs</title>
<style>body { color: red }</style></head>
<body><h1>Install</h1><p>Run <code>pip install flash</code>.</p>
<script>console.log("ignore me")</script>
<ul><li>First</li><li>Second</li></ul></body></html>"""


class _Response(io.BytesIO):
    def __init__(self, body, content_type="text/html; charset=utf-8",
                 url="https://example.com/docs"):
        super().__init__(body)
        self._url = url
        self.headers = _Headers(content_type)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Headers:
    def __init__(self, content_type):
        self._content_type = content_type

    def get_content_type(self):
        return self._content_type.split(";")[0].strip()

    def get(self, _name, default=""):
        return self._content_type or default


def _serve(monkeypatch, response):
    monkeypatch.setattr(
        tools.urllib.request, "urlopen", lambda *_a, **_kw: response
    )


def test_fetch_returns_the_readable_text(monkeypatch):
    _serve(monkeypatch, _Response(HTML))

    out = fetch("https://example.com/docs")

    assert "URL: https://example.com/docs" in out  # nosec B101
    assert "Title: Docs" in out  # nosec B101
    assert "Install" in out  # nosec B101
    assert "pip install flash" in out  # nosec B101


def test_fetch_drops_script_and_style_content(monkeypatch):
    _serve(monkeypatch, _Response(HTML))

    out = fetch("https://example.com/docs")

    assert "ignore me" not in out  # nosec B101
    assert "color: red" not in out  # nosec B101


def test_fetch_keeps_block_elements_apart(monkeypatch):
    _serve(monkeypatch, _Response(HTML))

    out = fetch("https://example.com/docs")

    assert "FirstSecond" not in out  # nosec B101


def test_fetch_leaves_non_html_alone(monkeypatch):
    # An HTML parser would eat the angle brackets a JSON payload uses as
    # data, so anything that is not HTML comes back untouched.
    body = b'{"tag": "<b>", "n": 1}'
    _serve(monkeypatch, _Response(body, content_type="application/json"))

    out = fetch("https://api.example.com")

    assert '{"tag": "<b>", "n": 1}' in out  # nosec B101


def test_fetch_reports_the_url_it_ended_on(monkeypatch):
    _serve(monkeypatch, _Response(HTML, url="https://example.com/final"))

    out = fetch("https://example.com")

    assert "URL: https://example.com/final" in out  # nosec B101


def test_fetch_truncates_a_long_page(monkeypatch):
    body = b"<html><body><p>" + b"x" * 60000 + b"</p></body></html>"
    _serve(monkeypatch, _Response(body))

    out = fetch("https://example.com/long")

    assert "truncated" in out  # nosec B101
    assert len(out) < 60000  # nosec B101


def test_fetch_refuses_a_non_web_scheme():
    for url in ("file:///etc/passwd", "ftp://example.com", "data:text/html,x"):
        assert "only handles http" in fetch(url)  # nosec B101


def test_fetch_reports_an_http_error(monkeypatch):
    def _raise(*_a, **_kw):
        raise urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", None, None
        )

    monkeypatch.setattr(tools.urllib.request, "urlopen", _raise)

    assert "HTTP 404" in fetch("https://example.com")  # nosec B101


def test_fetch_reports_an_unreachable_host(monkeypatch):
    def _raise(*_a, **_kw):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(tools.urllib.request, "urlopen", _raise)

    assert "could not fetch" in fetch("https://nowhere.invalid")  # nosec B101


def test_fetch_reports_a_page_with_nothing_at_all(monkeypatch):
    _serve(monkeypatch, _Response(b"<html><body></body></html>"))

    out = fetch("https://example.com/empty")

    assert "no readable text" in out  # nosec B101
    assert "JavaScript" in out  # nosec B101


def test_fetch_survives_broken_markup(monkeypatch):
    _serve(monkeypatch, _Response(b"<html><p>kept<<<>>> <div"))

    assert "kept" in fetch("https://example.com/broken")  # nosec B101


def test_fetch_is_registered_as_a_tool():
    assert tools.FUNCTIONS["fetch"] is fetch  # nosec B101
    assert any(  # nosec B101
        t["function"]["name"] == "fetch" for t in tools.tools
    )


# A page whose sections are filled in client-side: empty body, real head.
SHELL_PAGE = b"""<html><head>
<title>Flash CLI</title>
<meta name="description" content="Local AI shell.">
<meta property="og:description" content="Social card copy.">
</head><body>
<section data-include="partials/hero.html"></section>
<script type="module" src="app.js"></script>
</body></html>"""


def test_fetch_falls_back_to_the_head_when_the_body_is_empty(monkeypatch):
    _serve(monkeypatch, _Response(SHELL_PAGE))

    out = fetch("https://flashproject.dev")

    assert "Title: Flash CLI" in out  # nosec B101
    assert "Description: Local AI shell." in out  # nosec B101
    assert "JavaScript" in out  # nosec B101
    assert "screenshot" in out  # nosec B101


def test_fetch_prefers_the_description_over_the_social_card(monkeypatch):
    # og:description is written for a preview box, not for a reader.
    _serve(monkeypatch, _Response(SHELL_PAGE))

    assert "Social card copy" not in fetch("https://example.com")  # nosec B101


def test_fetch_uses_the_social_card_when_it_is_all_there_is(monkeypatch):
    page = b"""<html><head><title>T</title>
    <meta property="og:description" content="Only this."></head>
    <body></body></html>"""
    _serve(monkeypatch, _Response(page))

    assert "Only this." in fetch("https://example.com")  # nosec B101


def test_fetch_carries_the_description_on_a_normal_page(monkeypatch):
    page = b"""<html><head><title>T</title>
    <meta name="description" content="What it is."></head>
    <body><p>Body text here.</p></body></html>"""
    _serve(monkeypatch, _Response(page))

    out = fetch("https://example.com")

    assert "Description: What it is." in out  # nosec B101
    assert "Body text here." in out  # nosec B101
