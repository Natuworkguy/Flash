"""Radio and speakers: "Hey Flash, play WBUR", "play WBUR on the kitchen
speaker", "switch the music to the living room".

A station is found by its name or call sign in the Radio Browser
directory (a free, open list of some fifty thousand stations), which
gives the stream itself. Where it plays:

- on a speaker Home Assistant knows (a Sonos, a Google or Nest speaker,
  a Chromecast, an Apple TV, anything that is a media player there),
  when Home Assistant is connected;
- on a Mac, in the Music app, out of the Mac or the AirPlay speakers
  named;
- else in the terminal through mpv.

Apple Music on a Mac plays songs from the whole catalogue (found with
Apple's public search, then opened in Music), and its live radio:
Apple Music 1, Hits and Country.

"Switch the media to the bedroom" moves what is playing: from one Home
Assistant speaker to another (the same stream, the old one stopped), or
the Music app's output to other AirPlay speakers.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import time
from typing import Optional

import httpx

TIMEOUT = 12
DEFAULT_DEVICE = "FLASH_MEDIA_DEVICE"  # where to play when no device is named
RADIO_BROWSER = (
    "https://all.api.radio-browser.info",
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
)
AGENT = "Flash/1 (radio)"

# Apple Music's live stations, by what people call them.
APPLE_RADIO = {
    "apple music 1": (
        "Apple Music 1",
        "https://music.apple.com/us/station/apple-music-1/ra.978194965",
    ),
    "apple music hits": (
        "Apple Music Hits",
        "https://music.apple.com/us/station/apple-music-hits/ra.1498155548",
    ),
    "apple music country": (
        "Apple Music Country",
        "https://music.apple.com/us/station/apple-music-country/ra.1498157166",
    ),
}
_APPLE_RADIO_WORDS = {
    "apple music radio": "apple music 1",
    "beats 1": "apple music 1",
    "beats one": "apple music 1",
    "apple music one": "apple music 1",
    "apple hits": "apple music hits",
    "apple country": "apple music country",
}


class MediaError(Exception):
    """A station, a song or a speaker that could not be found or played."""


# ---- stations


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def find_station(name: str) -> dict:
    """The station NAME means: {name, url, homepage, favicon, codec, bitrate}.
    Call signs (WBUR, KEXP) match exactly first; then the most voted for, among
    those that work.
    """

    q = str(name or "").strip()
    if not q:
        raise MediaError("Name the station.")
    found = None
    last = None
    for base in RADIO_BROWSER:
        try:
            r = httpx.get(
                f"{base}/json/stations/search",
                params={
                    "name": q,
                    "limit": 40,
                    "order": "votes",
                    "reverse": "true",
                    "hidebroken": "true",
                },
                headers={"User-Agent": AGENT},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                found = r.json()
                break
            last = f"{r.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
    if found is None:
        raise MediaError(f"The radio directory could not be reached ({last}).")
    if not found:
        raise MediaError(f"No station called {q!r} in the radio directory.")
    want = _words(q)

    def score(s: dict) -> tuple:
        words = _words(s.get("name", ""))
        exact = all(w in words for w in want)
        callsign = len(want) == 1 and words[:1] == want
        codec = str(s.get("codec", "")).upper()
        good = codec in ("MP3", "AAC", "AAC+")
        # the main station over its HD side channels, and the closest
        # name over a longer one
        sub = any(re.fullmatch(r"hd[2-9]", w) for w in words)
        extra = len(
            [
                w
                for w in words
                if w not in want and w not in ("fm", "am", "radio")
            ]
        )
        return (
            callsign and not sub,
            exact,
            -extra,
            good,
            int(s.get("votes") or 0),
            int(s.get("bitrate") or 0),
        )

    best = max(found, key=score)
    url = best.get("url_resolved") or best.get("url")
    if not url:
        raise MediaError(f"{best.get('name', q)} has no stream listed.")
    return {
        "name": str(best.get("name", q)).strip(),
        "url": url,
        "homepage": best.get("homepage", ""),
        "favicon": best.get("favicon", ""),
        "codec": best.get("codec", ""),
        "bitrate": best.get("bitrate", 0),
    }


def apple_radio(name: str) -> Optional[tuple]:
    """(title, link) when NAME is one of Apple Music's live stations."""
    key = " ".join(_words(name))
    key = _APPLE_RADIO_WORDS.get(key, key)
    return APPLE_RADIO.get(key)


# ---- Home Assistant's speakers


def _ha():
    from . import smarthome

    return smarthome


def ha_ready() -> bool:
    try:
        return _ha().connected()
    except Exception:  # noqa: BLE001
        return False


def speakers() -> list[dict]:
    """Home Assistant's media players."""
    return [
        e
        for e in _ha().states()
        if str(e.get("entity_id", "")).startswith("media_player.")
    ]


