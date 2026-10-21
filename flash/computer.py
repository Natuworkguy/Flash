"""Simple things Flash can do on the computer it runs on, once the user
lets it: play, pause and skip music, say what is playing, set the
volume, open and quit apps, open a link, file or folder, lock the
screen, put text on the clipboard, and switch dark mode.

Each is one fixed step through what the system already offers: scripts
of Flash's own on a Mac, media keys and the system's own calls on
Windows, playerctl and the sound server on Linux. Nothing is clicked or
typed, and nothing the model writes is run as a command: what it names
(an app, a file, a level) only ever goes in as an argument. Off until
the user turns it on (COMPUTER_ACTIONS), since even a pause reaches
outside the chat.
"""

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

SETTING = "COMPUTER_ACTIONS"
TIMEOUT = 10

# What the model may ask for, and what each does, for its tool.
ACTIONS = {
    "play_pause": "play or pause what is playing",
    "play": "start or resume music or video",
    "pause": "pause music or video",
    "next_track": "skip to the next track",
    "previous_track": "go back a track",
    "now_playing": "say what is playing, or paused, and in which app",
    "set_volume": "set the volume, value 0 to 100",
    "volume_up": "turn the volume up a step",
    "volume_down": "turn the volume down a step",
    "mute": "mute the sound",
    "unmute": "unmute the sound",
    "open_app": "open an app, value its name",
    "quit_app": "quit an app, value its name (it asks to save as usual)",
    "open": "open a link, file or folder, value the URL or path",
    "lock_screen": "lock the screen",
    "sleep_display": "turn the display off",
    "copy": "put text on the clipboard, value the text",
    "dark_mode": "switch dark mode, value on or off",
}
# How much a volume step moves it, in percent.
STEP = 10
COPY_CHARS = 100_000

# An app is named, never a path or a command: letters, digits and the
# few marks app names have.
_APP_NAME = re.compile(r"^[\w][\w .&'+-]{0,79}$")
# Opening one of these runs it rather than showing it: open_app is for
# apps, and a script or installer is not opened at all.
_RUNS = {
    ".app", ".command", ".tool", ".sh", ".bash", ".zsh", ".csh", ".fish",
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".msi", ".msix",
    ".appx", ".scr", ".pif", ".lnk", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".hta", ".cpl", ".jar", ".py", ".pyw", ".pl", ".rb",
    ".scpt", ".applescript", ".workflow", ".pkg", ".mpkg", ".dmg",
    ".desktop", ".run", ".bin", ".appimage", ".deb", ".rpm",
}
_SCHEMES = {"http", "https", "mailto"}


class ComputerError(Exception):
    """An action that could not be done, and why, for the model."""


def enabled() -> bool:
    return (os.environ.get(SETTING) or "").strip().lower() in (
        "1", "on", "true", "yes",
    )


def system() -> str:
    """"mac", "windows" or "linux"."""

    if sys.platform == "darwin":
        return "mac"
    return "windows" if sys.platform.startswith("win") else "linux"


def _run(argv: list[str], given: str = "") -> str:
    """Run ARGV, with GIVEN on its input, and return what it printed.
    Raises ComputerError when it could not run or failed."""

    try:
        # Fixed commands, never through a shell.
        done = subprocess.run(  # nosec B603
            argv, input=given, capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )
    except FileNotFoundError:
        raise ComputerError(f"{argv[0]} is not on this computer.") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise ComputerError(f"{argv[0]} could not run: {exc}") from None
    if done.returncode:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        raise ComputerError(
            f"{argv[0]} failed" + (f": {said[-1]}" if said else ".")
        )
    return (done.stdout or "").strip()


def do(action: str, value: str = "") -> str:
    """Do ACTION, with VALUE where it takes one. Returns what happened,
    in a sentence; raises ComputerError when it could not."""

    action = str(action or "").strip().lower()
    value = str(value or "").strip()
    if action not in ACTIONS:
        raise ComputerError(
            f"{action!r} is not an action. One of: {', '.join(ACTIONS)}."
        )
    kind = system()
    if action in _MEDIA:
        return _media(kind, action)
    if action in ("set_volume", "volume_up", "volume_down", "mute",
                  "unmute"):
        return _volume(kind, action, value)
    if action in ("open_app", "quit_app"):
        return _app(kind, action, _app_name(value))
    if action == "open":
        return _open(kind, value)
    if action == "lock_screen":
        return _lock(kind)
    if action == "sleep_display":
        return _sleep_display(kind)
    if action == "copy":
        return _copy(kind, value)
    return _dark_mode(kind, value)


