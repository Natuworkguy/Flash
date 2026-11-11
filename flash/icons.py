"""Flash Icons: the icons Flash puts in the websites and apps it builds.

One set, drawn for Flash, in one style: 24 by 24, rounded strokes in the
text's colour, nothing filled. Each icon has a small motion of its own
that plays when it is pointed at, focused, or (if the user wants) when it
appears or all the time: an arrow nudges the way it points, a bell rings,
a heart beats, a gear turns, a check draws itself. They are inline SVG
with one small stylesheet, so they need no font, no CDN and no build step,
and take the colour of the text around them.

How they look and move is the user's to change, by asking Flash: a
thicker or thinner stroke, square corners, a soft fill behind the lines,
a size, a colour, livelier or calmer motion or none, motion on hover, on
appearing, or always, and how fast. That style is kept in
~/.flash/icon-style.json and every icon Flash makes from then on follows
it.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Optional

from .paths import FLASH_DIR

# ----- the icons ---------------------------------------------------------
# Each is (category, motion, [path d, ...]). Every shape is a path, so the
# draw-on motion works the same on all of them.


def _circle(cx: float, cy: float, r: float) -> str:
    return (f"M{cx - r:g} {cy:g}a{r:g} {r:g} 0 1 0 {2 * r:g} 0"
            f"a{r:g} {r:g} 0 1 0 {-2 * r:g} 0")


def _rect(x: float, y: float, w: float, h: float, rx: float = 0) -> str:
    if not rx:
        return f"M{x:g} {y:g}h{w:g}v{h:g}h{-w:g}z"
    return (f"M{x + rx:g} {y:g}h{w - 2 * rx:g}a{rx:g} {rx:g} 0 0 1 {rx:g} {rx:g}"
            f"v{h - 2 * rx:g}a{rx:g} {rx:g} 0 0 1 {-rx:g} {rx:g}h{-(w - 2 * rx):g}"
            f"a{rx:g} {rx:g} 0 0 1 {-rx:g} {-rx:g}v{-(h - 2 * rx):g}"
            f"a{rx:g} {rx:g} 0 0 1 {rx:g} {-rx:g}z")


def _rays(cx: float, cy: float, r0: float, r1: float, n: int, turn: float = 0) -> list[str]:
    import math

    out = []
    for i in range(n):
        a = math.pi * 2 * i / n + turn
        out.append(f"M{cx + math.cos(a) * r0:.2f} {cy + math.sin(a) * r0:.2f}"
                   f"L{cx + math.cos(a) * r1:.2f} {cy + math.sin(a) * r1:.2f}")
    return out


DOT = "v.01"   # a round dot: a zero-length line with round caps

ICONS: dict[str, tuple[str, str, list[str]]] = {
    # navigation
    "home": ("navigation", "pop", ["M3 10.5 12 3l9 7.5", "M5 9.5V21h14V9.5", "M10 21v-6h4v6"]),
    "menu": ("navigation", "shift", ["M4 6h16", "M4 12h16", "M4 18h16"]),
    "close": ("navigation", "turn", ["M6 6l12 12", "M18 6 6 18"]),
    "arrow-right": ("navigation", "nudge-right", ["M4 12h16", "M13 5l7 7-7 7"]),
    "arrow-left": ("navigation", "nudge-left", ["M20 12H4", "M11 5l-7 7 7 7"]),
    "arrow-up": ("navigation", "nudge-up", ["M12 20V4", "M5 11l7-7 7 7"]),
    "arrow-down": ("navigation", "nudge-down", ["M12 4v16", "M5 13l7 7 7-7"]),
    "chevron-right": ("navigation", "nudge-right", ["M9 5l7 7-7 7"]),
    "chevron-left": ("navigation", "nudge-left", ["M15 5l-7 7 7 7"]),
    "chevron-up": ("navigation", "nudge-up", ["M5 15l7-7 7 7"]),
    "chevron-down": ("navigation", "nudge-down", ["M5 9l7 7 7-7"]),
    "external": ("navigation", "nudge-out", ["M14 4h6v6", "M20 4l-9 9", "M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"]),
    "login": ("navigation", "nudge-right", ["M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4", "M10 8l4 4-4 4", "M14 12H4"]),
    "logout": ("navigation", "nudge-left", ["M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4", "M15 8l-4 4 4 4", "M11 12h9"]),
    "grid": ("navigation", "pop", [_rect(4, 4, 6.5, 6.5, 1.5), _rect(13.5, 4, 6.5, 6.5, 1.5), _rect(4, 13.5, 6.5, 6.5, 1.5), _rect(13.5, 13.5, 6.5, 6.5, 1.5)]),
    "list": ("navigation", "shift", ["M9 6h11", "M9 12h11", "M9 18h11", "M4.5 6" + DOT, "M4.5 12" + DOT, "M4.5 18" + DOT]),
    # actions
    "search": ("actions", "tilt", [_circle(11, 11, 7), "M16.2 16.2 21 21"]),
    "check": ("actions", "draw", ["M4 12.5 9.5 18 20 6.5"]),
    "plus": ("actions", "turn", ["M12 5v14", "M5 12h14"]),
    "minus": ("actions", "pop", ["M5 12h14"]),
    "edit": ("actions", "wiggle", ["M15 4.5l4.5 4.5L9 19.5H4.5V15z", "M13 6.5l4.5 4.5"]),
    "trash": ("actions", "wiggle", ["M4 7h16", "M9 7V4.5h6V7", "M6.5 7l1 13h9l1-13", "M10 11v5", "M14 11v5"]),
    "copy": ("actions", "pop", [_rect(8, 8, 12, 12, 2), "M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"]),
    "download": ("actions", "nudge-down", ["M12 4v11", "M7 10l5 5 5-5", "M4 19.5h16"]),
    "upload": ("actions", "nudge-up", ["M12 15V4", "M7 9l5-5 5 5", "M4 19.5h16"]),
    "share": ("actions", "pop", [_circle(18, 5.5, 2.5), _circle(6, 12, 2.5), _circle(18, 18.5, 2.5), "M8.2 10.8l7.6-4.1", "M8.2 13.2l7.6 4.1"]),
    "link": ("actions", "tilt", ["M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1", "M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"]),
    "refresh": ("actions", "spin", ["M20 12a8 8 0 1 1-2.35-5.65", "M20 4v4.5h-4.5"]),
    "filter": ("actions", "pop", ["M4 5h16l-6 7.5V19l-4 1.5v-8z"]),
    "send": ("actions", "fly", ["M21 3 10 14", "M21 3l-6.5 18-4.5-7-7-4.5z"]),
    "settings": ("actions", "slide", ["M4 7h16", "M4 17h16", _circle(9, 7, 2.2), _circle(15, 17, 2.2)]),
    "gear": ("actions", "spin", [_circle(12, 12, 6.5), _circle(12, 12, 2.5)] + _rays(12, 12, 6.5, 9, 8)),
    "lock": ("actions", "shake", [_rect(5, 11, 14, 10, 2), "M8 11V7.5a4 4 0 0 1 8 0V11"]),
    "unlock": ("actions", "pop", [_rect(5, 11, 14, 10, 2), "M8 11V7.5a4 4 0 0 1 7.6-1.8"]),
    "eye": ("actions", "blink", ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z", _circle(12, 12, 3)]),
    "bookmark": ("actions", "pop", ["M6.5 3.5h11v17l-5.5-4-5.5 4z"]),
    "tag": ("actions", "tilt", ["M3.5 12.5V4.5a1 1 0 0 1 1-1h8L21 12l-8.5 8.5z", _circle(8, 8, 1.5)]),
    # communication
    "mail": ("communication", "pop", [_rect(3, 5, 18, 14, 2), "M3.5 6.5 12 13l8.5-6.5"]),
    "message": ("communication", "bounce", ["M4 5.5h16v11H9.5L5 20v-3.5H4z"]),
    "bell": ("communication", "ring", ["M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z", "M10 20.5a2 2 0 0 0 4 0"]),
    "phone": ("communication", "ring", ["M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"]),
    "user": ("communication", "pop", [_circle(12, 8, 4), "M4 21a8 8 0 0 1 16 0"]),
    "users": ("communication", "pop", [_circle(9, 8, 3.5), "M2.5 20a6.5 6.5 0 0 1 13 0", "M16 4.5a3.5 3.5 0 0 1 0 7", "M18 14.2a6.5 6.5 0 0 1 3.5 5.8"]),
    "globe": ("communication", "tilt", [_circle(12, 12, 8.5), "M3.5 12h17", "M12 3.5c2.5 2.6 3.7 5.4 3.7 8.5s-1.2 5.9-3.7 8.5c-2.5-2.6-3.7-5.4-3.7-8.5S9.5 6.1 12 3.5z"]),
    "info": ("communication", "pop", [_circle(12, 12, 8.5), "M12 11v5.5", "M12 7.8" + DOT]),
    "help": ("communication", "tilt", [_circle(12, 12, 8.5), "M9.5 9.5a2.6 2.6 0 0 1 5 1c0 1.8-2.5 2.2-2.5 3.8", "M12 17.2" + DOT]),
    "alert": ("communication", "shake", ["M12 3.5 21.5 20h-19z", "M12 10v4", "M12 17" + DOT]),
    # media
    "play": ("media", "nudge-right", ["M8 5.5v13l10.5-6.5z"]),
    "pause": ("media", "pop", ["M9 5.5v13", "M15 5.5v13"]),
    "music": ("media", "bounce", ["M9 18V5.5l11-2V16", _circle(6.5, 18, 2.5), _circle(17.5, 16, 2.5)]),
    "camera": ("media", "pop", ["M3.5 8.5a2 2 0 0 1 2-2H8L9.5 4h5L16 6.5h2.5a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z", _circle(12, 13, 3.5)]),
    "image": ("media", "pop", [_rect(3.5, 4.5, 17, 15, 2), _circle(9, 9.5, 1.5), "M4 18l5.5-5.5L13 16l2.5-2.5L20 18"]),
    "video": ("media", "nudge-right", [_rect(3, 6, 13, 12, 2), "M16 10.5l5-3v9l-5-3"]),
    "mic": ("media", "bounce", [_rect(9, 3, 6, 11, 3), "M5.5 11a6.5 6.5 0 0 0 13 0", "M12 17.5V21"]),
    # commerce
    "cart": ("commerce", "nudge-right", ["M3 4h2.5l2.2 11h10.6l2-8H6.5", _circle(9.5, 19.5, 1.3), _circle(17, 19.5, 1.3)]),
    "bag": ("commerce", "bounce", ["M5 8h14l-1 12.5H6z", "M9 8V6.5a3 3 0 0 1 6 0V8"]),
    "card": ("commerce", "tilt", [_rect(3, 5.5, 18, 13, 2), "M3 10h18", "M6.5 15H10"]),
    "gift": ("commerce", "bounce", [_rect(4, 9, 16, 4, 1), "M5.5 13v7.5h13V13", "M12 9v11.5", "M12 9C10 5 6.5 5.5 7.5 7.5 8.3 9 12 9 12 9z", "M12 9c2-4 5.5-3.5 4.5-1.5C15.7 9 12 9 12 9z"]),
    "chart": ("commerce", "draw", ["M4 4v16h16", "M7.5 15l3.5-4 3 2.5L19 8"]),
    # time and places
    "calendar": ("time", "pop", [_rect(3.5, 5, 17, 15.5, 2), "M3.5 10h17", "M8 3v4", "M16 3v4"]),
    "clock": ("time", "tick", [_circle(12, 12, 8.5), "M12 7.5V12l3 2"]),
    "map-pin": ("time", "bounce", ["M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21z", _circle(12, 10, 2.5)]),
    "compass": ("time", "spin", [_circle(12, 12, 8.5), "M15.5 8.5l-2 5-5 2 2-5z"]),
    # things
    "file": ("files", "pop", ["M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z", "M14 3.5v5h5"]),
    "folder": ("files", "pop", ["M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"]),
    "code": ("files", "pop", ["M8 7l-5 5 5 5", "M16 7l5 5-5 5", "M13.5 4.5l-3 15"]),
    "terminal": ("files", "cursor", [_rect(3, 4.5, 18, 15, 2), "M7 9.5l3 2.5-3 2.5", "M12.5 14.5H17"]),
    "shield": ("files", "pop", ["M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z", "M8.5 12l2.5 2.5 4.5-4.5"]),
    # mood and nature
    "heart": ("mood", "beat", ["M12 20s-7-4.4-9-9.2C1.6 7.4 3.8 4 7.2 4c2 0 3.6 1.1 4.8 2.9C13.2 5.1 14.8 4 16.8 4c3.4 0 5.6 3.4 4.2 6.8C19 15.6 12 20 12 20z"]),
    "star": ("mood", "twirl", ["M12 3l2.8 5.9 6.2.7-4.6 4.3 1.2 6.2L12 17l-5.6 3.1 1.2-6.2L3 9.6l6.2-.7z"]),
    "sparkle": ("mood", "twinkle", ["M12 3l1.8 5.4 5.2 1.8-5.2 1.8L12 17.4l-1.8-5.4L5 10.2l5.2-1.8z", "M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"]),
    "bolt": ("mood", "flash", ["M13 2.5 5 13.5h6l-1 8 8-11h-6z"]),
    "rocket": ("mood", "float", ["M12 15.5 8.5 12C10 6.5 13.5 3.5 20.5 3.5c0 7-3 10.5-8.5 12z", "M8.5 12H5l2.5-3.5h4", "M12 15.5V19l3.5-2.5v-4", "M6.5 17.5c-1 1-1.5 3-1.5 3s2-.5 3-1.5"]),
    "sun": ("mood", "spin", [_circle(12, 12, 4)] + _rays(12, 12, 7, 9.5, 8)),
    "moon": ("mood", "tilt", ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"]),
    "cloud": ("mood", "float", ["M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.1 9.6 4.2 4.2 0 0 0 7 18z"]),
    "leaf": ("mood", "sway", ["M5 19C5 10 10 5 20 4c-1 10-6 15-15 15z", "M5 19l7-7"]),
    "flame": ("mood", "flicker", ["M12 21a6 6 0 0 0 6-6c0-4-3-6-4-9-1 2-2.5 3-3.5 4C9.3 8.8 9 7.5 9 6.5 7.2 8 6 10.8 6 15a6 6 0 0 0 6 6z"]),
    "coffee": ("mood", "steam", ["M5 9h11v5.5a5.5 5.5 0 0 1-5.5 5.5A5.5 5.5 0 0 1 5 14.5z", "M16 11h1.5a2.5 2.5 0 0 1 0 5H16", "M8.5 3.5c-.8 1 .8 2 0 3", "M12 3.5c-.8 1 .8 2 0 3"]),
}

# what people call them, so "x", "trash-can" or "cog" find the right one
ALIASES = {"x": "close", "cross": "close", "hamburger": "menu", "magnifier": "search", "tick": "check",
           "add": "plus", "remove": "minus", "pencil": "edit", "delete": "trash", "bin": "trash",
           "trash-can": "trash", "duplicate": "copy", "reload": "refresh", "sync": "refresh",
           "cog": "gear", "sliders": "settings", "padlock": "lock", "view": "eye", "save": "bookmark",
           "email": "mail", "envelope": "mail", "chat": "message", "comment": "message",
           "notification": "bell", "notifications": "bell", "person": "user", "account": "user",
           "profile": "user", "team": "users", "people": "users", "world": "globe", "language": "globe",
           "warning": "alert", "error": "alert", "question": "help", "photo": "image", "picture": "image",
           "microphone": "mic", "basket": "cart", "shopping-cart": "cart", "shopping-bag": "bag",
           "credit-card": "card", "payment": "card", "present": "gift", "analytics": "chart",
           "stats": "chart", "date": "calendar", "time": "clock", "location": "map-pin", "pin": "map-pin",
           "place": "map-pin", "document": "file", "directory": "folder", "security": "shield",
           "like": "heart", "favourite": "star", "favorite": "star", "magic": "sparkle", "ai": "sparkle",
           "lightning": "bolt", "zap": "bolt", "fast": "bolt", "launch": "rocket", "light": "sun",
           "dark": "moon", "weather": "cloud", "eco": "leaf", "nature": "leaf", "fire": "flame",
           "hot": "flame", "cafe": "coffee", "explore": "compass", "sign-in": "login", "sign-out": "logout",
           "open": "external", "new-tab": "external", "dashboard": "grid", "apps": "grid"}


def find(name: str) -> Optional[str]:
    """The icon NAME means, by its own name or a common one, or None."""

    key = re.sub(r"[\s_]+", "-", str(name or "").strip().lower())
    if key in ICONS:
        return key
    key = ALIASES.get(key, key)
    return key if key in ICONS else None


# ----- the style: the user's to change ----------------------------------

DEFAULT_STYLE = {
    "stroke": 1.75,          # line width at 24 px
    "corners": "round",      # round | square
    "variant": "line",       # line | duotone (a soft fill behind the lines)
    "size": "1.5em",         # any CSS length; em follows the text
    "color": "currentColor",
    "motion": "lively",      # lively | subtle | none
    "trigger": "hover",      # hover | load | loop
    "speed": 600,            # ms for one motion
}
CHOICES = {"corners": ("round", "square"), "variant": ("line", "duotone"),
           "motion": ("lively", "subtle", "none"), "trigger": ("hover", "load", "loop")}


class IconError(ValueError):
    """An icon or a style Flash could not use."""


def _style_path() -> Path:
    # looked up here, not at import, so a test can point FLASH_DIR elsewhere
    return FLASH_DIR / "icon-style.json"


def style() -> dict:
    """The icon style as the user set it, over the defaults."""

    try:
        saved = json.loads(_style_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULT_STYLE, **{k: v for k, v in (saved if isinstance(saved, dict) else {}).items() if k in DEFAULT_STYLE}}


_CSS_VALUE = re.compile(r"^[#\w().,%\s-]{1,40}$")


def clean(changes: dict) -> dict:
    """CHANGES checked: each a setting there is, with a value it can take."""

    out = {}
    for key, value in (changes or {}).items():
        if value is None or value == "":
            continue
        if key not in DEFAULT_STYLE:
            raise IconError(f"There is no icon setting called {key}. There are: {', '.join(DEFAULT_STYLE)}.")
        if key in CHOICES:
            value = str(value).strip().lower()
            if value not in CHOICES[key]:
                raise IconError(f"{key} is one of {', '.join(CHOICES[key])}.")
        elif key == "stroke":
            try:
                value = round(float(value), 2)
            except (TypeError, ValueError):
                raise IconError("stroke is a number, the line width at 24 px (1 to 3 suits most).") from None
            if not .5 <= value <= 4:
                raise IconError("stroke is between 0.5 and 4.")
        elif key == "speed":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                raise IconError("speed is the length of one motion in milliseconds.") from None
            if not 100 <= value <= 5000:
                raise IconError("speed is between 100 and 5000 ms.")
        else:
            value = str(value).strip()
            if not _CSS_VALUE.match(value):
                raise IconError(f"{key} has to be a plain CSS value, like 24px, 1.5em, #0a84ff or currentColor.")
        out[key] = value
    return out


def set_style(changes: dict) -> dict:
    """Change the user's icon style; RESET-able by passing reset."""

    current = {k: v for k, v in style().items() if DEFAULT_STYLE.get(k) != v}
    current.update(clean(changes))
    path = _style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return style()


