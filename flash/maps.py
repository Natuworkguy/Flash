"""Maps: places found by name or address, what is near them, and how long it takes to get between them.

OpenStreetMap's own services, so nothing needs an account or a key: Nominatim finds places, Overpass finds what is
around one (cafes, pharmacies, charging points), and OSRM routes between them by car, bike or on foot. What the tool
finds goes into a map block in the reply, drawn on OpenStreetMap's tiles.

Nominatim asks for at most one request a second and a name for the program asking; both are kept here, and every
answer is cached on disk so the same address is looked up once.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

NOMINATIM = "https://nominatim.openstreetmap.org/"
OVERPASS = "https://overpass-api.de/api/interpreter"
OSRM = {"driving": "https://router.project-osrm.org/route/v1/driving/",
        "walking": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/",
        "cycling": "https://routing.openstreetmap.de/routed-bike/route/v1/bike/"}
AGENT = "Flash (an open-source desktop assistant)"
# OpenStreetMap's tile servers ask every app to name itself and keep the tiles it fetched: the map's tiles are
# fetched here, by Flash, named, and kept on disk, never asked for again within TILE_DAYS.
TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_AGENT = "Flash/{version} (open-source desktop assistant; +https://github.com/Natuworkguy/flash)"
TILE_DAYS = 14
TILE_ZOOM = 19
TIMEOUT = 25
_gate = threading.Lock()
_last = [0.0]

# What people ask for, as OpenStreetMap tags.
KINDS = {
    "cafe": 'amenity="cafe"', "coffee": 'amenity="cafe"', "restaurant": 'amenity="restaurant"', "bar": 'amenity="bar"',
    "pub": 'amenity="pub"', "fast food": 'amenity="fast_food"', "pharmacy": 'amenity="pharmacy"', "hospital": 'amenity="hospital"',
    "doctor": 'amenity="doctors"', "dentist": 'amenity="dentist"', "atm": 'amenity="atm"', "bank": 'amenity="bank"',
    "fuel": 'amenity="fuel"', "gas station": 'amenity="fuel"', "petrol": 'amenity="fuel"', "charging": 'amenity="charging_station"',
    "ev charger": 'amenity="charging_station"', "parking": 'amenity="parking"', "toilet": 'amenity="toilets"', "library": 'amenity="library"',
    "school": 'amenity="school"', "post office": 'amenity="post_office"', "police": 'amenity="police"', "gym": 'leisure="fitness_centre"',
    "park": 'leisure="park"', "playground": 'leisure="playground"', "supermarket": 'shop="supermarket"', "grocery": 'shop="supermarket"',
    "bakery": 'shop="bakery"', "hotel": 'tourism="hotel"', "hostel": 'tourism="hostel"', "museum": 'tourism="museum"',
    "attraction": 'tourism="attraction"', "viewpoint": 'tourism="viewpoint"', "station": 'railway="station"', "train": 'railway="station"',
    "bus stop": 'highway="bus_stop"', "bike rental": 'amenity="bicycle_rental"', "cinema": 'amenity="cinema"', "vet": 'amenity="veterinary"',
}


class MapsError(Exception):
    pass


def _cache_dir():
    from . import paths

    d = paths.FLASH_DIR / "maps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, params: Optional[dict] = None, data: Optional[bytes] = None, polite: bool = False) -> Any:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    key = hashlib.sha1((url + (data or b"").decode("utf-8", "replace")).encode()).hexdigest()[:20]
    cached = _cache_dir() / f"{key}.json"
    if cached.is_file() and time.time() - cached.stat().st_mtime < 30 * 86400:
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except ValueError:
            pass
    if polite:  # Nominatim: one a second
        with _gate:
            wait = 1.05 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310 -- fixed https hosts above
            out = json.loads(r.read(6_000_000))
    except urllib.error.HTTPError as exc:
        raise MapsError(f"{urllib.parse.urlsplit(url).netloc} answered {exc.code} {exc.reason}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MapsError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {getattr(exc, 'reason', exc)}") from None
    try:
        cached.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


_LATLNG = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def geocode(query: str, limit: int = 1) -> list[dict]:
    """Places called QUERY (a name, an address, "lat,lng"): name, lat, lng, address, kind."""
    query = (query or "").strip()
    if not query:
        raise MapsError("say which place")
    m = _LATLNG.match(query)
    if m:
        return [{"name": query, "lat": float(m.group(1)), "lng": float(m.group(2)), "address": "", "kind": "point"}]
    got = _get(NOMINATIM + "search", {"q": query, "format": "jsonv2", "limit": max(1, min(limit, 8)), "addressdetails": 0}, polite=True)
    if not got:
        raise MapsError(f"no place called {query!r}")
    return [{"name": (g.get("name") or g.get("display_name", "").split(",")[0]).strip(), "lat": round(float(g["lat"]), 6),
             "lng": round(float(g["lon"]), 6), "address": g.get("display_name", ""), "kind": g.get("type") or g.get("category", "")}
            for g in got]


def _metres(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def nearby(what: str, near: str, radius: int = 1500, limit: int = 10) -> list[dict]:
    """Places of kind WHAT ("cafe", "pharmacy", "ev charger", or an OSM tag like shop=books) within RADIUS metres of
    NEAR, the closest first."""
    centre = geocode(near)[0]
    w = (what or "").strip().lower()
    tag = KINDS.get(w) or KINDS.get(w.rstrip("s")) or ""
    if not tag and re.fullmatch(r"[a-z_:]+=[a-z_:;0-9-]+", w):
        k, v = w.split("=", 1)
        tag = f'{k}="{v}"'
    radius = max(100, min(int(radius or 1500), 20000))
    if tag:
        sel = f"nwr[{tag}](around:{radius},{centre['lat']},{centre['lng']});"
    else:  # a name: "Starbucks", "Lidl"
        safe = re.sub(r'["\\\\]', "", what)[:60]
        sel = f'nwr["name"~"{safe}",i](around:{radius},{centre["lat"]},{centre["lng"]});'
    q = f"[out:json][timeout:20];({sel});out center 60;"
    try:
        got = _get(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode())
    except MapsError:
        try:  # Overpass is often busy: once more, then Nominatim's own search around the place
            time.sleep(1.5)
            got = _get(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode())
        except MapsError:
            return [centre] + _nearby_by_name(what, centre, radius, limit, tag)
    out = []
    for e in got.get("elements") or []:
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        tags = e.get("tags") or {}
        if lat is None or not tags.get("name"):
            continue
        p = {"name": tags["name"], "lat": round(lat, 6), "lng": round(lng, 6)}
        p["metres"] = round(_metres(centre, p))
        street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
        if street:
            p["address"] = street
        for k in ("opening_hours", "website", "phone", "cuisine", "wheelchair"):
            if tags.get(k):
                p[k] = tags[k]
        out.append(p)
    out.sort(key=lambda p: p["metres"])
    return [centre] + out[:max(1, min(int(limit or 10), 25))]


def _nearby_by_name(what: str, centre: dict, radius: int, limit: Any, tag: str = "") -> list[dict]:
    d_lat = radius / 111_000
    d_lng = radius / (111_000 * max(0.2, math.cos(math.radians(centre["lat"]))))
    box = f"{centre['lng'] - d_lng},{centre['lat'] + d_lat},{centre['lng'] + d_lng},{centre['lat'] - d_lat}"
    got = _get(NOMINATIM + "search", {"q": f"[{what}]" if tag else what, "format": "jsonv2", "limit": 30, "viewbox": box, "bounded": 1}, polite=True)
    out = []
    for g in got or []:
        if tag and f'{g.get("category")}="{g.get("type")}"' != tag:
            continue
        p = {"name": (g.get("name") or g.get("display_name", "").split(",")[0]).strip(), "lat": round(float(g["lat"]), 6),
             "lng": round(float(g["lon"]), 6), "address": ", ".join(g.get("display_name", "").split(", ")[1:3])}
        p["metres"] = round(_metres(centre, p))
        out.append(p)
    out.sort(key=lambda p: p["metres"])
    return out[:max(1, min(int(limit or 10), 25))]


def line(points: list[tuple[float, float]], mode: str = "driving") -> dict:
    """The road or path through POINTS (lat, lng), for the map to draw: {"line", "metres", "seconds"}."""
    if len(points) < 2:
        raise MapsError("a route needs at least two places")
    mode = mode if mode in OSRM else "driving"
    coords = ";".join(f"{lng},{lat}" for lat, lng in points[:12])
    got = _get(OSRM[mode] + coords, {"overview": "simplified", "geometries": "geojson", "steps": "false"})
    if got.get("code") != "Ok" or not got.get("routes"):
        raise MapsError(f"no {mode} route between those places")
    r = got["routes"][0]
    return {"line": [[round(la, 5), round(ln, 5)] for ln, la in r["geometry"]["coordinates"]],
            "metres": round(r["distance"]), "seconds": round(r["duration"])}


def route(places: list[str], mode: str = "driving") -> dict:
    """The way through PLACES in order, by MODE (driving, walking, cycling): total and per-leg distance and time,
    and the line to draw."""
    if len(places) < 2:
        raise MapsError("a route needs at least two places")
    mode = mode if mode in OSRM else "driving"
    stops = [geocode(p)[0] for p in places[:12]]
    coords = ";".join(f"{s['lng']},{s['lat']}" for s in stops)
    got = _get(OSRM[mode] + coords, {"overview": "simplified", "geometries": "geojson", "steps": "false"})
    if got.get("code") != "Ok" or not got.get("routes"):
        raise MapsError(f"no {mode} route between those places")
    r = got["routes"][0]
    line = [[round(lat, 5), round(lng, 5)] for lng, lat in r["geometry"]["coordinates"]]
    return {"mode": mode, "metres": round(r["distance"]), "seconds": round(r["duration"]), "stops": stops,
            "legs": [{"metres": round(leg["distance"]), "seconds": round(leg["duration"])} for leg in r.get("legs") or []],
            "line": line}


def _dist(m: float) -> str:
    return f"{m / 1000:.1f} km ({m / 1609.34:.1f} mi)" if m >= 950 else f"{round(m)} m"


def _time(s: float) -> str:
    h, mnt = divmod(round(s / 60), 60)
    return f"{h} h {mnt} min" if h else f"{max(1, mnt)} min"


def run(action: str, query: str = "", near: str = "", what: str = "", places: Any = None, mode: str = "driving",
        radius: Any = 1500, limit: Any = 8) -> str:
    """The maps tool: find, nearby or route."""
    try:
        if action == "find":
            found = geocode(query, int(limit or 5))
            lines = [f"{i}. {p['name']} ({p['kind']}): {p['lat']}, {p['lng']} · {p['address']}" for i, p in enumerate(found, 1)]
            block = {"type": "map", "places": [{"name": p["name"], "lat": p["lat"], "lng": p["lng"], "note": p["address"][:80]} for p in found]}
        elif action == "nearby":
            centre, *found = nearby(what or query, near, int(radius or 1500), int(limit or 8))
            if not found:
                return f"Nothing tagged {what!r} within {radius} m of {centre['name']} on OpenStreetMap. Try a wider radius or another word."
            lines = [f"Around {centre['name']} ({centre['lat']}, {centre['lng']}):"]
            for i, p in enumerate(found, 1):
                extra = " · ".join(f"{k.replace('_', ' ')}: {p[k]}" for k in ("address", "opening_hours", "cuisine", "phone", "website") if p.get(k))
                lines.append(f"{i}. {p['name']}, {_dist(p['metres'])} away" + (f" · {extra}" if extra else ""))
            block = {"type": "map", "places": [{"name": centre["name"], "lat": centre["lat"], "lng": centre["lng"], "home": True}]
                     + [{"name": p["name"], "lat": p["lat"], "lng": p["lng"], "note": p.get("address") or p.get("opening_hours", "")} for p in found]}
        elif action == "route":
            stops = places if isinstance(places, list) else [s.strip() for s in str(places or query).split("|") if s.strip()]
            r = route(stops, mode)
            lines = [f"{r['mode'].capitalize()}: {_dist(r['metres'])}, about {_time(r['seconds'])} without traffic."]
            for i, leg in enumerate(r["legs"]):
                lines.append(f"  {r['stops'][i]['name']} to {r['stops'][i + 1]['name']}: {_dist(leg['metres'])}, {_time(leg['seconds'])}")
            block = {"type": "map", "route": {"mode": r["mode"], "summary": f"{_dist(r['metres'])} · {_time(r['seconds'])}"},
                     "places": [{"name": s["name"], "lat": s["lat"], "lng": s["lng"]} for s in r["stops"]]}
            lines.append("The route's line is drawn by the map block itself; give it the places in order with \"route\".")
        else:
            return "Error: action is find, nearby or route."
    except MapsError as exc:
        return f"Error: {exc}"
    return "\n".join(lines) + "\n\nTo show it, put this map block in the reply (trim or add places as you like):\n```ui\n" + json.dumps(block, ensure_ascii=False) + "\n```"


def tile(z: int, x: int, y: int) -> bytes:
    """One map tile, as PNG bytes: from disk when it was fetched in the last TILE_DAYS, else from OpenStreetMap."""

    from .version import __version__

    if not (0 <= z <= TILE_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise MapsError("no such tile")
    folder = _cache_dir() / "tiles" / str(z) / str(x)
    path = folder / f"{y}.png"
    if path.is_file() and time.time() - path.stat().st_mtime < TILE_DAYS * 86400:
        return path.read_bytes()
    req = urllib.request.Request(TILES.format(z=z, x=x, y=y), headers={
        "User-Agent": TILE_AGENT.format(version=__version__), "Referer": "https://github.com/Natuworkguy/flash"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # nosec B310 -- the fixed tile server
            data = r.read(2_000_000)
    except (urllib.error.URLError, OSError) as exc:
        if path.is_file():   # old is better than none
            return path.read_bytes()
        raise MapsError(f"could not fetch the map: {getattr(exc, 'reason', exc)}") from None
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data
