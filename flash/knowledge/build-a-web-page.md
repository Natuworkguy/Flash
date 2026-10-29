---
name: build-a-web-page
description: Build a web page, app screen, landing page or HTML report, and check how it looks
---
1. Write one self-contained .html file with write: CSS and scripts inline, no build step, external libraries only from a CDN when needed.
2. Make it responsive from the start: a max-width container, flexible grids, no fixed widths wider than a phone.
3. Look at it with screenshot at the default width, then again at width 390 for a phone. Reading the source does not tell you how it looks.
4. Anything interactive (buttons, forms, tabs, a game): open it with open_page and click through it with interact, watching for JavaScript errors in each result.
5. Fix what you see, with edit, and look again. Repeat until it is right at both widths.
6. Show it with send_html, or, for a design the user will comment on and adjust, follow the design instructions in your system prompt.

Make it look designed: a clear type scale, generous spacing, one accent colour, real content rather than lorem ipsum, and dark and light that both work if it uses prefers-color-scheme.

Pitfalls:
- Text overflowing its box, buttons wrapping, and horizontal scrolling on a phone are the common faults: check for them in the narrow screenshot.
- A page that loads data from a local file path works in the browser only when served; inline the data instead.

## Examples

```
write {"path": "site/index.html", "content": "<!doctype html>\n<html lang=\"en\">...</html>"}
```

Look at it on a desktop, then a phone.
```
screenshot {"target": "site/index.html", "full_page": true}
```

```
screenshot {"target": "site/index.html", "width": 390, "full_page": true}
```

Click through what it does.
```
open_page {"target": "site/index.html"}
```

```
interact {"action": "click", "selector": "text=Sign up"}
```

```
send_html {"path": "site/index.html", "caption": "The landing page"}
```
