---
name: debug-a-web-page
description: Find out why a web page or web app misbehaves: errors, failed requests, layout bugs
---
1. open_page the page (a file or a URL). Its result lists clickable elements and any JavaScript errors.
2. Reproduce the bug with interact, one step at a time: click, fill, press, wait.
3. Look underneath with devtools:
   - console: everything logged, with errors and warnings
   - network: the requests made; failed_only for errors and 4xx/5xx; request N for one in full (headers and body)
   - inspect: an element's box, attributes and computed style, for layout bugs
   - run: JavaScript in the live page, to read state (await works)
4. Form a hypothesis from evidence, fix the source with edit, reload with interact (action reload), and reproduce again to confirm.

Pitfalls:
- Do not guess from the source when the page can tell you: read the error and the failed request first.
- A layout bug is found with inspect's computed style (widths, overflow, positioning), not by reading CSS.
- For a canvas or a map, use interact's move and click at points in the screenshot.

## Examples

```
open_page {"target": "http://localhost:3000"}
```

```
interact {"action": "fill", "selector": "#email", "value": "a@example.com"}
```

```
devtools {"action": "network", "failed_only": true}
```

Read one request in full by its number.
```
devtools {"action": "request", "target": "7"}
```

```
devtools {"action": "console", "level": "error"}
```

```
devtools {"action": "inspect", "target": ".sidebar", "properties": ["width", "overflow", "position"]}
```

```
devtools {"action": "run", "value": "await fetch('/api/me').then(r => r.status)"}
```
