"""Hybrid file search: words, meaning, fusion and citations."""

from pathlib import Path

import pytest

from flash import filesearch, tools


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(filesearch, "FLASH_DIR", tmp_path / "flash")


def _folder(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "garden.md").write_text(
        "Tomatoes need full sun and deep watering twice a week.\n"
        "Basil grows well beside them.\n", encoding="utf-8")
    (docs / "car.txt").write_text(
        "Change the oil every five thousand miles.\n", encoding="utf-8")
    (docs / "skip.bin").write_bytes(b"\x00tomatoes")
    nested = docs / "node_modules"
    nested.mkdir()
    (nested / "x.md").write_text("tomatoes tomatoes", encoding="utf-8")
    return docs


def test_words_find_the_right_file(tmp_path):
    hits, how = filesearch.search("how often to water tomatoes",
                                  str(_folder(tmp_path)), embed_model="")
    assert how == "words"
    assert hits[0].path == "garden.md"
    assert all(h.path != "node_modules/x.md" for h in hits)
    assert (hits[0].start, hits[0].end) == (1, 2)


def test_bm25_weighs_rare_words():
    passages = [{"text": "the the the cat"}, {"text": "the dog"},
                {"text": "the bird"}]
    ranked = filesearch.bm25("the cat", passages)
    assert ranked[0][0] == 0


def test_fuse_rewards_both_rankings():
    fused = filesearch.fuse([(1, 9.0), (2, 5.0)], [(2, 0.9), (3, 0.8)])
    assert fused[0][0] == 2


class _Embedder:
    def embed(self, model, input):
        def vec(text):
            text = text.lower()
            return [float("oil" in text or "car" in text or "engine" in text),
                    float("sun" in text or "garden" in text)]
        return {"embeddings": [vec(t) for t in input]}


def test_meaning_finds_what_words_miss(tmp_path):
    hits, how = filesearch.search("engine care", str(_folder(tmp_path)),
                                  client=_Embedder(), embed_model="e")
    assert how == "words and meaning"
    assert hits[0].path == "car.txt"


def test_a_failing_embedder_falls_back(tmp_path):
    class Broken:
        def embed(self, **_):
            raise RuntimeError("down")

    hits, how = filesearch.search("tomatoes", str(_folder(tmp_path)),
                                  client=Broken(), embed_model="e")
    assert "did not answer" in how and hits


def test_index_rereads_only_changed_files(tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    filesearch.index(folder)
    read = []
    real = filesearch._read
    monkeypatch.setattr(filesearch, "_read",
                        lambda p: read.append(p.name) or real(p))
    filesearch.index(folder)
    assert read == []
    import os
    os.utime(folder / "car.txt", (1, 1))
    filesearch.index(folder)
    assert read == ["car.txt"]


def test_cited_numbers_the_evidence(tmp_path):
    folder = str(_folder(tmp_path))
    hits, _ = filesearch.search("tomatoes", folder, embed_model="")
    text = filesearch.cited(hits, folder)
    assert text.startswith("[e1] ") and "garden.md:1-2" in text
    assert filesearch.cited([], folder).startswith("Nothing")


def test_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "")
    out = tools.FUNCTIONS["search_files"](
        query="water tomatoes", folder=str(_folder(tmp_path)))
    assert "[e1]" in out and "Cite these" in out
    assert tools.search_files("x", str(tmp_path / "nope")).startswith(
        "Error")
    names = [t["function"]["name"] for t in tools.tools]
    assert "search_files" in names
