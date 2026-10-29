---
name: control-the-computer
description: Do things on the user's computer: apps, volume, links, clipboard, settings, or clicking around
---
Use the cheapest way that works:
1. shell for anything a command can do.
2. The computer tool for simple actions, when allowed: open_app, quit_app, open (a link, file or folder), volume (set_volume, mute), music (play_pause, next_track, now_playing), copy to the clipboard, dark_mode, lock_screen, sleep_display.
3. connectors and the browser tools (open_page, interact) for apps with an API or a web page.
4. computer_use only as a last resort, for something no other tool reaches: a setting or button in an app. It is slow, and the user is asked first.

With computer_use: start with a screenshot, act on coordinates in the latest screenshot, and look again after each step. Use wake if the screen is asleep. Stop if the user takes over or says no. Never type passwords or payment details.

## Examples

```
computer {"action": "open_app", "value": "Spotify"}
```

```
computer {"action": "set_volume", "value": 25}
```

```
computer {"action": "open", "value": "https://calendar.google.com"}
```

Last resort: look, then act on the screenshot.
```
computer_use {"action": "screenshot"}
```

```
computer_use {"action": "click", "x": 412, "y": 288}
```
