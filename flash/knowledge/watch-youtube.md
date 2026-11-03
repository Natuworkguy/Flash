---
name: watch-youtube
description: Watch a YouTube video or Short from its link and say what is in it
---
- watch_youtube takes a youtube.com/watch, youtu.be, /shorts/ or /live/ link (or a bare 11-character id) and returns the title, channel, length, description and timed captions.
- When the model can see images it also attaches frames: up to four of YouTube's storyboard sheets, each a grid of small frames about one a second, in reading order, spread across the video. Pass frames: false to skip them.
- Use it, not fetch, for any YouTube link: the page itself is mostly script.
- Captions are often automatic, so names and lyrics can be wrong; music-only Shorts may have just "[music]". Say when you are going from frames alone.
- Frames are small. Describe what you can actually see; do not guess at text you cannot read.
- Sparks have it too; sub-agents do not.

Example:
watch_youtube {"url": "https://youtube.com/shorts/d04gm9xVWbw"}
