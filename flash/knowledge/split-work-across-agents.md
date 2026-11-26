---
name: split-work-across-agents
description: Do independent parts of a big task at the same time with sub-agents
---
1. Split the work into pieces that do not depend on each other: research topics, files to review, options to evaluate.
2. Call agent once per piece with a complete, self-contained task: what to do, where, what to return, and the format. A sub-agent cannot ask the user anything or start agents of its own.
3. End your turn; you are woken with each answer as it finishes. Use agent_result only if you cannot continue without one.
4. Combine the answers yourself, checking them against each other.

Do not use agents for small or sequential work: the overhead is not worth it. The user can watch them with /agents.

## Examples

```
agent {"task": "Read every file in src/auth and list each place a password or token is logged, compared without constant-time comparison, or stored unhashed. Return file:line and one sentence each."}
```

```
agent {"task": "Read every file in src/api and list each endpoint with no authentication check. Return the route, file:line and the handler name."}
```

Only if you cannot go on without it.
```
agent_result {"agent_id": "a1", "wait_seconds": 120}
```
