"""Textures for the 3D models the agent builds.

A part's surface can be a pattern drawn here (wood, brick, marble, a
checkerboard and the rest) or a picture from a file. Patterns are drawn
in pure Python, like the models themselves, and every one tiles: its
right edge runs on into its left and its top into its bottom, so it
repeats across a surface of any size without a seam.

Each pattern is one tile, a square SCALE metres on a side. The model
lays the tile across a part by the part's own size in metres, so bricks
on a long wall are the same size as bricks on a short one.
"""

import math
import random
import struct
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

PATTERNS = (
    "wood", "brick", "tiles", "checker", "stripes", "marble", "noise",
    "grass", "fabric", "dots",
)

# How many metres one tile of each pattern covers, unless the part says.
DEFAULT_SCALE = {
    "wood": 1.0, "brick": 0.6, "tiles": 0.6, "checker": 0.5,
    "stripes": 0.5, "marble": 1.0, "noise": 1.0, "grass": 0.5,
    "fabric": 0.1, "dots": 0.3,
}

SIZE = 256
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}

Color = tuple[float, float, float]


class TextureError(ValueError):
    """A texture that cannot be made, and why."""


# --- Tiling noise ------------------------------------------------------------


def _lattice(period: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)  # nosec B311 -- a pattern, not a secret
    return [[rng.random() for _ in range(period)] for _ in range(period)]


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _value(grid: list[list[float]], x: float, y: float) -> float:
    """Smooth value noise on a grid that wraps, so it tiles."""

    period = len(grid)
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = _smooth(x - x0), _smooth(y - y0)
    x0, y0 = x0 % period, y0 % period
    x1, y1 = (x0 + 1) % period, (y0 + 1) % period
    top = grid[y0][x0] + (grid[y0][x1] - grid[y0][x0]) * fx
    bottom = grid[y1][x0] + (grid[y1][x1] - grid[y1][x0]) * fx
    return top + (bottom - top) * fy


class Noise:
    """Fractal noise over one tile, 0..1, that wraps at the tile's edges."""

    def __init__(self, seed: int, base: int = 4, octaves: int = 4) -> None:
        self.layers = [
            (_lattice(base * 2 ** k, seed + k), base * 2 ** k, 0.5 ** k)
            for k in range(octaves)
        ]
        self.total = sum(weight for _, _, weight in self.layers)

    def at(self, u: float, v: float) -> float:
        """Noise at (u, v), each 0..1 across the tile."""

        return sum(
            _value(grid, u * period, v * period) * weight
            for grid, period, weight in self.layers
        ) / self.total


# --- Colours -----------------------------------------------------------------


def _mix(a: Color, b: Color, t: float) -> Color:
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _shade(c: Color, amount: float) -> Color:
    """C lighter (amount > 0) or darker (amount < 0)."""

    target = (1.0, 1.0, 1.0) if amount > 0 else (0.0, 0.0, 0.0)
    return _mix(c, target, abs(amount))


# --- The patterns ------------------------------------------------------------
#
# Each takes the tile's two colours and its noise, and answers the colour
# at (u, v), both 0..1 across the tile.

Painter = Callable[[float, float], Color]


def _wood(a: Color, b: Color, noise: Noise) -> Painter:
    # Straight grain along u: thin dark lines across v, each wavering a
    # little, over a board whose tone drifts. Noise is only ever read at
    # whole multiples of the tile, so the grain tiles too.
    def paint(u: float, v: float) -> Color:
        drift = noise.at(u, v)
        line = (math.sin((v * 14 + drift * 1.6) * math.pi * 2) + 1) / 2
        fine = noise.at(u * 3 % 1, v * 5 % 1)
        return _mix(a, b, line ** 6 * 0.7 + fine * 0.2 + drift * 0.15)
    return paint


def _brick(a: Color, b: Color, noise: Noise) -> Painter:
    # Four courses to a tile, two bricks to a course, every other one
    # set over by half a brick. B is the mortar.
    rows, across, mortar = 4, 2, 0.06

    def paint(u: float, v: float) -> Color:
        row = int(v * rows)
        offset = 0.5 / across if row % 2 else 0.0
        x = (u + offset) % 1 * across
        y = v * rows
        in_x, in_y = x - int(x), y - int(y)
        joint = min(in_x, 1 - in_x) * 2 < mortar * across / 2 or \
            min(in_y, 1 - in_y) * 2 < mortar
        if joint:
            return _shade(b, (noise.at(u, v) - 0.5) * 0.2)
        brick = int(x) + row * across
        tone = (random.Random(brick).random() - 0.5) * 0.25  # nosec B311
        return _shade(a, tone + (noise.at(u, v) - 0.5) * 0.25)
    return paint


def _tiles(a: Color, b: Color, noise: Noise) -> Painter:
    # A 4 by 4 grid of square tiles; B is the grout.
    count, grout = 4, 0.05

    def paint(u: float, v: float) -> Color:
        x, y = u * count, v * count
        in_x, in_y = x - int(x), y - int(y)
        if min(in_x, 1 - in_x, in_y, 1 - in_y) < grout / 2:
            return b
        return _shade(a, (noise.at(u, v) - 0.5) * 0.12)
    return paint


def _checker(a: Color, b: Color, noise: Noise) -> Painter:
    def paint(u: float, v: float) -> Color:
        return a if (int(u * 4) + int(v * 4)) % 2 == 0 else b
    return paint