def reset_style() -> dict:
    try:
        _style_path().unlink()
    except OSError:
        pass
    return style()


def describe(s: Optional[dict] = None) -> str:
    s = s or style()
    move = "no motion" if s["motion"] == "none" else (
        f"{s['motion']} motion {'when pointed at or focused' if s['trigger'] == 'hover' else 'as they appear' if s['trigger'] == 'load' else 'all the time'}, "
        f"{s['speed']} ms")
    return (f"{s['stroke']} px {s['corners']} lines{', with a soft fill' if s['variant'] == 'duotone' else ''}, "
            f"{s['size']}, colour {s['color']}, {move}")


# ----- what Flash puts in a page --------------------------------------------

def svg(name: str, label: str = "") -> str:
    """One icon, inline. LABEL names it for screen readers; without one it is hidden from them, as a decoration."""

    key = find(name)
    if key is None:
        raise IconError(f"There is no icon called {name}. flash_icons with action list shows them all.")
    _cat, motion, paths = ICONS[key]
    a11y = f'role="img" aria-label="{html.escape(label, quote=True)}"' if label else 'aria-hidden="true"'
    body = "".join(f'<path d="{d}" pathLength="1"/>' for d in paths)
    return (f'<svg class="fi fi-{key}" data-motion="{motion}" viewBox="0 0 24 24" {a11y} focusable="false">'
            f"{body}</svg>")