# --- Music and video --------------------------------------------------

_MEDIA = ("play_pause", "play", "pause", "next_track", "previous_track",
          "now_playing")

# On a Mac, the players that can be asked directly, as they name
# themselves: only these say what they are playing, and only once
# already running are they asked anything, so none is opened. Anything
# else, a browser, a call, any app, gets the keyboard's media keys,
# which go to whatever played last.
MAC_PLAYERS = ("Spotify", "Music")
# The media keys, as macOS numbers them (NX_KEYTYPE_*): play and pause
# are one key.
MAC_KEYS = {"play_pause": 16, "play": 16, "pause": 16, "next_track": 17,
            "previous_track": 18}
# What macOS shows as Now Playing in Control Center, for whatever app it
# is: a browser tab, a call, any player. Apple lets only its own programs
# read it, which a script run by osascript is. {"known": false} when it
# cannot be read here, otherwise whether it is playing (null when macOS
# does not say), the title, the artist and the app.
_NOW_JS = """
const nowPlaying = () => {
  const said = (thing) => { try {
    const value = ObjC.unwrap(thing);
    return typeof value === "string" ? value : "";
  } catch (e) { return ""; } };
  try {
    $.NSBundle.bundleWithPath(
      "/System/Library/PrivateFrameworks/MediaRemote.framework").load;
    const request = $.NSClassFromString("MRNowPlayingRequest");
    if (!request || request.isNil()) return {known: false};
    const has = (name) => request.respondsToSelector(name);
    const item = has("localNowPlayingItem") ? request.localNowPlayingItem
      : null;
    const info = item && !item.isNil() ? item.nowPlayingInfo : null;
    const field = (key) => info && !info.isNil()
      ? info.objectForKey(`kMRMediaRemoteNowPlayingInfo${key}`) : null;
    let app = "";
    if (has("localNowPlayingPlayerPath")) {
      const path = request.localNowPlayingPlayerPath;
      const client = path && !path.isNil() ? path.client : null;
      if (client && !client.isNil()) {
        app = said(client.displayName) || said(client.bundleIdentifier);
      }
    }
    const rate = field("PlaybackRate");
    const playing = has("localIsPlaying") ? Boolean(request.localIsPlaying)
      : rate ? Number(ObjC.unwrap(rate)) > 0 : null;
    const title = said(field("Title")), artist = said(field("Artist"));
    return {known: Boolean(app || title), playing, title, artist, app};
  } catch (e) { return {known: false}; }
};
"""
_MAC_NOW = _NOW_JS + "JSON.stringify(nowPlaying());"
# How long a pressed key takes to show in Now Playing.
SETTLE = 0.6

_MAC_MEDIA = _NOW_JS + """
const action = %s, players = %s;
// An app that is not installed cannot even say it is not running.
const ask = (fn, otherwise) => { try { return fn(); } catch (e) {
  return otherwise; } };
const running = players.filter((p) => ask(() => Application(p).running(),
  false));
const playing = running.filter((p) => ask(
  () => String(Application(p).playerState()), "") === "playing");
const track = (p) => ask(() => {
  const t = Application(p).currentTrack;
  return `${p}: ${t.name()} by ${t.artist()}`;
}, p);
let did = "", told = [];
if (action === "now_playing") {
  told = playing.map(track);
} else if (playing.length && ["pause", "play_pause"].includes(action)) {
  playing.forEach((p) => Application(p).pause());
  did = "paused";
  told = playing;
} else if (playing.length && action === "play") {
  did = "already";
  told = playing.map(track);
} else if (playing.length && action !== "play_pause") {
  const app = Application(playing[0]);
  action === "next_track" ? app.nextTrack() : app.previousTrack();
  delay(0.3);
  did = "skipped";
  told = [track(playing[0])];
} else {
  // Anything else playing gets the key, pressed from Python.
  did = "press";
}
const now = did === "press" || action === "now_playing" ? nowPlaying()
  : {known: false};
JSON.stringify({did, told, now});
"""

