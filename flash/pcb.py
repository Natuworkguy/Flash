"""Printed circuit boards the agent designs: from a list of parts and
what each pin connects to, a finished board.

The model says what goes on the board (each part's package and which
net each of its pins is on) and the board's shape; this does the rest:

- footprints, made from the package's name ("0402", "SOT-23-5",
  "QFN-48-6x6", "WLCSP-49-0.4", "USB-C", "XTAL-3225", "MIC-PDM", ...),
  or pads the model lays out itself;
- placement of every part not given a place, inside the outline, near
  what it connects to;
- routing: each net found a path, on any copper layer, with vias, kept
  clear of every other net; ground on a plane or a pour;
- design rule checks on the result, measured exactly, not on the grid;
- the files a board house takes: Gerbers, a drill file, a bill of
  materials and a pick-and-place list, zipped; a KiCad board to open and
  go on with; and a 3D model of it.

Pure Python, as model3d is: a board of a few hundred pads routes in
seconds without numpy. Millimetres throughout, y up, as KiCad shows it
(its files are written y down).
"""

from __future__ import annotations

import csv
import heapq
import io
import json
import math
import re
import time
import zipfile
from array import array
from dataclasses import dataclass, field
from typing import Any, Optional

SUFFIX = ".fpcb"
VERSION = 1

# What a board house makes without an upcharge, at the fine end.
DEFAULT_RULES = {
    "trace": 0.1,         # trace width
    "clearance": 0.1,     # copper to copper, between nets
    "via": 0.4,           # via pad
    "drill": 0.2,         # via drill
    "edge": 0.25,         # copper to the board's edge
    "power": 0.25,        # trace width of power nets
    "mask": 0.05,         # solder mask opening around each pad
}
MIN_RULES = {"trace": 0.075, "clearance": 0.075, "via": 0.25, "drill": 0.1,
             "edge": 0.1, "power": 0.075, "mask": 0.0}
LAYER_COUNTS = (1, 2, 4, 6)
MAX_PARTS = 400
MAX_PADS = 4000
MAX_CELLS = 450_000          # routing grid cells on each layer
NECK_MM = 0.6                # how near its pads a wide trace narrows
ROUTE_SECONDS = 75.0         # the most routing may take, all nets
MASK_COLORS = {
    "green": "#1f6b3a", "black": "#141414", "matte-black": "#1b1c1e",
    "blue": "#1b3f8f", "red": "#8f1d1d", "white": "#e8e8e4",
    "purple": "#4b2a7a", "yellow": "#c9a300",
}
POWER_RE = re.compile(
    r"^(?:\+?\d+V\d*|V(?:BAT|BUS|SYS|CC|DD|IN|OUT|REG|CORE)\w*|PWR\w*|"
    r"3V3|5V|1V8|VBATT?)$", re.IGNORECASE)
GROUND_RE = re.compile(r"^(?:A?GND\w*|VSS|0V)$", re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-z0-9_+\-./#~]{1,40}$")


class PcbError(ValueError):
    """A board that cannot be made as asked, and why."""


# --- Footprints ------------------------------------------------------------


@dataclass
class PadSpec:
    """A pad in its part's own frame, before the part is placed."""

    name: str
    x: float
    y: float
    w: float
    h: float
    shape: str = "rect"     # rect, round, oval
    drill: float = 0.0      # through-hole when > 0
    plated: bool = True


@dataclass
class Footprint:
    package: str
    pads: list[PadSpec]
    body: tuple[float, float, float]     # width, length, height (mm)
    kind: str = "ic"                     # what it looks like in 3D
    keepout: float = 0.0                 # copper kept this far clear


# Chip passives by imperial code: body length, width, height; pad
# length, width, and the distance between pad centres.
CHIPS = {
    "01005": (0.4, 0.2, 0.13, 0.2, 0.23, 0.36),
    "0201": (0.6, 0.3, 0.3, 0.3, 0.36, 0.6),
    "0402": (1.0, 0.5, 0.35, 0.54, 0.64, 1.02),
    "0603": (1.6, 0.8, 0.45, 0.8, 0.95, 1.65),
    "0805": (2.0, 1.25, 0.6, 1.0, 1.45, 1.9),
    "1206": (3.2, 1.6, 0.6, 1.15, 1.8, 2.95),
    "1210": (3.2, 2.5, 0.6, 1.15, 2.7, 2.95),
    "2010": (5.0, 2.5, 0.6, 1.2, 2.7, 4.6),
    "2512": (6.3, 3.2, 0.6, 1.4, 3.4, 5.9),
    # Power inductors.
    "0806": (2.0, 1.6, 1.0, 0.7, 1.8, 1.6),
    "1008": (2.5, 2.0, 1.2, 0.8, 2.2, 2.0),
}
# Metric chip codes inductors and crystals use, to the imperial.
METRIC_CHIPS = {"0402M": "01005", "0603M": "0201", "1005": "0402",
                "1608": "0603", "2012": "0805", "3216": "1206",
                "3225": "1210", "2016": "0806", "2520": "1008"}
BALL_ROWS = "ABCDEFGHJKLMNPRTUVWY"


def _chip(code: str, kind: str = "passive") -> Footprint:
    length, width, height, pad_l, pad_w, span = CHIPS[code]
    return Footprint(code, [
        PadSpec("1", -span / 2, 0, pad_l, pad_w),
        PadSpec("2", span / 2, 0, pad_l, pad_w),
    ], (length, width, height), kind)


def _two_rows(count: int, pitch: float, span: float, pad_l: float,
              pad_w: float) -> list[PadSpec]:
    """Gull-wing or no-lead pads in two rows: 1 at the top left, down
    the left, then up the right, as every datasheet numbers them."""

    side = count // 2
    top = (side - 1) * pitch / 2
    pads = [PadSpec(str(i + 1), -span / 2, top - i * pitch, pad_l, pad_w)
            for i in range(side)]
    pads += [PadSpec(str(side + i + 1), span / 2, -top + i * pitch, pad_l,
                     pad_w) for i in range(side)]
    return pads


def _quad(count: int, width: float, height: float, pitch: float,
          pad_l: float, pad_w: float, toe: float) -> list[PadSpec]:
    """Pads on four sides, counter-clockwise from 1 at the top of the
    left side, as QFNs number them. COUNT splits between the sides in
    proportion to their lengths."""

    across = round(count * height / (2 * (width + height)))
    along = count // 2 - across
    pads: list[PadSpec] = []
    n = 1

    def row(k, fixed, start, step, vertical):
        nonlocal n
        for i in range(k):
            at = start + i * step
            x, y = (fixed, at) if vertical else (at, fixed)
            w, h = (pad_l, pad_w) if vertical else (pad_w, pad_l)
            pads.append(PadSpec(str(n), x, y, w, h))
            n += 1

    left = -width / 2 + pad_l / 2 - toe
    bottom = -height / 2 + pad_l / 2 - toe
    row(across, left, (across - 1) * pitch / 2, -pitch, True)
    row(along, bottom, -(along - 1) * pitch / 2, pitch, False)
    row(across, -left, -(across - 1) * pitch / 2, pitch, True)
    row(along, -bottom, (along - 1) * pitch / 2, -pitch, False)
    return pads


def _pad_length(pitch: float, lga: bool = False) -> float:
    if lga:
        return 0.5 if pitch >= 0.5 else 0.4
    return 0.875 if pitch >= 0.5 else 0.75 if pitch >= 0.4 else 0.65


def _fit_pitch(per_side: int, side: float, four_sides: bool = True,
               lga: bool = False) -> float:
    """The widest standard pitch whose row of pads stays clear of the
    pads on the sides next to it (0.12 mm), as real parts do."""

    toe = 0.0 if lga else 0.375
    for pitch in (0.8, 0.65, 0.5, 0.4, 0.35, 0.3):
        reach = (per_side - 1) * pitch / 2 + pitch * 0.25
        room = side / 2 + toe - _pad_length(pitch, lga) - 0.12 \
            if four_sides else side / 2 - 0.1
        if reach <= room:
            return pitch
    raise PcbError(f"{per_side} pins a side do not fit along {side} mm")


def _overlapping(pads: list[PadSpec]) -> Optional[tuple[str, str]]:
    for n, a in enumerate(pads):
        for b in pads[n + 1:]:
            if a.name == b.name:
                continue
            if abs(a.x - b.x) < (a.w + b.w) / 2 - 1e-6 and \
                    abs(a.y - b.y) < (a.h + b.h) / 2 - 1e-6:
                return a.name, b.name
    return None


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]


PACKAGE_HELP = (
    "chip passives 01005, 0201, 0402, 0603, 0805, 1206, 1210, 2010, 2512 "
    "(or LED-0603 and the like); SOT-23, SOT-23-5, SOT-23-6, SC-70-6, "
    "SOT-563, SOD-123, SOD-323, SOD-523; SOIC-n, TSSOP-n, MSOP-n, SSOP-n; "
    "QFN-n-WxH[-pitch] and DFN-n-WxH[-pitch] (with an exposed pad EP), "
    "LGA-n-WxH[-pitch]; BGA-RxC-pitch and WLCSP-n-pitch (balls A1, A2, "
    "...); USB-C; XTAL-3225, XTAL-2016, XTAL-2012 (32 kHz, two pads); "
    "MIC-PDM (pads VDD, GND, SEL, CLK, DATA); ANT-3216 (chip antenna, "
    "keeps copper clear); FPC-n[-pitch]; HDR-1xn[-pitch]; TACT-WxH; "
    "PAD-WxH and PAD-ROUND-D (one pad: battery, speaker, pogo contact); "
    "TESTPAD-D; TH-D-DRILL; HOLE-D (mounting hole)")


def footprint(package: str) -> Footprint:
    """The footprint a package name stands for. Raises PcbError naming
    what packages there are when it is not one, or when its pads would
    overlap (too fine a pitch asked for, say)."""

    made = _footprint(package)
    clash = _overlapping(made.pads)
    if clash:
        raise PcbError(f"{package}: pads {clash[0]} and {clash[1]} would "
                       "overlap; give a finer pitch or a bigger body")
    return made