# how each motion moves, at full strength (subtle halves it)
_MOTIONS = {
    "pop": "0%{transform:scale(1)}40%{transform:scale(calc(1 + .18 * var(--fi-amp)))}100%{transform:scale(1)}",
    "shift": "0%,100%{transform:translateX(0)}50%{transform:translateX(calc(2px * var(--fi-amp)))}",
    "turn": "0%{transform:rotate(0)}100%{transform:rotate(calc(90deg * var(--fi-amp)))}",
    "nudge-right": "0%,100%{transform:translateX(0)}45%{transform:translateX(calc(3px * var(--fi-amp)))}",
    "nudge-left": "0%,100%{transform:translateX(0)}45%{transform:translateX(calc(-3px * var(--fi-amp)))}",
    "nudge-up": "0%,100%{transform:translateY(0)}45%{transform:translateY(calc(-3px * var(--fi-amp)))}",
    "nudge-down": "0%,100%{transform:translateY(0)}45%{transform:translateY(calc(3px * var(--fi-amp)))}",
    "nudge-out": "0%,100%{transform:translate(0,0)}45%{transform:translate(calc(2px * var(--fi-amp)),calc(-2px * var(--fi-amp)))}",
    "tilt": "0%,100%{transform:rotate(0)}35%{transform:rotate(calc(-12deg * var(--fi-amp)))}70%{transform:rotate(calc(6deg * var(--fi-amp)))}",
    "wiggle": "0%,100%{transform:rotate(0)}20%{transform:rotate(calc(-10deg * var(--fi-amp)))}40%{transform:rotate(calc(9deg * var(--fi-amp)))}60%{transform:rotate(calc(-6deg * var(--fi-amp)))}80%{transform:rotate(calc(3deg * var(--fi-amp)))}",
    "ring": "0%,100%{transform:rotate(0)}15%{transform:rotate(calc(16deg * var(--fi-amp)))}30%{transform:rotate(calc(-14deg * var(--fi-amp)))}45%{transform:rotate(calc(10deg * var(--fi-amp)))}60%{transform:rotate(calc(-6deg * var(--fi-amp)))}75%{transform:rotate(calc(3deg * var(--fi-amp)))}",
    "shake": "0%,100%{transform:translateX(0)}20%,60%{transform:translateX(calc(-2px * var(--fi-amp)))}40%,80%{transform:translateX(calc(2px * var(--fi-amp)))}",
    "spin": "0%{transform:rotate(0)}100%{transform:rotate(calc(180deg * var(--fi-amp)))}",
    "beat": "0%,100%{transform:scale(1)}20%{transform:scale(calc(1 + .22 * var(--fi-amp)))}40%{transform:scale(1)}60%{transform:scale(calc(1 + .14 * var(--fi-amp)))}",
    "twirl": "0%{transform:rotate(0) scale(1)}50%{transform:rotate(calc(36deg * var(--fi-amp))) scale(calc(1 + .15 * var(--fi-amp)))}100%{transform:rotate(calc(72deg * var(--fi-amp))) scale(1)}",
    "twinkle": "0%,100%{transform:scale(1) rotate(0);opacity:1}50%{transform:scale(calc(1 - .25 * var(--fi-amp))) rotate(calc(20deg * var(--fi-amp)));opacity:calc(1 - .3 * var(--fi-amp))}",
    "flash": "0%,100%{opacity:1;transform:translateY(0)}25%{opacity:calc(1 - .7 * var(--fi-amp))}50%{opacity:1;transform:translateY(calc(-1.5px * var(--fi-amp)))}75%{opacity:calc(1 - .5 * var(--fi-amp))}",
    "float": "0%,100%{transform:translate(0,0)}50%{transform:translate(calc(1.5px * var(--fi-amp)),calc(-3px * var(--fi-amp)))}",
    "bounce": "0%,100%{transform:translateY(0)}30%{transform:translateY(calc(-3px * var(--fi-amp)))}55%{transform:translateY(0)}70%{transform:translateY(calc(-1.2px * var(--fi-amp)))}",
    "fly": "0%{transform:translate(0,0);opacity:1}45%{transform:translate(calc(5px * var(--fi-amp)),calc(-5px * var(--fi-amp)));opacity:0}46%{transform:translate(calc(-5px * var(--fi-amp)),calc(5px * var(--fi-amp)));opacity:0}100%{transform:translate(0,0);opacity:1}",
    "slide": "0%,100%{transform:translateX(0)}50%{transform:translateX(calc(2.5px * var(--fi-amp)))}",
    "blink": "0%,100%{transform:scaleY(1)}45%{transform:scaleY(calc(1 - .9 * var(--fi-amp)))}55%{transform:scaleY(calc(1 - .9 * var(--fi-amp)))}",
    "tick": "0%{transform:rotate(0)}100%{transform:rotate(calc(360deg * var(--fi-amp)))}",
    "sway": "0%,100%{transform:rotate(0)}30%{transform:rotate(calc(-8deg * var(--fi-amp)))}70%{transform:rotate(calc(6deg * var(--fi-amp)))}",
    "flicker": "0%,100%{transform:scale(1,1)}25%{transform:scale(calc(1 - .06 * var(--fi-amp)),calc(1 + .1 * var(--fi-amp)))}50%{transform:scale(calc(1 + .04 * var(--fi-amp)),calc(1 - .05 * var(--fi-amp)))}75%{transform:scale(calc(1 - .03 * var(--fi-amp)),calc(1 + .06 * var(--fi-amp)))}",
    "steam": "0%,100%{transform:translateY(0)}50%{transform:translateY(calc(-1.5px * var(--fi-amp)))}",
    "cursor": "0%,100%{opacity:1}50%{opacity:calc(1 - .8 * var(--fi-amp))}",
}


