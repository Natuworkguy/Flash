"""Computer Use (beta): Flash looks at the screen and works the mouse and
keyboard itself, for the things no tool of its own can do: a setting
deep in an app, a button in a program with no other way in.

It takes a screenshot, the model says where to click, what to type or
which keys to press, and a fresh screenshot shows what happened. Every
other tool comes first; this is the last way in, and the slowest.

Off until the user turns it on (COMPUTER_USE). Even then it asks before
it takes the mouse, every reply that wants it, autonomous mode or not
(only UltraAuto skips that). While it has it, the user takes it back by
pressing the stop keys (Ctrl+Alt+Esc, Control+Option+Esc on a Mac) or
by moving the mouse themselves: either stops the reply then and there.

On a Mac it uses the system's own screenshot command and posts mouse
and key events through Core Graphics, in a Python of Flash's own, so
the app Flash runs in needs Accessibility (and Screen Recording, for
the screenshots). On Windows the same through user32 and PowerShell; on
Linux, xdotool and whatever takes screenshots there.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

SETTING = "COMPUTER_USE"
TIMEOUT = 15
# How long a yes lasts with nothing done under it: a reply that ends, or
# is stopped, lets go of the computer at once.
IDLE_SECONDS = 300
# Screenshots are made at most this wide, and the model's points are
# turned back into the screen's own.
MOST_WIDTH = 1280
# How far the pointer has to be from where Flash left it to mean the
# user has taken the mouse back.
MOVED = 24
# How long the screen gets to change before the screenshot after a step.
SETTLE = 0.6
MOST_TYPED = 2000
MOST_WAIT = 10.0
MOST_SCROLL = 30
LOOK_EVERY = 0.05

ACTIONS = {
    "screenshot": "look at the screen",
    "click": "click at x, y (button left, right or middle)",
    "double_click": "double-click at x, y",
    "right_click": "right-click at x, y",
    "move": "move the pointer to x, y",
    "drag": "drag from x, y to to_x, to_y",
    "type": "type text where the cursor is",
    "key": "press keys, e.g. enter, cmd+c, ctrl+shift+t",
    "scroll": "scroll at x, y, direction up, down, left or right, amount "
              "steps",
    "wait": "wait seconds (at most 10) for something to load",
}
_POINTED = ("click", "double_click", "right_click", "move", "drag",
            "scroll")


class UseError(Exception):
    """A step that could not be done, and why, for the model."""


class TakenBack(UseError):
    """The user took the computer back: the reply stops."""


def enabled() -> bool:
    from .notify import _saved

    return _saved(SETTING).strip().lower() in ("1", "on", "true", "yes")


def set_enabled(on: bool) -> None:
    from .envfile import set_env_var
    from .paths import ENV_PATH

    os.environ[SETTING] = "1" if on else "0"
    set_env_var(ENV_PATH, SETTING, os.environ[SETTING])
    if not on:
        end()


def system() -> str:
    from .computer import system as which

    return which()


def stop_keys() -> str:
    """The stop keys, as this computer writes them."""

    return "Control+Option+Esc" if system() == "mac" else "Ctrl+Alt+Esc"


def missing() -> str:
    """What this computer lacks for it, or ""."""

    kind = system()
    if kind == "linux":
        if not shutil.which("xdotool"):
            return ("Computer Use on Linux needs xdotool (sudo apt install "
                    "xdotool), and an X session.")
        if _linux_shooter() is None:
            return ("Computer Use on Linux needs a screenshot program: "
                    "gnome-screenshot, scrot, grim or ImageMagick.")
    if kind == "windows" and not shutil.which("powershell"):
        return "Computer Use on Windows needs PowerShell for screenshots."
    return ""


# --- Who has the computer -------------------------------------------------

_lock = threading.Lock()
# The one reply that has the computer: who it is, how to stop it, when it
# last did something, and where it left the pointer.
_holder: dict = {}
_watching: dict = {}


def holds(owner: str) -> bool:
    """Whether OWNER has the user's yes, and still has it."""

    with _lock:
        if not _holder or _holder["owner"] != owner:
            return False
        if time.monotonic() - _holder["at"] > IDLE_SECONDS:
            _release()
            return False
        return True


