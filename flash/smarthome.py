"""Smart home control, through Home Assistant.

Home Assistant already speaks to nearly every light, plug, thermostat,
lock, blind, speaker and sensor there is, whatever its brand, and keeps
them on the local network. So Flash talks to it, not to each brand:
its address and a long-lived access token (made on your Home Assistant
profile page) are all it needs. The token is kept in Flash's vault.

Devices are found by the names they have in Home Assistant ("kitchen
lights", "thermostat"), or by their entity ids. Reading is free; any
change asks first in ask-first mode, and locks, alarms and garage doors
ask every time, autonomous or not.
"""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from . import vault

URL_SETTING = "HOME_ASSISTANT_URL"
TOKEN = "HOME_ASSISTANT_TOKEN"
TIMEOUT = 10

# The kinds of device worth listing and controlling; sensors and the
# rest are read when asked about by name.
CONTROLLED = (
    "light",
    "switch",
    "fan",
    "climate",
    "cover",
    "lock",
    "media_player",
    "scene",
    "script",
    "vacuum",
    "humidifier",
    "water_heater",
    "alarm_control_panel",
    "input_boolean",
    "button",
)
# Those that keep a home shut: asked about every time.
GUARDED = ("lock", "alarm_control_panel")
GUARDED_COVERS = ("garage", "door", "gate")

ACTIONS = (
    "list",
    "state",
    "turn_on",
    "turn_off",
    "toggle",
    "set",
    "activate",
    "lock",
    "unlock",
    "open",
    "close",
    "stop",
)

# Colours by name, for lights.
COLOURS = {
    "red": [255, 0, 0],
    "orange": [255, 140, 0],
    "yellow": [255, 220, 0],
    "green": [0, 200, 60],
    "cyan": [0, 220, 220],
    "blue": [30, 90, 255],
    "purple": [150, 50, 255],
    "pink": [255, 105, 180],
    "white": [255, 255, 255],
    "warm white": [255, 200, 140],
    "cool white": [210, 225, 255],
}


class HomeError(Exception):
    """Home Assistant could not be reached, or would not do it."""


def address() -> str:
    return (os.environ.get(URL_SETTING) or "").strip().rstrip("/")


def connected() -> bool:
    return bool(address() and vault.get(TOKEN))