# A media key, pressed as the keyboard presses it, by the number in
# argv[1]: straight through macOS's own calls, in a Python of its own so
# none of AppKit is loaded into Flash. macOS drops a key pressed by an
# app it has not let make key presses, without a word, so that is asked
# first, and asked for when it is not so (Privacy & Security >
# Accessibility). Prints "pressed", "not allowed" or "no key".
_MAC_KEY = """
import ctypes, sys, time
from ctypes import (c_bool, c_char_p, c_double, c_long, c_short, c_uint32,
                    c_ulong, c_void_p)
objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
cg = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/"
    "ApplicationServices")
objc.objc_getClass.restype = objc.sel_registerName.restype = c_void_p
objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [c_char_p]
objc.objc_autoreleasePoolPush.restype = c_void_p
objc.objc_autoreleasePoolPop.argtypes = [c_void_p]
cg.CGPreflightPostEventAccess.restype = c_bool
cg.CGRequestPostEventAccess.restype = c_bool
cg.CGEventPost.argtypes = [c_uint32, c_void_p]
if not cg.CGPreflightPostEventAccess():
    cg.CGRequestPostEventAccess()
    print("not allowed")
    sys.exit()


class Point(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


make = ctypes.CFUNCTYPE(
    c_void_p, c_void_p, c_void_p, c_ulong, Point, c_ulong, c_double, c_long,
    c_void_p, c_short, c_long, c_long,
)(("objc_msgSend", objc))
as_cg = ctypes.CFUNCTYPE(c_void_p, c_void_p, c_void_p)(("objc_msgSend", objc))
NSEvent = objc.objc_getClass(b"NSEvent")
other = objc.sel_registerName(
    b"otherEventWithType:location:modifierFlags:timestamp:windowNumber:"
    b"context:subtype:data1:data2:")
to_cg = objc.sel_registerName(b"CGEvent")
key = int(sys.argv[1])
pool = objc.objc_autoreleasePoolPush()
said = "pressed"
# Down, then up (NX_KEYDOWN, NX_KEYUP): a system-defined event (14) of the
# media keys' kind (8).
for state in (0xA, 0xB):
    event = make(NSEvent, other, 14, Point(0, 0), state << 8, 0.0, 0, None,
                 8, (key << 16) | (state << 8), -1)
    ref = as_cg(event, to_cg) if event else None
    if not ref:
        said = "no key"
        break
    cg.CGEventPost(0, ref)
    time.sleep(0.05)
objc.objc_autoreleasePoolPop(pool)
print(said)
"""

