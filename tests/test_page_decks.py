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


def page_code(*calls: str) -> str:
    """The page's deck code, with what it leans on, for node."""

    page = PAGE.read_text(encoding="utf-8")
    return (
        "const CSS = { supports: (p, v) => !/nope/.test(v) };\n"
        + page[page.index("function frontMatter("):
               page.index("function frontTable(")]
        + page[page.index("const DECK_THEMES"):
               page.index("// The deck's look")]
        + "".join(f"console.log(JSON.stringify({call}));\n"
                  for call in calls)
    )


def run(script: str):
    done = subprocess.run(  # nosec B603
        [node, "-e", script], capture_output=True, text=True, check=True,
    )
    return json.loads(done.stdout)


def slides(text: str):
    """TEXT's slides as the page splits a deck, or None for a document
    that is not one: its own code, from the page, run in node."""

    return run(page_code(f"deckSlides({json.dumps(text)})"))


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


def lead(text: str) -> bool:
    return run(page_code(f"leadSlide({json.dumps(text)})"))


@pytest.mark.skipif(node is None, reason="needs node")
@pytest.mark.parametrize("text, centred", [
    ("# Ember\n\nCatch the idea before it cools.", True),
    ("## How many good ideas have you lost this week?", True),
    ("## The problem\n\n- Find the phone\n- Unlock it", False),
    ("## Two lines\n\nOne.\n\nTwo.", False),
    ("Just a sentence, no heading.", False),
])
def test_a_title_or_question_slide_is_centred(text, centred):
    assert lead(text) is centred  # nosec B101


def deck(text: str):
    return run(page_code(f"deckOf({json.dumps(text)})"))


@pytest.mark.skipif(node is None, reason="needs node")
def test_a_deck_is_styled_and_each_slide_by_its_directives():
    found = deck(
        "---\nmarp: true\ntheme: ember\naccent: '#ff7a2f'\nfont: serif\n"
        "transition: push\npaginate: false\n---\n\n# Title\n\n---\n\n"
        "<!-- _class: invert lead -->\n<!-- _transition: zoom -->\n"
        "## Turn\n\n---\n\n<!-- transition: flip -->\n"
        "![bg](https://example.com/a.jpg)\n## From here on\n\n---\n\n"
        "<!-- _background: nope -->\n## Last\n"
    )
    look, slides = found["look"], found["slides"]
    assert look["theme"] == "ember" and look["accent"] == "#ff7a2f"
    assert look["transition"] == "push" and look["paginate"] is False
    assert [s["transition"] for s in slides] == [
        "push", "zoom", "flip", "flip"]  # nosec B101
    assert slides[1]["classes"] == ["invert", "lead"]  # nosec B101
    assert slides[2]["image"] == "https://example.com/a.jpg"  # nosec
    assert slides[2]["text"] == "## From here on"  # nosec B101
    # What is not a colour is not used.
    assert slides[3]["background"] == ""  # nosec B101


@pytest.mark.skipif(node is None, reason="needs node")
def test_an_unknown_theme_or_transition_falls_back():
    found = deck("---\nmarp: true\ntheme: neon\ntransition: spin\n---\n\n"
                 "# One\n")
    assert found["look"]["theme"] == "default"  # nosec B101
    assert found["look"]["transition"] == "fade"  # nosec B101
