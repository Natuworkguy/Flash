---
name: guide-with-the-camera
description: Walk the user through a hands-on task step by step, showing each step on what their camera sees
---
- When they show you something and want to be shown how ("how do I reset this", "which cable goes where", "walk me through it"), guide them with guide_step: one step at a time, each on the photo that came with their message.
- First work out the whole task from the photo, then give step 1. Ring what they should touch, press or turn (a button, a valve, a port, a screw), with a short label such as "Hold 5 s" or "This one". Use an arrow when the thing is small or crowded by others. Size each ring to the thing: about 0.05 for a button, 0.3 for a whole panel.
- After each step, say it in a sentence or two, then stop. In voice mode their next words come with a new photo: look at it first. If the step is done, give the next. If not, or something looks wrong, say what you see and show the same step again on the new photo.
- If the photo does not show what you need, ask them to point the camera at it, move closer, or turn on a light, instead of guessing where things are.
- Anything risky (mains power, gas, a hot surface, a sharp blade, a car), say how to make it safe first, as its own step.
- At the end, call guide_step once more with done true and what they finished.

## Examples

```
guide_step {"step": 1, "of": 4, "say": "Unplug the router: the round black cable at the back.", "marks": [{"x": 0.71, "y": 0.58, "size": 0.08, "label": "Power"}]}
```

```
guide_step {"step": 2, "of": 4, "say": "Press and hold the reset hole for ten seconds, with a pin.", "marks": [{"x": 0.42, "y": 0.61, "size": 0.03, "label": "Hold 10 s", "kind": "arrow"}]}
```

```
guide_step {"step": 4, "of": 4, "say": "The light is steady green: the router is reset.", "done": true}
```