def busy(owner: str) -> bool:
    """Whether another reply has the computer right now."""

    with _lock:
        return bool(_holder) and _holder["owner"] != owner and \
            time.monotonic() - _holder["at"] <= IDLE_SECONDS


def take(owner: str, stop: Optional[Callable[[], None]] = None) -> None:
    """Give OWNER the computer, after the user's yes. STOP stops its reply
    when the user presses the stop keys or moves the mouse."""

    with _lock:
        _holder.clear()
        _holder.update(owner=owner, stop=stop, at=time.monotonic(),
                       pointer=None)
    _watch()


def end(owner: Optional[str] = None) -> None:
    """Let go of the computer: OWNER's reply ended (any reply's, with
    none). The next one that wants it asks again."""

    with _lock:
        if _holder and (owner is None or _holder["owner"] == owner):
            _release()


def _release() -> None:
    _holder.clear()
    process = _watching.pop("process", None)
    _watching.pop("thread", None)
    if process is not None and process.poll() is None:
        process.kill()


def taken_back() -> None:
    """The user pressed the stop keys: the reply that has the computer
    stops, and lets go of it."""

    with _lock:
        stop = _holder.get("stop")
        _release()
    if stop is not None:
        with contextlib.suppress(Exception):
            stop()


def interrupt_terminal() -> None:
    """Stop the terminal's reply, as Ctrl+C would."""

    if os.name == "nt":
        import _thread

        _thread.interrupt_main()
    else:
        os.kill(os.getpid(), signal.SIGINT)


def _watch() -> None:
    """Look for the stop keys while a reply has the computer."""

    if _watching.get("thread") is not None and \
            _watching["thread"].is_alive():
        return
    kind = system()
    thread = threading.Thread(target=_watch_keys, args=(kind,), daemon=True)
    _watching["thread"] = thread
    thread.start()


def _watch_keys(kind: str) -> None:
    try:
        if kind == "mac":
            pressed = _mac_watch()
        elif kind == "windows":
            pressed = _poll(_win_stop_down)
        else:
            pressed = _poll(_linux_stop_down())
    except Exception:  # noqa: BLE001  (moving the mouse still stops it)
        return
    if pressed and threading.current_thread() is _watching.get("thread"):
        taken_back()


def _poll(down: Optional[Callable[[], bool]]) -> bool:
    if down is None:
        return False
    me = threading.current_thread()
    while _watching.get("thread") is me:
        if down():
            return True
        time.sleep(LOOK_EVERY)
    return False


def _mac_watch() -> bool:
    # Its own process, as every Core Graphics call here is: it reads the
    # keyboard's state, and prints "stop" when the keys are down.
    process = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _MAC_WATCH, str(os.getpid())],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True,
    )
    _watching["process"] = process
    said = (process.stdout.readline() if process.stdout else "").strip()
    return said == "stop"


# --- Doing it -------------------------------------------------------------

# The last screenshot: how many screen points one of its pixels is, and
# its size, to check the model's points against.
_shot: dict = {}


def do(action: str, args: dict, owner: str) -> tuple[str, Optional[bytes]]:
    """Do ACTION with ARGS for OWNER, which has the computer. Returns
    what happened, and a screenshot after it (PNG bytes). Raises
    UseError when it could not, TakenBack when the user took it back."""

    action = str(action or "").strip().lower()
    if action not in ACTIONS:
        raise UseError(f"{action!r} is not an action. One of: "
                       f"{', '.join(ACTIONS)}.")
    why = missing()
    if why:
        raise UseError(why)
    if action == "wait":
        seconds = min(MOST_WAIT, max(0.0, _number(args.get("seconds"), 1)))
        time.sleep(seconds)
        said = f"Waited {seconds:g} seconds."
    elif action == "screenshot":
        said = ""
    else:
        _check_pointer(owner)
        said = _act(action, args)
        time.sleep(SETTLE)
    with _lock:
        if _holder.get("owner") == owner:
            _holder["at"] = time.monotonic()
    image, size = screenshot()
    shown = f"The screen now, {size[0]}x{size[1]}."
    return (f"{said} {shown}".strip(), image)