def css(s: Optional[dict] = None) -> str:
    """The stylesheet every page with Flash Icons needs, once: the style as variables, and the motions."""

    s = s or style()
    amp = {"lively": 1, "subtle": .5, "none": 0}[s["motion"]]
    cap = "round" if s["corners"] == "round" else "square"
    join = "round" if s["corners"] == "round" else "miter"
    fill = "color-mix(in srgb, currentColor 16%, transparent)" if s["variant"] == "duotone" else "none"
    rules = [
        "/* Flash Icons: change these to restyle every icon on the page */",
        f":root{{--fi-size:{s['size']};--fi-stroke:{s['stroke']};--fi-color:{s['color']};--fi-speed:{s['speed']}ms;"
        f"--fi-amp:{amp};--fi-cap:{cap};--fi-join:{join};--fi-fill:{fill}}}",
        ".fi{width:var(--fi-size);height:var(--fi-size);flex:none;display:inline-block;vertical-align:-.2em;"
        "color:var(--fi-color);fill:var(--fi-fill);stroke:currentColor;stroke-width:var(--fi-stroke);"
        "stroke-linecap:var(--fi-cap);stroke-linejoin:var(--fi-join);overflow:visible;transform-box:fill-box;transform-origin:center}",
        ".fi path{transform-box:fill-box;transform-origin:center}",
    ]
    if amp:
        play = {
            "hover": [":is(a,button,[role=button],label,summary,.fi-host):is(:hover,:focus-visible) .fi[data-motion]",
                      ".fi[data-motion]:hover"],
            "load": [".fi[data-motion]"],
            "loop": [".fi[data-motion]"],
        }[s["trigger"]]
        times = "infinite" if s["trigger"] == "loop" else "1"
        delay = " .15s" if s["trigger"] == "load" else ""
        for motion, frames in _MOTIONS.items():
            rules.append(f"@keyframes fi-{motion}{{{frames}}}")
            sel = ",".join(f"{p}[data-motion={motion}]" for p in play)
            rules.append(f"{sel}{{animation:fi-{motion} var(--fi-speed) cubic-bezier(.3,.7,.3,1){delay} {times}}}")
        # draw: the lines draw themselves on
        rules.append("@keyframes fi-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}")
        sel = ",".join(f"{p}[data-motion=draw] path" for p in play)
        rules.append(f"{sel}{{stroke-dasharray:1;animation:fi-draw var(--fi-speed) ease-out{delay} {times}}}")
        rules.append("@media (prefers-reduced-motion:reduce){.fi,.fi path{animation:none!important}}")
    return "\n".join(rules)


