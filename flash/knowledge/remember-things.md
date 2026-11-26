---
name: remember-things
description: Remember facts and preferences across sessions, and look them up
---
- remember saves a fact or preference for future sessions: how the user likes things, their setup, recurring names. In a project chat, project true saves it for every chat in the project.
- recall searches memory by phrase before assuming something is not known.
- forget deletes one entry by the number recall shows.

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
