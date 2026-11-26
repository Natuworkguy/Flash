"""Colour themes extensions add to the web UI.

The built-in ones live in the page itself; an extension adds more with
"themes" in its manifest, each a set of the page's colours for dark
mode, light mode, or both:

    "themes": [
      {"name": "nord", "label": "Nord",
       "dark": {"bg": "#2e3440", "accent": "#88c0d0"},
       "light": {"bg": "#eceff4", "accent": "#5e81ac"}}
    ]

Only the colours named in TOKENS can be set, and only to a colour, so
a theme can't slip anything else into the page's styles.
"""

import re
from dataclasses import dataclass, field

# The page's colours a theme may set, as its CSS variables are named.
TOKENS = (
    "bg", "sidebar", "surface", "text", "text-2", "text-3", "accent",
    "accent-text", "border", "border-strong", "hover", "pressed",
    "code-bg", "add", "del", "warn", "working", "invert-bg", "invert-text",
)

MODES = ("dark", "light")

_HEX = r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
_NUMBER = r"-?\d+(?:\.\d+)?%?"
_FUNCTION = (
    rf"(?:rgb|rgba|hsl|hsla)\(\s*{_NUMBER}(?:\s*[,\s/]\s*{_NUMBER}){{2,3}}"
    r"\s*\)"
)
COLOUR_RE = re.compile(rf"^(?:{_HEX}|{_FUNCTION})$")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    dark: dict = field(default_factory=dict)
    light: dict = field(default_factory=dict)
    source: str = ""

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "dark": dict(self.dark),
            "light": dict(self.light), "source": self.source,
        }


def from_manifest(entry: object, where: str, source: str) -> Theme:
    """A theme an extension's manifest describes, checked."""

    if not isinstance(entry, dict):
        raise ValueError(f"{where} has to be an object")
    theme_id = str(entry.get("name") or "").strip().lower()
    if not NAME_RE.match(theme_id):
        raise ValueError(
            f"{where} needs a name of lowercase letters, digits and dashes"
        )
    modes = {}
    for mode in MODES:
        colours = entry.get(mode) or {}
        if not isinstance(colours, dict):
            raise ValueError(f"{where}: \"{mode}\" has to be an object")
        for token, value in colours.items():
            if token not in TOKENS:
                raise ValueError(
                    f"{where}: {token!r} is not a colour a theme can set "
                    f"({', '.join(TOKENS)})"
                )
            if not isinstance(value, str) or not COLOUR_RE.match(
                value.strip()
            ):
                raise ValueError(
                    f"{where}: {token} is {value!r}, not a colour like "
                    "#1f1e1d or rgb(31, 30, 29)"
                )
        modes[mode] = {k: v.strip() for k, v in colours.items()}
    if not modes["dark"] and not modes["light"]:
        raise ValueError(f"{where} sets no colours for dark or light")
    return Theme(
        theme_id, str(entry.get("label") or theme_id.title())[:40],
        modes["dark"], modes["light"], source,
    )


def extension_themes() -> list[Theme]:
    """Every theme the installed extensions add, the first of a name
    winning."""

    from . import extensions  # deferred: extensions are read on demand

    found, taken = [], set()
    for extension in extensions.installed():
        for theme in getattr(extension, "themes", []):
            if theme.id not in taken:
                taken.add(theme.id)
                found.append(theme)
    return found