def pack(names: list[str], fmt: str = "html") -> str:
    """NAMES as a page needs them: FMT "html" (the stylesheet, then each icon's SVG), "react" (a module of
    components and the stylesheet) or "css" (only the stylesheet)."""

    keys = []
    for n in names:
        k = find(n)
        if k is None:
            raise IconError(f"There is no icon called {n}. flash_icons with action list shows them all.")
        if k not in keys:
            keys.append(k)
    if fmt == "css":
        return css()
    if fmt == "react":
        comps = []
        for k in keys:
            comp = "".join(part.title() for part in k.split("-")) + "Icon"
            body = "".join(f'<path d="{d}" pathLength={{1}} />' for d in ICONS[k][2])
            comps.append(
                f"export function {comp}({{ label, ...props }}) {{\n"
                f"  return <svg className=\"fi fi-{k}\" data-motion=\"{ICONS[k][1]}\" viewBox=\"0 0 24 24\" "
                f"focusable=\"false\" {{...(label ? {{ role: 'img', 'aria-label': label }} : {{ 'aria-hidden': true }})}} {{...props}}>"
                f"{body}</svg>;\n}}")
        return ("// Flash Icons. Put flashIconsCss in a <style> once (or in your global CSS).\n"
                f"export const flashIconsCss = {json.dumps(css())};\n\n" + "\n\n".join(comps) + "\n")
    if fmt != "html":
        raise IconError("format is html, react or css.")
    return (f"<style>\n{css()}\n</style>\n\n"
            + "\n".join(f"<!-- {k} -->\n{svg(k)}" for k in keys))


