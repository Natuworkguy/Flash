"""Read a YouTube video or Short from its link: details, captions, frames.

Uses only the standard library. The watch page gives the title, channel,
description, length and storyboard (YouTube's own grids of frames, one a
second); captions come from the player API as the Android app asks for
them, since the web page's caption links now come back empty.
"""

from __future__ import annotations

import html
import json
import math
import platform
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TIMEOUT_SECONDS = 20
MAX_TRANSCRIPT_CHARS = 30_000
MAX_SHEETS = 4

_CHROME_VERSION = "142.0.0.0"


def _platform_token() -> str:
    """The OS part of a Chrome User-Agent, for the machine this runs on.

    Chrome's own format: macOS always says "Intel" (Apple silicon Macs
    send it too) and Windows 11 still says NT 10.0.
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        version = platform.mac_ver()[0] or "10.15.7"
        return f"Macintosh; Intel Mac OS X {version.replace('.', '_')}"
    if system == "Windows":
        parts = platform.version().split(".")
        nt = ".".join(parts[:2]) if len(parts) >= 2 else "10.0"
        arch = "ARM64" if machine in ("arm64", "aarch64") else "x64"
        return f"Windows NT {nt}; Win64; {arch}"
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    return f"X11; Linux {arch or 'x86_64'}"


_WEB_AGENT = (
    f"Mozilla/5.0 ({_platform_token()}) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_VERSION} Safari/537.36"
)
_ANDROID_VERSION = "20.10.38"
_ANDROID_AGENT = (
    f"com.google.android.youtube/{_ANDROID_VERSION} "
    "(Linux; U; Android 11) gzip"
)
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com",
          "youtube-nocookie.com"}


@dataclass
class Video:
    id: str
    title: str = ""
    channel: str = ""
    description: str = ""
    seconds: int = 0
    transcript: str = ""
    caption_language: str = ""
    frames: list[bytes] = field(default_factory=list)
    frame_note: str = ""


def video_id(url: str) -> str | None:
    """The 11-character id in a watch, youtu.be, /shorts/, /live/ or
    /embed/ link (or a bare id), else None."""

    text = url.strip()
    if _ID.match(text):
        return text
    if "://" not in text:
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = [p for p in parts.path.split("/") if p]
    found = None
    if host == "youtu.be" and path:
        found = path[0]
    elif host in _HOSTS:
        if path[:1] == ["watch"]:
            found = urllib.parse.parse_qs(parts.query).get("v", [None])[0]
        elif len(path) >= 2 and path[0] in ("shorts", "live", "embed", "v"):
            found = path[1]
    return found if found and _ID.match(found) else None


def _get(url: str, data: bytes | None = None, headers: dict | None = None
         ) -> bytes:
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": _WEB_AGENT, "Accept-Language": "en", **(headers or {}),
    })
    with urllib.request.urlopen(  # nosec B310 -- fixed https YouTube URLs
        request, timeout=TIMEOUT_SECONDS
    ) as response:
        return response.read()


def player_from_page(page: str) -> dict:
    """The ytInitialPlayerResponse object embedded in a watch page."""

    start = page.find("ytInitialPlayerResponse")
    if start < 0:
        return {}
    start = page.find("{", start)
    try:
        value, _ = json.JSONDecoder().raw_decode(page, start)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _android_player(vid: str) -> dict:
    body = json.dumps({"videoId": vid, "context": {"client": {
        "clientName": "ANDROID", "clientVersion": _ANDROID_VERSION,
        "androidSdkVersion": 30, "hl": "en",
    }}}).encode()
    raw = _get("https://www.youtube.com/youtubei/v1/player", body, {
        "Content-Type": "application/json", "User-Agent": _ANDROID_AGENT,
    })
    return json.loads(raw)


def pick_track(tracks: list[dict]) -> dict | None:
    """Prefer English written captions, then any written ones, then
    English automatic ones, then whatever there is."""

    def rank(track: dict) -> tuple[int, int]:
        english = track.get("languageCode", "").startswith("en")
        auto = track.get("kind") == "asr"
        return (auto, not english)

    return min(tracks, key=rank) if tracks else None


def _stamp(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_captions(xml: str) -> str:
    """Timed lines from a timedtext document (format 3 <p t=…> or the
    older <text start=…>)."""

    lines = []
    for match in re.finditer(
        r'<(p|text)\b([^>]*)>(.*?)</\1>', xml, re.DOTALL
    ):
        tag, attrs, inner = match.groups()
        words = html.unescape(re.sub(r"<[^>]+>", "", inner))
        words = " ".join(html.unescape(words).split())
        if not words:
            continue
        if tag == "p":
            at = re.search(r'\bt="(\d+)"', attrs)
            ms = int(at.group(1)) if at else 0
        else:
            at = re.search(r'\bstart="([\d.]+)"', attrs)
            ms = int(float(at.group(1)) * 1000) if at else 0
        if lines and lines[-1][1] == words:
            continue
        lines.append((ms, words))
    return "\n".join(f"[{_stamp(ms)}] {words}" for ms, words in lines)


def storyboard_urls(spec: str, limit: int = MAX_SHEETS) -> list[str]:
    """Up to `limit` frame-grid image URLs, spread over the video, from
    the largest storyboard level."""

    levels = spec.split("|")
    if len(levels) < 2:
        return []
    base, level = levels[0], len(levels) - 2
    bits = levels[-1].split("#")
    if len(bits) < 8:
        return []
    try:
        count, cols, rows = int(bits[2]), int(bits[3]), int(bits[4])
    except ValueError:
        return []
    sheets = max(1, math.ceil(count / max(1, cols * rows)))
    if sheets <= limit:
        picks = list(range(sheets))
    else:
        picks = sorted({round(i * (sheets - 1) / (limit - 1))
                        for i in range(limit)})
    name, sigh = bits[6], bits[7]
    return [
        base.replace("$L", str(level))
        .replace("$N", name.replace("$M", str(n)))
        + "&sigh=" + sigh
        for n in picks
    ]


def read_video(vid: str, want_frames: bool = True, transcript_chars: int = MAX_TRANSCRIPT_CHARS) -> Video:
    """Fetch everything we can about one video. Raises on a page that
    cannot be loaded at all; missing captions or frames are noted."""

    page = _get(f"https://www.youtube.com/watch?v={vid}&hl=en").decode(
        "utf-8", "replace"
    )
    player = player_from_page(page)
    details = player.get("videoDetails") or {}
    status = player.get("playabilityStatus") or {}
    if not details and status.get("status") not in (None, "OK"):
        raise ValueError(status.get("reason") or status.get("status"))

    video = Video(
        id=vid,
        title=details.get("title", ""),
        channel=details.get("author", ""),
        description=details.get("shortDescription", ""),
        seconds=int(details.get("lengthSeconds") or 0),
    )

    try:
        android = _android_player(vid)
    except (OSError, ValueError):
        android = {}
    tracks = (
        (android.get("captions") or player.get("captions") or {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    track = pick_track(tracks)
    if track:
        try:
            xml = _get(track["baseUrl"]).decode("utf-8", "replace")
            video.transcript = parse_captions(xml)[:transcript_chars]
            video.caption_language = track.get("languageCode", "") + (
                " (automatic)" if track.get("kind") == "asr" else ""
            )
        except (OSError, KeyError):
            pass

    if want_frames:
        spec = ((player.get("storyboards") or {})
                .get("playerStoryboardSpecRenderer", {}).get("spec", ""))
        for url in storyboard_urls(spec):
            try:
                video.frames.append(_get(url))
            except OSError:
                continue
        if video.frames:
            video.frame_note = (
                f"{len(video.frames)} storyboard sheet(s), each a grid of "
                "small frames in reading order, about one a second, "
                "spread across the video"
            )
        else:
            try:
                video.frames.append(_get(
                    f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"))
                video.frame_note = "the thumbnail only (no storyboard)"
            except OSError:
                pass
    return video
