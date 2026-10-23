# pylint: disable=C0114,C0115,C0116

import http.client
import json
import shutil
import subprocess  # nosec B404
import threading
from pathlib import Path

import pytest

from flash import web

PAGE = Path(__file__).parent.parent / "flash" / "web" / "index.html"

node = shutil.which("node")


def slides(text: str):
    """TEXT's slides as the page splits a deck, or None for a document
    that is not one: its own code, from the page, run in node."""

    page = PAGE.read_text(encoding="utf-8")
    script = (
        page[page.index("function frontMatter("):
             page.index("function frontTable(")]
        + page[page.index("function deckSlides("):
               page.index("const slideHtml")]
        + f"console.log(JSON.stringify(deckSlides({json.dumps(text)})));\n"
    )
    done = subprocess.run(  # nosec B603
        [node, "-e", script], capture_output=True, text=True, check=True,
    )
    return json.loads(done.stdout)


@pytest.mark.skipif(node is None, reason="needs node")
def test_a_deck_is_split_into_its_slides():
    deck = (
        "---\nmarp: true\ntheme: default\n---\n\n# Title\n\n---\n\n"
        "<!-- _class: lead -->\n## Two\n\n```yaml\n---\nkept: in code\n```\n"
        "\n---\n\n- three\n"
    )

    assert slides(deck) == [  # nosec B101
        "# Title",
        "## Two\n\n```yaml\n---\nkept: in code\n```",
        "- three",
    ]


@pytest.mark.skipif(node is None, reason="needs node")
@pytest.mark.parametrize("text", [
    "# Notes\n\n---\n\nNot a deck, only a rule.",
    "---\ntitle: Post\n---\n\nText",
    "---\nmarp: false\n---\n\n# One\n\n---\n\n# Two",
])
def test_a_document_that_does_not_say_so_is_not_a_deck(text):
    assert slides(text) is None  # nosec B101


def test_mermaid_is_served_with_the_page():
    srv = web.Server(0)
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", "/static/mermaid/mermaid.min.js")
        answer = conn.getresponse()
        body = answer.read()
    finally:
        srv.shutdown()
        srv.server_close()

    assert answer.status == 200  # nosec B101
    assert answer.getheader("Content-Type").startswith(  # nosec B101
        "text/javascript")
    assert b'globalThis["mermaid"]' in body  # nosec B101