def find_speaker(name: str) -> dict:
    sh = _ha()
    every = speakers()
    hits = [
        e
        for e in sh.find(name, every)
        if e["entity_id"].startswith("media_player.")
    ]
    if not hits:
        names = ", ".join(sorted(sh._name(e) for e in every)) or "none"
        raise MediaError(
            f"No speaker called {name!r} in Home Assistant. It has: {names}."
        )
    return hits[0]


def playing_speaker() -> Optional[dict]:
    """The speaker playing now, if one is (the most recently changed, if
    several).
    """
    live = [e for e in speakers() if e.get("state") == "playing"]
    live.sort(key=lambda e: str(e.get("last_changed", "")), reverse=True)
    return live[0] if live else None


def ha_play(
    speaker: dict,
    url: str,
    title: str = "",
    kind: str = "music",
    art: str = "",
) -> str:
    sh = _ha()
    data = {
        "entity_id": speaker["entity_id"],
        "media_content_id": url,
        "media_content_type": kind,
    }
    extra = {k: v for k, v in (("title", title), ("thumb", art)) if v}
    if extra:
        data["extra"] = extra
    sh._call("POST", "/api/services/media_player/play_media", data)
    return f"Playing {title or 'it'} on {sh._name(speaker)}."


def ha_move(target_name: str, source_name: str = "") -> str:
    """Move what is playing on SOURCE (or whatever plays) to TARGET: the same
    thing started there, then the old one stopped.
    """
    sh = _ha()
    target = find_speaker(target_name)
    source = find_speaker(source_name) if source_name else playing_speaker()
    if source is None:
        raise MediaError(
            "Nothing is playing on a Home Assistant speaker to move."
        )
    if source["entity_id"] == target["entity_id"]:
        return f"It is already playing on {sh._name(target)}."
    a = source.get("attributes") or {}
    content, kind = (
        a.get("media_content_id"),
        a.get("media_content_type") or "music",
    )
    if not content:
        raise MediaError(
            f"{sh._name(source)} does not say what it is playing, so it "
            "cannot be moved. "
            "Name the station or song and the speaker."
        )
    title = a.get("media_title") or a.get("media_artist") or ""
    vol = a.get("volume_level")
    if vol is not None:
        sh._call(
            "POST",
            "/api/services/media_player/volume_set",
            {"entity_id": target["entity_id"], "volume_level": vol},
        )
    ha_play(target, content, title, kind, a.get("entity_picture", ""))
    sh._call(
        "POST",
        "/api/services/media_player/media_stop",
        {"entity_id": source["entity_id"]},
    )
    what = title or "the music"
    return f"Moved {what} from {sh._name(source)} to {sh._name(target)}."


# ---- the Mac's Music app


def _osa(script: str) -> str:
    from .music import _osascript

    return _osascript(script)


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def on_mac() -> bool:
    return sys.platform == "darwin"


def airplay_devices() -> list[str]:
    if not on_mac():
        return []
    said = _osa('tell application "Music" to get name of every AirPlay device')
    return [s.strip() for s in said.split(",") if s.strip()]


def _match(name: str, names: list[str]) -> Optional[str]:
    want = _words(name)
    for n in names:
        if _words(n) == want:
            return n
    for n in names:
        if all(w in _words(n) for w in want):
            return n
    return None


def set_airplay(names: list[str]) -> str:
    """Send the Music app's sound to the AirPlay speakers NAMES (the Mac's own
    is "Computer").
    """
    have = airplay_devices()
    picked = []
    for n in names:
        m = _match(n, have) or (
            _match("computer", have)
            if _words(n) in (["this", "mac"], ["here"], ["mac"])
            else None
        )
        if m is None:
            raise MediaError(
                f"No AirPlay speaker called {n!r}. The Mac sees: "
                f"{', '.join(have) or 'none'}."
            )
        picked.append(m)
    devs = ", ".join(f"AirPlay device {_q(p)}" for p in picked)
    _osa(
        'tell application "Music" to set current AirPlay devices to '
        f"{{{devs}}}"
    )
    return ", ".join(picked)


def mac_play_stream(url: str, title: str) -> str:
    _osa(f'tell application "Music"\nopen location {_q(url)}\nplay\nend tell')
    return f"Playing {title} in Music."


def apple_catalog(query: str) -> Optional[dict]:
    """The song QUERY names in the Apple Music catalogue (Apple's public
    search): {title, link, id}.
    """
    country = (os.environ.get("FLASH_COUNTRY") or "us").lower()
    try:
        r = httpx.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "media": "music",
                "entity": "song",
                "limit": 5,
                "country": country,
            },
            timeout=TIMEOUT,
        )
        items = r.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not items:
        return None
    t = items[0]
    return {
        "title": f"{t.get('trackName')} by {t.get('artistName')}",
        "id": t.get("trackId"),
        "link": t.get("trackViewUrl", ""),
    }


