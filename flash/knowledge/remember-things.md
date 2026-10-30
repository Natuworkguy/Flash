---
name: remember-things
description: Remember facts and preferences across sessions, and look them up
---
- remember saves a fact or preference for future sessions: how the user likes things, their setup, recurring names. In a project chat, project true saves it for every chat in the project.
- recall searches memory by phrase before assuming something is not known.
- forget deletes one entry by the number recall shows.
- journal is what happened, not facts: a few lines on each past day, written from the user's chats, with a summary of each finished week and month. For "what was I doing in March?" read the month (2026-03); for "when did I fix the login bug?" search it.

Save what will matter again (the user's name, their stack, "prefers short answers"), not one-off details of today's task. Do not save secrets: keys and passwords go in the vault (/secret set NAME), which Flash uses by name without showing them.

## Examples

```
remember {"entry": "Prefers metric units and 24-hour times."}
```

```
remember {"entry": "Run tests with make test, not pytest directly.", "project": true}
```

```
recall {"phrase": "units"}
```

```
forget {"index": 3}
```

```
journal {"action": "search", "query": "login bug"}
```
