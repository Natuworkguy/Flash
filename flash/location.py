"""Where the user is, as their paired Apple Watch last told Flash.

The watch sends a place only while its "Let Flash see my location"
switch is on, and turning it off has Flash forget the place. Kept in
~/.flash/location.json, never sent anywhere else.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

from . import paths

# Older than this, a place is said to be stale rather than given as now.
FRESH = 60 * 60


def _file():
    return paths.FLASH_DIR / "location.json"


def save(lat: Any, lon: Any, accuracy: Any = 0, device: str = "") -> dict:
    """Keep the watch's latest place. Raises ValueError on a bad one."""

    try:
        place = {"lat": float(lat), "lon": float(lon),
                 "accuracy": max(0.0, float(accuracy or 0))}
    except (TypeError, ValueError):
        raise ValueError(
            "A location needs a number for lat and lon.") from None
    if not (math.isfinite(place["lat"]) and math.isfinite(place["lon"])
            and -90 <= place["lat"] <= 90 and -180 <= place["lon"] <= 180):
        raise ValueError("That is not a place on Earth.")
    place["device"] = device
    place["at"] = time.time()
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(place), encoding="utf-8")
    return place


def forget() -> None:
    """The switch went off: no place is kept."""

    _file().unlink(missing_ok=True)


def current() -> dict | None:
    """The last place the watch gave, or None."""

    try:
        place = json.loads(_file().read_text(encoding="utf-8"))
        float(place["lat"]), float(place["lon"]), float(place["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return place


def describe(place: dict | None = None, now: float | None = None) -> str:
    """The place in words, for the model."""

    place = place if place is not None else current()
    if not place:
        return ("No location. The user can turn on \"Let Flash see my "
                "location\" in Settings in Flash on their Apple Watch.")
    age = max(0, int((now if now is not None else time.time()) - place["at"]))
    ago = (f"{age // 60} minutes ago" if age < 2 * 60 * 60
           else f"{age // 3600} hours ago")
    line = (f"Latitude {place['lat']:.5f}, longitude {place['lon']:.5f}, "
            f"to within about {round(place.get('accuracy') or 0)} m, "
            f"from the user's Apple Watch {ago}.")
    if age > FRESH:
        line += " It may be out of date."
    return line + (" Maps link: https://maps.apple.com/?ll="
                   f"{place['lat']:.5f},{place['lon']:.5f}")
