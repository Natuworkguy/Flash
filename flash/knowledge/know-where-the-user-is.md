---
name: know-where-the-user-is
description: Use the user's location from their Apple Watch, for "near me", local weather or distances
---
- The user_location tool gives the latitude and longitude the user's paired Apple Watch last sent, how exact it is and how long ago.
- It is offered only while the watch's "Let Flash see my location" switch is on (Settings in Flash on the watch, under Location) and a place has come in. It is off until the user turns it on; the watch asks for location permission only then.
- The watch sends a place when Flash is opened on it, at most every 5 minutes. Turning the switch off stops it and Flash forgets the last place.
- An old place is marked as possibly out of date: say so rather than treat it as where they are now.
- Without the tool, do not guess where the user is; ask, or tell them about the switch.
- Keep the place to answering the user: never send it to anyone or put it in an email or message unless they ask.

Example:
user_location {}