def _check_pointer(owner: str) -> None:
    """Whether the user has moved the mouse since Flash last did: then it
    is theirs again, and the reply stops."""

    with _lock:
        left = _holder.get("pointer") if _holder.get("owner") == owner \
            else None
    if left is None:
        return
    now = pointer()
    if abs(now[0] - left[0]) > MOVED or abs(now[1] - left[1]) > MOVED:
        taken_back()
        raise TakenBack(
            "The user moved the mouse, which takes the computer back. "
            "Stop here: do not try again unless they ask."
        )


def _left_at(x: float, y: float) -> None:
    with _lock:
        if _holder:
            _holder["pointer"] = (x, y)


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _point(args: dict, x: str = "x", y: str = "y") -> tuple[float, float]:
    """The model's point, from the screenshot, as the screen's own."""

    if not _shot:
        raise UseError("Take a screenshot first, to see where things are.")
    if args.get(x) is None or args.get(y) is None:
        raise UseError(f"This needs {x} and {y}, from the screenshot.")
    px, py = _number(args.get(x), -1), _number(args.get(y), -1)
    width, height = _shot["size"]
    if not (0 <= px <= width and 0 <= py <= height):
        raise UseError(
            f"({px:g}, {py:g}) is off the screenshot, which is "
            f"{width}x{height}."
        )
    return px * _shot["scale"], py * _shot["scale"]


def _act(action: str, args: dict) -> str:
    kind = system()
    if action == "type":
        text = str(args.get("text") or "")
        if not text:
            raise UseError("Nothing to type: give text.")
        if len(text) > MOST_TYPED:
            raise UseError(f"At most {MOST_TYPED} characters at a time.")
        _send(kind, {"do": "type", "text": text})
        return f"Typed {len(text)} characters."
    if action == "key":
        mods, key = parse_keys(str(args.get("keys") or args.get("text")
                                   or ""))
        _send(kind, {"do": "key", "mods": mods, "key": key})
        return "Pressed " + "+".join([*mods, key]) + "."
    x, y = _point(args)
    if action == "drag":
        to_x, to_y = _point(args, "to_x", "to_y")
        _send(kind, {"do": "drag", "x": x, "y": y, "to_x": to_x,
                     "to_y": to_y})
        _left_at(to_x, to_y)
        return "Dragged."
    if action == "scroll":
        direction = str(args.get("direction") or "down").lower()
        if direction not in ("up", "down", "left", "right"):
            raise UseError("direction is up, down, left or right.")
        amount = int(min(MOST_SCROLL, max(1, _number(args.get("amount"), 3))))
        _send(kind, {"do": "scroll", "x": x, "y": y, "direction": direction,
                     "amount": amount})
        _left_at(x, y)
        return f"Scrolled {direction} {amount}."
    if action == "move":
        _send(kind, {"do": "move", "x": x, "y": y})
        _left_at(x, y)
        return "Moved the pointer."
    button = "right" if action == "right_click" else str(
        args.get("button") or "left").lower()
    if button not in ("left", "right", "middle"):
        raise UseError("button is left, right or middle.")
    count = 2 if action == "double_click" else 1
    _send(kind, {"do": "click", "x": x, "y": y, "button": button,
                 "count": count})
    _left_at(x, y)
    return ("Double-clicked." if count == 2 else
            "Right-clicked." if button == "right" else "Clicked.")


# --- Keys -----------------------------------------------------------------

