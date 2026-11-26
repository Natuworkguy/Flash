---
name: plan-a-long-task
description: Work through a long, multi-step task visibly and reliably
---
1. Read enough first to plan well.
2. plan posts the steps as a checklist the user watches; keep steps concrete and checkable.
3. check_step ticks each one the moment it is done. Post a new plan if the work changes shape.
4. reason shares a short thought or decision without ending your turn, when the user would want to know why.
5. Verify as you go (tests, screenshots, re-reading output) rather than all at the end.
6. Finish with what was done, what was checked, and anything left open.

For a decision with real trade-offs on the way, see weigh-a-decision. For pieces that can run at once, see split-work-across-agents.

## Examples

```
plan {"steps": ["Read the payment module", "Add the refund endpoint", "Write its tests", "Run the suite", "Update the API docs"]}
```

```
check_step {"index": 1}
```

```
reason {"thought": "The refund needs the original charge id, so I am storing it on the order first."}
```
