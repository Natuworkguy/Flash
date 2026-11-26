# pylint: disable=C0114,C0116
"""Flash's own timed reminders, and the devices tool."""

import pytest

from flash import devices, paths, timers, tools, web

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "FLASH_DIR", tmp_path)
    return tmp_path


def test_in_minutes_and_iso():
    assert timers.parse_when("in 20m", NOW) == NOW + 1200
    assert timers.parse_when("in 2 h", NOW) == NOW + 7200
    assert timers.parse_when("2026-11-03T18:00") > 0
    with pytest.raises(ValueError):
        timers.parse_when("soonish", NOW)


def test_add_list_cancel_and_keep_across_reads():
    one = timers.add("Call Sam", "in 1h", now=NOW)
    timers.add("Stretch", "in 10m", now=NOW)
    assert [t["text"] for t in timers.upcoming()] == ["Stretch", "Call Sam"]
    assert timers.cancel(one["id"])["text"] == "Call Sam"
    assert timers.cancel("nope") is None
    assert len(timers.upcoming()) == 1


def test_past_and_empty_refused():
    with pytest.raises(ValueError):
        timers.add("x", "2000-01-01T00:00", now=NOW)
    with pytest.raises(ValueError):
        timers.add("  ", "in 1m", now=NOW)


def test_fires_once_when_due(monkeypatch):
    shown = []
    monkeypatch.setattr(timers, "_remind", shown.append)
    timers.add("Tea", "in 5m", now=NOW)
    timers.add("Later", "in 1d", now=NOW)
    assert timers.fire_due(NOW + 60) == []
    assert [t["text"] for t in timers.fire_due(NOW + 301)] == ["Tea"]
    assert timers.fire_due(NOW + 302) == []
    assert [t["text"] for t in shown] == ["Tea"]
    assert [t["text"] for t in timers.upcoming()] == ["Later"]


def test_tool_words():
    assert "Set." in timers.tool("add", "Tea", "in 5m")
    assert "Tea" in timers.tool("list")
    assert timers.tool("add", "Tea", "whenever").startswith("Error")
    assert timers.tool("explode").startswith("Error")


def test_offered_to_flash_and_sparks():
    names = [t["function"]["name"] for t in tools.flash_tools()]
    assert "remind_me" in names and "devices" in names
    assert "remind_me" in tools.SPARK_TOOL_NAMES
    assert "devices" in tools.SPARK_TOOL_NAMES


def test_mac_battery_lines():
    batt = ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t"
            "84%; discharging; 3:12 remaining present: true\n")
    assert devices.mac_battery(batt) == "84%, discharging, 3:12 remaining."
    assert devices.mac_battery("Now drawing from 'AC Power'\n") == (
        "No battery; on AC Power.")


def test_bluetooth_batteries():
    raw = ('{"SPBluetoothDataType": [{"device_connected": [{"AirPods": '
           '{"device_minorType": "Headphones", "device_batteryLevelLeft": '
           '"80%", "device_batteryLevelRight": "75%"}}]}]}')
    found = devices.bluetooth(raw)
    assert found == [{"name": "AirPods", "kind": "Headphones",
                      "battery": {"left": "80%", "right": "75%"}}]
    assert devices.bluetooth("not json") == []


def test_watch_reports_battery():
    said = web.command(web.Session(), {"name": "device", "arg": "",
                                       "battery": 0.62, "charging": True,
                                       "watch": "Nat"})
    assert said["device"]["battery"] == 62
    assert devices.watch()["charging"] is True
    with pytest.raises(ValueError):
        devices.save_watch(250)
