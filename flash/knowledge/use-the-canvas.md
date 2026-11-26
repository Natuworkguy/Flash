---
name: use-the-canvas
description: Show a live page, form, chart or mock-up in the chat, change it in place, read what the user did in it
---
- "Show it on the canvas", "draw me a chart", "mock up the page", "keep a live board of the progress": use canvas.
- The first call makes a card in the chat and returns its id. To change it, call canvas again with that id and the WHOLE new content: the card is redrawn where it already is, not added again.
- format is html (default), markdown or svg. HTML runs in a sandbox: its scripts and inline styles work, but it cannot reach Flash, the user's files or cookies. Put everything (CSS, JS, data) inline in one document; external libraries from a CDN may load.
- Use it for things worth seeing while you work: a dashboard you refresh as steps finish, a chart of results, a diagram, a UI draft. For a long text the user keeps, use make_document instead; for slides and diagrams to save as files, see make-slides-and-diagrams.
- An HTML canvas is live. Act on it by id with action:
  - patch: change parts in place, keeping what the user typed and scrolled. patches is a list of {selector, how, html}: how is inner (default), outer, append, prepend, text, remove, attr (name is the attribute, html its value) or value (an input's value); all: true for every match. Scripts in patched HTML do not run; use run for code. Patches are kept, so the canvas looks the same after a reload.
  - run: script is the body of an async function run in the canvas; what it returns (JSON) comes back. Use it to update a chart, call the page's own functions, or measure something.
  - read: every input's value (by name or id), flash.state, and the text shown. Read before acting on a form the user filled in.
  - look: a picture of the canvas as the user sees it now. Look after a big change to check it.
- Give the canvas's own code state worth reading: put it in flash.state (an object, already there).
- Its buttons can message you: onclick="flash.send('Approved: ' + pick.value)". It arrives as a message marked [From the canvas "title" (id ...)]. It works only from a click or other user action, at most once a second. Build approvals, pickers, quizzes and forms this way, then wait for the message or read the canvas.
- Prefer patch to redrawing: redrawing with the whole content resets the canvas and loses what the user typed.
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

```
canvas {"id": "a1b2c3d4e5f6", "action": "patch", "patches": [{"selector": "#done", "how": "text", "html": "4 of 5"}, {"selector": "ul", "how": "append", "html": "<li>Tests pass</li>"}]}
```

```
canvas {"id": "a1b2c3d4e5f6", "action": "read"}
```

```
canvas {"id": "a1b2c3d4e5f6", "action": "run", "script": "chart.data.push(42); chart.update(); return chart.data.length"}
```
