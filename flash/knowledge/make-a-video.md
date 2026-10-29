---
name: make-a-video
description: Make an MP4: an animation, a title card, a promo or an explainer clip
---
make_video renders an HTML page frame by frame on an exact clock, so every frame is right however heavy the page.

1. Write the composition as HTML: <html data-duration="8" data-width="1920" data-height="1080" data-fps="30">.
2. Animate with CSS animations and transitions, the Web Animations API, requestAnimationFrame or a canvas. Time starts at 0 when the video starts. Timers (setTimeout, setInterval) and Date.now follow the video's clock too.
3. For full control, define window.flashSeek = (seconds) => { ...draw that moment... }; it is called before each frame.
4. make_video with the html (or its path) and out.
5. Check it: render a few still moments by making one-frame versions, or extract frames with ffmpeg and view_image them. Fix and render again.

Pacing: give text time to be read (about a second per three words), ease movements (ease-out in, ease-in out), and leave a beat at the end. Videos need ffmpeg installed.

For sound on it, make the audio (make_sound, or a file) and mux with ffmpeg: ffmpeg -i video.mp4 -i audio.wav -c:v copy -c:a aac -shortest out.mp4.

## Examples

```
make_video {"html": "<html data-duration=\"4\" data-width=\"1280\" data-height=\"720\"><body style=\"margin:0;background:#111;display:grid;place-items:center;height:100vh\"><h1 style=\"color:#fff;font:700 96px system-ui;animation:in 1s ease-out both\">Launch day</h1><style>@keyframes in{from{opacity:0;transform:translateY(40px)}}</style></body></html>", "out": "title.mp4"}
```

From a file, overriding its length.
```
make_video {"html": "promo/intro.html", "out": "promo/intro.mp4", "seconds": 12, "fps": 60}
```

Check a frame.
```
shell {"command": "ffmpeg -y -loglevel error -ss 2 -i title.mp4 -frames:v 1 frame.png"}
```

```
view_image {"path": "frame.png"}
```
