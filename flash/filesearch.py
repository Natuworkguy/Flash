"""Searching a folder of documents by meaning and by words, with citations.

grep finds the words typed; this finds the passages that answer a
question. Each file is cut into passages of a few lines, and every
passage is ranked two ways: by its words (BM25, which weighs a rare word
over a common one), and, when an embedding model is set (EMBED_MODEL, a
model Ollama serves for embeddings), by what it means. The two rankings
are merged by reciprocal rank fusion, so a passage near the top of
either comes out near the top. Each result carries its file and lines,
numbered [e1], [e2]..., for the answer to cite.

The index is kept in ~/.flash/search, one per folder, and only files
changed since are read again.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .paths import FLASH_DIR

TEXT = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml",
        ".yaml", ".toml", ".html", ".css", ".rs", ".go", ".java", ".c",
        ".h", ".cpp", ".rb", ".sh", ".csv", ".tex", ".rst", ".org", ".swift",
        ".kt", ".sql", ".ini", ".cfg"}
DOCUMENTS = {".pdf", ".docx"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".next", ".cache", "target"}
MOST_FILES = 4000
MOST_BYTES = 2_000_000
LINES = 12
OVERLAP = 3
RRF_K = 60
_WORD = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class Hit:
    path: str
    start: int
    end: int
    text: str
    score: float


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if len(w) > 1]


def _store(folder: Path) -> Path:
    key = hashlib.sha256(str(folder).encode()).hexdigest()[:16]
    return FLASH_DIR / "search" / f"{key}.json"


def _read(path: Path) -> str:
    if path.suffix.lower() in DOCUMENTS:
        from .documents import extract_document_text

        try:
            return extract_document_text(str(path)) or ""
        except Exception:  # noqa: BLE001  (a document it cannot open)
            return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _files(folder: Path) -> list[Path]:
    found = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith(".")]
        for name in names:
            path = Path(root) / name
            if path.suffix.lower() in TEXT | DOCUMENTS:
                try:
                    if path.stat().st_size <= MOST_BYTES:
                        found.append(path)
                except OSError:
                    continue
            if len(found) >= MOST_FILES:
                return found
    return found


def _passages(text: str) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    out = []
    step = LINES - OVERLAP
    for at in range(0, max(1, len(lines)), step):
        chunk = "\n".join(lines[at:at + LINES]).strip()
        if chunk:
            out.append((at + 1, min(len(lines), at + LINES), chunk))
    return out


def index(folder: Path) -> list[dict]:
    """Every passage in FOLDER, read again only where a file changed."""

    folder = folder.expanduser().resolve()
    store = _store(folder)
    try:
        kept = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    files = kept.get("files", {}) if isinstance(kept, dict) else {}
    fresh: dict[str, Any] = {}
    for path in _files(folder):
        rel = str(path.relative_to(folder))
        stamp = path.stat().st_mtime
        held = files.get(rel)
        if held and held.get("stamp") == stamp:
            fresh[rel] = held
            continue
        fresh[rel] = {"stamp": stamp, "passages": [
            {"start": a, "end": b, "text": t[:3000]}
            for a, b, t in _passages(_read(path))]}
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"folder": str(folder), "files": fresh}),
                     encoding="utf-8")
    return [{"path": rel, **p} for rel, f in fresh.items()
            for p in f["passages"]]


def bm25(query: str, passages: list[dict], k1: float = 1.5,
         b: float = 0.75) -> list[tuple[int, float]]:
    """(passage index, score) for those that share a word with QUERY,
    best first."""

    asked = _words(query)
    if not asked or not passages:
        return []
    docs = [Counter(_words(p["text"])) for p in passages]
    sizes = [sum(d.values()) for d in docs]
    mean = sum(sizes) / len(sizes) or 1
    having = Counter(w for d in docs for w in set(d) if w in asked)
    n = len(docs)
    scores = []
    for i, doc in enumerate(docs):
        score = 0.0
        for word in asked:
            tf = doc.get(word, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - having[word] + 0.5) / (having[word] + 0.5))
            score += idf * tf * (k1 + 1) / (
                tf + k1 * (1 - b + b * sizes[i] / mean))
        if score:
            scores.append((i, score))
    return sorted(scores, key=lambda s: -s[1])


def _embed(client: Any, model: str, texts: list[str]) -> list[list[float]]:
    said = client.embed(model=model, input=texts)
    return list(getattr(said, "embeddings", None) or said["embeddings"])


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


def by_meaning(client: Any, model: str, query: str, passages: list[dict],
               keep: int = 200) -> list[tuple[int, float]]:
    """(passage index, similarity), best first, over the KEEP passages
    the words found first (or all, for a small folder): embedding every
    passage of a large folder on every question would be slow."""

    pool = list(range(len(passages)))
    if len(pool) > keep:
        pool = [i for i, _ in bm25(query, passages)[:keep]] or pool[:keep]
    vectors = _embed(client, model, [query] + [passages[i]["text"][:2000]
                                               for i in pool])
    wanted, rest = vectors[0], vectors[1:]
    scored = [(pool[j], _cosine(wanted, v)) for j, v in enumerate(rest)]
    return sorted(scored, key=lambda s: -s[1])


def fuse(*rankings: list[tuple[int, float]], k: int = RRF_K
         ) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: each ranking adds 1 / (k + rank)."""

    total: dict[int, float] = {}
    for ranking in rankings:
        for rank, (i, _) in enumerate(ranking, 1):
            total[i] = total.get(i, 0.0) + 1 / (k + rank)
    return sorted(total.items(), key=lambda s: -s[1])


def search(query: str, folder: str = ".", limit: int = 8,
           client: Any = None, embed_model: Optional[str] = None
           ) -> tuple[list[Hit], str]:
    """The passages in FOLDER that best answer QUERY, and how they were
    found ("words", or "words and meaning")."""

    root = Path(folder or ".").expanduser()
    if not root.is_dir():
        raise ValueError(f"{folder} is not a folder.")
    passages = index(root)
    rankings = [bm25(query, passages)]
    how = "words"
    model = embed_model if embed_model is not None else \
        os.getenv("EMBED_MODEL", "")
    if model and client is not None:
        try:
            rankings.append(by_meaning(client, model, query, passages))
            how = "words and meaning"
        except Exception:  # noqa: BLE001  (words alone, then)
            how = "words (the embedding model did not answer)"
    hits = []
    for i, score in fuse(*rankings)[:limit]:
        p = passages[i]
        hits.append(Hit(p["path"], p["start"], p["end"], p["text"], score))
    return hits, how


def cited(hits: list[Hit], folder: str) -> str:
    """The hits as numbered evidence, for the model to cite as [eN]."""

    if not hits:
        return "Nothing in that folder matches."
    out = []
    for n, hit in enumerate(hits, 1):
        out.append(f"[e{n}] {Path(folder) / hit.path}:{hit.start}-{hit.end}"
                   f"\n{hit.text[:1200]}")
    return "\n\n".join(out)