# Windows and Linux both press the keyboard's media keys, which every
# player listens for: Windows' virtual key codes.
_WIN_KEYS = {
    "play_pause": 0xB3, "next_track": 0xB0, "previous_track": 0xB1,
    "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}
_PLAYERCTL = {
    "play_pause": ["play-pause"], "play": ["play"],
    "pause": ["--all-players", "pause"], "next_track": ["next"],
    "previous_track": ["previous"],
}


def _jxa(script: str) -> str:
    return _run(["osascript", "-l", "JavaScript", "-e", script])


def _media(kind: str, action: str) -> str:
    if kind == "mac":
        return _mac_media(action)
    if kind == "windows":
        if action == "now_playing":
            raise ComputerError(
                "Flash cannot see what is playing on Windows yet."
            )
        # Windows only has the one key for both: it pauses what plays.
        _press(_WIN_KEYS.get(action, _WIN_KEYS["play_pause"]))
        return f"Pressed the {action.replace('_', ' ')} media key."
    if action == "now_playing":
        playing = _playerctl(["metadata", "--format",
                              "{{title}} by {{artist}} ({{status}})"])
        return f"Playing: {playing}" if playing else "Nothing is playing."
    _playerctl(_PLAYERCTL[action])
    return f"Done ({action.replace('_', ' ')})."


def _mac_media(action: str) -> str:
    found = json.loads(_jxa(_MAC_MEDIA % (
        json.dumps(action), json.dumps(MAC_PLAYERS),
    )) or "{}")
    did, told = found.get("did", ""), found.get("told") or []
    now = _known(found.get("now"))
    if action == "now_playing":
        if now is not None:
            return f"{_state(now).capitalize()}: {_described(now)}."
        return ("Playing: " + "; ".join(told) + ".") if told else (
            "Flash cannot see what is playing on this Mac, and neither "
            "Spotify nor Music is playing anything."
        )
    if did == "paused":
        return "Paused " + " and ".join(told) + "."
    if did == "already":
        return "Already playing: " + "; ".join(told) + "."
    if did == "skipped":
        return f"Skipped. Now playing {'; '.join(told)}."
    # Play and pause are one key: one that would only turn it the wrong
    # way is not pressed.
    if now is not None and action == "pause" and now["playing"] is False:
        return f"Nothing to pause: {_described(now)} is already paused."
    if now is not None and action == "play" and now["playing"] is True:
        return f"Already playing: {_described(now)}."
    pressed = _run([sys.executable, "-c", _MAC_KEY, str(MAC_KEYS[action])])
    if pressed == "not allowed":
        raise ComputerError(
            "macOS has to let the app Flash runs in (Terminal, iTerm, VS "
            "Code and the like) control the computer and press keys before "
            "Flash can press the media keys. It just asked: turn that app "
            "on in System Settings > Privacy & Security > Accessibility. "
            "If it is on already, turn it off and on again, quit and "
            "reopen that app, then ask again."
        )
    if pressed == "no key":
        raise ComputerError("macOS would not make the media key press.")
    key = "play/pause" if MAC_KEYS[action] == 16 else (
        action.replace("_", " ")
    )
    return f"Pressed the {key} media key. " + _after(action, now)


def _known(now) -> Optional[dict]:
    """Now Playing as macOS shows it, when it could be read."""

    return now if isinstance(now, dict) and now.get("known") else None


def _mac_now() -> Optional[dict]:
    try:
        return _known(json.loads(_jxa(_MAC_NOW) or "null"))
    except (ComputerError, ValueError):
        return None


def _state(now: dict) -> str:
    states = {True: "playing", False: "paused"}
    return states.get(now.get("playing"), "showing")


def _described(now: dict) -> str:
    what = now.get("title") or "something"
    if now.get("artist"):
        what += f" by {now['artist']}"
    if now.get("app"):
        what += f" in {now['app']}"
    return what


def _after(action: str, before: Optional[dict]) -> str:
    """What Now Playing shows once a pressed key has had time to land,
    and whether that is what the key was for, so the model can tell the
    user it worked, or did not."""

    time.sleep(SETTLE)
    now = _mac_now()
    if now is None:
        return (
            "It goes to whatever played last; Flash cannot see what is "
            "playing on this Mac to check it worked."
        )
    said = f"Now {_state(now)}: {_described(now)}."
    if action in ("next_track", "previous_track"):
        if before is not None and before.get("title") == now.get("title"):
            return said + " That is the same as before, so it may not " \
                "have skipped."
        return said
    wanted = {"play": True, "pause": False}.get(action)
    if wanted is None and before is not None and before["playing"] is not None:
        wanted = not before["playing"]
    if wanted is not None and now["playing"] is not None \
            and now["playing"] != wanted:
        return said + " So the key did not do it."
    return said


def _playerctl(args: list[str]) -> str:
    if not shutil.which("playerctl"):
        raise ComputerError(
            "Controlling music on Linux needs playerctl. Install it with "
            "your package manager (`sudo apt install playerctl`)."
        )
    try:
        return _run(["playerctl", *args])
    except ComputerError as exc:
        if "No players found" in str(exc):
            raise ComputerError("No music or video player is open.") from None
        raise


def _press(key: int, times: int = 1) -> None:
    """Press a key on Windows, as the keyboard would."""

    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)  # KEYEVENTF_KEYUP


# --- Volume -----------------------------------------------------------


def _level(value: str) -> int:
    try:
        level = round(float(value.rstrip("%")))
    except ValueError:
        raise ComputerError(
            "set_volume needs a level from 0 to 100 as its value."
        ) from None
    return max(0, min(100, level))