def _stripes(a: Color, b: Color, noise: Noise) -> Painter:
    def paint(u: float, v: float) -> Color:
        return a if int(v * 8) % 2 == 0 else b
    return paint


def _marble(a: Color, b: Color, noise: Noise) -> Painter:
    # A few thin veins of B wandering across A.
    def paint(u: float, v: float) -> Color:
        turn = noise.at(u, v) * 2.4
        vein = abs(math.sin((u + v * 2 + turn) * math.pi * 2))
        return _mix(b, a, vein ** 0.18)
    return paint


def _noise(a: Color, b: Color, noise: Noise) -> Painter:
    # Stone, concrete, sand, rust: whatever two colours make of it.
    def paint(u: float, v: float) -> Color:
        return _mix(a, b, (noise.at(u, v) - 0.3) * 2.5)
    return paint


def _grass(a: Color, b: Color, noise: Noise) -> Painter:
    # Blades: a finer noise over the patches the coarse one makes.
    fine = Noise(7919, base=32, octaves=2)

    def paint(u: float, v: float) -> Color:
        return _mix(a, b, fine.at(u, v) * 0.7 + noise.at(u, v) * 0.6 - 0.3)
    return paint


def _fabric(a: Color, b: Color, noise: Noise) -> Painter:
    # A plain weave: threads over and under, eight each way.
    def paint(u: float, v: float) -> Color:
        x, y = u * 8, v * 8
        over = (int(x) + int(y)) % 2 == 0
        along = y - int(y) if over else x - int(x)
        thread = math.sin(along * math.pi)
        return _mix(b, a, 0.35 + thread * 0.65)
    return paint


def _dots(a: Color, b: Color, noise: Noise) -> Painter:
    # B dots on A, four by four, every other row set over.
    def paint(u: float, v: float) -> Color:
        row = int(v * 4)
        x = (u * 4 + (0.5 if row % 2 else 0)) % 1
        y = v * 4 % 1
        return b if (x - 0.5) ** 2 + (y - 0.5) ** 2 < 0.07 else a
    return paint


_PAINTERS = {
    "wood": _wood, "brick": _brick, "tiles": _tiles, "checker": _checker,
    "stripes": _stripes, "marble": _marble, "noise": _noise,
    "grass": _grass, "fabric": _fabric, "dots": _dots,
}

# The second colour of each, from the part's own, when none is given.
_SECOND = {
    "wood": lambda c: _shade(c, -0.45),
    "brick": lambda c: (0.78, 0.76, 0.72),
    "tiles": lambda c: _shade(c, -0.35),
    "checker": lambda c: _shade(c, -0.7) if sum(c) > 1.5
    else _shade(c, 0.75),
    "stripes": lambda c: _shade(c, 0.6),
    "marble": lambda c: _shade(c, -0.55),
    "noise": lambda c: _shade(c, -0.4),
    "grass": lambda c: _shade(c, -0.45),
    "fabric": lambda c: _shade(c, -0.3),
    "dots": lambda c: (1.0, 1.0, 1.0) if sum(c) < 1.5 else (0.1, 0.1, 0.1),
}


def second_color(pattern: str, color: Color) -> Color:
    return _SECOND[pattern](color)


def draw(pattern: str, a: Color, b: Color, seed: int = 1) -> bytes:
    """One tile of PATTERN in colours A and B, as a PNG."""

    if pattern not in _PAINTERS:
        raise TextureError(
            f"texture {pattern!r} is not one of {', '.join(PATTERNS)}"
        )
    paint = _PAINTERS[pattern](a, b, Noise(seed, base=6 if pattern in (
        "noise", "grass") else 4))
    rows = []
    for y in range(SIZE):
        v = (y + 0.5) / SIZE
        row = bytearray(b"\x00")
        for x in range(SIZE):
            r, g, bl = paint((x + 0.5) / SIZE, v)
            row += bytes((
                max(0, min(255, round(r * 255))),
                max(0, min(255, round(g * 255))),
                max(0, min(255, round(bl * 255))),
            ))
        rows.append(bytes(row))
    return png(SIZE, SIZE, b"".join(rows))


def png(width: int, height: int, scanlines: bytes) -> bytes:
    """An RGB PNG from its scanlines, each led by its filter byte."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + \
            struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", header),
        chunk(b"IDAT", zlib.compress(scanlines, 9)),
        chunk(b"IEND", b""),
    ))


# --- Pictures from files -----------------------------------------------------


def image_type(data: bytes) -> Optional[str]:
    """The MIME type of a PNG or a JPEG, or None for anything else."""

    for magic, mime in IMAGE_TYPES.items():
        if data.startswith(magic):
            return mime
    return None


def load_image(path: Any) -> tuple[bytes, str]:
    """A PNG or JPEG file's bytes and MIME type, for a texture."""

    found = Path(str(path or "")).expanduser()
    if not found.is_file():
        raise TextureError(f"texture image {found} is not a file")
    try:
        size = found.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise TextureError(
                f"texture image {found.name} is over "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB"
            )
        data = found.read_bytes()
    except OSError as exc:
        raise TextureError(f"could not read {found}: {exc}") from None
    mime = image_type(data)
    if mime is None:
        raise TextureError(
            f"texture image {found.name} is not a PNG or a JPEG"
        )
    return data, mime
