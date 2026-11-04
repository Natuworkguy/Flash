---
name: play-a-browser-game
description: Play or beat a real-time browser game, like a Geometry Dash level, or anything too fast for a turn per move
---
1. open_page the game. Take a screenshot to learn its colours, where the player is, and what kills it. Start it (a click, Space) and screenshot again while it moves.
2. Write a policy: JavaScript run every frame with api.
   - See: api.blobs(colour, x0, y0, x1, y1) for every patch of a colour (obstacles, platforms, the player) as boxes; api.runs and api.find along a row; api.findDown down a column; api.px for one pixel. A WebGL canvas (most game engines) is read the same way.
   - Measure: track what you see from frame to frame (api.dt is the time between frames), or api.speed(y0, y1) for how fast the picture scrolls.
   - Act: api.tap, api.press and api.release for keys; api.click; api.down(x, y) and api.up() to hold the mouse, for a ship or jetpack; api.later(ms, fn) for timing finer than a frame.
   - Keep state in api.memory; api.log what it decided.
3. For a precise game, simulate it: work out its gravity, jump speed and scroll speed from how the player moves, then in the policy run the game ahead for each choice (jump now, in 2 frames, never) and pick the one that lives with the most room either side. Jump in the middle of the window of frames that work, not at its edge.
4. play_realtime runs it for some seconds (or until a condition) and reports frames, actions, logs, the title and a screenshot. It films the game's canvas and sends the user the recording (WebM).
5. To beat a hard level, give attempts (up to 50) with died, a JavaScript expression true when a try is lost (often the title or a pixel of the death screen), restart_keys, and progress (how far a try got, 0 to 100). It retries, keeping api.memory, so the policy can learn where it died (api.deaths). The best try is filmed.
6. Improve the policy from what happened: where it died, what it logged, and run again.

open_page('flash-dash') opens a practice level like Geometry Dash: spikes, blocks to land on, pads, rings to tap in the air, a speed-up, and a ship to fly between pillars.

## Examples

```
open_page {"target": "flash-dash"}
```

Jump when an obstacle colour is close ahead.
```
play_realtime {"policy": "const x = api.find('#ff3b5c', 236, 160, 300); if (x >= 0 && !api.memory.jumped) { api.tap(' '); api.memory.jumped = true; } if (x < 0) api.memory.jumped = false;", "seconds": 30, "start_keys": "r"}
```

Keep trying until the level is beaten, retrying on a crash.
```
play_realtime {"policy": "...", "seconds": 120, "attempts": 10, "until": "document.title.includes('won')", "died": "document.title.includes('crashed')", "restart_keys": "r", "progress": "+(document.title.match(/(\\d+)%/) || [0, 0])[1]"}
```
