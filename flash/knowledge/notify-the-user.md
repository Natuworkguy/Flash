---
name: notify-the-user
description: Tell the user something the moment it happens, outside the chat
---
notify_user sends a pop-up on Flash's page, or a desktop notification. Use it for news they would want right away: a long job finished, something you were watching for turned up, or you are blocked and need them. Not for what they will read in your reply anyway. Keep it to one line with the outcome first. It is rate-limited; a refusal says when the next can go.

## Examples

```
notify_user {"title": "Build finished", "message": "All 412 tests pass; the release is tagged v2.3.0."}
```