def _open_in_music(link: str) -> None:
    """Open an Apple Music link in the Music app (not the browser) and start
    it.
    """
    music = re.sub(r"^https?://", "music://", link)
    subprocess.run(
        ["open", "-g", music],
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )  # nosec B603 B607
    # Music shows the song or station a moment later; then play it
    for _ in range(12):
        time.sleep(0.5)
        try:
            state = _osa(
                'tell application "Music" to get player state as string'
            )
        except Exception:  # noqa: BLE001
            continue
        if state == "playing":
            return
        with contextlib.suppress(Exception):
            _osa('tell application "Music" to play')


def mac_apple_music(query: str) -> str:
    """A song from the library if it is there, else from the whole catalogue.
    """
    from .music import MusicError, play_apple_music

    try:
        said = _osa(
            'tell application "Music"\n'
            f'set found to (search playlist "Library" for {_q(query)})\n'
            'if found is {} then return ""\n'
            "play item 1 of found\n"
            'return (name of item 1 of found) & " by " & '
            "(artist of item 1 of found)\nend tell"
        )
    except MusicError:
        said = ""
    if said:
        return f"Playing {said} in Music."
    hit = apple_catalog(query)
    if hit is None or not hit["link"]:
        return play_apple_music(query)
    _open_in_music(hit["link"])
    return f"Playing {hit['title']} from Apple Music."


def mac_apple_radio(name: str) -> str:
    hit = apple_radio(name)
    if hit is None:
        raise MediaError(
            "Apple Music's live radio is Apple Music 1, Apple Music Hits "
            "or Apple Music Country."
        )
    title, link = hit
    _open_in_music(link)
    return f"Playing {title} in Music."


# ---- the one way in


def _terminal_stream(url: str, title: str) -> str:
    if not shutil.which("mpv"):
        raise MediaError(
            "Nothing here can play a stream: connect Home Assistant, "
            "or install mpv."
        )
    from . import music

    return music.terminal_play({"url": url, "title": title})


def play(what: str, device: str = "", service: str = "radio") -> str:
    """Play WHAT (a station, or a song with SERVICE "apple_music") on DEVICE (a
    Home Assistant speaker, or an AirPlay speaker on a Mac), or where it plays
    when none is named.
    """
    device = (device or os.environ.get(DEFAULT_DEVICE) or "").strip()
    if service == "apple_music_radio" or (
        service == "radio" and apple_radio(what)
    ):
        if not on_mac():
            raise MediaError(
                "Apple Music Radio plays through the Music app on a Mac."
            )
        out = ""
        if device:
            out = set_airplay(
                [d for d in re.split(r"\s*(?:,| and )\s*", device) if d]
            )
        said = mac_apple_radio(what)
        return said + (f" On {out}." if out else "")
    if service == "apple_music":
        if not on_mac():
            raise MediaError(
                "Apple Music plays through the Music app on a Mac."
            )
        out = set_airplay([device]) if device else ""
        said = mac_apple_music(what)
        return said + (f" On {out}." if out else "")
    station = find_station(what)
    title = station["name"]
    # a named speaker: Home Assistant's first, then the Mac's AirPlay speakers
    if device and ha_ready():
        try:
            return ha_play(
                find_speaker(device),
                station["url"],
                title,
                "music",
                station.get("favicon", ""),
            )
        except MediaError:
            if not on_mac():
                raise
    if on_mac():
        out = set_airplay([device]) if device else ""
        return mac_play_stream(station["url"], title) + (
            f" On {out}." if out else ""
        )
    if device:
        raise MediaError(
            "To play on a speaker, connect Home Assistant "
            "(Settings, Connectors)."
        )
    return _terminal_stream(station["url"], title)


def move(device: str, source: str = "") -> str:
    """Move what is playing to DEVICE."""
    if not device:
        raise MediaError("Name the speaker to move it to.")
    if ha_ready():
        try:
            return ha_move(device, source)
        except MediaError:
            if not on_mac():
                raise
    if on_mac():
        out = set_airplay(
            [d for d in re.split(r"\s*(?:,| and )\s*", device) if d]
        )
        return f"Music is playing on {out} now."
    raise MediaError(
        "To move music between speakers, connect Home Assistant "
        "(Settings, Connectors)."
    )


def speaker_names() -> list[str]:
    out = []
    if ha_ready():
        sh = _ha()
        out += [sh._name(e) for e in speakers()]
    if on_mac():
        with contextlib.suppress(Exception):
            out += [f"{n} (AirPlay)" for n in airplay_devices()]
    return out