_MODIFIERS = {
    "cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd",
    "win": "cmd", "windows": "cmd", "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt", "shift": "shift",
    "fn": "fn",
}
_NAMED = {
    "enter": "return", "return": "return", "esc": "escape",
    "escape": "escape", "tab": "tab", "space": "space",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "home": "home", "end": "end", "pageup": "pageup", "page_up": "pageup",
    "pgup": "pageup", "pagedown": "pagedown", "page_down": "pagedown",
    "pgdn": "pagedown", "up": "up", "down": "down", "left": "left",
    "right": "right", **{f"f{n}": f"f{n}" for n in range(1, 13)},
}
_PUNCTUATION = "-=[];',./\\`"


def parse_keys(keys: str) -> tuple[list[str], str]:
    """KEYS ("cmd+shift+4", "enter") as its modifiers and its key. Raises
    UseError for keys it does not know, and for the stop keys."""

    parts = [p.strip().lower() for p in keys.replace(" ", "").split("+")]
    if not keys.strip() or not all(parts):
        raise UseError("Say which keys, e.g. enter, cmd+c, ctrl+shift+t.")
    mods: list[str] = []
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            raise UseError(f"{part!r} is not a modifier: cmd, ctrl, alt, "
                           "shift or fn.")
        if _MODIFIERS[part] not in mods:
            mods.append(_MODIFIERS[part])
    key = parts[-1]
    if key in _NAMED:
        key = _NAMED[key]
    elif not (len(key) == 1 and (key.isalnum() and key.isascii()
                                 or key in _PUNCTUATION)):
        raise UseError(f"{key!r} is not a key Flash can press. Use type "
                       "for text.")
    if key == "escape" and {"ctrl", "alt"} <= set(mods):
        raise UseError("Those are the user's stop keys: Flash never "
                       "presses them.")
    return mods, key


# --- Each system ----------------------------------------------------------


def _run(argv: list[str], env: Optional[dict] = None) -> str:
    try:
        # Fixed programs, never through a shell; what the model gave only
        # ever goes in as an argument.
        done = subprocess.run(  # nosec B603
            argv, capture_output=True, text=True, timeout=TIMEOUT,
            check=False, env=env,
        )
    except FileNotFoundError:
        raise UseError(f"{argv[0]} is not on this computer.") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise UseError(f"{argv[0]} could not run: {exc}") from None
    if done.returncode:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        raise UseError(f"{Path(argv[0]).name} failed"
                       + (f": {said[-1]}" if said else "."))
    return (done.stdout or "").strip()


def _send(kind: str, command: dict) -> dict:
    """Do COMMAND with the mouse or keyboard."""

    if kind == "mac":
        return _mac(command)
    if kind == "windows":
        return _win(command)
    return _linux(command)


def pointer() -> tuple[float, float]:
    """Where the pointer is, in the screen's own points."""

    kind = system()
    if kind == "mac":
        found = _mac({"do": "where"})
        return float(found["x"]), float(found["y"])
    if kind == "windows":
        return _win({"do": "where"})["at"]
    said = _run(["xdotool", "getmouselocation", "--shell"])
    values = dict(line.split("=", 1) for line in said.splitlines()
                  if "=" in line)
    return float(values.get("X", 0)), float(values.get("Y", 0))


def screenshot() -> tuple[bytes, tuple[int, int]]:
    """The main screen, at most MOST_WIDTH wide, as PNG bytes and its
    size. Remembers how its pixels map to the screen's points."""

    with tempfile.TemporaryDirectory(prefix="flash-screen-") as folder:
        path = Path(folder) / "screen.png"
        kind = system()
        if kind == "mac":
            screen, size = _mac_shot(path)
        elif kind == "windows":
            screen, size = _win_shot(path)
        else:
            screen, size = _linux_shot(path)
        try:
            data = path.read_bytes()
        except OSError:
            raise UseError("The screenshot could not be taken.") from None
    if not data:
        raise UseError("The screenshot came out empty.")
    _shot.update(scale=screen[0] / size[0] if size[0] else 1.0, size=size)
    return data, size


def _fit(width: float, height: float) -> tuple[int, int]:
    wide = min(MOST_WIDTH, round(width))
    return wide, round(height * wide / width) if width else 0


