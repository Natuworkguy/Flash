---
name: research-the-web
description: Research a question on the web and answer with sources
---
1. Break the question into the facts you need. Search each with web_search, phrasing queries the way a page that answers would be written.
2. Open the most promising results with fetch to read them, not just their snippets. Prefer primary sources: official docs, the paper, the company's own page, the changelog.
3. For several independent threads, start a sub-agent per thread with agent and end your turn; you are woken with each answer.
4. Cross-check anything surprising or recent in a second source. Note dates: say how current the information is.
5. Answer directly, then the supporting detail, with links to the pages you used. Say what you could not confirm.

Pitfalls:
- Search results summarise; fetch the page before quoting it.
- For prices, versions, schedules and news, assume your own knowledge is out of date and look it up.
- A page that needs JavaScript or a login may fetch empty: try open_page instead.

## Examples

```
web_search {"query": "Python 3.14 release date free-threading", "max_results": 8}
```

```
fetch {"url": "https://docs.python.org/3.14/whatsnew/3.14.html"}
```

Two threads at once.
```
agent {"task": "Find the current price and plan limits of three hosted Postgres services (name, cheapest paid plan, storage, connections), from their own pricing pages. Return a table with a link per row."}
```
