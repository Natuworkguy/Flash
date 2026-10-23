# pylint: disable=C0114,C0115,C0116

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

PAGE = Path(__file__).parent.parent / "flash" / "web" / "index.html"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="needs node")


def pulled(text: str) -> tuple[str, list]:
    """TEXT as the page's math pass leaves it, and the math it took out:
    its own code, from the page, run in node."""

    page = PAGE.read_text(encoding="utf-8")
    start = page.index("// Math: $...$")
    end = page.index("function renderMath(")
    script = page[start:end] + (
        "const spans = [];\n"
        f"const out = pullMath({json.dumps(text)}, spans);\n"
        "console.log(JSON.stringify([out, spans]));\n"
    )
    done = subprocess.run(  # nosec B603
        [node, "-e", script], capture_output=True, text=True, check=True,
    )
    out, spans = json.loads(done.stdout)
    return out, spans


def test_words_in_escaped_brackets_stay_text():
    # A document edited in the page keeps [brackets] as \[brackets\].
    out, spans = pulled("Stage cues are in \\[brackets\\].")

    assert spans == []  # nosec B101
    assert out == "Stage cues are in \\[brackets\\]."  # nosec B101


@pytest.mark.parametrize("text, tex", [
    ("\\[ x^2 + y^2 = z^2 \\]", " x^2 + y^2 = z^2 "),
    ("\\[\\frac{a}{b}\\]", "\\frac{a}{b}"),
    ("$$E = mc^2$$", "E = mc^2"),
])
def test_real_display_math_is_still_math(text, tex):
    _, spans = pulled(text)

    assert [(s["tex"], s["display"]) for s in spans] == [(tex, True)]
