---
name: write-a-document
description: Write a report, plan, spec, README, essay or notes the user will read and edit
---
1. Write it as Markdown with write: a title, short sections with clear headings, the conclusion or recommendation first, details after.
2. Show it with send_document. In the web UI it opens beside the chat, rendered; the user can edit it and comment on passages, and their comments come to you quoted.
3. Use send_document's comments to flag open questions, assumptions or gaps pinned to the passages they are about.
4. Once it is open, change it in place with edit; your edits show as they land. Do not send it again.

Inside the document:
- Tables for comparisons, lists for steps, ```mermaid blocks for diagrams.
- Plain, specific language. Short paragraphs.

For a PDF or Word file, see make-office-files. For slides, see make-slides-and-diagrams.

## Examples

```
write {"path": "docs/plan.md", "content": "# Launch plan\n\n## Summary\n..."}
```

```
send_document {"path": "docs/plan.md", "caption": "Draft launch plan", "comments": [{"quote": "Launch on 14 November", "note": "Is this date fixed, or can it move a week?"}]}
```