def _mac(command: dict) -> dict:
    said = _run([sys.executable, "-c", _MAC_INPUT, json.dumps(command)])
    if said == "not allowed":
        raise UseError(
            "macOS has to let the app Flash runs in (Terminal, iTerm, VS "
            "Code and the like) control the computer first. It just "
            "asked: turn that app on in System Settings > Privacy & "
            "Security > Accessibility, then ask again."
        )
    try:
        return json.loads(said or "{}")
    except ValueError:
        raise UseError(f"macOS said: {said[:200]}") from None


def _mac_shot(path: Path) -> tuple[tuple[float, float], tuple[int, int]]:
    screen = _mac({"do": "size"})
    width, height = float(screen["w"]), float(screen["h"])
    _run(["screencapture", "-x", "-m", "-t", "png", str(path)])
    if not path.exists():
        raise UseError(
            "macOS would not take the screenshot: turn on the app Flash "
            "runs in under System Settings > Privacy & Security > Screen "
            "Recording, then ask again."
        )
    size = _fit(width, height)
    _run(["sips", "--resampleWidth", str(size[0]), str(path)])
    return (width, height), size


# Core Graphics, in a Python of its own: one step with the mouse or the
# keyboard, given as JSON. Prints JSON, or "not allowed" when macOS has
# not let the app Flash runs in post events (Accessibility).
_MAC_INPUT = """
import ctypes, json, sys, time
from ctypes import (c_bool, c_double, c_int32, c_int64, c_uint16, c_uint32,
                    c_uint64, c_ulong, c_void_p)
cg = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/"
    "ApplicationServices")


class Point(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class Size(ctypes.Structure):
    _fields_ = [("w", c_double), ("h", c_double)]


class Rect(ctypes.Structure):
    _fields_ = [("origin", Point), ("size", Size)]


cg.CGPreflightPostEventAccess.restype = c_bool
cg.CGRequestPostEventAccess.restype = c_bool
cg.CGMainDisplayID.restype = c_uint32
cg.CGDisplayBounds.restype = Rect
cg.CGDisplayBounds.argtypes = [c_uint32]
cg.CGEventCreate.restype = c_void_p
cg.CGEventCreate.argtypes = [c_void_p]
cg.CGEventGetLocation.restype = Point
cg.CGEventGetLocation.argtypes = [c_void_p]
cg.CGEventCreateMouseEvent.restype = c_void_p
cg.CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, Point, c_uint32]
cg.CGEventCreateKeyboardEvent.restype = c_void_p
cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
cg.CGEventCreateScrollWheelEvent2.restype = c_void_p
cg.CGEventCreateScrollWheelEvent2.argtypes = [
    c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32]
cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
cg.CGEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
cg.CGEventKeyboardSetUnicodeString.argtypes = [c_void_p, c_ulong, c_void_p]
cg.CGEventPost.argtypes = [c_uint32, c_void_p]

KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "return": 36, "l": 37, "j": 38, "'": 39, "k": 40,
    ";": 41, "\\\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "tab": 48, "space": 49, "`": 50, "backspace": 51, "escape": 53,
    "home": 115, "pageup": 116, "delete": 117, "end": 119, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120,
    "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
}
FLAGS = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000,
         "cmd": 0x100000, "fn": 0x800000}
BUTTONS = {"left": (0, 1, 2, 6), "right": (1, 3, 4, 7),
           "middle": (2, 25, 26, 27)}


def post(event):
    cg.CGEventPost(0, event)
    time.sleep(0.01)


def mouse(kind, x, y, button=0, clicks=0):
    event = cg.CGEventCreateMouseEvent(None, kind, Point(x, y), button)
    if clicks:
        cg.CGEventSetIntegerValueField(event, 1, clicks)
    post(event)


def key(code, down, flags=0):
    event = cg.CGEventCreateKeyboardEvent(None, code, down)
    cg.CGEventSetFlags(event, flags)
    post(event)


def where():
    at = cg.CGEventGetLocation(cg.CGEventCreate(None))
    return {"x": at.x, "y": at.y}


def text(said):
    for line, part in enumerate(said.split("\\n")):
        if line:
            key(36, True)
            key(36, False)
        for piece, chunk in enumerate(part.split("\\t")):
            if piece:
                key(48, True)
                key(48, False)
            units = chunk.encode("utf-16-le")
            for at in range(0, len(units), 40):
                block = units[at:at + 40]
                buffer = ctypes.create_string_buffer(block)
                for down in (True, False):
                    event = cg.CGEventCreateKeyboardEvent(None, 0, down)
                    cg.CGEventKeyboardSetUnicodeString(
                        event, len(block) // 2, buffer)
                    post(event)


command = json.loads(sys.argv[1])
do = command["do"]
if do == "size":
    bounds = cg.CGDisplayBounds(cg.CGMainDisplayID())
    print(json.dumps({"w": bounds.size.w, "h": bounds.size.h}))
    sys.exit()
if do == "where":
    print(json.dumps(where()))
    sys.exit()
if not cg.CGPreflightPostEventAccess():
    cg.CGRequestPostEventAccess()
    print("not allowed")
    sys.exit()
x, y = command.get("x", 0), command.get("y", 0)
if do == "move":
    mouse(5, x, y)
elif do == "click":
    button, down, up, _ = BUTTONS[command.get("button", "left")]
    mouse(5, x, y)
    for click in range(1, int(command.get("count", 1)) + 1):
        mouse(down, x, y, button, click)
        mouse(up, x, y, button, click)
elif do == "drag":
    mouse(5, x, y)
    mouse(1, x, y, 0, 1)
    steps = 20
    for step in range(1, steps + 1):
        mouse(6, x + (command["to_x"] - x) * step / steps,
              y + (command["to_y"] - y) * step / steps, 0)
        time.sleep(0.01)
    mouse(2, command["to_x"], command["to_y"], 0, 1)
elif do == "scroll":
    mouse(5, x, y)
    amount = int(command.get("amount", 3))
    up, side = {"up": (amount, 0), "down": (-amount, 0),
                "left": (0, amount), "right": (0, -amount)}[
        command["direction"]]
    post(cg.CGEventCreateScrollWheelEvent2(None, 1, 2, up, side, 0))
elif do == "key":
    flags = 0
    for name in command.get("mods", []):
        flags |= FLAGS[name]
    key(KEYS[command["key"]], True, flags)
    key(KEYS[command["key"]], False, flags)
elif do == "type":
    text(command["text"])
print(json.dumps(where()))
"""

