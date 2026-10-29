---
name: search-the-users-files
description: Answer a question from the user's own notes, documents, PDFs or code, with citations
---
- search_files finds passages by meaning and words across a folder (notes, PDFs, Word files, code) and returns them numbered [e1], [e2] with file and lines. Use it for questions ("what did we decide about pricing?").
- grep is for an exact word, name or pattern; glob for files by name.
- read a whole file when a passage is not enough.

1. search_files with the question in plain words and the folder. Try a second phrasing if the first finds little.
2. Read the passages; read a file in full when the answer depends on context.
3. Answer from them and cite each claim as [eN]. Say so if the files do not answer it, rather than filling in from memory.

Searching by meaning needs EMBED_MODEL set to an Ollama embedding model; without it the search goes by words only, which still works for most questions.

## Examples

```
search_files {"query": "what did we decide about the pricing tiers", "folder": "~/Documents/notes", "limit": 8}
```

```
grep {"pattern": "TODO\\(release\\)", "path": "."}
```
