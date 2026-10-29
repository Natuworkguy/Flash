---
name: save-a-skill
description: Write down a procedure that worked, so the next time goes right first time
---
Skills are the user's own saved procedures, listed under Skills; this knowledge base is the guides that come with Flash and its extensions.

- After finishing a task that will come up again (a release, a deploy, a report format), skill_manage create a skill: a name for the kind of task (not today's instance), a one-line description of when to use it, and the steps, commands and pitfalls.
- Before a task a skill covers, skill_view it and follow it. Fix it with patch when it was wrong or out of date.
- A skill saved with a guide's name is used instead of that guide, for this user.

Write steps and pitfalls, not a story of what happened.

## Examples

```
skill_manage {"action": "create", "name": "release-flow", "description": "Cut a release: bump, tag, push, check CI", "content": "1. Bump version.py.\n2. Commit \"Bump version to X.Y.Z\".\n3. Tag vX.Y.Z and push the tag.\n4. Watch CI; fix before announcing.\n\nPitfall: tag the merge commit, not the bump."}
```

```
skill_view {"name": "release-flow"}
```

```
skill_manage {"action": "patch", "name": "release-flow", "old_string": "3. Tag vX.Y.Z", "new_string": "3. Tag vX.Y.Z (annotated: git tag -a)"}
```
