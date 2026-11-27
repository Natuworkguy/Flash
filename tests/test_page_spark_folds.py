"""Spark sections keep their fold state across detail redraws."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).parent.parent / "flash" / "web" / "index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def test_sections_fold_independently_and_remember_each_spark():
    page = PAGE.read_text(encoding="utf-8")
    start = page.index('  box.querySelectorAll("[data-fold]")')
    end = page.index('  box.querySelector(".spark-head").prepend', start)
    script = """
const saved = new Map();
const pref = (key, fallback) => saved.get(key) || fallback;
const savePref = (key, value) => saved.set(key, value);
const head = (kind) => ({
  dataset: {fold: kind}, attrs: {}, nextElementSibling: {hidden: false},
  parentElement: {classList: {toggle(name, on) { this[name] = on; }}},
  setAttribute(key, value) { this.attrs[key] = String(value); },
  getAttribute(key) { return this.attrs[key]; }
});
let heads;
const box = { querySelectorAll() { return heads; } };
let spark = {id: "first"};
function draw() {
  heads = [head("reports"), head("documents")];
""" + page[start:end] + """
}
draw();
const states = [];
states.push(heads.map(h => h.nextElementSibling.hidden));
heads[0].onclick();
states.push(heads.map(h => h.nextElementSibling.hidden));
draw();
states.push(heads.map(h => h.nextElementSibling.hidden));
heads[1].onclick();
heads[0].onclick();
draw();
states.push(heads.map(h => h.nextElementSibling.hidden));
spark = {id: "second"};
draw();
states.push(heads.map(h => h.nextElementSibling.hidden));
console.log(JSON.stringify(states));
"""
    done = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, check=True,
    )
    assert json.loads(done.stdout) == [
        [False, False], [True, False], [True, False],
        [False, True], [False, False],
    ]
