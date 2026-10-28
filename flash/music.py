"""Music: a song by name, played without a browser tab, and stopped by
voice.

YouTube plays in a small player at the foot of Flash's own page: the
song is found by a search, and nothing else opens. In the terminal it
plays through mpv (with yt-dlp), when they are installed. Spotify and
Apple Music play in their own apps on a Mac: Spotify finds a song with
its Web API when a client of the user's own is in the vault (it is
otherwise shown the search), and Apple Music plays what is in the
library. Pause, resume, skip and the volume go to whichever is playing.

"Hey Flash, pause the music" works while it plays: the page turns the
music down only once it has heard "Hey Flash", for as long as it takes
to hear the rest, so the listening is not touched until then.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx

SERVICES = ("youtube", "spotify", "apple_music")
ACTIONS = ("play", "pause", "resume", "stop", "next", "volume")
SPOTIFY_ID = "SPOTIFY_CLIENT_ID"
SPOTIFY_SECRET = "SPOTIFY_CLIENT_SECRET"  # nosec B105
TIMEOUT = 15
_YOUTUBE = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)"
    r"([\w-]{11})")

# What the terminal plays through, while it does.
_mpv: dict[str, Any] = {"process": None, "socket": "", "title": ""}


class MusicError(Exception):
    """Music that could not be found or played, in words."""


# Finding it -------------------------------------------------------------


def youtube_id(text: str) -> str:
    found = _YOUTUBE.search(str(text or ""))
    return found.group(1) if found else ""


def find_youtube(query: str) -> dict:
    """The video QUERY names, or a link to one: {id, title, url}."""

    given = youtube_id(query)
    if given:
        return {"id": given, "title": query,
                "url": f"https://www.youtube.com/watch?v={given}"}
    try:
        from ddgs import DDGS

        results = DDGS().videos(f"{query} youtube", max_results=10)
    except Exception as exc:  # noqa: BLE001  (the search's own failure)
        raise MusicError(f"The search for it failed: {exc}") from None
    for item in results or []:
        link = str(item.get("content") or item.get("embed_url") or "")
        video = youtube_id(link)
        if video:
            return {"id": video, "title": str(item.get("title") or query),
                    "url": f"https://www.youtube.com/watch?v={video}"}
    raise MusicError(f"Found nothing on YouTube for {query!r}.")


# Saying it ----------------------------------------------------------------

_INTENTS = (
    ("pause", r"\b(pause|stop|halt|mute)\s+(the\s+)?(music|song|track|"
              r"video|playback)\b"),
    ("resume", r"\b(resume|unpause|continue|play)\s+(the\s+)?(music|song|"
               r"track|video|playback)\b"),
    ("next", r"\b(next|skip)\s+(the\s+)?(song|track|one)\b|\bskip\s+(it|"
             r"this)\b"),
    ("louder", r"\b(turn\s+(it|the\s+music)\s+up|louder|volume\s+up)\b"),
    ("quieter", r"\b(turn\s+(it|the\s+music)\s+down|quieter|softer|"
                r"volume\s+down)\b"),
)


def intent(text: str) -> str:
    """What a spoken line asks of the music, or "": "pause", "resume",
    "next", "louder" or "quieter"."""

    said = " ".join(str(text or "").lower().split())
    for name, pattern in _INTENTS:
        if re.search(pattern, said):
            return name
    return ""


# Spotify and Apple Music, on a Mac ------------------------------------------


def _osascript(script: str) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607 -- a fixed program
            ["osascript", "-e", script], capture_output=True, text=True,
            timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MusicError(f"macOS would not run it: {exc}") from None
    if done.returncode:
        raise MusicError((done.stderr or "").strip().splitlines()[-1]
                         if done.stderr.strip() else "It did not play.")
    return done.stdout.strip()


def _quoted(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _spotify_track(query: str) -> Optional[dict]:
    """The track QUERY names, through Spotify's Web API, when a client of
    the user's own is in the vault."""

    from . import vault

    client, secret = vault.get(SPOTIFY_ID), vault.get(SPOTIFY_SECRET)
    if not client or not secret:
        return None
    pair = base64.b64encode(f"{client}:{secret}".encode()).decode()
    token = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {pair}"}, timeout=TIMEOUT)
    if token.status_code != 200:
        raise MusicError("Spotify would not let Flash search: check "
                         f"{SPOTIFY_ID} and {SPOTIFY_SECRET} in the vault.")
    found = httpx.get(
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "track", "limit": 1},
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=TIMEOUT).json()
    items = (found.get("tracks") or {}).get("items") or []
    if not items:
        raise MusicError(f"Spotify has nothing for {query!r}.")
    track = items[0]
    artists = ", ".join(a["name"] for a in track.get("artists") or [])
    return {"uri": track["uri"], "title": f"{track['name']} by {artists}"}