# Reads the keyboard's own state until the stop keys are down (Escape
# with Control and Option), then prints "stop". Gone when Flash is.
_MAC_WATCH = """
import ctypes, os, sys, time
from ctypes import c_bool, c_int32, c_uint16, c_uint64
cg = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework/"
    "ApplicationServices")
cg.CGEventSourceKeyState.restype = c_bool
cg.CGEventSourceKeyState.argtypes = [c_int32, c_uint16]
cg.CGEventSourceFlagsState.restype = c_uint64
cg.CGEventSourceFlagsState.argtypes = [c_int32]
parent = int(sys.argv[1])
while os.getppid() == parent:
    flags = cg.CGEventSourceFlagsState(1)
    if cg.CGEventSourceKeyState(1, 53) and flags & 0x40000 and \\
            flags & 0x80000:
        print("stop", flush=True)
        break
    time.sleep(0.05)
"""


# Windows: user32 in this process, and PowerShell for the screenshot.
_WIN_KEYS = {
    "return": 0x0D, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, ";": 0xBA, "=": 0xBB, ",": 0xBC,
    "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC,
    "]": 0xDD, "'": 0xDE, **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
_WIN_MODS = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12, "cmd": 0x5B}
_WIN_BUTTONS = {"left": (0x2, 0x4), "right": (0x8, 0x10),
                "middle": (0x20, 0x40)}
