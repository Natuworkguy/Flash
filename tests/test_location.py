# pylint: disable=C0114,C0116
"""The user's location, as their Apple Watch sends it."""

import pytest

from flash import location, paths, tools, web


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "FLASH_DIR", tmp_path)
    return tmp_path


def test_nothing_until_the_watch_sends():
    assert location.current() is None
    assert "Let Flash see my location" in location.describe()
    names = [t["function"]["name"] for t in tools.flash_tools()]
    assert "user_location" not in names


def test_watch_sends_then_switches_off():
    said = web.command(web.Session(), {"name": "location", "arg": "",
                                       "lat": 51.5, "lon": -0.12,
                                       "accuracy": 35})
    assert said["location"]["lat"] == 51.5
    names = [t["function"]["name"] for t in tools.flash_tools()]
    assert "user_location" in names
    text = tools.user_location_tool()
    assert "51.50000" in text and "-0.12000" in text and "35 m" in text

    assert web.command(web.Session(), {"name": "location", "arg": "off"}) == {
        "location": None}
    assert location.current() is None
    names = [t["function"]["name"] for t in tools.flash_tools()]
    assert "user_location" not in names


@pytest.mark.parametrize("lat,lon", [("x", 1), (91, 0), (0, 181),
                                     (float("nan"), 0), (None, None)])
def test_bad_places_refused(lat, lon):
    with pytest.raises(ValueError):
        web.command(web.Session(), {"name": "location", "arg": "",
                                    "lat": lat, "lon": lon})
    assert location.current() is None


def test_old_place_is_marked_stale():
    place = location.save(10, 20, 5)
    now = place["at"] + 3 * 3600
    assert "out of date" in location.describe(place, now)
    assert "out of date" not in location.describe(place, place["at"] + 60)