def catalogue() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, (cat, _m, _p) in ICONS.items():
        out.setdefault(cat, []).append(k)
    return out


def preview_page(names: Optional[list[str]] = None) -> str:
    """A page of the icons in the current style, each with its name, to see them and their motion."""

    keys = [find(n) for n in names] if names else list(ICONS)
    keys = [k for k in keys if k]
    s = style()
    tiles = "".join(f'<button class="tile fi-host" type="button">{svg(k)}<span>{k}</span></button>' for k in keys)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flash Icons</title><style>
{css(s)}
:root{{--bg:#fafaf9;--ink:#1c1b19;--sub:#6f6c66;--line:#e7e4df}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--ink:#ecebe8;--sub:#9c9890;--line:#2a2927}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:40px 24px 64px}}
h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--sub);margin:0 0 28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px}}
.tile{{display:flex;flex-direction:column;align-items:center;gap:10px;padding:20px 8px 14px;border:1px solid var(--line);border-radius:12px;background:none;color:inherit;font:inherit;cursor:default}}
.tile .fi{{--fi-size:28px}} .tile span{{font-size:12px;color:var(--sub)}}
</style></head><body><main><h1>Flash Icons</h1><p>{html.escape(describe(s))}. Point at one to see it move.</p>
<div class="grid">{tiles}</div></main></body></html>"""