_WIN_SHOT = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Namespace Flash -Name Dpi -MemberDefinition `
  '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[Flash.Dpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$shot = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($shot)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$w = [Math]::Min($b.Width, [int]$env:FLASH_WIDTH)
$h = [int]($b.Height * $w / $b.Width)
$small = New-Object System.Drawing.Bitmap $shot, $w, $h
$small.Save($env:FLASH_SHOT, [System.Drawing.Imaging.ImageFormat]::Png)
"$($b.Width) $($b.Height) $w $h"
"""


def _user32():
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.SetProcessDPIAware()
    return user32


def _win_stop_down() -> bool:
    user32 = _user32()
    return all(user32.GetAsyncKeyState(key) & 0x8000
               for key in (0x1B, 0x11, 0x12))


def _win_shot(path: Path) -> tuple[tuple[float, float], tuple[int, int]]:
    said = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 _WIN_SHOT],
                env={**os.environ, "FLASH_SHOT": str(path),
                     "FLASH_WIDTH": str(MOST_WIDTH)})
    try:
        width, height, wide, high = (int(n) for n in said.split()[-4:])
    except ValueError:
        raise UseError("Windows would not take the screenshot.") from None
    return (width, height), (wide, high)


def _win(command: dict) -> dict:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    do = command["do"]
    if do == "where":
        at = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(at))
        return {"at": (float(at.x), float(at.y))}
    x, y = int(command.get("x", 0)), int(command.get("y", 0))
    if do in ("move", "click", "drag", "scroll"):
        user32.SetCursorPos(x, y)
    if do == "click":
        down, up = _WIN_BUTTONS[command.get("button", "left")]
        for _ in range(int(command.get("count", 1))):
            user32.mouse_event(down, 0, 0, 0, 0)
            user32.mouse_event(up, 0, 0, 0, 0)
    elif do == "drag":
        user32.mouse_event(0x2, 0, 0, 0, 0)
        steps = 20
        for step in range(1, steps + 1):
            user32.SetCursorPos(
                round(x + (command["to_x"] - x) * step / steps),
                round(y + (command["to_y"] - y) * step / steps))
            time.sleep(0.01)
        user32.mouse_event(0x4, 0, 0, 0, 0)
    elif do == "scroll":
        amount = int(command.get("amount", 3)) * 120
        direction = command["direction"]
        flag = 0x800 if direction in ("up", "down") else 0x1000
        sign = 1 if direction in ("up", "right") else -1
        user32.mouse_event(flag, 0, 0, (sign * amount) & 0xFFFFFFFF, 0)
    elif do == "key":
        held = [_WIN_MODS[m] for m in command.get("mods", []) if m != "fn"]
        key = command["key"]
        code = _WIN_KEYS.get(key) or ord(key.upper())
        for vk in (*held, code):
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed((*held, code)):
            user32.keybd_event(vk, 0, 2, 0)
    elif do == "type":
        _win_type(user32, command["text"])
    return {}


def _win_type(user32, text: str) -> None:
    import ctypes
    from ctypes import wintypes

    class KeyInput(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class Either(ctypes.Union):
        _fields_ = [("ki", KeyInput), ("mi", MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", Either)]

    def send(vk: int, scan: int, flags: int) -> None:
        given = Input(type=1, u=Either(ki=KeyInput(vk, scan, flags, 0, 0)))
        user32.SendInput(1, ctypes.byref(given), ctypes.sizeof(Input))

    for char in text:
        if char in "\n\t":
            vk = 0x0D if char == "\n" else 0x09
            send(vk, 0, 0)
            send(vk, 0, 2)
            continue
        units = char.encode("utf-16-le")
        for at in range(0, len(units), 2):
            unit = int.from_bytes(units[at:at + 2], "little")
            send(0, unit, 0x4)
            send(0, unit, 0x4 | 0x2)


# Linux: xdotool, and the first screenshot program there is.
_LINUX_SHOOTERS = (
    ("gnome-screenshot", ["gnome-screenshot", "-f", "{path}"]),
    ("grim", ["grim", "{path}"]),
    ("scrot", ["scrot", "-o", "{path}"]),
    ("import", ["import", "-window", "root", "{path}"]),
)
_LINUX_KEYS = {
    "return": "Return", "escape": "Escape", "tab": "Tab", "space": "space",
    "backspace": "BackSpace", "delete": "Delete", "home": "Home",
    "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
    "left": "Left", "up": "Up", "right": "Right", "down": "Down",
    "-": "minus", "=": "equal", "[": "bracketleft", "]": "bracketright",
    ";": "semicolon", "'": "apostrophe", ",": "comma", ".": "period",
    "/": "slash", "\\": "backslash", "`": "grave",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}
