---
name: play-music
description: Play, pause, skip or turn music up and down for the user
---
- play_music plays a song, album or artist by name. YouTube (the default) plays in Flash's own small player on the page, or in the terminal through mpv. On a Mac it can play in Spotify or Apple Music instead (service).
- Then action pause, resume, stop, next or volume (0 to 100) controls whatever is playing.
- The computer tool's music actions (play_pause, next_track, set_volume, now_playing) control apps already playing on the user's computer, when computer actions are allowed.
- The user can also say "Hey Flash, pause the music" on the page without you.

Pick the best-known version unless they say otherwise ("official audio" helps on YouTube).

## Examples

```
play_music {"action": "play", "query": "Nujabes Aruarian Dance"}
```

```
play_music {"action": "play", "query": "Discovery by Daft Punk", "service": "spotify"}
```

```
play_music {"action": "volume", "value": 30}
```

```
play_music {"action": "pause"}
```
