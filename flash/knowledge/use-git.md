---
name: use-git
description: Work with git and GitHub: commits, branches, history, pull requests
---
- Look first: git status, git diff, git log --oneline -10, and which branch you are on.
- Commit in small, focused steps with clear messages in the repository's own style (look at git log). Stage the files you meant to change, not everything.
- Never rewrite shared history (rebase, amend, force-push) or push to the main branch unless the user asks.
- For GitHub (issues, pull requests, reviews), use the gh command line if installed, or connect GitHub's MCP server (see connect-an-app).
- Merge conflicts: read both sides, keep the behaviour both intended, run the tests, then commit the merge.
- Undo safely: git restore for uncommitted changes; git revert for a commit already pushed.
- Review a change for bugs with review_code: the uncommitted changes by default, a branch ("main": what this branch adds to it) or a range ("abc123..HEAD"). It double-checks what it finds, so report its findings as they are, then offer to fix them.

## Examples

```
shell {"command": "git status --short && git log --oneline -5"}
```

```
shell {"command": "git add src/dates.py tests/test_dates.py && git commit -m \"Trim whitespace before parsing dates\""}
```

```
review_code {"target": "main"}
```
