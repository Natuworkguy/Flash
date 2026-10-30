---
name: read-aloud
description: Read an article, a document or text aloud, and play sound files to the user
---
- "Read me this article", "read this aloud", "can I hear it": use read_aloud, not a written summary. Give it the url of the page (it reads the article itself, not the menus), the path of a document (PDF, Word, text, Markdown), or the text.
- Read what they asked for as it is written. Only read a summary aloud when they ask for one: write it, then read_aloud with text.
- In the browser it becomes a player in the chat that starts playing on its own; in the terminal it plays through the speakers and Ctrl+C stops it. Either way, do not paste the text into your reply as well: say in a line what it is and how long.
- It uses the voice chosen for voice mode. If no voice is installed, say so and point them to Settings (or /voice in the terminal).
- To save a reading as a file, give out a path ending in .m4a, .mp3 or .wav.
- send_audio plays a sound file that already exists (mp3, wav, ogg, m4a, flac) in the chat, with play true to start it straight away.

## Examples

```
read_aloud {"url": "https://example.com/news/the-article"}
```

```
read_aloud {"path": "~/Documents/report.pdf", "out": "~/Downloads/report.m4a"}
```

```
send_audio {"path": "~/Music/demo.mp3", "play": true}
```