def _call(method: str, path: str, body: Optional[dict] = None) -> Any:
    if not connected():
        raise HomeError(
            "Smart home is not connected. Connect Home Assistant in "
            "Settings, Connectors (or /smarthome connect <address> in the "
            "terminal), with a long-lived access token from your Home "
            "Assistant profile page."
        )
    request = urllib.request.Request(
        address() + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {vault.get(TOKEN)}",
            "Content-Type": "application/json",
        },
    )
    try:
        # nosec B310 -- the user's own Home Assistant, http(s) only
        with urllib.request.urlopen(  # nosec B310
            request, timeout=TIMEOUT
        ) as answer:
            raw = answer.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise HomeError(
                "Home Assistant did not take the token: make a "
                "new long-lived access token and connect again."
            ) from None
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise HomeError(
            f"Home Assistant said {exc.code}: {detail or exc.reason}"
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HomeError(
            f"Could not reach Home Assistant at {address()}: {exc}"
        ) from None
    return json.loads(raw) if raw else None


def check() -> str:
    """Whether the address and token work: Home Assistant's own words."""

    said = _call("GET", "/api/")
    return str((said or {}).get("message") or "Home Assistant answered")


def states() -> list[dict]:
    found = _call("GET", "/api/states")
    return found if isinstance(found, list) else []


def _name(entity: dict) -> str:
    return str(
        (entity.get("attributes") or {}).get("friendly_name")
        or entity.get("entity_id")
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def find(name: str, every: Optional[list[dict]] = None) -> list[dict]:
    """The devices NAME means, best first: an entity id exactly, else the
    friendly names with every word of NAME in them, controllable first."""

    every = states() if every is None else every
    wanted = str(name or "").strip().lower()
    exact = [e for e in every if e.get("entity_id") == wanted]
    if exact:
        return exact
    words = [w for w in _words(wanted) if w not in ("the", "my", "all", "s")]
    if not words:
        return []
    hits = []
    for entity in every:
        entity_id = entity.get("entity_id", "")
        named = " ".join(_words(_name(entity)))
        have = named + " " + " ".join(_words(entity_id.replace("_", " ")))
        if not all(w in have or w.rstrip("s") in have for w in words):
            continue
        domain, _, own = entity_id.partition(".")
        hits.append(
            (
                _name(entity).lower() != wanted,  # its very name
                own != "_".join(words),  # its id, as said
                -sum(w in named for w in words),  # said in its name
                domain not in CONTROLLED,  # a device, not a sensor
                len(_name(entity)),
                entity,
            )
        )
    return [h[-1] for h in sorted(hits, key=lambda h: h[:5])]


def describe(entity: dict) -> str:
    """One device in a line: its name, id, and how it is now."""

    domain = entity.get("entity_id", "").split(".")[0]
    a = entity.get("attributes") or {}
    state = entity.get("state")
    extra = []
    if domain == "light" and state == "on" and a.get("brightness") is not None:
        extra.append(f"{round(a['brightness'] / 2.55)}%")
    if domain == "climate":
        if a.get("current_temperature") is not None:
            extra.append(f"now {a['current_temperature']}°")
        if a.get("temperature") is not None:
            extra.append(f"set to {a['temperature']}°")
    if domain == "cover" and a.get("current_position") is not None:
        extra.append(f"{a['current_position']}% open")
    if domain == "media_player" and a.get("media_title"):
        extra.append(f"playing {a['media_title']}")
    if a.get("unit_of_measurement"):
        state = f"{state} {a['unit_of_measurement']}"
    return f"{_name(entity)} ({entity.get('entity_id')}): {state}" + (
        f", {', '.join(extra)}" if extra else ""
    )


def listing(query: str = "") -> str:
    every = states()
    if query:
        shown = find(query, every)[:40]
    else:
        shown = sorted(
            (
                e
                for e in every
                if e.get("entity_id", "").split(".")[0] in CONTROLLED
            ),
            key=lambda e: (e["entity_id"].split(".")[0], _name(e).lower()),
        )[:120]
    if not shown:
        return (
            f"No device matches {query!r}."
            if query
            else "Home Assistant has no devices to control."
        )
    return "\n".join(describe(e) for e in shown)


def guarded(entity: dict, action: str) -> bool:
    """Whether changing ENTITY this way could let someone in: asked
    about every time."""

    domain = entity.get("entity_id", "").split(".")[0]
    if domain in GUARDED:
        return action not in ("lock",) or domain == "alarm_control_panel"
    if domain == "cover":
        kind = str((entity.get("attributes") or {}).get("device_class") or "")
        return kind in GUARDED_COVERS and action in (
            "open",
            "turn_on",
            "toggle",
            "set",
        )
    return False


def service_for(
    entity: dict, action: str, value: dict
) -> tuple[str, str, dict]:
    """The Home Assistant service ACTION means for ENTITY: (domain,
    service, data)."""

    entity_id = entity["entity_id"]
    domain = entity_id.split(".")[0]
    data: dict = {"entity_id": entity_id}
    if action == "activate" or (
        domain in ("scene", "script") and action == "turn_on"
    ):
        if domain not in ("scene", "script", "button"):
            raise HomeError(f"{_name(entity)} is not a scene or a script")
        return (domain, "press" if domain == "button" else "turn_on", data)
    if action in ("lock", "unlock"):
        if domain != "lock":
            raise HomeError(f"{_name(entity)} is not a lock")
        return ("lock", action, data)
    if action in ("open", "close", "stop"):
        if domain == "cover":
            return ("cover", f"{action}_cover", data)
        if domain == "lock" and action == "open":
            return ("lock", "open", data)
        if domain == "valve":
            return ("valve", f"{action}_valve", data)
        raise HomeError(f"{_name(entity)} does not open or close")
    if action == "set":
        if domain == "light":
            if "brightness" in value:
                data["brightness_pct"] = max(
                    0, min(100, float(value["brightness"]))
                )
            colour = str(value.get("color") or "").strip().lower()
            if colour:
                if colour in COLOURS:
                    data["rgb_color"] = COLOURS[colour]
                elif re.fullmatch(r"#?[0-9a-f]{6}", colour):
                    hexed = colour.lstrip("#")
                    data["rgb_color"] = [
                        int(hexed[i:i + 2], 16) for i in (0, 2, 4)
                    ]
                else:
                    raise HomeError(
                        f"no colour {colour!r}: use a name like warm "
                        "white or blue, or a hex code"
                    )
            return ("light", "turn_on", data)
        if domain == "climate":
            if "temperature" in value:
                data["temperature"] = float(value["temperature"])
                return ("climate", "set_temperature", data)
            if value.get("mode"):
                data["hvac_mode"] = str(value["mode"])
                return ("climate", "set_hvac_mode", data)
        if domain == "cover" and "position" in value:
            data["position"] = max(0, min(100, int(float(value["position"]))))
            return ("cover", "set_cover_position", data)
        if domain == "fan" and "percentage" in value:
            data["percentage"] = max(
                0, min(100, int(float(value["percentage"])))
            )
            return ("fan", "set_percentage", data)
        if domain == "media_player" and "volume" in value:
            data["volume_level"] = max(
                0.0, min(1.0, float(value["volume"]) / 100)
            )
            return ("media_player", "volume_set", data)
        raise HomeError(
            f"nothing to set on {_name(entity)}: give brightness or "
            "color for a light, temperature or mode for a thermostat, "
            "position for blinds, percentage for a fan, volume for a "
            "speaker"
        )
    if action in ("turn_on", "turn_off", "toggle"):
        if domain in ("lock",):
            raise HomeError(f"{_name(entity)} is a lock: use lock or unlock")
        return (
            "homeassistant" if domain not in CONTROLLED else domain,
            action,
            data,
        )
    raise HomeError(f"no action {action!r}; it is one of {', '.join(ACTIONS)}")


def run(entity: dict, action: str, value: Optional[dict] = None) -> str:
    domain, service, data = service_for(entity, action, value or {})
    _call("POST", f"/api/services/{domain}/{service}", data)
    try:
        now = _call("GET", f"/api/states/{entity['entity_id']}")
        return (
            describe(now)
            if isinstance(now, dict)
            else f"{_name(entity)}: done"
        )
    except HomeError:
        return f"{_name(entity)}: done"