def _footprint(package: str) -> Footprint:
    raw = str(package or "").strip()
    name = raw.upper().replace("_", "-").replace(" ", "")
    nums = _numbers(name)

    if name.startswith("LED-"):
        return _led(raw, name)
    chip = re.match(r"^(?:[RCLDF]|CAP|RES|IND|FB)?-?(\d{4,5}M?)$", name)
    if chip:
        code = METRIC_CHIPS.get(chip.group(1), chip.group(1))
        if code in CHIPS:
            found = _chip(code)
            found.package = raw
            return found

    simple = {
        "SOT-23": ([PadSpec("1", -1.1, 0.95, 1.0, 0.6),
                    PadSpec("2", -1.1, -0.95, 1.0, 0.6),
                    PadSpec("3", 1.1, 0, 1.0, 0.6)], (1.6, 2.9, 1.1)),
        "SOD-123": ([PadSpec("1", -1.65, 0, 0.9, 1.2),
                     PadSpec("2", 1.65, 0, 0.9, 1.2)], (2.7, 1.6, 1.1)),
        "SOD-323": ([PadSpec("1", -1.15, 0, 0.6, 0.45),
                     PadSpec("2", 1.15, 0, 0.6, 0.45)], (1.7, 1.25, 0.9)),
        "SOD-523": ([PadSpec("1", -0.7, 0, 0.5, 0.4),
                     PadSpec("2", 0.7, 0, 0.5, 0.4)], (1.2, 0.8, 0.6)),
    }
    if name in simple:
        pads, body = simple[name]
        return Footprint(raw, pads, body,
                         "diode" if name.startswith("SOD") else "ic")
    if name in ("SOT-23-5", "SOT-23-6", "SOT-25", "SOT-26"):
        count = 5 if name in ("SOT-23-5", "SOT-25") else 6
        pads = _two_rows(6, 0.95, 2.2, 1.0, 0.6)
        if count == 5:
            pads = [p for p in pads if p.name != "5"]
            for p in pads:
                if p.name == "6":
                    p.name = "5"
        return Footprint(raw, pads, (1.6, 2.9, 1.1))
    if name in ("SC-70-6", "SOT-363", "SC-88"):
        return Footprint(raw, _two_rows(6, 0.65, 1.9, 0.8, 0.4),
                         (1.25, 2.0, 0.9))
    if name in ("SOT-563", "SOT-666"):
        return Footprint(raw, _two_rows(6, 0.5, 1.4, 0.55, 0.3),
                         (1.2, 1.6, 0.55))

    gull = re.match(r"^(SOIC|TSSOP|MSOP|SSOP)-(\d+)$", name)
    if gull:
        family, count = gull.group(1), int(gull.group(2))
        if count % 2 or not 4 <= count <= 64:
            raise PcbError(f"{raw}: an even number of pins, 4 to 64")
        pitch, span, pad_l, pad_w, width = {
            "SOIC": (1.27, 5.4, 1.55, 0.6, 3.9),
            "TSSOP": (0.65, 5.8, 1.5, 0.45, 4.4),
            "MSOP": (0.5, 4.4, 1.4, 0.3, 3.0),
            "SSOP": (0.65, 7.2, 1.75, 0.45, 5.3),
        }[family]
        length = (count // 2) * pitch + 0.5
        return Footprint(raw, _two_rows(count, pitch, span, pad_l, pad_w),
                         (width, length, 1.2))

    nolead = re.match(
        r"^(QFN|DFN|SON|LGA)-(\d+)-(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)"
        r"(?:-(\d?\.\d+))?(-NOEP)?$", name)
    if nolead:
        family = nolead.group(1)
        count = int(nolead.group(2))
        width, height = float(nolead.group(3)), float(nolead.group(4))
        if not 4 <= count <= 128 or not 1 <= width <= 14 \
                or not 1 <= height <= 14:
            raise PcbError(f"{raw}: 4 to 128 pins, 1 to 14 mm a side")
        two_sides = family in ("DFN", "SON")
        per_side = count // 2 if two_sides else math.ceil(count / 4)
        pitch = float(nolead.group(5)) if nolead.group(5) else \
            _fit_pitch(per_side, min(width, height), not two_sides,
                       family == "LGA")
        pad_l = _pad_length(pitch, family == "LGA")
        pad_w = round(pitch * 0.5, 3)
        if two_sides:
            pads = _two_rows(count, pitch, width - pad_l + 0.75, pad_l,
                             pad_w)
        else:
            pads = _quad(count, width, height, pitch, pad_l, pad_w,
                         0.375 if family != "LGA" else 0.0)
        if family != "LGA" and not nolead.group(6):
            inset = pad_l - 0.375 + 0.3
            ep_w = width - 2 * inset if not two_sides else \
                width - 2 * inset
            ep_h = height - 2 * inset if not two_sides else \
                max(pitch, (count // 2 - 1) * pitch)
            if ep_w > 0.4 and ep_h > 0.4:
                pads.append(PadSpec("EP", 0, 0, round(ep_w, 3),
                                    round(ep_h, 3)))
        return Footprint(raw, pads, (width, height, 0.85 if
                                     family != "LGA" else 1.0))

    grid = re.match(r"^(BGA|WLCSP|CSP)-(\d+)(?:X(\d+))?-(\d?\.\d+)$", name)
    if grid:
        first, second = int(grid.group(2)), grid.group(3)
        pitch = float(grid.group(4))
        if second:
            rows, cols = first, int(second)
        else:
            rows = math.ceil(math.sqrt(first))
            cols = math.ceil(first / rows)
        if rows > len(BALL_ROWS) or cols > 40 or not 0.3 <= pitch <= 1.27:
            raise PcbError(f"{raw}: up to {len(BALL_ROWS)} rows of 40, "
                           "at a pitch of 0.3 to 1.27 mm")
        ball = round(pitch * 0.55, 3)
        pads = []
        total = first if not second else rows * cols
        for r in range(rows):
            for c in range(cols):
                if len(pads) >= total:
                    break
                pads.append(PadSpec(
                    f"{BALL_ROWS[r]}{c + 1}",
                    (c - (cols - 1) / 2) * pitch,
                    ((rows - 1) / 2 - r) * pitch, ball, ball, "round"))
        return Footprint(raw, pads, (cols * pitch + 0.3, rows * pitch + 0.3,
                                     0.6 if grid.group(1) != "BGA" else 1.2))

    if name in ("USB-C", "USB-C-16", "USBC"):
        signal = [("B8", -1.75), ("A5", -1.25), ("B7", -0.75),
                  ("A6", -0.25), ("A7", 0.25), ("B6", 0.75), ("A8", 1.25),
                  ("B5", 1.75)]
        pads = [PadSpec("GND", -3.2, 2.4, 0.6, 1.15),
                PadSpec("VBUS", -2.4, 2.4, 0.6, 1.15)]
        pads += [PadSpec(n, x, 2.4, 0.3, 1.15) for n, x in signal]
        pads += [PadSpec("VBUS", 2.4, 2.4, 0.6, 1.15),
                 PadSpec("GND", 3.2, 2.4, 0.6, 1.15)]
        for x in (-4.32, 4.32):
            for y in (1.85, -2.0):
                pads.append(PadSpec("SH", x, y, 1.0, 1.6, "oval", 0.6))
        return Footprint(raw, pads, (8.94, 7.3, 3.2), "usb")

    crystals = {
        "XTAL-3225": ([("1", -1.1, -0.8), ("2", 1.1, -0.8), ("3", 1.1, 0.8),
                       ("4", -1.1, 0.8)], 1.4, 1.15, (3.2, 2.5, 0.8)),
        "XTAL-2520": ([("1", -0.85, -0.65), ("2", 0.85, -0.65),
                       ("3", 0.85, 0.65), ("4", -0.85, 0.65)], 1.1, 1.0,
                      (2.5, 2.0, 0.6)),
        "XTAL-2016": ([("1", -0.7, -0.55), ("2", 0.7, -0.55),
                       ("3", 0.7, 0.55), ("4", -0.7, 0.55)], 0.9, 0.8,
                      (2.0, 1.6, 0.5)),
        "XTAL-2012": ([("1", -0.75, 0), ("2", 0.75, 0)], 0.7, 1.1,
                      (2.0, 1.2, 0.6)),
        "XTAL-1610": ([("1", -0.6, 0), ("2", 0.6, 0)], 0.5, 0.9,
                      (1.6, 1.0, 0.45)),
    }
    if name in crystals:
        spots, w, h, body = crystals[name]
        return Footprint(raw, [PadSpec(n, x, y, w, h) for n, x, y in spots],
                         body, "crystal")

    if name in ("MIC-PDM", "MIC-LGA", "MIC"):
        return Footprint(raw, [
            PadSpec("VDD", -1.0, -0.8, 0.5, 0.6),
            PadSpec("SEL", 0.0, -0.8, 0.5, 0.6),
            PadSpec("CLK", 1.0, -0.8, 0.5, 0.6),
            PadSpec("DATA", -1.0, 0.8, 0.5, 0.6),
            PadSpec("GND", 1.0, 0.8, 0.5, 0.6),
            PadSpec("PORT", 0.0, 0.8, 0.5, 0.5, "round", 0.5, False),
        ], (3.5, 2.65, 0.98), "can")

    if name.startswith("ANT"):
        size = name.split("-")[1] if "-" in name else "3216"
        found = _chip(METRIC_CHIPS.get(size, size)
                      if METRIC_CHIPS.get(size, size) in CHIPS else "1206",
                      "antenna")
        found.package = raw
        found.pads[0].name, found.pads[1].name = "FEED", "NC"
        found.keepout = 1.0
        return found

    fpc = re.match(r"^FPC-(\d+)(?:-(\d?\.\d+))?$", name)
    if fpc:
        count = int(fpc.group(1))
        pitch = float(fpc.group(2) or 0.5)
        if not 2 <= count <= 80:
            raise PcbError(f"{raw}: 2 to 80 contacts")
        span = (count - 1) * pitch
        pads = [PadSpec(str(i + 1), -span / 2 + i * pitch, 1.0,
                        round(pitch * 0.6, 3), 1.0) for i in range(count)]
        pads += [PadSpec("MP", -(span / 2 + 1.6), -0.9, 1.0, 1.6),
                 PadSpec("MP", span / 2 + 1.6, -0.9, 1.0, 1.6)]
        return Footprint(raw, pads, (span + 4.4, 3.6, 1.0), "connector")

    header = re.match(r"^HDR-1X(\d+)(?:-(\d?\.\d+))?$", name)
    if header:
        count = int(header.group(1))
        pitch = float(header.group(2) or 2.54)
        if not 1 <= count <= 40:
            raise PcbError(f"{raw}: 1 to 40 pins")
        size, drill = (1.7, 1.0) if pitch >= 2.0 else (1.0, 0.6)
        pads = [PadSpec(str(i + 1), (i - (count - 1) / 2) * pitch, 0, size,
                        size, "rect" if i == 0 else "round", drill)
                for i in range(count)]
        return Footprint(raw, pads, (count * pitch, pitch, 8.5), "header")

    tact = re.match(r"^TACT-(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", name)
    if tact:
        w, h = float(tact.group(1)), float(tact.group(2))
        pads = [PadSpec(n, x * (w / 2 + 0.3), y * (h / 2 - 0.5), 1.0, 0.7)
                for n, x, y in (("1", -1, 1), ("2", 1, 1), ("3", -1, -1),
                                ("4", 1, -1))]
        return Footprint(raw, pads, (w, h, 1.5), "switch")

    if name.startswith("PAD-ROUND-") or name.startswith("TESTPAD-"):
        d = nums[-1] if nums else 1.0
        return Footprint(raw, [PadSpec("1", 0, 0, d, d, "round")],
                         (d, d, 0.0), "pad")
    if name.startswith("PAD-") and len(nums) == 2:
        w, h = nums
        return Footprint(raw, [PadSpec("1", 0, 0, w, h)], (w, h, 0.0),
                         "pad")
    if name.startswith("TH-") and len(nums) == 2:
        d, drill = nums
        return Footprint(raw, [PadSpec("1", 0, 0, d, d, "round", drill)],
                         (d, d, 0.0), "pad")
    if name.startswith("HOLE-") and nums:
        d = nums[0]
        return Footprint(raw, [PadSpec("H", 0, 0, d, d, "round", d, False)],
                         (d, d, 0.0), "hole")

    raise PcbError(f"no package called {raw!r}. There are: {PACKAGE_HELP}")


def _led(raw: str, name: str) -> Footprint:
    code = name.split("-", 1)[1]
    code = METRIC_CHIPS.get(code, code)
    if code not in CHIPS:
        raise PcbError(f"no package called {raw!r}. There are: "
                       f"{PACKAGE_HELP}")
    found = _chip(code, "led")
    found.package = raw
    found.pads[0].name, found.pads[1].name = "K", "A"
    return found


def custom_footprint(spec: dict, where: str) -> Footprint:
    """A footprint the model lays out itself: "pads" of {name, x, y, w,
    h, shape, drill}, and maybe "body": [w, l, height]."""

    pads = []
    for n, pad in enumerate(spec.get("pads") or []):
        if not isinstance(pad, dict):
            raise PcbError(f"{where}: pads[{n}] has to be an object")
        try:
            w = float(pad.get("w", pad.get("d", 0)))
            h = float(pad.get("h", w))
            made = PadSpec(str(pad.get("name", n + 1)), float(pad["x"]),
                           float(pad["y"]), w, h,
                           str(pad.get("shape", "rect")),
                           float(pad.get("drill", 0)))
        except (KeyError, TypeError, ValueError):
            raise PcbError(f"{where}: pads[{n}] needs numbers x, y and w "
                           "(and h)") from None
        if made.w <= 0 or made.h <= 0 or made.shape not in (
                "rect", "round", "oval"):
            raise PcbError(f"{where}: pads[{n}] needs a size above 0 and a "
                           "shape of rect, round or oval")
        pads.append(made)
    if not pads:
        raise PcbError(f"{where}: give a package, or pads of its own")
    xs = [p.x - p.w / 2 for p in pads] + [p.x + p.w / 2 for p in pads]
    ys = [p.y - p.h / 2 for p in pads] + [p.y + p.h / 2 for p in pads]
    body = spec.get("body") or [max(xs) - min(xs), max(ys) - min(ys), 1.0]
    try:
        body = tuple(float(v) for v in body[:3])
    except (TypeError, ValueError):
        raise PcbError(f"{where}: body is [width, length, height]") \
            from None
    return Footprint("custom", pads, body + (1.0,) * (3 - len(body)))


# --- The board -------------------------------------------------------------


@dataclass
class Pad:
    part: str
    name: str
    x: float
    y: float
    w: float
    h: float
    shape: str
    layers: tuple[int, ...]
    drill: float = 0.0
    plated: bool = True
    net: str = ""


@dataclass
class Part:
    ref: str
    package: str
    value: str
    x: float
    y: float
    rotation: int
    side: str                      # top or bottom
    print_: Footprint
    pins: dict[str, str]           # pin name -> net
    fixed: bool = False
    pads: list[Pad] = field(default_factory=list)
    note: str = ""
    edge: bool = False
    side_given: bool = False

    def box(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        """Its courtyard: body and pads, turned and placed, plus MARGIN."""

        xs, ys = [], []
        bw, bl = self.print_.body[0] / 2, self.print_.body[1] / 2
        for x, y in ((-bw, -bl), (bw, bl)):
            px, py = _turn(x, y, self.rotation, self.side)
            xs.append(px)
            ys.append(py)
        for p in self.print_.pads:
            for sx in (-1, 1):
                for sy in (-1, 1):
                    px, py = _turn(p.x + sx * p.w / 2, p.y + sy * p.h / 2,
                                   self.rotation, self.side)
                    xs.append(px)
                    ys.append(py)
        return (self.x + min(xs) - margin, self.y + min(ys) - margin,
                self.x + max(xs) + margin, self.y + max(ys) + margin)


Point = tuple[float, float]


def _turn(x: float, y: float, rotation: int, side: str) -> Point:
    """A point in a part's frame, turned as the part is and mirrored
    when it is on the bottom (seen from the top)."""

    if side == "bottom":
        x = -x
    for _ in range((rotation // 90) % 4):
        x, y = -y, x
    return x, y


def _place_pads(part: Part, layers: int) -> None:
    turned = (part.rotation // 90) % 2 == 1
    copper = (0,) if part.side == "top" else (layers - 1,)
    part.pads = []
    for spec in part.print_.pads:
        dx, dy = _turn(spec.x, spec.y, part.rotation, part.side)
        w, h = (spec.h, spec.w) if turned else (spec.w, spec.h)
        through = spec.drill > 0
        part.pads.append(Pad(
            part.ref, spec.name, round(part.x + dx, 4),
            round(part.y + dy, 4), w, h, spec.shape,
            tuple(range(layers)) if through and spec.plated else
            (() if through else copper),
            spec.drill, spec.plated, part.pins.get(spec.name, "")))


def outline_points(board: dict) -> list[tuple[float, float]]:
    """The board's edge, as a closed polygon (counter-clockwise), from
    its shape: rect, rounded, stadium, circle, or polygon."""

    shape = str(board.get("shape") or "rounded").lower()
    if shape == "polygon":
        try:
            points = [(float(x), float(y)) for x, y in board["points"]]
        except (KeyError, TypeError, ValueError):
            raise PcbError(
                'board: a polygon needs "points": [[x, y], ...] in mm') \
                from None
        if len(points) < 3:
            raise PcbError("board: a polygon needs 3 or more points")
        if _area(points) < 0:
            points.reverse()
        return points
    if shape == "circle":
        d = _size(board, "diameter", board.get("width", 10))
        r = d / 2
        return [(r + r * math.cos(2 * math.pi * k / 72),
                 r + r * math.sin(2 * math.pi * k / 72)) for k in range(72)]
    w = _size(board, "width", 30)
    h = _size(board, "height", 20)
    if shape == "rect":
        return [(0, 0), (w, 0), (w, h), (0, h)]
    if shape == "stadium":
        r = min(w, h) / 2
    elif shape == "rounded":
        r = min(float(board.get("radius", min(w, h) * 0.15)), min(w, h) / 2)
    else:
        raise PcbError("board: shape is rect, rounded, stadium, circle or "
                       "polygon")
    points: list[tuple[float, float]] = []
    corners = ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90),
               (r, r, 180))
    for cx, cy, start in corners:
        for k in range(10):
            a = math.radians(start + 90 * k / 9)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def _size(board: dict, key: str, fallback: Any) -> float:
    try:
        value = float(board.get(key, fallback))
    except (TypeError, ValueError):
        raise PcbError(f"board: {key} has to be a number of mm") from None
    if not 2 <= value <= 500:
        raise PcbError(f"board: {key} has to be 2 to 500 mm")
    return value


def _area(points: list[tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in
               zip(points, points[1:] + points[:1])) / 2


def _inside(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    found = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and \
                x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            found = not found
        j = i
    return found


def _seg_point(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - x1) * dx + (
        py - y1) * dy) / length))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _edge_distance(x: float, y: float, poly) -> float:
    return min(_seg_point(x, y, *poly[i], *poly[(i + 1) % len(poly)])
               for i in range(len(poly)))


# --- Reading what the model asked for --------------------------------------


@dataclass
class Design:
    name: str
    outline: list[tuple[float, float]]
    layers: int
    thickness: float
    color: str
    rules: dict[str, float]
    parts: list[Part]
    widths: dict[str, float]       # net -> trace width
    plane: str                     # the net on the inner plane, or ""
    pours: list[int]               # outer layers poured with the plane net
    ground: str
    notes: list[str] = field(default_factory=list)

    @property
    def nets(self) -> list[str]:
        found = {p.net for part in self.parts for p in part.pads if p.net}
        return sorted(found)

    def pads(self) -> list[Pad]:
        return [p for part in self.parts for p in part.pads]


def read(spec: Any) -> Design:
    """The design SPEC asks for, checked, with every footprint made."""

    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as exc:
            raise PcbError(f"the design is not JSON: {exc}") from None
    if not isinstance(spec, dict):
        raise PcbError("the design is an object: {board, parts, ...}")
    board = spec.get("board") or {}
    if not isinstance(board, dict):
        raise PcbError("board has to be an object")
    layers = int(board.get("layers", 2))
    if layers not in LAYER_COUNTS:
        raise PcbError(f"board: layers is one of {LAYER_COUNTS}")
    thickness = float(board.get("thickness", 1.6 if layers <= 2 else 0.8))
    if not 0.2 <= thickness <= 3.2:
        raise PcbError("board: thickness is 0.2 to 3.2 mm")
    color = str(board.get("color", "black")).lower()
    if color not in MASK_COLORS and not re.match(r"^#[0-9a-f]{6}$", color):
        raise PcbError("board: color is one of "
                       + ", ".join(MASK_COLORS) + ", or #rrggbb")
    outline = outline_points(board)

    rules = dict(DEFAULT_RULES)
    for key, value in (spec.get("rules") or {}).items():
        if key not in rules:
            raise PcbError(f"rules: {key!r} is not a rule; they are "
                           + ", ".join(rules))
        try:
            rules[key] = float(value)
        except (TypeError, ValueError):
            raise PcbError(f"rules: {key} is a number of mm") from None
        if rules[key] < MIN_RULES[key]:
            raise PcbError(f"rules: {key} is at least {MIN_RULES[key]} mm, "
                           "the finest a board house makes")
    if rules["via"] < rules["drill"] + 0.1:
        raise PcbError("rules: a via's pad is at least 0.1 mm wider than "
                       "its drill")

    raw_parts = spec.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise PcbError("parts: list each part, with its package and pins")
    if len(raw_parts) > MAX_PARTS:
        raise PcbError(f"parts: at most {MAX_PARTS}")
    parts: list[Part] = []
    seen: set[str] = set()
    for n, raw in enumerate(raw_parts):
        parts.append(_read_part(raw, n, seen))
    for part in parts:
        _place_pads(part, layers)
    if sum(len(p.pads) for p in parts) > MAX_PADS:
        raise PcbError(f"at most {MAX_PADS} pads on one board")

    widths: dict[str, float] = {}
    nets_given = spec.get("nets") or {}
    if not isinstance(nets_given, dict):
        raise PcbError('nets: {"NAME": {"width": mm}}')
    all_nets = {p.net for part in parts for p in part.pads if p.net}
    for net in all_nets:
        widths[net] = rules["power"] if POWER_RE.match(net) else \
            rules["trace"]
    for net, opts in nets_given.items():
        if net not in all_nets:
            raise PcbError(f"nets: no pin is on {net!r}")
        try:
            widths[net] = max(rules["trace"],
                              float((opts or {}).get("width",
                                                     widths[net])))
        except (TypeError, ValueError, AttributeError):
            raise PcbError(f'nets: {net} is {{"width": mm}}') from None

    ground = str(spec.get("ground") or "")
    if not ground:
        ground = next((n for n in sorted(all_nets) if GROUND_RE.match(n)), "")
    elif ground not in all_nets:
        raise PcbError(f"ground: no pin is on {ground!r}")
    plane = ground if layers >= 4 and ground else ""
    pour_given = spec.get("pour", None)
    if pour_given is None:
        pours = [layers - 1] if layers == 2 and ground else []
    else:
        names = {"top": 0, "bottom": layers - 1}
        pours = sorted({names[p] for p in pour_given if p in names})
        if any(p not in names for p in pour_given):
            raise PcbError('pour: a list of "top" and "bottom"')
    return Design(
        name=str(spec.get("name") or "board").strip()[:80] or "board",
        outline=outline, layers=layers, thickness=thickness, color=color,
        rules=rules, parts=parts, widths=widths, plane=plane, pours=pours,
        ground=ground)


def _read_part(raw: Any, n: int, seen: set[str]) -> Part:
    if not isinstance(raw, dict):
        raise PcbError(f"parts[{n}] has to be an object")
    ref = str(raw.get("ref") or "").strip()
    where = f"parts[{n}]" + (f" ({ref})" if ref else "")
    if not ref or not NAME_RE.match(ref):
        raise PcbError(f"{where}: give it a ref, like U1 or C4")
    if ref in seen:
        raise PcbError(f"{where}: {ref} is used twice")
    seen.add(ref)
    if raw.get("pads"):
        made = custom_footprint(raw, where)
    else:
        try:
            made = footprint(str(raw.get("package") or ""))
        except PcbError as exc:
            raise PcbError(f"{where}: {exc}") from None
    names = list(dict.fromkeys(p.name for p in made.pads))
    pins = raw.get("pins") or {}
    if isinstance(pins, list):
        if len(pins) > len(names):
            raise PcbError(f"{where}: {len(pins)} nets for {len(names)} "
                           f"pins ({', '.join(names)})")
        pins = {names[i]: v for i, v in enumerate(pins) if v}
    if not isinstance(pins, dict):
        raise PcbError(f"{where}: pins is {{pin: net}} or a list of nets")
    clean: dict[str, str] = {}
    for pin, net in pins.items():
        pin = str(pin)
        if pin not in names:
            raise PcbError(f"{where}: no pin {pin!r} on {made.package}; its "
                           f"pins are {', '.join(names)}")
        net = str(net or "").strip()
        if net and not NAME_RE.match(net):
            raise PcbError(f"{where}: {net!r} is not a net name (letters, "
                           "digits and _+-./#~)")
        if net:
            clean[pin] = net
    side = str(raw.get("side", "top")).lower()
    if side not in ("top", "bottom"):
        raise PcbError(f"{where}: side is top or bottom")
    rotation = int(round(float(raw.get("rotation", 0)) / 90) * 90) % 360
    fixed = raw.get("x") is not None and raw.get("y") is not None
    try:
        x = float(raw.get("x", 0))
        y = float(raw.get("y", 0))
    except (TypeError, ValueError):
        raise PcbError(f"{where}: x and y are numbers of mm") from None
    return Part(ref=ref, package=made.package, value=str(
        raw.get("value") or "").strip()[:60], x=x, y=y, rotation=rotation,
        side=side, print_=made, pins=clean, fixed=fixed,
        note=str(raw.get("note") or "").strip()[:120],
        edge=bool(raw.get("edge")), side_given="side" in raw)


# --- Placement -------------------------------------------------------------


FANOUT_MM = 0.4      # room kept round a many-pin part for its traces


def _box_of(part: Part) -> tuple[float, float, float, float]:
    """Where nothing else may sit: its courtyard, an antenna's keepout,
    and round a part with many pins, room for its traces to get out."""

    many = len(part.print_.pads) > 8 and part.print_.kind not in (
        "connector", "header")
    fanout = FANOUT_MM if many else 0.0
    return part.box(0.1 + part.print_.keepout + fanout)


def _through(part: Part) -> bool:
    return any(p.drill > 0 for p in part.print_.pads)


class _Room:
    """Where parts can still go, on each side: a grid of the board, each
    cell taken by the edge or a part, and summed-area tables to ask in
    one step whether a part's courtyard fits around a point."""

    def __init__(self, design: Design) -> None:
        xs = [x for x, _ in design.outline]
        ys = [y for _, y in design.outline]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        self.res = max(0.1, span / 320)
        self.x0, self.y0 = min(xs), min(ys)
        self.w = int(math.ceil((max(xs) - self.x0) / self.res)) + 1
        self.h = int(math.ceil((max(ys) - self.y0) / self.res)) + 1
        # A cell's worth more than the edge rule: a courtyard's corner
        # can sit anywhere in a cell.
        edge = design.rules["edge"] + self.res
        base = bytearray(b"\x01") * (self.w * self.h)
        poly = design.outline
        for j in range(self.h):
            y = self.y0 + (j + 0.5) * self.res
            crossings = sorted(
                x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1])
                if (y1 > y) != (y2 > y))
            for a, b in zip(crossings[::2], crossings[1::2]):
                for i in range(max(0, int((a - self.x0) / self.res)),
                               min(self.w, int((b - self.x0) / self.res))):
                    base[j * self.w + i] = 0
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            i0, j0 = self.cell(min(x1, x2) - edge, min(y1, y2) - edge)
            i1, j1 = self.cell(max(x1, x2) + edge, max(y1, y2) + edge)
            for j in range(max(0, j0), min(self.h, j1 + 1)):
                for i in range(max(0, i0), min(self.w, i1 + 1)):
                    cx = self.x0 + (i + 0.5) * self.res
                    cy = self.y0 + (j + 0.5) * self.res
                    if _seg_point(cx, cy, x1, y1, x2, y2) < edge:
                        base[j * self.w + i] = 1
        self.taken = {"top": bytearray(base), "bottom": bytearray(base)}
        self.sums: dict[str, Optional[list[array]]] = {"top": None,
                                                       "bottom": None}

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return (int((x - self.x0) / self.res), int((y - self.y0) / self.res))

    def take(self, part: Part) -> None:
        x0, y0, x1, y1 = _box_of(part)
        # A hole, or an antenna's keepout, is on both sides.
        sides = ("top", "bottom") if _through(part) or \
            part.print_.keepout else (part.side,)
        i0, j0 = self.cell(x0, y0)
        i1, j1 = self.cell(x1, y1)
        for side in sides:
            grid = self.taken[side]
            for j in range(max(0, j0), min(self.h, j1 + 1)):
                for i in range(max(0, i0), min(self.w, i1 + 1)):
                    grid[j * self.w + i] = 1
            self.sums[side] = None

    def _table(self, side: str) -> list[array]:
        if self.sums[side] is None:
            grid = self.taken[side]
            rows = [array("i", [0]) * (self.w + 1)]
            for j in range(self.h):
                row = array("i", [0]) * (self.w + 1)
                above = rows[-1]
                run = 0
                base = j * self.w
                for i in range(self.w):
                    run += grid[base + i]
                    row[i + 1] = above[i + 1] + run
                rows.append(row)
            self.sums[side] = rows
        return self.sums[side]

    def taken_in(self, side: str, i0: int, j0: int, i1: int,
                 j1: int) -> int:
        """How many cells of the rectangle are taken (or off the grid)."""

        lost = 0
        if i0 < 0 or j0 < 0 or i1 >= self.w or j1 >= self.h:
            lost = (i1 - i0 + 1) * (j1 - j0 + 1)
            i0, j0 = max(0, i0), max(0, j0)
            i1, j1 = min(self.w - 1, i1), min(self.h - 1, j1)
            if i0 > i1 or j0 > j1:
                return lost
        t = self._table(side)
        return lost + (t[j1 + 1][i1 + 1] - t[j0][i1 + 1] - t[j1 + 1][i0]
                       + t[j0][i0])

    def free(self, side: str, i0: int, j0: int, i1: int, j1: int) -> bool:
        return self.taken_in(side, i0, j0, i1, j1) == 0


def _area_of(part: Part) -> float:
    x0, y0, x1, y1 = _box_of(part)
    return (x1 - x0) * (y1 - y0)


def place(design: Design) -> None:
    """Put every part without a place on the board. Tried eight ways,
    the first part each time pushed toward another side of the board to
    leave room for the rest; the way that finds room for the most parts,
    then has the least wire, is kept."""

    loose = [p for p in design.parts if not p.fixed]
    for part in design.parts:
        _place_pads(part, design.layers)
    if not loose:
        return
    best = None
    notes = list(design.notes)
    for way in range(8):
        design.notes = list(notes)
        _place_once(design, loose, way)
        score = (len(design.notes) - len(notes), _wiring(design))
        if best is None or score < best[0]:
            best = (score, [(p.x, p.y, p.rotation, p.side) for p in loose],
                    list(design.notes))
        if score[0] == 0 and way >= 3:
            break
    for part, (x, y, rotation, side) in zip(loose, best[1]):
        part.x, part.y, part.rotation, part.side = x, y, rotation, side
        _place_pads(part, design.layers)
    design.notes = best[2]


def _wiring(design: Design) -> float:
    """How much wire the placement needs, roughly: each net's box."""

    boxes: dict[str, list[float]] = {}
    for part in design.parts:
        for pad in part.pads:
            if pad.net and pad.net not in (design.plane, design.ground):
                b = boxes.setdefault(pad.net, [1e9, 1e9, -1e9, -1e9])
                b[0], b[1] = min(b[0], pad.x), min(b[1], pad.y)
                b[2], b[3] = max(b[2], pad.x), max(b[3], pad.y)
    return sum(b[2] - b[0] + b[3] - b[1] for b in boxes.values())


def _place_once(design: Design, loose: list[Part], way: int) -> None:
    """One placement, one part at a time, the most connected and biggest
    first: each at the free spot, any turn, nearest what it connects to;
    underneath when the top has no room and its side was not given.
    Never over another part or off the board while there is room
    anywhere. WAY picks the side the first part leans to."""

    room = _Room(design)
    placed: list[Part] = []
    for part in design.parts:
        if part.fixed:
            room.take(part)
            placed.append(part)
    xs = [x for x, _ in design.outline]
    ys = [y for _, y in design.outline]
    middle = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    reach = max(max(xs) - min(xs), max(ys) - min(ys))
    angle = way * math.pi / 4
    lean = (middle[0] + math.cos(angle) * reach,
            middle[1] + math.sin(angle) * reach)
    pads_on: dict[str, list[Pad]] = {}

    def remember(part: Part) -> None:
        for pad in part.pads:
            if pad.net and pad.net != design.plane:
                pads_on.setdefault(pad.net, []).append(pad)

    for part in placed:
        remember(part)

    def links(part: Part) -> int:
        nets = {n for n in part.pins.values()
                if n not in (design.plane, design.ground)}
        return sum(1 for n in nets if n in pads_on)

    remaining = sorted(loose, key=lambda p: -_area_of(p))
    while remaining:
        # The most tied to what is down already, then the biggest.
        remaining.sort(key=lambda p: (-links(p), -_area_of(p)))
        part = remaining.pop(0)
        nets = [n for n in set(part.pins.values()) if n in pads_on]
        boxes = []
        for net in nets:
            weight = 0.15 if net == design.ground else 1.0
            px = [q.x for q in pads_on[net]]
            py = [q.y for q in pads_on[net]]
            boxes.append((min(px), min(py), max(px), max(py), weight))
        sides = [part.side]
        if not part.side_given and design.layers >= 2 and \
                not _through(part):
            sides.append("bottom" if part.side == "top" else "top")
        best = None
        through = _through(part) or part.print_.keepout > 0
        for n, side in enumerate(sides):
            for rotation in (0, 90, 180, 270):
                part.side, part.rotation, part.x, part.y = side, rotation, 0, 0
                bx0, by0, bx1, by1 = _box_of(part)
                stride = 2 if room.w * room.h > 40_000 else 1
                for j in range(0, room.h, stride):
                    cy = room.y0 + (j + 0.5) * room.res
                    rj0 = int((cy + by0 - room.y0) / room.res)
                    rj1 = int((cy + by1 - room.y0) / room.res)
                    if rj0 < 0 or rj1 >= room.h:
                        continue
                    for i in range(0, room.w, stride):
                        cx = room.x0 + (i + 0.5) * room.res
                        ri0 = int((cx + bx0 - room.x0) / room.res)
                        ri1 = int((cx + bx1 - room.x0) / room.res)
                        lost = room.taken_in(side, ri0, rj0, ri1, rj1)
                        if through:
                            lost += room.taken_in(
                                "bottom" if side == "top" else "top",
                                ri0, rj0, ri1, rj1)
                        if lost and best is not None and best[0] == 0:
                            continue
                        cost = n * 0.5   # its own side, when it can
                        for x0, y0, x1, y1, weight in boxes:
                            dx = x0 - cx if cx < x0 else cx - x1 \
                                if cx > x1 else 0
                            dy = y0 - cy if cy < y0 else cy - y1 \
                                if cy > y1 else 0
                            cost += (dx + dy) * weight
                        if not boxes:
                            # Nothing to be near yet: toward this way's
                            # side, to leave the rest of the board open.
                            cost += math.hypot(cx - lean[0],
                                               cy - lean[1]) * 0.05
                        if part.print_.keepout:
                            cost += _edge_distance(cx, cy,
                                                   design.outline) * 3
                        # A free spot always beats one that overlaps.
                        key = (1 if lost else 0, lost if lost else cost)
                        if best is None or key < best[0:2]:
                            best = (key[0], key[1], cx, cy, rotation, side)
            if best is not None and best[0] == 0 and n == 0:
                break   # it fits on its own side: it stays there
        if best[0]:
            # No room anywhere: where it overlaps least, and the check
            # says what it is over.
            design.notes.append(f"no room for {part.ref}")
        part.x, part.y, part.rotation, part.side = best[2:]
        part.x, part.y = round(part.x, 3), round(part.y, 3)
        _place_pads(part, design.layers)
        room.take(part)
        remember(part)
        placed.append(part)


# --- Routing ---------------------------------------------------------------

FREE = 0
BLOCKED = -1
OUTSIDE = -2


@dataclass
class Track:
    layer: int
    points: list[tuple[float, float]]
    width: float
    net: str
    order: int = 0


@dataclass
class Via:
    x: float
    y: float
    net: str
    order: int = 0


class Router:
    """A grid router: each net a path across cells on any copper layer,
    cells near another net's copper closed to it, vias where every layer
    has room."""

    def __init__(self, design: Design) -> None:
        self.design = design
        rules = design.rules
        xs = [x for x, _ in design.outline]
        ys = [y for _, y in design.outline]
        self.x0, self.y0 = min(xs) - 0.5, min(ys) - 0.5
        width, height = max(xs) + 0.5 - self.x0, max(ys) + 0.5 - self.y0
        fine = min((self._pitch_of(p) for p in design.parts), default=1.27)
        pitch = 0.05 if fine <= 0.5 else 0.0625 if fine <= 0.65 else 0.1
        pitch = max(pitch, math.sqrt(width * height / MAX_CELLS))
        self.pitch = round(pitch, 4)
        self.w = int(math.ceil(width / self.pitch)) + 1
        self.h = int(math.ceil(height / self.pitch)) + 1
        self.layers = design.layers
        self.net_ids = {n: i + 1 for i, n in enumerate(design.nets)}
        self.names = {i: n for n, i in self.net_ids.items()}
        self.tracks: dict[str, list[Track]] = {}
        self.vias: dict[str, list[Via]] = {}
        self.failed: list[str] = []
        self.order = 0
        self.base: list[array] = []
        self.grid: list[array] = []
        # Enough for a diagonal step past a corner: the check measures
        # the real copper, not the grid.
        self.margin = self.pitch * 0.75
        self.default_w = rules["trace"]
        self.terminals: dict[str, list[set[int]]] = {}
        self._build_base()

    @staticmethod
    def _pitch_of(part: Part) -> float:
        pads = part.print_.pads
        if len(pads) < 2:
            return 1.27
        best = 9.0
        for i, a in enumerate(pads):
            for b in pads[i + 1:]:
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d > 0:
                    best = min(best, d)
        return best

    # Cells and millimetres.
    def cell(self, x: float, y: float) -> tuple[int, int]:
        return (int(round((x - self.x0) / self.pitch)),
                int(round((y - self.y0) / self.pitch)))

    def at(self, i: int, j: int) -> tuple[float, float]:
        return (round(self.x0 + i * self.pitch, 4),
                round(self.y0 + j * self.pitch, 4))

    def _cells_near_rect(self, x0, y0, x1, y1, grow):
        """Every cell whose centre lies within GROW of the rectangle."""

        i0, j0 = self.cell(x0 - grow, y0 - grow)
        i1, j1 = self.cell(x1 + grow, y1 + grow)
        for j in range(max(0, j0 - 1), min(self.h, j1 + 2)):
            cy = self.y0 + j * self.pitch
            dy = max(y0 - cy, 0.0, cy - y1)
            for i in range(max(0, i0 - 1), min(self.w, i1 + 2)):
                cx = self.x0 + i * self.pitch
                dx = max(x0 - cx, 0.0, cx - x1)
                if dx * dx + dy * dy <= grow * grow:
                    yield j * self.w + i

    def _cells_near_disc(self, x, y, r):
        i0, j0 = self.cell(x - r, y - r)
        i1, j1 = self.cell(x + r, y + r)
        for j in range(max(0, j0), min(self.h, j1 + 1)):
            cy = self.y0 + j * self.pitch
            for i in range(max(0, i0), min(self.w, i1 + 1)):
                cx = self.x0 + i * self.pitch
                if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                    yield j * self.w + i

    def _claim(self, grid, k, net_id):
        here = grid[k]
        if here == FREE:
            grid[k] = net_id
        elif here != net_id and here > 0:
            grid[k] = BLOCKED

    def _build_base(self) -> None:
        design, rules = self.design, self.design.rules
        size = self.w * self.h
        outside = array("i", [OUTSIDE]) * size
        edge = rules["edge"] + self.default_w / 2
        poly = design.outline
        for j in range(self.h):
            y = self.y0 + j * self.pitch
            crossings = []
            for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
                if (y1 > y) != (y2 > y):
                    crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            crossings.sort()
            for a, b in zip(crossings[::2], crossings[1::2]):
                i0 = max(0, int(math.ceil((a - self.x0) / self.pitch)))
                i1 = min(self.w - 1, int(math.floor((b - self.x0) /
                                                    self.pitch)))
                for i in range(i0, i1 + 1):
                    outside[j * self.w + i] = FREE
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            i0, j0 = self.cell(min(x1, x2) - edge, min(y1, y2) - edge)
            i1, j1 = self.cell(max(x1, x2) + edge, max(y1, y2) + edge)
            for j in range(max(0, j0), min(self.h, j1 + 1)):
                for i in range(max(0, i0), min(self.w, i1 + 1)):
                    k = j * self.w + i
                    if outside[k] == FREE:
                        x, y = self.at(i, j)
                        if _seg_point(x, y, x1, y1, x2, y2) < edge:
                            outside[k] = OUTSIDE
        self.base = [array("i", outside) for _ in range(self.layers)]
        grow = rules["clearance"] + self.default_w / 2 + self.margin
        cores: list[tuple[int, int, set[int]]] = []
        for pad in design.pads():
            net_id = self.net_ids.get(pad.net, BLOCKED) if pad.net else \
                BLOCKED
            x0, y0 = pad.x - pad.w / 2, pad.y - pad.h / 2
            x1, y1 = pad.x + pad.w / 2, pad.y + pad.h / 2
            layers = pad.layers if pad.layers else tuple(range(self.layers))
            for layer in layers:
                for k in self._cells_near_rect(x0, y0, x1, y1, grow):
                    if self.base[layer][k] != OUTSIDE:
                        self._claim(self.base[layer], k, net_id)
            if pad.layers and pad.net:
                inner = set()
                for k in self._cells_near_rect(
                        x0 + min(pad.w / 2, self.pitch * 0.4),
                        y0 + min(pad.h / 2, self.pitch * 0.4),
                        x1 - min(pad.w / 2, self.pitch * 0.4),
                        y1 - min(pad.h / 2, self.pitch * 0.4),
                        self.pitch * 0.5):
                    inner.add(k)
                if not inner:
                    inner.add(self.cell(pad.x, pad.y)[1] * self.w +
                              self.cell(pad.x, pad.y)[0])
                for layer in pad.layers:
                    cores.append((layer, net_id, inner))
                self.terminals.setdefault(pad.net, []).append(
                    {layer * self.w * self.h + k for layer in pad.layers
                     for k in inner})
        # Each pad's own cells are its, whatever is near.
        for layer, net_id, inner in cores:
            for k in inner:
                self.base[layer][k] = net_id
        # The plane layer belongs to its net, but for the edge.
        if design.plane:
            plane_id = self.net_ids[design.plane]
            for k in range(size):
                if self.base[1][k] != OUTSIDE:
                    self.base[1][k] = plane_id
        # Around an antenna, no copper but its own feed's: the area is
        # kept for its nets on every layer, and the plane is cut away.
        for part in design.parts:
            if part.print_.keepout:
                x0, y0, x1, y1 = part.box(0)
                mine = [self.net_ids[n] for n in part.pins.values()
                        if n in self.net_ids]
                own = mine[0] if mine else BLOCKED
                for layer in range(self.layers):
                    plane_layer = design.plane and layer == 1
                    for k in self._cells_near_rect(x0, y0, x1, y1,
                                                   part.print_.keepout):
                        here = self.base[layer][k]
                        if plane_layer:
                            self.base[layer][k] = OUTSIDE
                        elif here == FREE:
                            self.base[layer][k] = own
        self.grid = [array("i", layer) for layer in self.base]

    def _rebuild(self) -> None:
        self.grid = [array("i", layer) for layer in self.base]
        for net, tracks in self.tracks.items():
            for track in tracks:
                self._mark_track(track)
        for net, vias in self.vias.items():
            for via in vias:
                self._mark_via(via)

    def _mark_track(self, track: Track) -> None:
        net_id = self.net_ids[track.net]
        grow = self.design.rules["clearance"] + track.width / 2 + \
            self.default_w / 2 + self.margin
        grid = self.grid[track.layer]
        for (x1, y1), (x2, y2) in zip(track.points, track.points[1:]):
            steps = max(1, int(math.hypot(x2 - x1, y2 - y1) /
                               (self.pitch * 0.5)))
            for s in range(steps + 1):
                t = s / steps
                for k in self._cells_near_disc(x1 + (x2 - x1) * t,
                                               y1 + (y2 - y1) * t, grow):
                    if grid[k] != OUTSIDE:
                        self._claim(grid, k, net_id)

    def _mark_via(self, via: Via) -> None:
        net_id = self.net_ids[via.net]
        grow = self.design.rules["clearance"] + self.design.rules["via"] / 2 \
            + self.default_w / 2 + self.margin
        for layer in range(self.layers):
            if self.design.plane and layer == 1 and \
                    via.net != self.design.plane:
                continue
            grid = self.grid[layer]
            for k in self._cells_near_disc(via.x, via.y, grow):
                if grid[k] != OUTSIDE:
                    self._claim(grid, k, net_id)

    def _stencil(self, radius: float) -> list[tuple[int, int]]:
        r = int(math.ceil(radius / self.pitch))
        return [(di, dj) for di in range(-r, r + 1) for dj in range(-r, r + 1)
                if (di * di + dj * dj) * self.pitch ** 2 <= radius * radius]

    def _route_net(self, net: str, avoid_cost: Optional[float] = None
                   ) -> Optional[tuple[list[Track], list[Via], set[str]]]:
        """Connect NET's pads: a tree grown from one pad to the nearest
        of the rest, one A* search at a time. With AVOID_COST, other
        nets' cells may be crossed at that cost: a trial run, to find
        whom to move. Answers the tracks, vias, and the nets crossed."""

        groups = [set(g) for g in self.terminals.get(net, [])]
        if len(groups) < 2 and not (self.design.plane == net and groups):
            return [], [], set()
        net_id = self.net_ids[net]
        width = self.design.widths.get(net, self.default_w)
        wide = [d for d in self._stencil(max(0.0, (
            width - self.default_w) / 2)) if d != (0, 0)]
        # A wide trace narrows to the usual width near its own pads, as
        # it must to leave a fine-pitch part.
        neck: set[int] = set()
        if wide:
            for pad in self.design.pads():
                if pad.net == net:
                    neck.update(self._cells_near_rect(
                        pad.x - pad.w / 2, pad.y - pad.h / 2,
                        pad.x + pad.w / 2, pad.y + pad.h / 2, NECK_MM))
        via_grow = self._stencil(max(0.0, (self.design.rules["via"] -
                                           self.default_w) / 2))
        layer_size = self.w * self.h
        plane = 1 if self.design.plane else -1
        signal_layers = [L for L in range(self.layers) if L != plane]
        crossed: set[str] = set()

        def open_(layer, k, strict=True):
            here = self.grid[layer][k]
            if here == net_id or here == FREE:
                return 0.0
            if here == OUTSIDE or avoid_cost is None or strict:
                return None
            if here > 0:
                return avoid_cost
            return avoid_cost * 2

        def passable(layer, i, j):
            k = j * self.w + i
            cost = open_(layer, k, strict=False)
            if cost is None or k in neck:
                return cost
            for di, dj in wide:
                ii, jj = i + di, j + dj
                if not (0 <= ii < self.w and 0 <= jj < self.h):
                    return None
                extra = open_(layer, jj * self.w + ii, strict=False)
                if extra is None:
                    return None
                cost = max(cost, extra)
            return cost

        via_seen: dict[int, Optional[float]] = {}

        def via_ok(i, j):
            k = j * self.w + i
            if k not in via_seen:
                via_seen[k] = via_fits(i, j)
            return via_seen[k]

        def via_fits(i, j):
            cost = 0.0
            for layer in range(self.layers):
                if layer == plane and net != self.design.plane:
                    continue
                for di, dj in via_grow or [(0, 0)]:
                    ii, jj = i + di, j + dj
                    if not (0 <= ii < self.w and 0 <= jj < self.h):
                        return None
                    here = open_(layer, jj * self.w + ii, strict=False)
                    if here is None:
                        return None
                    cost = max(cost, here)
            return cost

        tree: set[int] = set(groups[0])
        rest = groups[1:]
        if self.design.plane == net:
            # Every pad goes down to the plane: the plane is the tree.
            tree = set()
            rest = groups
        tracks: list[Track] = []
        vias: list[Via] = []
        via_cost = 12.0
        steps8 = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                  (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142),
                  (-1, -1, 1.4142)]
        deadline = time.monotonic() + 4.0
        while rest:
            # The pad nearest the tree goes next.
            if tree:
                tx = [(s // layer_size, (s % layer_size) % self.w,
                       (s % layer_size) // self.w) for s in list(tree)[:400]]
            else:
                tx = []

            def gap(group):
                s = next(iter(group))
                i, j = (s % layer_size) % self.w, (s % layer_size) // self.w
                return min((abs(i - a) + abs(j - b) for _, a, b in tx),
                           default=0)
            rest.sort(key=gap)
            target_group = rest.pop(0)
            # Search from the pad to the tree (or, for the plane's net,
            # to the plane: any cell of it, through a via).
            start = target_group
            goal = tree
            # Toward the box around the tree: never more than the way
            # left, and one comparison a cell.
            if goal:
                cells = [s % layer_size for s in goal]
                gx0 = min(k % self.w for k in cells)
                gx1 = max(k % self.w for k in cells)
                gy0 = min(k // self.w for k in cells)
                gy1 = max(k // self.w for k in cells)
            else:
                gx0 = gx1 = gy0 = gy1 = None

            def h(i, j):
                if gx0 is None:
                    return 0.0
                dx = gx0 - i if i < gx0 else i - gx1 if i > gx1 else 0
                dy = gy0 - j if j < gy0 else j - gy1 if j > gy1 else 0
                return max(dx, dy) + 0.4142 * min(dx, dy)

            frontier: list = []
            came: dict[int, int] = {}
            best: dict[int, float] = {}
            for s in start:
                layer = s // layer_size
                if layer == plane and net != self.design.plane:
                    continue
                k = s % layer_size
                i, j = k % self.w, k // self.w
                best[s] = 0.0
                came[s] = -1
                heapq.heappush(frontier, (h(i, j), 0.0, s))
            found = None
            expanded = 0
            while frontier:
                f, g, s = heapq.heappop(frontier)
                if g > best.get(s, 1e18):
                    continue
                layer = s // layer_size
                k = s % layer_size
                if s in goal or (self.design.plane == net and layer == plane):
                    found = s
                    break
                expanded += 1
                if expanded > 250_000 or (
                        expanded % 2048 == 0 and time.monotonic() > deadline):
                    break
                i, j = k % self.w, k // self.w
                for di, dj, step in steps8:
                    ii, jj = i + di, j + dj
                    if not (0 <= ii < self.w and 0 <= jj < self.h):
                        continue
                    if layer == plane:
                        continue
                    extra = passable(layer, ii, jj)
                    if extra is None:
                        continue
                    if di and dj and (passable(layer, i + di, j) is None or
                                      passable(layer, i, j + dj) is None):
                        continue
                    nk = jj * self.w + ii
                    ns = layer * layer_size + nk
                    ng = g + step + extra
                    if ng < best.get(ns, 1e18):
                        best[ns] = ng
                        came[ns] = s
                        heapq.heappush(frontier, (ng + h(ii, jj), ng, ns))
                # A via, to any other layer.
                cost = via_ok(i, j)
                if cost is not None:
                    for other in range(self.layers):
                        if other == layer:
                            continue
                        if other == plane and net != self.design.plane:
                            continue
                        ns = other * layer_size + k
                        ng = g + via_cost + cost
                        if ng < best.get(ns, 1e18):
                            best[ns] = ng
                            came[ns] = s
                            heapq.heappush(frontier, (ng + h(i, j), ng, ns))
            if found is None:
                return None
            # Walk back: the cells, the layer changes, the nets crossed.
            path = []
            s = found
            while s != -1:
                path.append(s)
                s = came[s]
            path.reverse()
            for s in path:
                layer, k = s // layer_size, s % layer_size
                here = self.grid[layer][k]
                if here > 0 and here != net_id:
                    crossed.add(self.names[here])
            self._add_path(net, path, width, tracks, vias, signal_layers,
                           neck)
            tree |= set(path) | target_group
        return tracks, vias, crossed

    def _add_path(self, net, path, width, tracks, vias, signal_layers,
                  neck=frozenset()):
        """PATH's cells as tracks, a new one at each via and wherever
        the width changes (narrow near the net's own pads)."""

        layer_size = self.w * self.h
        run: list[tuple[float, float]] = []
        run_layer = None
        run_width = None
        previous = None

        def finish():
            if len(run) >= 2 and run_layer in signal_layers:
                tracks.append(Track(run_layer, _simplify(run), run_width,
                                    net))

        for s in path:
            layer, k = s // layer_size, s % layer_size
            i, j = k % self.w, k // self.w
            here = self.default_w if k in neck else width
            if previous is not None and previous[0] != layer:
                finish()
                x, y = self.at(i, j)
                if not any(abs(v.x - x) < 1e-6 and abs(v.y - y) < 1e-6
                           for v in vias):
                    vias.append(Via(x, y, net))
                run = []
            elif previous is not None and here != run_width:
                # The width changes here: the last cell ends one track
                # and starts the next.
                finish()
                run = [run[-1]]
            run_layer = layer
            run_width = here
            run.append(self.at(i, j))
            previous = (layer, i, j)
        finish()

    def route(self) -> None:
        design = self.design
        started = time.monotonic()
        nets = [n for n in design.nets if len(self.terminals.get(n, [])) > 1
                or n == design.plane]

        def span(net):
            xs, ys = [], []
            for group in self.terminals.get(net, []):
                s = next(iter(group)) % (self.w * self.h)
                xs.append(s % self.w)
                ys.append(s // self.w)
            return (max(xs) - min(xs) + max(ys) - min(ys)) if xs else 0
        # Short nets first, power and the plane's net last.
        nets.sort(key=lambda n: (n == design.plane, n == design.ground,
                                 POWER_RE.match(n) is not None, span(n)))
        queue = list(nets)
        tries: dict[str, int] = {}
        while queue and time.monotonic() - started < ROUTE_SECONDS:
            net = queue.pop(0)
            done = self._route_net(net)
            if done is not None:
                self._keep(net, done)
                continue
            tries[net] = tries.get(net, 0) + 1
            if tries[net] > 3:
                self.failed.append(net)
                continue
            # Find whom it would cross, and move them out of the way.
            trial = self._route_net(net, avoid_cost=25.0)
            if trial is None or not trial[2]:
                self.failed.append(net)
                continue
            for other in trial[2]:
                if other == net or other not in self.tracks:
                    continue
                self.tracks.pop(other, None)
                self.vias.pop(other, None)
                if other not in queue:
                    queue.append(other)
            self._rebuild()
            queue.insert(0, net)
        for net in queue:
            if net not in self.failed:
                self.failed.append(net)

    def _keep(self, net, done) -> None:
        tracks, vias, _ = done
        for t in tracks:
            self.order += 1
            t.order = self.order
        for v in vias:
            self.order += 1
            v.order = self.order
        self.tracks[net] = tracks
        self.vias[net] = vias
        for t in tracks:
            self._mark_track(t)
        for v in vias:
            self._mark_via(v)

    def pours(self) -> dict[int, list[tuple[float, float, float, float]]]:
        """Copper filled with the ground net on the poured layers, as
        rectangles, only where it reaches the ground's own copper."""

        design = self.design
        if not design.ground or not design.pours:
            return {}
        gid = self.net_ids[design.ground]
        found: dict[int, list] = {}
        for layer in design.pours:
            grid = self.grid[layer]
            ok = bytearray(self.w * self.h)
            for k in range(self.w * self.h):
                if grid[k] in (FREE, gid):
                    ok[k] = 1
            # A free cell is clear of other nets by more than the
            # clearance, edge to centre, so its square is too.
            seeds = []
            for pad in design.pads():
                if pad.net == design.ground and layer in pad.layers:
                    seeds += self._cells_near_rect(
                        pad.x - pad.w / 2, pad.y - pad.h / 2,
                        pad.x + pad.w / 2, pad.y + pad.h / 2, 0)
            for via in self.vias.get(design.ground, []):
                seeds += self._cells_near_disc(via.x, via.y,
                                               design.rules["via"] / 2)
            for track in self.tracks.get(design.ground, []):
                if track.layer == layer:
                    for x, y in track.points:
                        i, j = self.cell(x, y)
                        seeds.append(j * self.w + i)
            keep = bytearray(self.w * self.h)
            stack = [k for k in seeds if ok[k]]
            for k in stack:
                keep[k] = 1
            while stack:
                k = stack.pop()
                i, j = k % self.w, k // self.w
                for ii, jj in ((i + 1, j), (i - 1, j), (i, j + 1),
                               (i, j - 1)):
                    if 0 <= ii < self.w and 0 <= jj < self.h:
                        n = jj * self.w + ii
                        if ok[n] and not keep[n]:
                            keep[n] = 1
                            stack.append(n)
            found[layer] = self._rectangles(keep)
        return found

    def _rectangles(self, cells: bytearray) -> list:
        """The cells as few rectangles as rows merge into, in mm."""

        runs_above: dict[tuple[int, int], list[int]] = {}
        rects = []
        half = self.pitch / 2
        for j in range(self.h + 1):
            runs = []
            if j < self.h:
                i = 0
                while i < self.w:
                    if cells[j * self.w + i]:
                        start = i
                        while i < self.w and cells[j * self.w + i]:
                            i += 1
                        runs.append((start, i - 1))
                    else:
                        i += 1
            here: dict[tuple[int, int], list[int]] = {}
            for run in runs:
                here[run] = runs_above.pop(run, [j])
            for (a, b), (start,) in runs_above.items():
                x0, y0 = self.at(a, start)
                x1, y1 = self.at(b, j - 1)
                rects.append((round(x0 - half, 4), round(y0 - half, 4),
                              round(x1 + half, 4), round(y1 + half, 4)))
            runs_above = here
        return rects


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Straight runs as their ends."""

    if len(points) < 3:
        return points
    out = [points[0]]
    for a, b in zip(points[1:], points[2:]):
        p = out[-1]
        d1 = (round(a[0] - p[0], 4), round(a[1] - p[1], 4))
        d2 = (round(b[0] - a[0], 4), round(b[1] - a[1], 4))
        if d1[0] * d2[1] - d1[1] * d2[0] != 0 or \
                d1[0] * d2[0] + d1[1] * d2[1] < 0:
            out.append(a)
    out.append(points[-1])
    return out


# --- Checks ----------------------------------------------------------------


def _copper(board: dict) -> list[tuple]:
    """Every piece of copper: (layer, kind, geometry, net, owner)."""

    items = []
    for pad in board["pads"]:
        for layer in pad["layers"]:
            items.append((layer, "rect" if pad["shape"] == "rect" else
                          "round", (pad["x"], pad["y"], pad["w"], pad["h"]),
                          pad["net"], pad["part"]))
    for track in board["tracks"]:
        for a, b in zip(track["points"], track["points"][1:]):
            items.append((track["layer"], "seg",
                          (a[0], a[1], b[0], b[1], track["width"]),
                          track["net"], ""))
    for via in board["vias"]:
        for layer in range(board["layers"]):
            if board["plane"] and layer == 1:
                continue
            items.append((layer, "disc", (via["x"], via["y"],
                                          board["rules"]["via"]),
                          via["net"], ""))
    return items


def _as_segment(kind, geo):
    """An item as a segment with a radius: a round pad, a via and a
    trace are exactly that; a rectangle's long axis with its half-width
    is within the rectangle, so a gap measured to it is never short."""

    if kind == "seg":
        x1, y1, x2, y2, w = geo
        return (x1, y1, x2, y2, w / 2)
    if kind == "disc":
        x, y, d = geo
        return (x, y, x, y, d / 2)
    x, y, w, h = geo
    if w >= h:
        return (x - (w - h) / 2, y, x + (w - h) / 2, y, h / 2)
    return (x, y - (h - w) / 2, x, y + (h - w) / 2, w / 2)


def _seg_seg(a, b) -> float:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b

    def cross(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d1 = cross(x3, y3, x4, y4, x1, y1)
    d2 = cross(x3, y3, x4, y4, x2, y2)
    d3 = cross(x1, y1, x2, y2, x3, y3)
    d4 = cross(x1, y1, x2, y2, x4, y4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 \
            and d3 and d4:
        return 0.0
    return min(_seg_point(x1, y1, x3, y3, x4, y4),
               _seg_point(x2, y2, x3, y3, x4, y4),
               _seg_point(x3, y3, x1, y1, x2, y2),
               _seg_point(x4, y4, x1, y1, x2, y2))


def _rect_gap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = max(abs(ax - bx) - (aw + bw) / 2, 0.0)
    dy = max(abs(ay - by) - (ah + bh) / 2, 0.0)
    if dx == 0 and dy == 0:
        return -1.0
    return math.hypot(dx, dy)


def _gap(ka, ga, kb, gb) -> float:
    if ka == "rect" and kb == "rect":
        return _rect_gap(ga, gb)
    if ka == "rect" or kb == "rect":
        rect, other = (ga, (kb, gb)) if ka == "rect" else (gb, (ka, ga))
        x1, y1, x2, y2, r = _as_segment(*other)
        rx, ry, rw, rh = rect
        # The segment against the rectangle: inside it, or its edges.
        corners = [(rx - rw / 2, ry - rh / 2), (rx + rw / 2, ry - rh / 2),
                   (rx + rw / 2, ry + rh / 2), (rx - rw / 2, ry + rh / 2)]
        if (abs(x1 - rx) <= rw / 2 and abs(y1 - ry) <= rh / 2) or \
                (abs(x2 - rx) <= rw / 2 and abs(y2 - ry) <= rh / 2):
            return -1.0
        best = min(
            _seg_seg((x1, y1, x2, y2), (*corners[i], *corners[(i + 1) % 4]))
            for i in range(4))
        return best - r
    sa, sb = _as_segment(ka, ga), _as_segment(kb, gb)
    return _seg_seg(sa[:4], sb[:4]) - sa[4] - sb[4]


def check(board: dict) -> list[dict]:
    """Every rule the board breaks, measured on its real copper: gaps
    between nets, copper near the edge, parts off the board or over each
    other, and connections left unmade."""

    rules = board["rules"]
    problems: list[dict] = []
    tolerance = 0.002
    items = _copper(board)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    size = 1.0

    def box(item):
        kind, geo = item[1], item[2]
        if kind == "seg":
            x1, y1, x2, y2, w = geo
            return (min(x1, x2) - w / 2, min(y1, y2) - w / 2,
                    max(x1, x2) + w / 2, max(y1, y2) + w / 2)
        if kind == "disc":
            x, y, d = geo
            return (x - d / 2, y - d / 2, x + d / 2, y + d / 2)
        x, y, w, h = geo
        return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)

    boxes = [box(it) for it in items]
    for n, (it, b) in enumerate(zip(items, boxes)):
        for gx in range(int(math.floor(b[0] / size)),
                        int(math.floor(b[2] / size)) + 1):
            for gy in range(int(math.floor(b[1] / size)),
                            int(math.floor(b[3] / size)) + 1):
                buckets.setdefault((it[0], gx, gy), []).append(n)
    seen: set[tuple[int, int]] = set()
    for members in buckets.values():
        for x, a in enumerate(members):
            for b in members[x + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                ia, ib = items[a], items[b]
                if ia[3] and ia[3] == ib[3]:
                    continue
                if ia[4] and ia[4] == ib[4]:
                    continue    # a footprint's own pads, as it is made
                ba, bb = boxes[a], boxes[b]
                if ba[0] - bb[2] > rules["clearance"] or \
                        bb[0] - ba[2] > rules["clearance"] or \
                        ba[1] - bb[3] > rules["clearance"] or \
                        bb[1] - ba[3] > rules["clearance"]:
                    continue
                gap = _gap(ia[1], ia[2], ib[1], ib[2])
                if gap < rules["clearance"] - tolerance:
                    cx = (ba[0] + ba[2] + bb[0] + bb[2]) / 4
                    cy = (ba[1] + ba[3] + bb[1] + bb[3]) / 4
                    what = " and ".join(
                        n or "no net" for n in (ia[3], ib[3]))
                    problems.append({
                        "kind": "clearance",
                        "text": f"{what} are {max(gap, 0):.3f} mm apart on "
                                f"{board['layer_names'][ia[0]]}, under "
                                f"{rules['clearance']} mm",
                        "x": round(cx, 3), "y": round(cy, 3),
                        "layer": ia[0]})
    outline = [tuple(p) for p in board["outline"]]
    for it, b in zip(items, boxes):
        for x, y in ((b[0], b[1]), (b[2], b[3]), (b[0], b[3]), (b[2], b[1])):
            if not _inside(x, y, outline) or \
                    _edge_distance(x, y, outline) < rules["edge"] - 0.05:
                if it[4] and any(p["ref"] == it[4] and p.get("edge")
                                 for p in board["parts"]):
                    break
                problems.append({
                    "kind": "edge", "text": f"copper of {it[3] or 'no net'} "
                    f"is closer than {rules['edge']} mm to the edge",
                    "x": round(x, 3), "y": round(y, 3), "layer": it[0]})
                break
    for part in board["parts"]:
        x0, y0, x1, y1 = part["box"]
        if part.get("edge"):
            continue
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        if not all(_inside(x, y, outline) for x, y in corners):
            problems.append({"kind": "placement",
                             "text": f"{part['ref']} hangs off the board",
                             "x": part["x"], "y": part["y"]})
    parts = board["parts"]
    for n, a in enumerate(parts):
        for b in parts[n + 1:]:
            if a["side"] != b["side"] and not (a["through"] or b["through"]):
                continue
            ax0, ay0, ax1, ay1 = a["box"]
            bx0, by0, bx1, by1 = b["box"]
            if min(ax1, bx1) - max(ax0, bx0) > 0.05 and \
                    min(ay1, by1) - max(ay0, by0) > 0.05:
                problems.append({"kind": "placement",
                                 "text": f"{a['ref']} and {b['ref']} overlap",
                                 "x": (a["x"] + b["x"]) / 2,
                                 "y": (a["y"] + b["y"]) / 2})
    for net in board["unrouted"]:
        spot = next((p for p in board["pads"] if p["net"] == net), None)
        problems.append({"kind": "unrouted",
                         "text": f"{net} is not fully connected",
                         "x": spot["x"] if spot else 0,
                         "y": spot["y"] if spot else 0, "net": net})
    for net, count in board["single"].items():
        problems.append({"kind": "single",
                         "text": f"{net} reaches only one pin ({count})",
                         "x": 0, "y": 0, "net": net, "warning": True})
    return problems


# --- Making the board ------------------------------------------------------


def layer_names(count: int) -> list[str]:
    if count == 1:
        return ["top"]
    return ["top"] + [f"inner {n}" for n in range(1, count - 1)] + ["bottom"]


def design(spec: Any) -> dict:
    """The board SPEC describes, placed, routed and checked: everything
    the viewer draws and every file is written from."""

    started = time.monotonic()
    made = read(spec)
    place(made)
    placed_at = time.monotonic()
    router = Router(made)
    router.route()
    pours = router.pours()
    routed_at = time.monotonic()
    names = layer_names(made.layers)
    single = {}
    pins_on: dict[str, list[str]] = {}
    for pad in made.pads():
        if pad.net:
            pins_on.setdefault(pad.net, []).append(f"{pad.part}.{pad.name}")
    for net, pins in pins_on.items():
        if len(set(pins)) == 1 and net != made.plane:
            single[net] = pins[0]
    tracks = [t for ts in router.tracks.values() for t in ts]
    vias = [v for vs in router.vias.values() for v in vs]
    board = {
        "format": "flash-pcb", "version": VERSION, "name": made.name,
        "outline": [[round(x, 4), round(y, 4)] for x, y in made.outline],
        "layers": made.layers, "layer_names": names,
        "thickness": made.thickness,
        "color": MASK_COLORS.get(made.color, made.color),
        "rules": made.rules, "plane": made.plane, "ground": made.ground,
        "pours": {str(k): v for k, v in pours.items()},
        "grid": router.pitch,
        "parts": [{
            "ref": p.ref, "package": p.package, "value": p.value,
            "x": p.x, "y": p.y, "rotation": p.rotation, "side": p.side,
            "body": list(p.print_.body), "kind": p.print_.kind,
            "box": [round(v, 4) for v in p.box(0.1)],
            "through": any(s.drill > 0 for s in p.print_.pads),
            "pins": p.pins, "fixed": p.fixed, "note": p.note,
            "keepout": p.print_.keepout, "edge": p.edge,
        } for p in made.parts],
        "pads": [{
            "part": p.part, "name": p.name, "x": p.x, "y": p.y, "w": p.w,
            "h": p.h, "shape": p.shape, "layers": list(p.layers),
            "drill": p.drill, "plated": p.plated, "net": p.net,
        } for p in made.pads()],
        "tracks": [{"layer": t.layer, "points": [list(pt) for pt in t.points],
                    "width": t.width, "net": t.net, "order": t.order}
                   for t in sorted(tracks, key=lambda t: t.order)],
        "vias": [{"x": v.x, "y": v.y, "net": v.net, "order": v.order}
                 for v in sorted(vias, key=lambda v: v.order)],
        "nets": [{"name": n, "width": made.widths.get(n, 0),
                  "pins": len(set(pins_on.get(n, []))),
                  "power": bool(POWER_RE.match(n)),
                  "ground": n == made.ground}
                 for n in made.nets],
        "unrouted": sorted(set(router.failed)),
        "single": single,
    }
    board["problems"] = check(board)
    board["stats"] = stats(board, placed_at - started, routed_at - placed_at)
    return board


def stats(board: dict, place_s: float = 0.0, route_s: float = 0.0) -> dict:
    xs = [x for x, _ in board["outline"]]
    ys = [y for _, y in board["outline"]]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                 for t in board["tracks"]
                 for a, b in zip(t["points"], t["points"][1:]))
    connected = [n for n in board["nets"] if n["pins"] > 1]
    unrouted = set(board["unrouted"])
    errors = [p for p in board["problems"] if not p.get("warning")]
    return {
        "width": round(max(xs) - min(xs), 2),
        "height": round(max(ys) - min(ys), 2),
        "area": round(abs(_area([tuple(p) for p in board["outline"]])), 1),
        "parts": len(board["parts"]), "pads": len(board["pads"]),
        "nets": len(connected),
        "routed": len([n for n in connected if n["name"] not in unrouted]),
        "vias": len(board["vias"]), "length": round(length, 1),
        "errors": len(errors),
        "warnings": len(board["problems"]) - len(errors),
        "place_s": round(place_s, 2), "route_s": round(route_s, 2),
    }


def summary(board: dict) -> str:
    """The board, in words the model can act on."""

    s = board["stats"]
    lines = [
        f"{board['name']}: {s['width']} x {s['height']} mm, "
        f"{board['layers']} layer{'s' if board['layers'] > 1 else ''}, "
        f"{s['parts']} parts, {s['pads']} pads.",
        f"Routed {s['routed']} of {s['nets']} nets with {s['vias']} vias, "
        f"{s['length']} mm of track"
        + (f"; {board['plane']} is the plane on inner 1"
           if board["plane"] else "")
        + (f"; {board['ground']} poured on "
           + " and ".join(board["layer_names"][int(k)]
                          for k in board["pours"])
           if board["pours"] else "") + ".",
    ]
    errors = [p for p in board["problems"] if not p.get("warning")]
    warnings = [p for p in board["problems"] if p.get("warning")]
    if not errors:
        lines.append("Design rule check: clean.")
    else:
        lines.append(f"Design rule check: {len(errors)} problem"
                     f"{'s' if len(errors) != 1 else ''}:")
        kinds: dict[str, int] = {}
        for p in errors:
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
            if kinds[p["kind"]] <= 6:
                lines.append(f"- {p['text']} (at {p['x']:.2f}, "
                             f"{p['y']:.2f})")
        for kind, count in kinds.items():
            if count > 6:
                lines.append(f"- and {count - 6} more {kind} problems")
    for p in warnings[:6]:
        lines.append(f"- note: {p['text']}")
    if board["unrouted"]:
        lines.append(
            "To route what is left: give the board more room or another "
            "two layers, move the parts on those nets nearer each other "
            "(x, y), or turn them (rotation).")
    placed = ", ".join(f"{p['ref']} ({p['x']}, {p['y']}"
                       + (f", {p['rotation']}°" if p["rotation"] else "")
                       + (", bottom" if p["side"] == "bottom" else "") + ")"
                       for p in board["parts"][:40])
    lines.append(f"Where the parts went (x, y in mm from the bottom left): "
                 f"{placed}" + (" ..." if len(board["parts"]) > 40 else ""))
    return "\n".join(lines)


# --- Files for a board house -----------------------------------------------


def _g(v: float) -> str:
    """A coordinate in Gerber's 4.6 format, millimetres."""

    return str(int(round(v * 1_000_000)))


class _Gerber:
    def __init__(self, name: str, function: str, polarity: str = "Positive"):
        self.lines = [
            "%TF.GenerationSoftware,Flash,PCB,1*%",
            f"%TF.FileFunction,{function}*%",
            f"%TF.FilePolarity,{polarity}*%",
            "%FSLAX46Y46*%", "%MOMM*%", "%LPD*%",
            f"G04 {name}*",
        ]
        self.apertures: dict[tuple, int] = {}
        self.body: list[str] = []
        self.current = None

    def aperture(self, shape: str, *size: float) -> int:
        key = (shape,) + tuple(round(s, 4) for s in size)
        if key not in self.apertures:
            code = 10 + len(self.apertures)
            self.apertures[key] = code
            dims = "X".join(f"{s:.4f}" for s in size)
            self.lines.append(f"%ADD{code}{shape},{dims}*%")
        code = self.apertures[key]
        if self.current != code:
            self.body.append(f"D{code}*")
            self.current = code
        return code

    def flash(self, x, y):
        self.body.append(f"X{_g(x)}Y{_g(y)}D03*")

    def line(self, points, width):
        self.aperture("C", width)
        self.body.append(f"X{_g(points[0][0])}Y{_g(points[0][1])}D02*")
        for x, y in points[1:]:
            self.body.append(f"X{_g(x)}Y{_g(y)}D01*")

    def region(self, points):
        self.body.append("G36*")
        self.body.append(f"X{_g(points[0][0])}Y{_g(points[0][1])}D02*")
        for x, y in points[1:] + points[:1]:
            self.body.append(f"X{_g(x)}Y{_g(y)}D01*")
        self.body.append("G37*")

    def clear(self):
        self.body.append("%LPC*%")

    def dark(self):
        self.body.append("%LPD*%")

    def pad(self, pad, grow=0.0):
        w, h = pad["w"] + 2 * grow, pad["h"] + 2 * grow
        if pad["shape"] == "round" and abs(w - h) < 1e-6:
            self.aperture("C", w)
        elif pad["shape"] in ("round", "oval"):
            self.aperture("O", w, h)
        else:
            self.aperture("R", w, h)
        self.flash(pad["x"], pad["y"])

    def text(self) -> str:
        return "\n".join(self.lines + ["G01*"] + self.body + ["M02*"]) + "\n"


def gerbers(board: dict) -> dict[str, str]:
    """Every Gerber file, by file name: copper, mask, paste, silkscreen,
    and the edge."""

    name = re.sub(r"[^A-Za-z0-9_-]+", "-", board["name"]).strip("-") or "board"
    count = board["layers"]
    rules = board["rules"]
    files: dict[str, str] = {}
    copper_names = ["F_Cu"] + [f"In{n}_Cu" for n in range(1, count - 1)] + \
        (["B_Cu"] if count > 1 else [])
    for layer in range(count):
        side = "Top" if layer == 0 else "Bot" if layer == count - 1 else \
            "Inr"
        g = _Gerber(f"{board['name']} copper {layer + 1}",
                    f"Copper,L{layer + 1},{side}")
        if board["plane"] and layer == 1:
            g.region([tuple(p) for p in board["outline"]])
            g.clear()
            clearance = rules["clearance"]
            for via in board["vias"]:
                if via["net"] != board["plane"]:
                    g.aperture("C", rules["via"] + 2 * clearance)
                    g.flash(via["x"], via["y"])
            for pad in board["pads"]:
                if pad["drill"] and pad["net"] != board["plane"]:
                    g.aperture("C", max(pad["w"], pad["h"]) + 2 * clearance)
                    g.flash(pad["x"], pad["y"])
            # Keep the plane off the edge.
            outline = [tuple(p) for p in board["outline"]]
            g.line(outline + outline[:1], 2 * rules["edge"])
            g.dark()
            for via in board["vias"]:
                if via["net"] == board["plane"]:
                    g.aperture("C", rules["via"])
                    g.flash(via["x"], via["y"])
        for rect in board["pours"].get(str(layer), []):
            x0, y0, x1, y1 = rect
            g.region([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        for pad in board["pads"]:
            if layer in pad["layers"]:
                g.pad(pad)
        for track in board["tracks"]:
            if track["layer"] == layer:
                g.line(track["points"], track["width"])
        if not (board["plane"] and layer == 1):
            for via in board["vias"]:
                g.aperture("C", rules["via"])
                g.flash(via["x"], via["y"])
        files[f"{name}-{copper_names[layer]}.gbr"] = g.text()
    sides = [("F", 0, "Top")] + ([("B", count - 1, "Bot")] if count > 1
                                 else [])
    for letter, layer, word in sides:
        mask = _Gerber(f"{board['name']} {word} mask", f"Soldermask,{word}",
                       "Negative")
        paste = _Gerber(f"{board['name']} {word} paste", f"Paste,{word}")
        silk = _Gerber(f"{board['name']} {word} silkscreen",
                       f"Legend,{word}")
        for pad in board["pads"]:
            if layer in pad["layers"] or (pad["drill"] and not pad["plated"]):
                mask.pad(pad, rules["mask"])
                if not pad["drill"]:
                    paste.pad(pad, -0.02 if min(pad["w"], pad["h"]) > 0.3
                              else 0.0)
        for part in board["parts"]:
            if part["side"] != ("top" if letter == "F" else "bottom") or \
                    part["kind"] in ("pad", "hole"):
                continue
            x0, y0, x1, y1 = part["box"]
            silk.line([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                      0.1)
            first = next((p for p in board["pads"] if p["part"] == part["ref"]
                          and p["name"] in ("1", "A1")), None)
            if first and len(part["pins"]) > 2:
                dx = -0.3 if first["x"] < part["x"] else 0.3
                dy = 0.3 if first["y"] > part["y"] else -0.3
                silk.aperture("C", 0.2)
                silk.flash(round(_clamp(first["x"] + dx, x0, x1), 4),
                           round(_clamp(first["y"] + dy, y0, y1), 4))
        files[f"{name}-{letter}_Mask.gbr"] = mask.text()
        files[f"{name}-{letter}_Paste.gbr"] = paste.text()
        files[f"{name}-{letter}_Silkscreen.gbr"] = silk.text()
    edge = _Gerber(f"{board['name']} outline", "Profile,NP")
    outline = [tuple(p) for p in board["outline"]]
    edge.line(outline + outline[:1], 0.05)
    files[f"{name}-Edge_Cuts.gbr"] = edge.text()
    return files


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def drill_file(board: dict, plated: bool = True) -> str:
    """Excellon drill holes: vias and through-hole pads, in mm."""

    holes: dict[float, list[tuple[float, float]]] = {}
    if plated:
        for via in board["vias"]:
            holes.setdefault(board["rules"]["drill"], []).append(
                (via["x"], via["y"]))
    for pad in board["pads"]:
        if pad["drill"] and pad["plated"] == plated:
            holes.setdefault(round(pad["drill"], 3), []).append(
                (pad["x"], pad["y"]))
    lines = ["M48", "; Flash PCB", f"; {'PTH' if plated else 'NPTH'}",
             "FMAT,2", "METRIC,TZ"]
    for n, size in enumerate(sorted(holes), 1):
        lines.append(f"T{n}C{size:.3f}")
    lines.append("%")
    lines.append("G90")
    lines.append("G05")
    for n, size in enumerate(sorted(holes), 1):
        lines.append(f"T{n}")
        for x, y in holes[size]:
            lines.append(f"X{x:.3f}Y{y:.3f}")
    lines.append("M30")
    return "\n".join(lines) + "\n"


def bom(board: dict) -> str:
    """The bill of materials: one line for each value and package."""

    groups: dict[tuple[str, str], list[str]] = {}
    for part in board["parts"]:
        if part["kind"] in ("pad", "hole"):
            continue
        groups.setdefault((part["value"], part["package"]), []).append(
            part["ref"])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Comment", "Designator", "Footprint", "Quantity"])
    for (value, package), refs in sorted(groups.items(),
                                         key=lambda kv: kv[1][0]):
        writer.writerow([value, ",".join(refs), package, len(refs)])
    return out.getvalue()


def placements(board: dict) -> str:
    """Pick and place: where each part's middle goes, and its turn."""

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    for part in board["parts"]:
        if part["kind"] in ("pad", "hole"):
            continue
        writer.writerow([part["ref"], f"{part['x']:.3f}mm",
                         f"{part['y']:.3f}mm",
                         "Top" if part["side"] == "top" else "Bottom",
                         part["rotation"]])
    return out.getvalue()


def fab_zip(board: dict) -> bytes:
    """Everything a board house asks for, in one zip."""

    name = re.sub(r"[^A-Za-z0-9_-]+", "-", board["name"]).strip("-") or "board"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        for file, text in gerbers(board).items():
            zipped.writestr(file, text)
        zipped.writestr(f"{name}-PTH.drl", drill_file(board, True))
        if any(p["drill"] and not p["plated"] for p in board["pads"]):
            zipped.writestr(f"{name}-NPTH.drl", drill_file(board, False))
        zipped.writestr(f"{name}-BOM.csv", bom(board))
        zipped.writestr(f"{name}-CPL.csv", placements(board))
        zipped.writestr(f"{name}.kicad_pcb", kicad(board))
        zipped.writestr("README.txt", _readme(board))
    return buffer.getvalue()


def _readme(board: dict) -> str:
    s = board["stats"]
    return (
        f"{board['name']}\n\n"
        f"{s['width']} x {s['height']} mm, {board['layers']} layers, "
        f"{board['thickness']} mm thick.\n"
        f"Smallest trace {board['rules']['trace']} mm, clearance "
        f"{board['rules']['clearance']} mm, vias "
        f"{board['rules']['via']}/{board['rules']['drill']} mm.\n\n"
        "Gerbers (RS-274X), Excellon drills, a bill of materials and a "
        "pick-and-place list for assembly, and a KiCad board to open and "
        "edit.\nMade with Flash.\n")


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def kicad(board: dict) -> str:
    """The board as a KiCad 7 .kicad_pcb file (KiCad 8 and 9 open it):
    footprints with their pads and nets, tracks, vias, the outline, and
    zones for the plane and pours, for KiCad to fill again."""

    count = board["layers"]
    copper = ["F.Cu"] + [f"In{n}.Cu" for n in range(1, count - 1)] + \
        (["B.Cu"] if count > 1 else [])
    ids = [0] + list(range(1, count - 1)) + ([31] if count > 1 else [])
    nets = [""] + [n["name"] for n in board["nets"]]
    number = {n: i for i, n in enumerate(nets)}

    def y(v):
        return round(-v, 4)

    out = ["(kicad_pcb (version 20221018) (generator flash)",
           f"  (general (thickness {board['thickness']}))",
           '  (paper "A4")', "  (layers"]
    for i, name in zip(ids, copper):
        out.append(f'    ({i} "{name}" signal)')
    for i, name, kind in ((32, "B.Adhes", "user"), (33, "F.Adhes", "user"),
                          (34, "B.Paste", "user"), (35, "F.Paste", "user"),
                          (36, "B.SilkS", "user"), (37, "F.SilkS", "user"),
                          (38, "B.Mask", "user"), (39, "F.Mask", "user"),
                          (44, "Edge.Cuts", "user"),
                          (46, "B.CrtYd", "user"), (47, "F.CrtYd", "user"),
                          (48, "B.Fab", "user"), (49, "F.Fab", "user")):
        out.append(f'    ({i} "{name}" {kind})')
    out.append("  )")
    rules = board["rules"]
    out.append(f"  (setup (pad_to_mask_clearance {rules['mask']}))")
    for i, net in enumerate(nets):
        out.append(f"  (net {i} {_q(net)})")
    pads_of: dict[str, list[dict]] = {}
    for pad in board["pads"]:
        pads_of.setdefault(pad["part"], []).append(pad)
    for part in board["parts"]:
        side = "F" if part["side"] == "top" else "B"
        # Pads are written as placed, turned already, so the footprint
        # itself sits at 0 degrees.
        rot = 0
        out.append(f'  (footprint {_q("Flash:" + part["package"])} '
                   f'(layer "{side}.Cu")')
        out.append(f"    (at {part['x']} {y(part['y'])})")
        out.append(f'    (fp_text reference {_q(part["ref"])} (at 0 0 {rot}) '
                   f'(layer "{side}.SilkS") hide '
                   "(effects (font (size 0.5 0.5) (thickness 0.1))))")
        out.append(f'    (fp_text value {_q(part["value"] or part["package"])}'
                   f' (at 0 0 {rot}) (layer "{side}.Fab") '
                   "(effects (font (size 0.5 0.5) (thickness 0.1))))")
        for pad in pads_of.get(part["ref"], []):
            lx, ly = pad["x"] - part["x"], pad["y"] - part["y"]
            w, h = pad["w"], pad["h"]
            shape = {"rect": "roundrect", "round": "circle" if abs(
                w - h) < 1e-6 else "oval", "oval": "oval"}[pad["shape"]]
            if pad["drill"] and not pad["plated"]:
                kind, layers = "np_thru_hole", '"*.Cu" "*.Mask"'
            elif pad["drill"]:
                kind, layers = "thru_hole", '"*.Cu" "*.Mask"'
            else:
                kind = "smd"
                layers = f'"{side}.Cu" "{side}.Paste" "{side}.Mask"'
            line = (f'    (pad {_q(pad["name"])} {kind} {shape} '
                    f"(at {round(lx, 4)} {round(-ly, 4)}) "
                    f"(size {w} {h})")
            if pad["drill"]:
                line += f" (drill {pad['drill']})"
            line += f" (layers {layers})"
            if shape == "roundrect":
                line += " (roundrect_rratio 0.15)"
            if pad["net"]:
                line += f" (net {number[pad['net']]} {_q(pad['net'])})"
            out.append(line + ")")
        out.append("  )")
    outline = board["outline"]
    for a, b in zip(outline, outline[1:] + outline[:1]):
        out.append(f"  (gr_line (start {a[0]} {y(a[1])}) (end {b[0]} "
                   f'{y(b[1])}) (layer "Edge.Cuts") (width 0.05))')
    for track in board["tracks"]:
        for a, b in zip(track["points"], track["points"][1:]):
            out.append(f"  (segment (start {a[0]} {y(a[1])}) (end {b[0]} "
                       f"{y(b[1])}) (width {track['width']}) "
                       f'(layer "{copper[track["layer"]]}") '
                       f"(net {number[track['net']]}))")
    for via in board["vias"]:
        out.append(f"  (via (at {via['x']} {y(via['y'])}) (size "
                   f"{rules['via']}) (drill {rules['drill']}) "
                   f'(layers "F.Cu" "B.Cu") (net {number[via["net"]]}))')
    zone_layers = []
    if board["plane"]:
        zone_layers.append((copper[1], board["plane"]))
    for key in board["pours"]:
        zone_layers.append((copper[int(key)], board["ground"]))
    for layer, net in zone_layers:
        pts = " ".join(f"(xy {p[0]} {y(p[1])})" for p in outline)
        out.append(
            f'  (zone (net {number[net]}) (net_name {_q(net)}) '
            f'(layer "{layer}") (hatch edge 0.5) '
            f"(connect_pads (clearance {rules['clearance']})) "
            f"(min_thickness {rules['trace']}) "
            f"(fill yes (thermal_gap {rules['clearance'] * 2}) "
            f"(thermal_bridge_width {rules['trace'] * 2})) "
            f"(polygon (pts {pts})))")
    out.append(")")
    return "\n".join(out) + "\n"


# --- The board in 3D -------------------------------------------------------

LOOKS = {
    "ic": ("#1c1c1e", 0.2, 0.55), "passive": ("#c9a46a", 0.1, 0.6),
    "led": ("#f2f2ea", 0.0, 0.3), "diode": ("#202020", 0.1, 0.5),
    "usb": ("#c7c9cc", 0.9, 0.3), "crystal": ("#d0d2d4", 0.9, 0.35),
    "can": ("#cfd1d3", 0.85, 0.35), "antenna": ("#e9e6dc", 0.0, 0.5),
    "connector": ("#2a2a2a", 0.1, 0.6), "header": ("#1a1a1a", 0.1, 0.6),
    "switch": ("#3a3a3a", 0.2, 0.5),
}


def parts_3d(board: dict) -> list[dict]:
    """The board as model3d's parts: the board, its copper, every
    component as a body you can point at, in metres."""

    m = 0.001
    t = board["thickness"]
    xs = [x for x, _ in board["outline"]]
    ys = [y for _, y in board["outline"]]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def at(x, y):
        return ((x - cx) * m, -(y - cy) * m)
    outline = [at(x, y) for x, y in board["outline"]]
    parts: list[dict] = [{
        "shape": "extrude", "name": "board", "points": [list(p) for p in
                                                        outline],
        "height": t * m, "color": board["color"], "roughness": 0.45,
        "info": f"{board['name']}: {board['stats']['width']} x "
                f"{board['stats']['height']} mm, {board['layers']} layers",
    }]
    top, bottom = t / 2 * m, -t / 2 * m
    for side, level, sign in (("top", top, 1), ("bottom", bottom, -1)):
        layer = 0 if side == "top" else board["layers"] - 1
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        lift = 0.012 * m * sign

        def quad(x0, y0, x1, y1):
            a, b = at(x0, y0), at(x1, y1)
            n = len(vertices)
            for x, z in ((a[0], a[1]), (b[0], a[1]), (b[0], b[1]),
                         (a[0], b[1])):
                vertices.append([x, level + lift, z])
            faces.append(_facing(vertices, n, sign))

        for pad in board["pads"]:
            if layer in pad["layers"]:
                quad(pad["x"] - pad["w"] / 2, pad["y"] + pad["h"] / 2,
                     pad["x"] + pad["w"] / 2, pad["y"] - pad["h"] / 2)
        if vertices:
            parts.append({"shape": "mesh", "name": f"{side} pads",
                          "vertices": vertices, "faces": faces,
                          "color": "#d9a441", "metalness": 0.9,
                          "roughness": 0.25})
        vertices, faces = [], []
        lift = 0.006 * m * sign
        for track in board["tracks"]:
            if track["layer"] != layer:
                continue
            w = track["width"] / 2
            for a, b in zip(track["points"], track["points"][1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = math.hypot(dx, dy) or 1
                nx, ny = -dy / length * w, dx / length * w
                n = len(vertices)
                for x, y in ((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                             (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)):
                    px, pz = at(x, y)
                    vertices.append([px, level + lift, pz])
                faces.append(_facing(vertices, n, sign))
        if vertices:
            parts.append({"shape": "mesh", "name": f"{side} traces",
                          "vertices": vertices, "faces": faces,
                          "color": _lighter(board["color"]),
                          "roughness": 0.4})
    for part in board["parts"]:
        if part["kind"] in ("pad", "hole"):
            continue
        w, length, height = part["body"]
        if part["rotation"] % 180:
            w, length = length, w
        color, metal, rough = LOOKS.get(part["kind"], LOOKS["ic"])
        x, z = at(part["x"], part["y"])
        sign = 1 if part["side"] == "top" else -1
        level = (top if sign > 0 else bottom) + sign * height / 2 * m
        parts.append({
            "shape": "box", "name": part["ref"], "position": [x, level, z],
            "scale": [w * m, max(height, 0.1) * m, length * m],
            "color": color, "metalness": metal, "roughness": rough,
            "info": " · ".join(
                v for v in (part["ref"], part["value"], part["package"])
                if v),
        })
    return parts[:480]


def _facing(vertices, n, sign) -> list[int]:
    """Quad N's corners in the order that faces up (SIGN 1) or down."""

    a, b, c = vertices[n], vertices[n + 1], vertices[n + 2]
    up = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
    order = [n, n + 1, n + 2, n + 3]
    return order if (up > 0) == (sign > 0) else order[::-1]


def _lighter(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(min(255, int(c * 1.35 + 18))
                                          for c in (r, g, b)))


def glb(board: dict) -> bytes:
    from . import model3d

    return model3d.to_glb(model3d.build_parts(parts_3d(board)),
                          board["name"])


def load(text: str) -> dict:
    """A board file, read back."""

    board = json.loads(text)
    if not isinstance(board, dict) or board.get("format") != "flash-pcb":
        raise PcbError("not a Flash board file")
    return board
