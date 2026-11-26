---
name: make-slides-and-diagrams
description: Make a slide deck, a flowchart, a sequence or architecture diagram
---
Slides:
1. Write a Markdown file with marp: true in its front matter, and --- on a line of its own between slides.
2. One idea per slide: a heading and a few short points or one picture. Ten to fifteen slides for a talk.
3. Show it with send_document: it shows as slides the user can present full screen.
For a designed deck (brand colours, layouts), build it as a design and export it as slides; see the design instructions in your system prompt.

Diagrams:
- A ```mermaid block in a document or a reply: flowchart TD for processes, sequenceDiagram for messages between parts, classDiagram, erDiagram for data, gantt for schedules, mindmap.
- Keep labels short; quote labels with punctuation: A["Login (OAuth)"].
- For a diagram that must look polished, draw it as SVG in an HTML page and check it with screenshot.

## Examples

A deck: marp in front matter, --- between slides.
```
write {"path": "talk.md", "content": "---\nmarp: true\n---\n\n# Why we moved to Rust\n\n---\n\n## The problem\n\n- Crashes under load\n- ...\n"}
```

```
send_document {"path": "talk.md", "caption": "Slides for Friday"}
```
