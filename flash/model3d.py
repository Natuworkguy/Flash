"""3D models the agent builds out of parts.

The model describes a model as a list of parts, each a shape (a box, a
sphere, a cylinder, a lathed profile, an extruded outline, blocky text,
or a mesh of its own) with a place, a turn, a size and a colour. This
turns that list into a binary glTF (.glb) file: one file that the web
UI's viewer, Blender, Windows' 3D Viewer and every game engine open as
it is.

Pure Python on purpose, with no numpy or trimesh to install, since a
model of a few hundred parts is a few hundred thousand floats at most.
"""

import base64
import json
import math
import re
import struct
from pathlib import Path
from typing import Any, Optional

# Every shape is built around the origin, Y up, in metres, as glTF has it.
SHAPES = (
    "box", "sphere", "cylinder", "cone", "torus", "plane", "lathe",
    "extrude", "mesh", "text",
)
MODEL_SUFFIXES = (".glb", ".stl", ".obj")

MAX_PARTS = 500
MAX_TRIANGLES = 2_000_000
MAX_POINTS = 5000
MIN_SEGMENTS = 3
MAX_SEGMENTS = 128
DEFAULT_SEGMENTS = 32

DEFAULT_COLOR = "#b0b4bb"

# The CSS names a model reaches for first. Anything else is a hex code.
NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "gray": "#808080",
    "grey": "#808080", "silver": "#c0c0c0", "red": "#e53935",
    "darkred": "#8b0000", "orange": "#fb8c00", "yellow": "#fdd835",
    "gold": "#ffd700", "green": "#43a047", "darkgreen": "#1b5e20",
    "lime": "#7cb342", "teal": "#00897b", "cyan": "#00bcd4",
    "blue": "#1e88e5", "navy": "#1a237e", "skyblue": "#87ceeb",
    "purple": "#8e24aa", "violet": "#ee82ee", "pink": "#f48fb1",
    "magenta": "#d81b60", "brown": "#795548", "tan": "#d2b48c",
    "beige": "#f5f5dc", "wood": "#a0522d", "glass": "#cfe8ff",
}
_HEX = re.compile(r"^#?([0-9a-f]{3}|[0-9a-f]{6})$", re.IGNORECASE)

_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_FLOAT = 5126
_UNSIGNED_INT = 5125


class ModelError(ValueError):
    """A part the model described that cannot be built, and why."""


class Geometry:
    """Triangles with a normal at every corner, ready to write out."""

    def __init__(self) -> None:
        self.positions: list[tuple[float, float, float]] = []
        self.normals: list[tuple[float, float, float]] = []
        self.indices: list[int] = []

    def vertex(self, p, n) -> int:
        self.positions.append((float(p[0]), float(p[1]), float(p[2])))
        self.normals.append(_unit(n))
        return len(self.positions) - 1

    def triangle(self, a: int, b: int, c: int) -> None:
        self.indices.extend((a, b, c))

    def flat(self, a, b, c) -> None:
        """One triangle with its own corners, lit flat."""

        n = _cross(_sub(b, a), _sub(c, a))
        if _length(n) == 0:
            return
        self.triangle(self.vertex(a, n), self.vertex(b, n), self.vertex(c, n))

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3


# --- Vector arithmetic ----------------------------------------------------


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(v) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _unit(v):
    size = _length(v)
    if size == 0:
        return (0.0, 1.0, 0.0)
    return (v[0] / size, v[1] / size, v[2] / size)


# --- Reading what the model gave ------------------------------------------