_LINUX_MODS = {"cmd": "super", "ctrl": "ctrl", "alt": "alt",
               "shift": "shift", "fn": ""}


def _linux_shooter() -> Optional[list[str]]:
    for program, argv in _LINUX_SHOOTERS:
        if shutil.which(program):
            return argv
    return None


def _linux_shot(path: Path) -> tuple[tuple[float, float], tuple[int, int]]:
    argv = _linux_shooter()
    if argv is None:
        raise UseError(missing())
    width, height = (float(n) for n in
                     _run(["xdotool", "getdisplaygeometry"]).split()[:2])
    _run([part.format(path=path) for part in argv])
    size = (round(width), round(height))
    if width > MOST_WIDTH and shutil.which("convert"):
        size = _fit(width, height)
        _run(["convert", str(path), "-resize", f"{size[0]}x{size[1]}!",
              str(path)])
    return (width, height), size


def _linux(command: dict) -> dict:
    do = command["do"]
    x, y = (str(round(command.get(k, 0))) for k in ("x", "y"))
    if do == "move":
        _run(["xdotool", "mousemove", x, y])
    elif do == "click":
        button = {"left": "1", "middle": "2", "right": "3"}[
            command.get("button", "left")]
        _run(["xdotool", "mousemove", x, y, "click", "--repeat",
              str(command.get("count", 1)), button])
    elif do == "drag":
        _run(["xdotool", "mousemove", x, y, "mousedown", "1", "mousemove",
              str(round(command["to_x"])), str(round(command["to_y"])),
              "mouseup", "1"])
    elif do == "scroll":
        button = {"up": "4", "down": "5", "left": "6", "right": "7"}[
            command["direction"]]
        _run(["xdotool", "mousemove", x, y, "click", "--repeat",
              str(command.get("amount", 3)), button])
    elif do == "key":
        mods = [_LINUX_MODS[m] for m in command.get("mods", [])
                if _LINUX_MODS[m]]
        key = _LINUX_KEYS.get(command["key"], command["key"])
        _run(["xdotool", "key", "--", "+".join([*mods, key])])
    elif do == "type":
        _run(["xdotool", "type", "--delay", "8", "--", command["text"]])
    return {}


def _linux_stop_down() -> Optional[Callable[[], bool]]:
    """Whether the stop keys are down, from X's own keymap; None where
    there is no X to ask."""

    import ctypes
    import ctypes.util

    found = ctypes.util.find_library("X11")
    if not found or not os.environ.get("DISPLAY"):
        return None
    x11 = ctypes.CDLL(found)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return None
    # Escape, Control and Alt, either side.
    escape = x11.XKeysymToKeycode(display, 0xFF1B)
    controls = [x11.XKeysymToKeycode(display, s) for s in (0xFFE3, 0xFFE4)]
    alts = [x11.XKeysymToKeycode(display, s) for s in (0xFFE9, 0xFFEA)]
    keymap = ctypes.create_string_buffer(32)

    def held(code: int) -> bool:
        return bool(keymap.raw[code // 8] & (1 << (code % 8)))

    def down() -> bool:
        x11.XQueryKeymap(display, keymap)
        return held(escape) and any(map(held, controls)) and \
            any(map(held, alts))

    return down
