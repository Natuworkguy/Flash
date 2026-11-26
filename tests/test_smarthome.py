"""Tests for smart home control, against a Home Assistant of our own."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from flash import smarthome, tools, vault, web

STATES = [
    {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"friendly_name": "Kitchen lights", "brightness": 128},
    },
    {
        "entity_id": "light.kitchen_strip",
        "state": "off",
        "attributes": {"friendly_name": "Kitchen strip"},
    },
    {
        "entity_id": "climate.hall",
        "state": "heat",
        "attributes": {
            "friendly_name": "Thermostat",
            "current_temperature": 19.5,
            "temperature": 21,
        },
    },
    {
        "entity_id": "lock.front_door",
        "state": "locked",
        "attributes": {"friendly_name": "Front door"},
    },
    {
        "entity_id": "cover.garage",
        "state": "closed",
        "attributes": {
            "friendly_name": "Garage door",
            "device_class": "garage",
        },
    },
    {
        "entity_id": "scene.movie_night",
        "state": "scening",
        "attributes": {"friendly_name": "Movie night"},
    },
    {
        "entity_id": "sensor.outside",
        "state": "12.5",
        "attributes": {
            "friendly_name": "Outside temperature",
            "unit_of_measurement": "°C",
        },
    },
]


@pytest.fixture
def home(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer good":
                return self.answer(401, {"message": "no"})
            if self.path == "/api/":
                return self.answer(200, {"message": "API running."})
            if self.path == "/api/states":
                return self.answer(200, STATES)
            if self.path.startswith("/api/states/"):
                wanted = self.path.rsplit("/", 1)[1]
                found = next(s for s in STATES if s["entity_id"] == wanted)
                return self.answer(200, found)
            self.answer(404, {})

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            calls.append((self.path, json.loads(self.rfile.read(size))))
            self.answer(200, [])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(
        smarthome.URL_SETTING, f"http://127.0.0.1:{server.server_address[1]}"
    )
    vault.set_secret(smarthome.TOKEN, "good")
    yield calls
    server.shutdown()


class TestFinding:
    def test_by_name_best_first_and_by_id(self, home):
        assert (
            smarthome.find("kitchen lights")[0]["entity_id"] == "light.kitchen"
        )
        assert (
            smarthome.find("kitchen strip")[0]["entity_id"]
            == "light.kitchen_strip"
        )
        assert smarthome.find("kitchen")[0]["entity_id"] == "light.kitchen"
        assert smarthome.find("climate.hall")[0]["entity_id"] == "climate.hall"
        assert smarthome.find("bathroom fan") == []

    def test_a_listing_says_how_each_is(self, home):
        said = smarthome.listing()
        assert "Kitchen lights (light.kitchen): on, 50%" in said
        assert "Thermostat (climate.hall): heat, now 19.5°, set to 21°" in said
        # Sensors only when asked about.
        assert "Outside" not in said
        assert "12.5 °C" in smarthome.listing("outside temperature")


class TestServices:
    def entity(self, entity_id):
        return next(s for s in STATES if s["entity_id"] == entity_id)

    def test_what_each_action_calls(self):
        light = self.entity("light.kitchen")
        assert smarthome.service_for(
            light, "set", {"brightness": 30, "color": "warm white"}
        ) == (
            "light",
            "turn_on",
            {
                "entity_id": "light.kitchen",
                "brightness_pct": 30.0,
                "rgb_color": [255, 200, 140],
            },
        )
        assert (
            smarthome.service_for(
                self.entity("climate.hall"), "set", {"temperature": 22}
            )[1]
            == "set_temperature"
        )
        assert smarthome.service_for(
            self.entity("scene.movie_night"), "activate", {}
        )[:2] == ("scene", "turn_on")
        assert (
            smarthome.service_for(self.entity("cover.garage"), "open", {})[1]
            == "open_cover"
        )
        with pytest.raises(smarthome.HomeError, match="is a lock"):
            smarthome.service_for(
                self.entity("lock.front_door"), "turn_off", {}
            )
        with pytest.raises(smarthome.HomeError, match="no colour"):
            smarthome.service_for(light, "set", {"color": "plaid"})

    def test_what_is_guarded(self):
        assert smarthome.guarded(self.entity("lock.front_door"), "unlock")
        assert not smarthome.guarded(self.entity("lock.front_door"), "lock")
        assert smarthome.guarded(self.entity("cover.garage"), "open")
        assert not smarthome.guarded(self.entity("cover.garage"), "close")
        assert not smarthome.guarded(self.entity("light.kitchen"), "turn_off")


class TestTheTool:
    def test_offered_only_once_connected(self, home, monkeypatch):
        names = [t["function"]["name"] for t in tools.available_tools()]
        assert "smart_home" in names
        monkeypatch.setenv(smarthome.URL_SETTING, "")
        names = [t["function"]["name"] for t in tools.available_tools()]
        assert "smart_home" not in names

    def test_a_change_asks_first_in_ask_first_mode(self, home, monkeypatch):
        asked = []
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", False)
        monkeypatch.setattr(
            tools,
            "_user_allows",
            lambda q, ask_why=False: asked.append(q) or True,
        )
        said = tools.smart_home("turn_off", "kitchen lights")
        assert asked == ["Turn off Kitchen lights?"]
        assert home == [
            ("/api/services/light/turn_off", {"entity_id": "light.kitchen"})
        ]
        assert said.startswith("Kitchen lights (light.kitchen)")

    def test_autonomous_still_asks_to_unlock(self, home, monkeypatch):
        asked = []
        monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
        monkeypatch.setattr(tools, "ultra_auto", lambda: False)
        monkeypatch.setattr(
            tools,
            "_user_allows",
            lambda q, ask_why=False: asked.append(q) or False,
        )
        said = tools.smart_home("unlock", "front door")
        assert asked == ["Unlock Front door?"] and home == []
        assert said.startswith("The user said no")
        tools.smart_home("set", "kitchen", brightness=20)
        assert home == [
            (
                "/api/services/light/turn_on",
                {"entity_id": "light.kitchen", "brightness_pct": 20.0},
            )
        ]
        assert len(asked) == 1

    def test_reading_and_errors(self, home):
        assert "Thermostat (climate.hall)" in tools.smart_home(
            "state", "thermostat"
        )
        assert "no device called" in tools.smart_home("turn_on", "sauna")
        vault.set_secret(smarthome.TOKEN, "bad")
        assert "did not take the token" in tools.smart_home("list")


class TestConnecting:
    def test_the_page_connects_only_what_works(self, home, monkeypatch):
        from flash import ai

        monkeypatch.setattr(
            ai,
            "set_config_var",
            lambda name, value: monkeypatch.setenv(name, value),
        )
        address = smarthome.address()
        session = web.Session()
        with pytest.raises(ValueError, match="did not take the token"):
            web.command(
                session,
                {"name": "smarthome-connect", "url": address, "token": "bad"},
            )
        assert vault.get(smarthome.TOKEN) == "good"
        said = web.command(
            session,
            {"name": "smarthome-connect", "url": address, "token": "good"},
        )
        assert said["devices"] == 6 and said["said"] == "API running."
        assert (
            web.command(session, {"name": "smarthome", "arg": "check"})[
                "devices"
            ]
            == 6
        )
        web.command(session, {"name": "smarthome-off"})
        assert not smarthome.connected()
