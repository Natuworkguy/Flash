---
name: use-the-canvas
description: Show a live page, chart, diagram or mock-up in the chat with the canvas tool, and keep updating it in place
---
- "Show it on the canvas", "draw me a chart", "mock up the page", "keep a live board of the progress": use canvas.
- The first call makes a card in the chat and returns its id. To change it, call canvas again with that id and the WHOLE new content: the card is redrawn where it already is, not added again.
- format is html (default), markdown or svg. HTML runs in a sandbox: its scripts and inline styles work, but it cannot reach Flash, the user's files or cookies. Put everything (CSS, JS, data) inline in one document; external libraries from a CDN may load.
- Use it for things worth seeing while you work: a dashboard you refresh as steps finish, a chart of results, a diagram, a UI draft. For a long text the user keeps, use make_document instead; for slides and diagrams to save as files, see make-slides-and-diagrams.
- Do not repeat the canvas's content in your reply; say in a line what it shows.
- The card has a full screen button; the frame can be resized by dragging its corner.
- Content is at most 500,000 characters.
- In the terminal there is no canvas: the tool says so, and you give the content in your reply.

## Examples

```
canvas {"title": "Build status", "format": "html", "content": "<h1>3 of 5 steps done</h1>"}
```

```
canvas {"id": "a1b2c3d4e5f6", "title": "Build status", "format": "html", "content": "<h1>5 of 5 steps done</h1>"}
```
