"""Maps: places, what is near them and routes, from recorded OpenStreetMap answers."""

import json

import pytest

from flash import maps, tools, visuals


@pytest.fixture
def osm(monkeypatch, tmp_path):
    asked = []

    def get(url, params=None, data=None, polite=False):
        asked.append((url, params, data))
        if "nominatim" in url:
            q = (params or {}).get("q", "")
            if "nowhere" in q:
                return []
            return [{"name": q.split(",")[0], "lat": "48.8583", "lon": "2.2945", "display_name": f"{q}, Paris, France", "type": "tower"}]
        if "overpass" in url:
            return {"elements": [
                {"lat": 48.8590, "lon": 2.2950, "tags": {"name": "Cafe Far", "opening_hours": "Mo-Su 08:00-20:00"}},
                {"lat": 48.8584, "lon": 2.2946, "tags": {"name": "Cafe Near"}},
                {"lat": 48.8585, "lon": 2.2947, "tags": {}},
            ]}
        if "/route/" in url:
            return {"code": "Ok", "routes": [{"distance": 3800, "duration": 3000, "geometry": {"coordinates": [[2.29, 48.85], [2.33, 48.86]]},
                                              "legs": [{"distance": 3800, "duration": 3000}]}]}
        raise AssertionError(url)

    monkeypatch.setattr(maps, "_get", get)
    return asked


def block_of(text):
    return json.loads(text.split("```ui\n", 1)[1].split("\n```", 1)[0])


def test_find_gives_coordinates_and_a_map(osm):
    said = maps.run("find", query="Eiffel Tower")
    assert "48.8583, 2.2945" in said
    assert block_of(said)["places"][0]["lat"] == 48.8583


def test_a_lat_lng_needs_no_lookup(osm):
    assert maps.geocode("51.5, -0.12")[0]["lng"] == -0.12
    assert not osm


def test_nearby_is_closest_first_and_skips_the_nameless(osm):
    said = maps.run("nearby", what="cafe", near="Eiffel Tower", radius=500)
    assert said.index("Cafe Near") < said.index("Cafe Far")
    assert 'amenity="cafe"' in osm[-1][2].decode().replace("%3D", "=").replace("%22", '"')
    places = block_of(said)["places"]
    assert places[0]["home"] and len(places) == 3


def test_route_times_each_leg(osm):
    said = maps.run("route", places=["Eiffel Tower", "Louvre"], mode="walking")
    assert said.startswith("Walking: 3.8 km") and "50 min" in said
    assert block_of(said)["route"]["mode"] == "walking"


def test_errors_are_said_plainly(osm):
    assert maps.run("find", query="nowhere at all").startswith("Error: no place called")
    assert maps.run("route", places=["one"]).startswith("Error:")


def test_it_is_a_tool():
    assert tools.FUNCTIONS["maps"] is tools.maps_tool
    assert any(t["function"]["name"] == "maps" for t in tools.tools)


def test_a_map_reads_in_the_terminal_and_aloud():
    text = '```ui\n{"type": "map", "title": "Walk", "route": {"mode": "walking", "summary": "4 km · 50 min"}, "places": [{"name": "A", "lat": 1, "lng": 2}]}\n```'
    assert "openstreetmap.org/?mlat=1.0&mlon=2.0" in visuals.for_terminal(text)
    assert "4 km · 50 min" in visuals.plain(text)