def _volume(kind: str, action: str, value: str) -> str:
    level = _level(value) if action == "set_volume" else 0
    if kind == "mac":
        script = {
            "set_volume": f"set volume output volume {level}",
            "volume_up": (
                "set volume output volume ((output volume of (get volume "
                f"settings)) + {STEP})"
            ),
            "volume_down": (
                "set volume output volume ((output volume of (get volume "
                f"settings)) - {STEP})"
            ),
            "mute": "set volume output muted true",
            "unmute": "set volume output muted false",
        }[action]
        now = _run(["osascript", "-e", script, "-e",
                    "output volume of (get volume settings)"])
        return _said_volume(action, now)
    if kind == "windows":
        if action == "set_volume":
            # Each key press moves it 2 points: all the way down, then
            # up to the level.
            _press(_WIN_KEYS["volume_down"], 50)
            _press(_WIN_KEYS["volume_up"], level // 2)
        elif action in ("mute", "unmute"):
            # One key for both, as with play and pause.
            _press(_WIN_KEYS["mute"])
        else:
            _press(_WIN_KEYS[action], STEP // 2)
        return _said_volume(action, str(level) if level else "")
    return _said_volume(action, _linux_volume(action, level))


def _said_volume(action: str, now: str) -> str:
    if action == "mute":
        return "Muted."
    if action == "unmute":
        return "Unmuted."
    return f"Volume is at {now}%." if now else "Volume changed."


def _linux_volume(action: str, level: int) -> str:
    """Through whichever sound server is there: PipeWire, PulseAudio, or
    ALSA. Returns the level when it is known."""

    if shutil.which("wpctl"):
        sink = "@DEFAULT_AUDIO_SINK@"
        _run(["wpctl", *{
            "set_volume": ["set-volume", sink, f"{level / 100:.2f}"],
            "volume_up": ["set-volume", "-l", "1", sink, f"{STEP}%+"],
            "volume_down": ["set-volume", sink, f"{STEP}%-"],
            "mute": ["set-mute", sink, "1"],
            "unmute": ["set-mute", sink, "0"],
        }[action]])
        found = re.search(r"Volume:\s*([\d.]+)",
                          _run(["wpctl", "get-volume", sink]))
        return str(round(float(found.group(1)) * 100)) if found else ""
    if shutil.which("pactl"):
        sink = "@DEFAULT_SINK@"
        _run(["pactl", *{
            "set_volume": ["set-sink-volume", sink, f"{level}%"],
            "volume_up": ["set-sink-volume", sink, f"+{STEP}%"],
            "volume_down": ["set-sink-volume", sink, f"-{STEP}%"],
            "mute": ["set-sink-mute", sink, "1"],
            "unmute": ["set-sink-mute", sink, "0"],
        }[action]])
        found = re.search(r"(\d+)%", _run(["pactl", "get-sink-volume",
                                          sink]))
        return found.group(1) if found else ""
    if shutil.which("amixer"):
        said = _run(["amixer", "set", "Master", {
            "set_volume": f"{level}%", "volume_up": f"{STEP}%+",
            "volume_down": f"{STEP}%-", "mute": "mute", "unmute": "unmute",
        }[action]])
        found = re.search(r"\[(\d+)%\]", said)
        return found.group(1) if found else ""
    raise ComputerError(
        "No sound control found: Flash uses wpctl, pactl or amixer."
    )


# --- Apps, links and files --------------------------------------------


def _app_name(value: str) -> str:
    name = value.strip().removesuffix(".app").removesuffix(".exe")
    if not _APP_NAME.match(name):
        raise ComputerError(
            "Name the app the way it is called, like Spotify or Visual "
            "Studio Code: not a path or a command."
        )
    return name


def _app(kind: str, action: str, name: str) -> str:
    if kind == "mac":
        if action == "open_app":
            _run(["open", "-a", name])
            return f"Opened {name}."
        said = _jxa(
            f"const app = Application({json.dumps(name)});"
            "app.running() ? (app.quit(), 'quit') : 'not running'"
        )
        if said == "not running":
            return f"{name} was not open."
        return f"Asked {name} to quit."
    if kind == "windows":
        if action == "open_app":
            # The shell finds it by name, the way Run does; "start"
            # treats the first quoted argument as a title.
            _run(["cmd", "/c", "start", "", name])
            return f"Opened {name}."
        # Asked to close, as its window's close button does, not killed.
        _run(["taskkill", "/IM", f"{name}.exe"])
        return f"Asked {name} to quit."
    if action == "open_app":
        found = shutil.which(name) or shutil.which(name.lower())
        if found:
            subprocess.Popen(  # nosec B603
                [found], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            return f"Opened {name}."
        if shutil.which("gtk-launch"):
            _run(["gtk-launch", name.lower()])
            return f"Opened {name}."
        raise ComputerError(f"No app called {name} was found.")
    try:
        _run(["pkill", "-x", "-i", name])
    except ComputerError:
        return f"{name} was not open."
    return f"Asked {name} to quit."


def _open(kind: str, value: str) -> str:
    target = _openable(value)
    if kind == "windows":
        os.startfile(target)  # type: ignore[attr-defined]  # nosec B606
    else:
        _run(["open" if kind == "mac" else "xdg-open", target])
    return f"Opened {target}."


def _openable(value: str) -> str:
    """VALUE, when it is a link or a file or folder to show rather than
    something that would run."""

    if not value:
        raise ComputerError("open needs a link, file or folder as its value.")
    parsed = urlparse(value)
    if parsed.scheme.lower() in _SCHEMES:
        return value
    if parsed.scheme and len(parsed.scheme) > 1:
        raise ComputerError(
            f"Only web and mail links open this way, not {parsed.scheme}:."
        )
    path = Path(value).expanduser()
    if not path.exists():
        raise ComputerError(f"Nothing is at {path}.")
    path = path.resolve()
    if path.suffix.lower() in _RUNS or (
        path.is_file() and os.name != "nt" and os.access(path, os.X_OK)
    ):
        raise ComputerError(
            f"{path.name} would run rather than open, so it was not opened. "
            "Use open_app for an app."
        )
    return str(path)


# --- The screen, the clipboard and the look ----------------------------


def _lock(kind: str) -> str:
    if kind == "mac":
        # Sleeping the display locks a Mac set to ask for its password
        # straight away, as it is by default.
        _run(["pmset", "displaysleepnow"])
        return "The display is asleep, and the Mac locked."
    if kind == "windows":
        import ctypes

        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
        return "Locked."
    for argv in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
        if shutil.which(argv[0]):
            _run(argv)
            return "Locked."
    raise ComputerError("No way to lock the screen was found.")


def _sleep_display(kind: str) -> str:
    if kind == "mac":
        _run(["pmset", "displaysleepnow"])
    elif kind == "windows":
        import ctypes

        # The monitor's power, off: to every window, without waiting.
        ctypes.windll.user32.PostMessageW(  # type: ignore[attr-defined]
            0xFFFF, 0x0112, 0xF170, 2,
        )
    elif shutil.which("xset"):
        _run(["xset", "dpms", "force", "off"])
    else:
        raise ComputerError("No way to turn the display off was found.")
    return "The display is off."


def _copy(kind: str, value: str) -> str:
    if not value:
        raise ComputerError("copy needs the text as its value.")
    text = value[:COPY_CHARS]
    if kind == "mac":
        _run(["pbcopy"], text)
    elif kind == "windows":
        # clip takes UTF-16 with its mark, which keeps every character.
        try:
            subprocess.run(  # nosec B603 B607
                ["clip"], input=("\ufeff" + text).encode("utf-16-le"),
                timeout=TIMEOUT, check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ComputerError(f"clip could not run: {exc}") from None
    else:
        for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                     ["xsel", "--clipboard", "--input"]):
            if shutil.which(argv[0]):
                _run(argv, text)
                break
        else:
            raise ComputerError(
                "No clipboard tool found: Flash uses wl-copy, xclip or xsel."
            )
    return f"Copied {len(text)} characters to the clipboard."


def _on(value: str) -> Optional[bool]:
    said = value.lower()
    if said in ("on", "dark", "true", "1", "yes"):
        return True
    if said in ("off", "light", "false", "0", "no"):
        return False
    return None


def _dark_mode(kind: str, value: str) -> str:
    dark = _on(value)
    if dark is None:
        raise ComputerError("dark_mode needs on or off as its value.")
    if kind == "mac":
        _jxa(
            "Application('System Events').appearancePreferences.darkMode = "
            + ("true" if dark else "false")
        )
    elif kind == "windows":
        import winreg  # type: ignore[import-not-found]

        light = 0 if dark else 1
        with winreg.CreateKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(  # type: ignore[attr-defined]
                    key, name, 0, winreg.REG_DWORD,  # type: ignore
                    light,
                )
    elif shutil.which("gsettings"):
        _run(["gsettings", "set", "org.gnome.desktop.interface",
              "color-scheme", "prefer-dark" if dark else "default"])
    else:
        raise ComputerError("No way to switch dark mode was found.")
    return f"Dark mode is {'on' if dark else 'off'}."
