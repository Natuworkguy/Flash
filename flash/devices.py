"""What is connected, and how charged: the Mac's own battery (pmset),
its Bluetooth devices (system_profiler), the network it is on, and the
paired Apple Watch as it last reported itself. Read-only throughout.

The watch sends its battery with the "device" command when Flash is
opened on it; that is kept in ~/.flash/watch.json.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import sys
import time
from typing import Any, Optional

from . import paths


def _watch_file():
    return paths.FLASH_DIR / "watch.json"


def save_watch(battery: Any, charging: Any = False, name: str = "",
               now: Optional[float] = None) -> dict:
    """Keep what the watch said of itself. BATTERY is 0 to 1, or 0 to 100."""

    level = float(battery)
    if level <= 1:
        level *= 100
    if not 0 <= level <= 100:
        raise ValueError("Battery level out of range.")
    data = {
        "battery": round(level),
        "charging": bool(charging),
        "name": " ".join(str(name or "").split())[:40],
        "at": time.time() if now is None else now,
    }
    path = _watch_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def watch() -> Optional[dict]:
    try:
        data = json.loads(_watch_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "battery" in data else None


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(  # nosec B603
            list(cmd), capture_output=True, text=True, timeout=15, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def mac_battery(text: Optional[str] = None) -> str:
    """From `pmset -g batt`: "84%, charging, 1:02 remaining", or on AC
    with no battery."""

    text = _run("pmset", "-g", "batt") if text is None else text
    found = re.search(r"(\d+)%;\s*([^;]+);\s*([^\n(]*)", text)
    if not found:
        source = re.search(r"'([^']+)'", text)
        return (f"No battery; on {source.group(1)}." if source
                else "Battery unknown.")
    level, state, left = (g.strip() for g in found.groups())
    left = left.replace("present: true", "").strip()
    return f"{level}%, {state}" + (f", {left}" if left else "") + "."


def bluetooth(raw: Optional[str] = None) -> list[dict]:
    """Connected Bluetooth devices, with battery where macOS knows it."""

    if raw is None:
        raw = _run("system_profiler", "SPBluetoothDataType", "-json")
    try:
        info = json.loads(raw)["SPBluetoothDataType"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    found = []
    for entry in info.get("device_connected") or []:
        for name, props in entry.items():
            levels = {
                key.replace("device_batteryLevel", "").lower() or "main": value
                for key, value in props.items()
                if key.startswith("device_batteryLevel")
            }
            found.append({"name": name,
                          "kind": props.get("device_minorType", ""),
                          "battery": levels})
    return found


def network() -> str:
    wifi = _run("networksetup", "-getairportnetwork", "en0").strip()
    if wifi.startswith("Current Wi-Fi Network:"):
        return "Wi-Fi " + wifi.split(":", 1)[1].strip()
    route = _run("route", "-n", "get", "default")
    iface = re.search(r"interface:\s*(\S+)", route)
    return f"Connected through {iface.group(1)}" if iface else "No network."


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return "just now"
    if seconds < 7200:
        return f"{seconds // 60} minutes ago"
    return f"{seconds // 3600} hours ago"


def report(now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    lines = []
    if sys.platform == "darwin":
        lines.append("Mac battery: " + mac_battery())
        lines.append("Network: " + network())
        devices = bluetooth()
        if devices:
            lines.append("Bluetooth, connected:")
            for d in devices:
                charge = ", ".join(f"{k} {v}" for k, v in d["battery"].items())
                kind = f" ({d['kind']})" if d["kind"] else ""
                lines.append(f"- {d['name']}{kind}"
                             + (f": {charge}" if charge else ""))
        else:
            lines.append("Bluetooth: nothing connected.")
    else:
        lines.append("Battery and Bluetooth are read only on a Mac.")
    seen = watch()
    if seen:
        lines.append(
            f"Apple Watch{' ' + seen['name'] if seen.get('name') else ''}: "
            f"{seen['battery']}%"
            f"{', charging' if seen.get('charging') else ''}, "
            f"last heard from {_ago(now - float(seen.get('at', 0)))}."
        )
    else:
        lines.append("Apple Watch: has not reported in.")
    return "\n".join(lines)