def _number(value: Any, what: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ModelError(f"{what} must be a number, not {value!r}") from None
    if not math.isfinite(number):
        raise ModelError(f"{what} must be a finite number")
    if positive and number <= 0:
        raise ModelError(f"{what} must be more than 0, not {number:g}")
    return number


def _vector(value: Any, what: str, fallback, *, positive=False):
    """Three numbers, from a list or from one number used for all three."""

    if value is None:
        return fallback
    if isinstance(value, (int, float, str)):
        one = _number(value, what, positive=positive)
        return (one, one, one)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelError(f"{what} must be [x, y, z]")
    return tuple(
        _number(v, f"{what}[{i}]", positive=positive)
        for i, v in enumerate(value)
    )


def _points(value: Any, what: str, size: int) -> list[tuple]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ModelError(f"{what} must be a list of points")
    if len(value) > MAX_POINTS:
        raise ModelError(f"{what} has over {MAX_POINTS} points")
    points = []
    for i, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != size:
            raise ModelError(
                f"{what}[{i}] must be {size} numbers, not {point!r}"
            )
        points.append(tuple(
            _number(v, f"{what}[{i}]") for v in point
        ))
    return points


def _segments(value: Any, fallback: int = DEFAULT_SEGMENTS) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, number))


def parse_color(value: Any) -> tuple[float, float, float]:
    """A colour as sRGB 0..1, from a hex code or a common name."""

    text = str(value or DEFAULT_COLOR).strip().lower().replace(" ", "")
    text = NAMED_COLORS.get(text, text)
    found = _HEX.match(text)
    if not found:
        raise ModelError(
            f"color {value!r} is not a hex code like #ff8800 or a common "
            "colour name"
        )
    digits = found.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return tuple(int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _linear(channel: float) -> float:
    """sRGB to the linear light glTF stores its colours in."""

    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _fraction(value: Any, what: str, fallback: float) -> float:
    if value is None:
        return fallback
    return max(0.0, min(1.0, _number(value, what)))


# --- The shapes -----------------------------------------------------------


# A box's faces, each as its normal and two edges, walked
# counter-clockwise as seen from outside.
_BOX_FACES = (
    ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
)


def _add_box(geo: Geometry, middle, half, hidden=()) -> None:
    """A box around MIDDLE, HALF its size each way, leaving out the
    faces whose normals are in HIDDEN, where another box touches it."""

    for normal, u, v in _BOX_FACES:
        if normal in hidden:
            continue
        centre = tuple(middle[i] + normal[i] * half[i] for i in range(3))
        corners = []
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append(tuple(
                centre[i] + a * u[i] * half[i] + b * v[i] * half[i]
                for i in range(3)
            ))
        first = [geo.vertex(c, normal) for c in corners]
        geo.triangle(first[0], first[1], first[2])
        geo.triangle(first[0], first[2], first[3])


def _box(part: dict) -> Geometry:
    sx, sy, sz = _vector(part.get("size"), "size", (1.0, 1.0, 1.0),
                         positive=True)
    geo = Geometry()
    _add_box(geo, (0, 0, 0), (sx / 2, sy / 2, sz / 2))
    return geo


# A blocky 5x7 font for the text shape, "#" for a block, drawn top row
# first. Narrow marks are narrower rows.
FONT = {
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("###", ".#.", ".#.", ".#.", ".#.", ".#.", "###"),
    "J": ("..###", "...#.", "...#.", "...#.", "#..#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    " ": ("...",) * 7,
    ".": (".", ".", ".", ".", ".", ".", "#"),
    ",": (".", ".", ".", ".", ".", "#", "#"),
    "!": ("#", "#", "#", "#", "#", ".", "#"),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "-": ("...", "...", "...", "###", "...", "...", "..."),
    ":": (".", "#", ".", ".", ".", "#", "."),
    "'": ("#", "#", ".", ".", ".", ".", "."),
    "&": (".##..", "#..#.", "#.#..", ".#...", "#.#.#", "#..#.", ".##.#"),
    "/": ("....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."),
}
FONT_ROWS = 7
MAX_TEXT = 200


def _text(part: dict) -> Geometry:
    """Words in blocks, one block to a pixel of the font, reading along
    X and facing +Z: a logo, a sign, a name over a door."""

    words = str(part.get("text") or "").upper()
    if not words.strip():
        raise ModelError("a text part needs text")
    if len(words) > MAX_TEXT:
        raise ModelError(f"a text part can have at most {MAX_TEXT} letters")
    unknown = sorted({c for c in words if c not in FONT and c != "\n"})
    if unknown:
        raise ModelError(
            f"the font has no {' '.join(repr(c) for c in unknown)}; it has "
            "A-Z, 0-9, spaces and . , ! ? - : ' & /"
        )
    # The height of a capital, and the block that makes it.
    height = _number(part.get("height", part.get("size", 1)), "height",
                     positive=True)
    block = height / FONT_ROWS
    depth = _number(part.get("depth", block), "depth", positive=True)

    # Each line as columns of blocks, a column of space between letters.
    lines = []
    for line in words.split("\n"):
        columns: list[str] = []
        for n, char in enumerate(line):
            if n:
                columns.append("." * FONT_ROWS)
            rows = FONT[char]
            for x in range(len(rows[0])):
                columns.append("".join(row[x] for row in rows))
        lines.append(columns)
    widest = max(len(columns) for columns in lines)
    # Lines two blocks apart, each centred.
    tall = len(lines) * (FONT_ROWS + 2) - 2
    filled = set()
    for k, columns in enumerate(lines):
        left = (widest - len(columns)) // 2
        top = tall - 1 - k * (FONT_ROWS + 2)
        for x, column in enumerate(columns):
            for y, pixel in enumerate(column):
                if pixel == "#":
                    filled.add((left + x, top - y))
    if not filled:
        raise ModelError("a text part needs at least one letter")

    geo = Geometry()
    half = (block / 2, block / 2, depth / 2)
    for x, y in filled:
        hidden = tuple(
            normal for normal, step in (
                ((1, 0, 0), (1, 0)), ((-1, 0, 0), (-1, 0)),
                ((0, 1, 0), (0, 1)), ((0, -1, 0), (0, -1)),
            )
            if (x + step[0], y + step[1]) in filled
        )
        middle = (
            (x + 0.5 - widest / 2) * block,
            (y + 0.5 - tall / 2) * block,
            0.0,
        )
        _add_box(geo, middle, half, hidden)
    return geo


def _plane(part: dict) -> Geometry:
    """A flat rectangle facing up: a floor, a table top, a sheet."""

    size = part.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        sx = _number(size[0], "size[0]", positive=True)
        sz = _number(size[1], "size[1]", positive=True)
    else:
        sx, _, sz = _vector(size, "size", (1.0, 1.0, 1.0), positive=True)
    x, z = sx / 2, sz / 2
    geo = Geometry()
    up = (0, 1, 0)
    a, b, c, d = (geo.vertex(p, up) for p in (
        (-x, 0, z), (x, 0, z), (x, 0, -z), (-x, 0, -z),
    ))
    geo.triangle(a, b, c)
    geo.triangle(a, c, d)
    return geo


def _sphere(part: dict) -> Geometry:
    radius = _number(part.get("radius", 0.5), "radius", positive=True)
    around = _segments(part.get("segments"))
    down = max(MIN_SEGMENTS, around // 2)
    geo = Geometry()
    rows = []
    for j in range(down + 1):
        polar = math.pi * j / down
        row = []
        for i in range(around + 1):
            azimuth = 2 * math.pi * i / around
            n = (
                -math.sin(polar) * math.cos(azimuth),
                math.cos(polar),
                math.sin(polar) * math.sin(azimuth),
            )
            row.append(geo.vertex(tuple(radius * c for c in n), n))
        rows.append(row)
    for j in range(down):
        for i in range(around):
            a, b = rows[j][i], rows[j][i + 1]
            c, d = rows[j + 1][i], rows[j + 1][i + 1]
            if j != 0:
                geo.triangle(a, d, b)
            if j != down - 1:
                geo.triangle(a, c, d)
    return geo


def _revolve(geo: Geometry, profile: list[tuple], around: int) -> None:
    """Spin a profile of (radius, y) points around the Y axis, with the
    normals of the surface it sweeps, smooth all the way round."""

    count = len(profile)
    # Each point's normal in the profile's plane: across the segments
    # either side of it, turned outwards.
    flat_normals = []
    for k in range(count):
        before = profile[max(0, k - 1)]
        after = profile[min(count - 1, k + 1)]
        dr, dy = after[0] - before[0], after[1] - before[1]
        flat_normals.append((dy, -dr))
    rings = []
    for k, (r, y) in enumerate(profile):
        nr, ny = flat_normals[k]
        ring = []
        for i in range(around + 1):
            angle = 2 * math.pi * i / around
            cos, sin = math.cos(angle), -math.sin(angle)
            ring.append(geo.vertex(
                (r * cos, y, r * sin), (nr * cos, ny, nr * sin),
            ))
        rings.append(ring)
    for k in range(count - 1):
        for i in range(around):
            a, b = rings[k][i], rings[k][i + 1]
            c, d = rings[k + 1][i], rings[k + 1][i + 1]
            if profile[k][0] > 0:
                geo.triangle(a, b, c)
            if profile[k + 1][0] > 0:
                geo.triangle(b, d, c)


def _cap(geo: Geometry, radius: float, y: float, around: int,
         up: bool) -> None:
    if radius <= 0:
        return
    normal = (0, 1 if up else -1, 0)
    centre = geo.vertex((0, y, 0), normal)
    edge = []
    for i in range(around + 1):
        angle = 2 * math.pi * i / around
        edge.append(geo.vertex(
            (radius * math.cos(angle), y, -radius * math.sin(angle)), normal,
        ))
    for i in range(around):
        if up:
            geo.triangle(centre, edge[i], edge[i + 1])
        else:
            geo.triangle(centre, edge[i + 1], edge[i])


def _cylinder(part: dict, *, cone: bool = False) -> Geometry:
    height = _number(part.get("height", 1), "height", positive=True)
    radius = part.get("radius", 0.5)
    bottom = _number(part.get("radius_bottom", radius), "radius_bottom")
    top = 0.0 if cone else _number(part.get("radius_top", radius),
                                   "radius_top")
    if bottom < 0 or top < 0 or bottom == top == 0:
        raise ModelError("a cylinder needs a radius more than 0")
    around = _segments(part.get("segments"))
    geo = Geometry()
    h = height / 2
    _revolve(geo, [(bottom, -h), (top, h)], around)
    _cap(geo, top, h, around, up=True)
    _cap(geo, bottom, -h, around, up=False)
    return geo


def _torus(part: dict) -> Geometry:
    """A ring lying flat, its hole along Y, like a donut on a plate."""

    radius = _number(part.get("radius", 0.5), "radius", positive=True)
    tube = _number(part.get("tube", radius / 4), "tube", positive=True)
    around = _segments(part.get("segments"), 48)
    sides = max(MIN_SEGMENTS, around // 2)
    geo = Geometry()
    rows = []
    for j in range(sides + 1):
        v = 2 * math.pi * j / sides
        row = []
        for i in range(around + 1):
            u = 2 * math.pi * i / around
            ring = (math.cos(u), 0.0, -math.sin(u))
            n = (
                ring[0] * math.cos(v), math.sin(v), ring[2] * math.cos(v),
            )
            p = tuple(radius * ring[k] + tube * n[k] for k in range(3))
            row.append(geo.vertex(p, n))
        rows.append(row)
    for j in range(sides):
        for i in range(around):
            a, b = rows[j][i], rows[j][i + 1]
            c, d = rows[j + 1][i], rows[j + 1][i + 1]
            geo.triangle(a, b, d)
            geo.triangle(a, d, c)
    return geo


def _lathe(part: dict) -> Geometry:
    """A profile of [radius, y] points, bottom to top, spun around Y:
    a vase, a bottle, a lamp, a chess piece."""

    profile = _points(part.get("points"), "points", 2)
    if len(profile) < 2:
        raise ModelError("a lathe needs at least 2 points")
    if any(r < 0 for r, _ in profile):
        raise ModelError("a lathe's radii cannot be negative")
    # Walked the other way, the surface would face inwards.
    if profile[-1][1] < profile[0][1]:
        profile.reverse()
    geo = Geometry()
    around = _segments(part.get("segments"), 48)
    _revolve(geo, profile, around)
    if part.get("closed", True):
        _cap(geo, profile[-1][0], profile[-1][1], around, up=True)
        _cap(geo, profile[0][0], profile[0][1], around, up=False)
    return geo


def _signed_area(points: list[tuple]) -> float:
    area = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2


def _inside(p, a, b, c) -> bool:
    def side(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - \
            (p2[0] - p3[0]) * (p1[1] - p3[1])

    d1, d2, d3 = side(p, a, b), side(p, b, c), side(p, c, a)
    negative = d1 < 0 or d2 < 0 or d3 < 0
    positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (negative and positive)


def triangulate(points: list[tuple]) -> list[tuple[int, int, int]]:
    """Ear-clip a simple polygon, convex or not, counter-clockwise."""

    order = list(range(len(points)))
    if _signed_area(points) < 0:
        order.reverse()
    found = []
    guard = 0
    while len(order) > 3 and guard < len(points) ** 2:
        guard += 1
        for k in range(len(order)):
            i, j, m = order[k - 1], order[k], order[(k + 1) % len(order)]
            a, b, c = points[i], points[j], points[m]
            turn = (b[0] - a[0]) * (c[1] - a[1]) - \
                (b[1] - a[1]) * (c[0] - a[0])
            if turn <= 0:
                continue
            if any(_inside(points[o], a, b, c)
                   for o in order if o not in (i, j, m)):
                continue
            found.append((i, j, m))
            order.pop(k)
            break
        else:
            break
    if len(order) == 3:
        found.append(tuple(order))
    return found


def _extrude(part: dict) -> Geometry:
    """An outline of [x, z] points, seen from above, raised to a height:
    a floor plan's walls, a letter, a gear, a sign."""

    outline = _points(part.get("points"), "points", 2)
    if len(outline) < 3:
        raise ModelError("an extrude needs at least 3 points")
    if outline[0] == outline[-1]:
        outline = outline[:-1]
    height = _number(part.get("height", 1), "height", positive=True)
    if abs(_signed_area(outline)) == 0:
        raise ModelError("an extrude's outline has no area")
    # Walls face out when the outline turns from x towards z.
    if _signed_area(outline) < 0:
        outline = outline[::-1]
    h = height / 2
    geo = Geometry()
    ears = triangulate(outline)
    for i, j, m in ears:
        a, b, c = outline[i], outline[j], outline[m]
        geo.flat((a[0], h, a[1]), (c[0], h, c[1]), (b[0], h, b[1]))
        geo.flat((a[0], -h, a[1]), (b[0], -h, b[1]), (c[0], -h, c[1]))
    for k, (x1, z1) in enumerate(outline):
        x2, z2 = outline[(k + 1) % len(outline)]
        low1, low2 = (x1, -h, z1), (x2, -h, z2)
        high1, high2 = (x1, h, z1), (x2, h, z2)
        geo.flat(low1, high1, high2)
        geo.flat(low1, high2, low2)
    return geo


def _mesh(part: dict) -> Geometry:
    """Any shape at all: vertices and the faces between them."""

    vertices = _points(part.get("vertices"), "vertices", 3)
    faces = part.get("faces")
    if not isinstance(faces, list) or not faces:
        raise ModelError("a mesh needs faces: lists of vertex indices")
    if len(faces) > MAX_TRIANGLES:
        raise ModelError(f"a mesh can have at most {MAX_TRIANGLES} faces")
    geo = Geometry()
    for n, face in enumerate(faces):
        if not isinstance(face, (list, tuple)) or len(face) < 3:
            raise ModelError(f"faces[{n}] must list 3 or more vertices")
        try:
            corners = [vertices[int(i)] for i in face]
        except (IndexError, TypeError, ValueError):
            raise ModelError(
                f"faces[{n}] names a vertex that is not in vertices "
                f"(there are {len(vertices)}, numbered from 0)"
            ) from None
        for k in range(1, len(corners) - 1):
            geo.flat(corners[0], corners[k], corners[k + 1])
    return geo


_BUILDERS = {
    "box": _box,
    "plane": _plane,
    "sphere": _sphere,
    "cylinder": _cylinder,
    "cone": lambda part: _cylinder(part, cone=True),
    "torus": _torus,
    "lathe": _lathe,
    "extrude": _extrude,
    "mesh": _mesh,
    "text": _text,
}


# --- Placing the parts ----------------------------------------------------


def _quaternion(degrees) -> tuple[float, float, float, float]:
    """Euler angles in degrees, turned X then Y then Z as three.js does,
    as the [x, y, z, w] quaternion glTF stores."""

    x, y, z = (math.radians(d) / 2 for d in degrees)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def _rotate(q, v):
    qx, qy, qz, qw = q
    tx = 2 * (qy * v[2] - qz * v[1])
    ty = 2 * (qz * v[0] - qx * v[2])
    tz = 2 * (qx * v[1] - qy * v[0])
    return (
        v[0] + qw * tx + (qy * tz - qz * ty),
        v[1] + qw * ty + (qz * tx - qx * tz),
        v[2] + qw * tz + (qx * ty - qy * tx),
    )


class Part:
    """One part, built and placed."""

    def __init__(self, index: int, spec: Any) -> None:
        if not isinstance(spec, dict):
            raise ModelError(f"part {index + 1} must be an object")
        where = f"part {index + 1}"
        self.name = str(spec.get("name") or "").strip()[:80]
        if self.name:
            where += f" ({self.name})"
        shape = str(spec.get("shape") or "").strip().lower()
        if shape not in _BUILDERS:
            raise ModelError(
                f"{where}: shape must be one of {', '.join(SHAPES)}, not "
                f"{spec.get('shape')!r}"
            )
        self.shape = shape
        self.name = self.name or f"{shape}-{index + 1}"
        try:
            self.geometry = _BUILDERS[shape](spec)
            self.position = _vector(spec.get("position"), "position",
                                    (0.0, 0.0, 0.0))
            turn = _vector(spec.get("rotation"), "rotation", (0.0, 0.0, 0.0))
            self.rotation = _quaternion(turn)
            self.scale = _vector(spec.get("scale"), "scale",
                                 (1.0, 1.0, 1.0), positive=True)
            self.color = parse_color(spec.get("color"))
            self.metalness = _fraction(spec.get("metalness"), "metalness",
                                       0.0)
            self.roughness = _fraction(spec.get("roughness"), "roughness",
                                       0.6)
            self.opacity = _fraction(spec.get("opacity"), "opacity", 1.0)
            self.emissive = spec.get("emissive")
            if self.emissive is not None:
                self.emissive = parse_color(self.emissive)
        except ModelError as exc:
            raise ModelError(f"{where}: {exc}") from None
        if not self.geometry.indices:
            raise ModelError(f"{where}: its shape came out with no faces")
        # A mesh's faces run whichever way the model wrote them, so both
        # sides of them are drawn.
        self.two_sided = shape in ("mesh", "plane")

    def world_points(self):
        for p in self.geometry.positions:
            scaled = (p[0] * self.scale[0], p[1] * self.scale[1],
                      p[2] * self.scale[2])
            turned = _rotate(self.rotation, scaled)
            yield (turned[0] + self.position[0],
                   turned[1] + self.position[1],
                   turned[2] + self.position[2])

    def material_key(self) -> tuple:
        return (self.color, self.metalness, self.roughness, self.opacity,
                self.emissive, self.two_sided)


def build_parts(parts: Any) -> list[Part]:
    """Every part built, or a ModelError naming the first bad one."""

    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except json.JSONDecodeError as exc:
            raise ModelError(f"parts is not valid JSON: {exc}") from None
    if isinstance(parts, dict):
        parts = parts.get("parts", [parts])
    if not isinstance(parts, list) or not parts:
        raise ModelError("parts must be a list with at least one part")
    if len(parts) > MAX_PARTS:
        raise ModelError(f"a model can have at most {MAX_PARTS} parts")
    built = [Part(i, spec) for i, spec in enumerate(parts)]
    triangles = sum(p.geometry.triangles for p in built)
    if triangles > MAX_TRIANGLES:
        raise ModelError(
            f"the model has {triangles} triangles; the limit is "
            f"{MAX_TRIANGLES}, so use fewer segments or fewer parts"
        )
    return built


# --- Writing it out -------------------------------------------------------


def _pad(data: bytes, filler: bytes) -> bytes:
    return data + filler * (-len(data) % 4)


def to_glb(parts: list[Part], title: str = "") -> bytes:
    """The parts as one binary glTF 2.0 file."""

    binary = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    materials: list[dict] = []
    material_of: dict[tuple, int] = {}
    meshes: list[dict] = []
    nodes: list[dict] = []

    def add(data: bytes, target: int) -> int:
        views.append({
            "buffer": 0, "byteOffset": len(binary),
            "byteLength": len(data), "target": target,
        })
        binary.extend(_pad(data, b"\x00"))
        return len(views) - 1

    for part in parts:
        geo = part.geometry
        flat = [c for p in geo.positions for c in p]
        position = add(struct.pack(f"<{len(flat)}f", *flat), _ARRAY_BUFFER)
        lows = [min(p[i] for p in geo.positions) for i in range(3)]
        highs = [max(p[i] for p in geo.positions) for i in range(3)]
        accessors.append({
            "bufferView": position, "componentType": _FLOAT,
            "count": len(geo.positions), "type": "VEC3",
            "min": lows, "max": highs,
        })
        flat = [c for n in geo.normals for c in n]
        normal = add(struct.pack(f"<{len(flat)}f", *flat), _ARRAY_BUFFER)
        accessors.append({
            "bufferView": normal, "componentType": _FLOAT,
            "count": len(geo.normals), "type": "VEC3",
        })
        index = add(struct.pack(f"<{len(geo.indices)}I", *geo.indices),
                    _ELEMENT_ARRAY_BUFFER)
        accessors.append({
            "bufferView": index, "componentType": _UNSIGNED_INT,
            "count": len(geo.indices), "type": "SCALAR",
        })

        key = part.material_key()
        if key not in material_of:
            material = {
                "pbrMetallicRoughness": {
                    "baseColorFactor": [
                        *(_linear(c) for c in part.color), part.opacity,
                    ],
                    "metallicFactor": part.metalness,
                    "roughnessFactor": part.roughness,
                },
            }
            if part.opacity < 1:
                material["alphaMode"] = "BLEND"
            if part.emissive is not None:
                material["emissiveFactor"] = [
                    _linear(c) for c in part.emissive
                ]
            if part.two_sided:
                material["doubleSided"] = True
            material_of[key] = len(materials)
            materials.append(material)

        meshes.append({
            "name": part.name,
            "primitives": [{
                "attributes": {
                    "POSITION": len(accessors) - 3,
                    "NORMAL": len(accessors) - 2,
                },
                "indices": len(accessors) - 1,
                "material": material_of[key],
            }],
        })
        node: dict[str, Any] = {"name": part.name, "mesh": len(meshes) - 1}
        if any(part.position):
            node["translation"] = list(part.position)
        if part.rotation != (0.0, 0.0, 0.0, 1.0):
            node["rotation"] = list(part.rotation)
        if part.scale != (1.0, 1.0, 1.0):
            node["scale"] = list(part.scale)
        nodes.append(node)

    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "Flash"},
        "scene": 0,
        "scenes": [{"name": title or "Model",
                    "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    json_chunk = _pad(json.dumps(document, separators=(",", ":"))
                      .encode("utf-8"), b" ")
    bin_chunk = bytes(binary)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(json_chunk), b"JSON"), json_chunk,
        struct.pack("<I4s", len(bin_chunk), b"BIN\x00"), bin_chunk,
    ))


def bounds(parts: list[Part]):
    """The smallest box around every part, as its low and high corners."""

    low = [math.inf] * 3
    high = [-math.inf] * 3
    for part in parts:
        for p in part.world_points():
            for i in range(3):
                low[i] = min(low[i], p[i])
                high[i] = max(high[i], p[i])
    return tuple(low), tuple(high)


def describe(parts: list[Part]) -> str:
    """What was built, in numbers the model can check its intent against."""

    low, high = bounds(parts)
    size = [high[i] - low[i] for i in range(3)]
    triangles = sum(p.geometry.triangles for p in parts)

    def fmt(values) -> str:
        return ", ".join(f"{v:.3g}" for v in values)

    lines = [
        f"{len(parts)} part{'s' if len(parts) != 1 else ''}, "
        f"{triangles} triangles.",
        f"Overall size (x, y, z): {fmt(size)}, from ({fmt(low)}) to "
        f"({fmt(high)}).",
    ]
    if low[1] < -1e-6 or low[1] > 1e-3:
        lines.append(
            f"Its lowest point is at y = {low[1]:.3g}, so it does not sit "
            "on the ground (y = 0); move it if it should."
        )
    return "\n".join(lines)


def is_glb(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"glTF"


def check_model_file(path: Path) -> Optional[str]:
    """Why a model file cannot be shown, or None when it can."""

    suffix = path.suffix.lower()
    if suffix not in MODEL_SUFFIXES:
        return (
            f"{path.name} is not a .glb, .stl, or .obj file; convert a "
            ".gltf with its separate files into one .glb first"
        )
    try:
        with open(path, "rb") as handle:
            head = handle.read(84)
    except OSError as exc:
        return f"could not read {path}: {exc}"
    if suffix == ".glb" and not is_glb(head):
        return f"{path.name} does not start like a binary glTF file"
    if not head:
        return f"{path.name} is empty"
    return None


# --- A picture of it ------------------------------------------------------


def preview_page(model: bytes, suffix: str, viewer_js: str,
                 three_js: str) -> str:
    """A page that draws the model with the web UI's own viewer, for the
    headless browser to take a picture of."""

    data = base64.b64encode(model).decode("ascii")
    # Inlined, a script must not close its own tag early.
    three_js, viewer_js = (
        js.replace("</script", "<\\/script") for js in (three_js, viewer_js)
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "html,body{margin:0;height:100%;background:#f4f4f5}"
        "#view{position:absolute;inset:0}</style>"
        f"<script>{three_js}</script><script>{viewer_js}</script>"
        "</head><body><div id=\"view\"></div><script>"
        f"const bytes=Uint8Array.from(atob(\"{data}\"),c=>c.charCodeAt(0));"
        "FlashModelView.show(document.getElementById('view'),"
        f"bytes.buffer,{json.dumps(suffix)},{{light:true,still:true}})"
        ".then(()=>{document.title='ready'},"
        "e=>{document.title='failed';console.error(String(e))});"
        "</script></body></html>"
    )
