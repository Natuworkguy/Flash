---
name: play-a-browser-game
description: Play or drive a real-time browser game or anything too fast for a turn per move
---
1. open_page the game. Take a screenshot to learn its colours and where things are.
2. Write a policy: JavaScript run every frame with api. Read pixels (api.px, api.find, api.runs, api.findDown), act (api.tap, api.press, api.release, api.click, api.move), keep state in api.memory, and log with api.log.
3. play_realtime runs it for some seconds (or until a condition) and reports frames, actions, logs, the title and a screenshot.
4. Improve the policy from what happened: measure speeds from movement between frames, react at the right distance, and run again.

open_page('flash-dash') opens a practice game.

## Examples

```
open_page {"target": "flash-dash"}
```

Jump when an obstacle colour is close ahead.
```
play_realtime {"policy": "const x = api.find('#e84a5f', 300, 120, 220, 30); if (x >= 0 && !api.memory.jumped) { api.tap(' '); api.memory.jumped = true; } if (x < 0) api.memory.jumped = false;", "seconds": 30, "start_keys": "r"}
```
