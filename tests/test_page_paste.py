# pylint: disable=C0114,C0115,C0116

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PAGE = HERE.parent / "flash" / "web" / "index.html"
DOM = HERE / "page_dom.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="needs node")

# What the slices of the page lean on, as far as these tests need it.
STUBS = """
function button(cls, iconName, tip, keys, onClick) {
  const b = el("button", cls, icon(iconName));
  b.type = "button";
  b.setAttribute("aria-label", tip);
  b.onclick = () => onClick(b);
  return b;
}
const fileSize = (n) => `${n} B`;
const extOf = (name) => name.split(".").pop().toUpperCase();
const clock = (s) => `${s}s`;
const mendKind = (f) => f;
const fileUrl = (f) => `/api/files/${f.id}`;
const audioPlayer = () => el("div", "audio");
const openViewer = (f) => said.commands.push({ name: "viewer", arg: f.id });
const input = { focus() {} };
const unattach = (item) =>
  said.commands.push({ name: "unattach", arg: item.name });
const flash = () => {};
const richText = (node, text) => { node.textContent = text; };
const closeForm = () => { said.closed = true; };
function openForm(html) {
  const form = el("form", "", `${html}<div class="form-error"></div>`);
  form.isConnected = true;
  said.form = form;
  return form;
}
"""


def _slice(page: str, start: str, end: str) -> str:
    return page[page.index(start):page.index(end)]


def run(script: str):
    """What SCRIPT prints as JSON, run in node after the page's pasted
    text code, drawn into a stand-in document."""

    page = PAGE.read_text(encoding="utf-8")
    code = "\n".join([
        DOM.read_text(encoding="utf-8"),
        STUBS,
        _slice(page, "// Text pasted in a big block goes with the message",
               "const uploadBody"),
        _slice(page, "function trayItem(item, remove) {",
               "// The team chat's own tray:"),
        _slice(page, "// Photos show as themselves and open in the panel",
               "// Who is speaking, above a reply"),
        f"(async () => {{ {script} }})().catch((e) => {{ "
        "console.log(JSON.stringify({ error: String(e && e.stack || e) }));"
        " });",
    ])
    done = subprocess.run(  # nosec B603
        [node, "-e", code], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )
    said = json.loads(done.stdout)
    assert "error" not in said, said.get("error")  # nosec B101
    return said


def test_only_a_big_block_is_a_card():
    said = run("""
      const lines = (n) =>
        Array.from({ length: n }, (_, i) => `line ${i}`).join("\\n");
      console.log(JSON.stringify({
        short: bigPaste("hello there"),
        empty: bigPaste(""),
        spaces: bigPaste(" ".repeat(5000)),
        at_chars: bigPaste("x".repeat(1000)),
        over_chars: bigPaste("x".repeat(1001)),
        nineteen: bigPaste(lines(19)),
        nineteen_and_breaks: bigPaste(lines(19) + "\\n\\n\\n"),
        twenty: bigPaste(lines(20)),
      }));
    """)

    assert said == {  # nosec B101
        "short": False, "empty": False, "spaces": False,
        "at_chars": False, "over_chars": True,
        "nineteen": False, "nineteen_and_breaks": False, "twenty": True,
    }


def test_the_box_shows_a_card_with_its_first_lines():
    said = run("""
      const text = "\\n\\n> Task :app:mergeDexRelease FAILED\\n"
        + "ERROR: /Users/me\\n" + "more\\n".repeat(300);
      const item = trayEntry(pastedFile(text));
      const node = trayItem(item);
      console.log(JSON.stringify({
        kind: item.kind, name: item.name, whole: item.pasted === text,
        cls: node.className, title: node.title,
        lines: texts(node, ".paste-lines")[0].split("\\n").slice(0, 2),
        cut: texts(node, ".paste-lines")[0].length,
        tag: texts(node, ".paste-tag"),
        remove: node.querySelectorAll(".unattach").length,
      }));
    """)

    assert said == {  # nosec B101
        "kind": "pasted", "name": "Pasted text.txt", "whole": True,
        "cls": "tray-item paste",
        "title": "304 lines · 1,554 characters",
        "lines": ["> Task :app:mergeDexRelease FAILED", "ERROR: /Users/me"],
        "cut": 300, "tag": ["Pasted"], "remove": 1,
    }


def test_in_the_box_it_opens_from_what_was_pasted():
    said = run("""
      const text = "first\\n" + "x".repeat(2000);
      const node = trayItem(trayEntry(pastedFile(text)));
      await node.querySelector(".paste-card").onclick();
      console.log(JSON.stringify({
        asked: said.commands.length,
        shown: said.form.querySelector(".paste-full").textContent === text,
        sub: texts(said.form, ".sub"),
      }));
    """)

    assert said == {  # nosec B101
        "asked": 0, "shown": True, "sub": ["2 lines · 2,006 characters"],
    }


def test_sent_it_shows_the_card_and_fetches_the_rest_once():
    said = run("""
      said.answer = (name, arg) => ({ text: "all\\nof it" });
      const f = { id: "abc", kind: "pasted", name: "Pasted text.txt",
                  preview: "all" };
      const doc = { id: "d", kind: "file", name: "notes.txt", size: 5 };
      const box = sentFiles([f, doc]);
      const card = box.querySelector(".paste-card");
      await card.onclick();
      const first = said.form.querySelector(".paste-full").textContent;
      await card.onclick();
      console.log(JSON.stringify({
        cards: texts(box, ".paste-lines"), docs: texts(box, ".doc .name"),
        asked: said.commands.map((c) => [c.name, c.arg]),
        first, again: said.form.querySelector(".paste-full").textContent,
      }));
    """)

    assert said == {  # nosec B101
        "cards": ["all"], "docs": ["notes.txt"],
        "asked": [["pasted-text", "abc"]],
        "first": "all\nof it", "again": "all\nof it",
    }
