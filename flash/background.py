"""Pixel-art scenes drawn behind the prompt.

A terminal has no wallpaper. What it has is the room between the last
thing printed and the line you type on, which on a fresh session is
most of the screen. That space is what a scene fills: the prompt sits
at the foot of the terminal with the picture above it, and the moment
there is a conversation to read the picture gives way to it.

Scenes are text, not images. Pillow is deliberately not a dependency of
this project, and a format that is a palette plus rows of characters
needs no decoder, survives a diff, and can be edited by hand:

    name: Sunset Ridge
    palette:
      . #0b1026
      o #f2a65a
    pixels:
    ..oo..
    ......

Each character is one pixel. They are drawn two to a cell with an upper
half block, the top pixel as the foreground and the bottom as the
background, which is what makes the pixels roughly square rather than
letter-shaped.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.color import Color
from rich.style import Style
from rich.text import Text

from . import extensions
from .paths import FLASH_DIR
from .theme import can_encode

# Two pixels to a cell: the top one is the foreground of an upper half
# block, the bottom one is the background behind it.
UPPER_HALF = "▀"

# Below this there is not enough room for a scene to read as anything,
# so the space is left blank instead.
MIN_ROWS = 4
MIN_COLUMNS = 20

SUFFIX = ".scene"

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class SceneError(ValueError):
    """A scene file that could not be read as one."""


@dataclass
class Scene:
    """A picture as rows of colours, top row first."""

    name: str = ""
    rows: list[list[str]] = field(default_factory=list)
    source: Optional[Path] = None

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0


def _palette_entry(line: str, where: str) -> tuple[str, str]:
    parts = line.split()

    if len(parts) != 2 or len(parts[0]) != 1:
        raise SceneError(
            f"{where}: a palette line is one character then one colour, "
            f"not {line.strip()!r}"
        )

    key, colour = parts

    if not HEX.match(colour):
        raise SceneError(
            f"{where}: {colour!r} is not a #rrggbb colour"
        )

    return key, colour.lower()


def parse(text: str, where: str = "scene") -> Scene:
    """Read a scene file's contents.

    Every failure names the file and what was wrong with it: a scene
    that silently renders as garbage is worse than one that refuses.
    """

    name = ""
    palette: dict[str, str] = {}
    rows: list[list[str]] = []
    section = ""

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n")
        spot = f"{where}:{number}"

        if section != "pixels":
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            if stripped.endswith(":") and " " not in stripped[:-1]:
                section = stripped[:-1].lower()
                continue

            if section == "palette":
                key, colour = _palette_entry(line, spot)
                palette[key] = colour
                continue

            label, _, value = stripped.partition(":")
            if label.strip().lower() == "name":
                name = value.strip()
                continue

            raise SceneError(
                f"{spot}: {stripped!r} is not part of a scene"
            )

        if not line.strip():
            continue

        row = []
        for column, char in enumerate(line):
            if char not in palette:
                raise SceneError(
                    f"{spot}: column {column + 1} uses {char!r}, which the "
                    "palette does not define"
                )
            row.append(palette[char])
        rows.append(row)

    if not rows:
        raise SceneError(f"{where}: no pixels")

    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise SceneError(
            f"{where}: every row has to be the same width, found "
            f"{min(widths)} to {max(widths)}"
        )

    return Scene(name=name, rows=rows)


def load(path: Path) -> Scene:
    """Read one scene off disk."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SceneError(f"could not read {path}: {exc}") from exc

    scene = parse(text, where=path.name)
    scene.source = path
    scene.name = scene.name or path.stem

    return scene


def bundled_dir() -> Path:
    """The scenes that ship with Flash."""

    return Path(__file__).parent / "backgrounds"


def user_dir() -> Path:
    """Where someone drops their own scenes."""

    return FLASH_DIR / "backgrounds"


def search_paths() -> list[Path]:
    """Every scene directory: the user's first so it can override, then
    the ones extensions bring, then the scenes that ship with Flash."""

    return [user_dir(), *extensions.background_dirs(), bundled_dir()]


def names() -> list[str]:
    """Every scene available, in alphabetical order, without repeats."""

    found: dict[str, None] = {}

    for folder in search_paths():
        try:
            entries = sorted(folder.glob(f"*{SUFFIX}"))
        except OSError:
            continue
        for entry in entries:
            found.setdefault(entry.stem, None)

    return sorted(found)


def find(name: str) -> Optional[Path]:
    """The file for a scene, or None if there is no such scene."""

    wanted = name.strip().lower()

    if not wanted:
        return None

    for folder in search_paths():
        candidate = folder / f"{wanted}{SUFFIX}"
        if candidate.is_file():
            return candidate

    return None


def drawable() -> bool:
    """Whether this console can draw a scene at all.

    A terminal that cannot encode the half block would print a row of
    replacement characters, which is worse than no background.
    """

    if os.environ.get("NO_COLOR"):
        return False

    return can_encode(UPPER_HALF)


def _sample(scene: Scene, columns: int, pixel_rows: int) -> list[list[str]]:
    """The scene at a new size, nearest pixel wins.

    Stretched to fill rather than fitted and centred: it is a
    background, and a band of dead terminal down one side would read
    as a bug rather than as a margin.
    """

    return [
        [
            scene.rows[y * scene.height // pixel_rows][
                x * scene.width // columns
            ]
            for x in range(columns)
        ]
        for y in range(pixel_rows)
    ]


def _blend(top: str, bottom: str) -> str:
    """One colour for a cell that has to hold a character instead.

    A cell showing a glyph has only one background to give it, so the
    two pixels it would have drawn are averaged into one.
    """

    first, second = (
        [int(colour[at:at + 2], 16) for at in (1, 3, 5)]
        for colour in (top, bottom)
    )

    return "#" + "".join(
        f"{(a + b) // 2:02x}" for a, b in zip(first, second)
    )


Cell = tuple  # (character, rich Style or None)


def render(
    scene: Scene,
    columns: int,
    rows: int,
    overlay: Optional[list] = None,
) -> list[Text]:
    """The scene as terminal rows, two pixels to a cell.

    `overlay` is rows of (character, style) cells drawn on top, which
    is how text ends up sitting on the picture rather than punching a
    hole in it. A cell whose character is blank leaves the scene
    showing; one that is not keeps its own colour but takes the
    scene's as its background.
    """

    if rows < MIN_ROWS or columns < MIN_COLUMNS or not scene.rows:
        return []

    pixels = _sample(scene, columns, rows * 2)
    drawn = []

    for row in range(rows):
        top = pixels[row * 2]
        bottom = pixels[row * 2 + 1]
        over = (overlay or [])[row] if row < len(overlay or []) else []
        line = Text(no_wrap=True, overflow="ignore")

        for column in range(columns):
            char, style = (
                over[column] if column < len(over) else (" ", None)
            )

            if char and not char.isspace():
                behind = Color.parse(_blend(top[column], bottom[column]))
                line.append(
                    char, style=(style or Style()) + Style(bgcolor=behind)
                )
                continue

            line.append(
                UPPER_HALF,
                style=f"{top[column]} on {bottom[column]}",
            )

        drawn.append(line)

    return drawn