def _open(target: str) -> None:
    if sys.platform == "darwin":
        argv = ["open", target]
    elif os.name == "nt":
        argv = ["cmd", "/c", "start", "", target]
    else:
        argv = ["xdg-open", target]
    subprocess.Popen(argv, stdout=subprocess.DEVNULL,  # nosec B603
                     stderr=subprocess.DEVNULL)


def play_spotify(query: str) -> str:
    track = _spotify_track(query)
    if track is None:
        _open("spotify:search:" + query.replace(" ", "%20"))
        return (f"Opened Spotify's search for {query!r}: pick it there. "
                f"With {SPOTIFY_ID} and {SPOTIFY_SECRET} in the vault (a "
                "free app at developer.spotify.com), Flash plays the song "
                "itself.")
    if sys.platform == "darwin":
        _osascript(f'tell application "Spotify" to play track '
                   f'{_quoted(track["uri"])}')
        return f"Playing {track['title']} in Spotify."
    if shutil.which("playerctl"):
        subprocess.run(["playerctl", "-p", "spotify", "open",  # nosec
                        track["uri"]], capture_output=True, check=False,
                       timeout=TIMEOUT)
        return f"Playing {track['title']} in Spotify."
    _open(track["uri"])
    return f"Opened {track['title']} in Spotify: press play there."


def play_apple_music(query: str) -> str:
    if sys.platform != "darwin":
        raise MusicError("Apple Music is played on a Mac. Try YouTube.")
    words = _quoted(query)
    said = _osascript(
        'tell application "Music"\n'
        f'set found to (search playlist "Library" for {words})\n'
        'if found is {} then return ""\n'
        'play item 1 of found\n'
        'return (name of item 1 of found) & " by " & '
        '(artist of item 1 of found)\nend tell')
    if not said:
        _open("music://music.apple.com/search?term="
              + query.replace(" ", "+"))
        return (f"{query!r} is not in your Music library: opened Apple "
                "Music's search for it.")
    return f"Playing {said} in Music."


# The terminal's player --------------------------------------------------


def terminal_can_play() -> bool:
    return bool(shutil.which("mpv") and shutil.which("yt-dlp"))


def _mpv_say(command: list) -> None:
    path = _mpv["socket"]
    if not path or _mpv["process"] is None or \
            _mpv["process"].poll() is not None:
        raise MusicError("Nothing is playing here.")
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
        link.settimeout(2)
        link.connect(path)
        link.sendall((json.dumps({"command": command}) + "\n").encode())


def terminal_play(found: dict) -> str:
    terminal_stop()
    folder = Path(tempfile.mkdtemp(prefix="flash-music-"))
    sock = str(folder / "mpv.sock")
    _mpv.update(process=subprocess.Popen(  # nosec B603 B607
        ["mpv", "--no-video", "--really-quiet",
         f"--input-ipc-server={sock}", found["url"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True),
        socket=sock, title=found["title"])
    return f"Playing {found['title']}."


def terminal_stop() -> None:
    process = _mpv["process"]
    if process is not None and process.poll() is None:
        process.terminate()
    _mpv.update(process=None, socket="", title="")


def terminal_control(action: str, value: int = 0) -> str:
    if action == "stop":
        terminal_stop()
        return "Stopped."
    if action in ("pause", "resume"):
        _mpv_say(["set_property", "pause", action == "pause"])
        return "Paused." if action == "pause" else "Playing again."
    if action == "volume":
        _mpv_say(["set_property", "volume", max(0, min(100, value))])
        return f"Volume {value}."
    raise MusicError("One song plays at a time here: play another.")


def playing_here() -> bool:
    process = _mpv["process"]
    return process is not None and process.poll() is None


def wait_until_started(seconds: float = 3.0) -> bool:
    """Whether mpv is still going a moment on: it quits at once on a
    link it cannot play."""

    time.sleep(min(seconds, 3.0))
    return playing_here()
