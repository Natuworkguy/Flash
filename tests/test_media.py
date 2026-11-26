"""Radio and speakers: stations found by call sign, played on Home Assistant
speakers, moved between them, Apple Music's live radio recognised."""


import pytest

from flash import media


class FakeResp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


STATIONS = [
    {
        "name": "WBUR-FM HD2",
        "url_resolved": "http://hd2",
        "votes": 900,
        "codec": "MP3",
        "bitrate": 64,
    },
    {
        "name": "WBUR",
        "url_resolved": "http://wbur",
        "votes": 500,
        "codec": "AAC",
        "bitrate": 128,
    },
    {
        "name": "Classic WBURN",
        "url_resolved": "http://other",
        "votes": 5000,
        "codec": "MP3",
        "bitrate": 128,
    },
]


def test_call_sign_wins_over_votes(monkeypatch):
    monkeypatch.setattr(media.httpx, "get", lambda *a, **k: FakeResp(STATIONS))
    s = media.find_station("WBUR")
    assert s["url"] == "http://wbur"
    assert s["name"] == "WBUR"


def test_no_station(monkeypatch):
    monkeypatch.setattr(media.httpx, "get", lambda *a, **k: FakeResp([]))
    with pytest.raises(media.MediaError):
        media.find_station("ZZZZ")


def test_apple_radio_names():
    assert media.apple_radio("Apple Music 1")[0] == "Apple Music 1"
    assert media.apple_radio("beats 1")[0] == "Apple Music 1"
    assert media.apple_radio("apple music hits")[0] == "Apple Music Hits"
    assert media.apple_radio("WBUR") is None


class FakeHA:
    def __init__(self):
        self.calls = []
        self.ents = [
            {
                "entity_id": "media_player.kitchen",
                "state": "playing",
                "last_changed": "2",
                "attributes": {
                    "friendly_name": "Kitchen Speaker",
                    "media_content_id": "http://wbur",
                    "media_content_type": "music",
                    "media_title": "WBUR",
                    "volume_level": 0.3,
                },
            },
            {
                "entity_id": "media_player.bedroom",
                "state": "idle",
                "last_changed": "1",
                "attributes": {"friendly_name": "Bedroom Speaker"},
            },
            {
                "entity_id": "light.kitchen",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Light"},
            },
        ]

    def install(self, monkeypatch):
        from flash import smarthome

        monkeypatch.setattr(smarthome, "connected", lambda: True)
        monkeypatch.setattr(smarthome, "states", lambda: self.ents)
        monkeypatch.setattr(
            smarthome,
            "_call",
            lambda m, p, b=None: self.calls.append((p, b)) or {},
        )


def test_play_station_on_speaker(monkeypatch):
    ha = FakeHA()
    ha.install(monkeypatch)
    monkeypatch.setattr(media.httpx, "get", lambda *a, **k: FakeResp(STATIONS))
    monkeypatch.setattr(media, "on_mac", lambda: False)
    said = media.play("WBUR", "bedroom")
    assert "Bedroom Speaker" in said
    path, body = ha.calls[-1]
    assert path.endswith("media_player/play_media")
    assert body["entity_id"] == "media_player.bedroom"
    assert body["media_content_id"] == "http://wbur"


def test_unknown_speaker_lists_known(monkeypatch):
    ha = FakeHA()
    ha.install(monkeypatch)
    monkeypatch.setattr(media.httpx, "get", lambda *a, **k: FakeResp(STATIONS))
    monkeypatch.setattr(media, "on_mac", lambda: False)
    with pytest.raises(media.MediaError) as e:
        media.play("WBUR", "garage")
    assert "Kitchen Speaker" in str(e.value)


def test_move_to_another_speaker(monkeypatch):
    ha = FakeHA()
    ha.install(monkeypatch)
    monkeypatch.setattr(media, "on_mac", lambda: False)
    said = media.move("bedroom")
    assert "from Kitchen Speaker to Bedroom Speaker" in said
    paths = [p for p, _ in ha.calls]
    assert paths == [
        "/api/services/media_player/volume_set",
        "/api/services/media_player/play_media",
        "/api/services/media_player/media_stop",
    ]
    assert ha.calls[1][1]["media_content_id"] == "http://wbur"
    assert ha.calls[2][1]["entity_id"] == "media_player.kitchen"


def test_move_nothing_playing(monkeypatch):
    ha = FakeHA()
    ha.ents[0]["state"] = "idle"
    ha.install(monkeypatch)
    monkeypatch.setattr(media, "on_mac", lambda: False)
    with pytest.raises(media.MediaError):
        media.move("bedroom")


def test_default_device_setting(monkeypatch):
    ha = FakeHA()
    ha.install(monkeypatch)
    monkeypatch.setattr(media.httpx, "get", lambda *a, **k: FakeResp(STATIONS))
    monkeypatch.setattr(media, "on_mac", lambda: False)
    monkeypatch.setenv(media.DEFAULT_DEVICE, "kitchen")
    assert "Kitchen Speaker" in media.play("WBUR")


def test_apple_radio_needs_a_mac(monkeypatch):
    monkeypatch.setattr(media, "on_mac", lambda: False)
    with pytest.raises(media.MediaError):
        media.play("Apple Music 1", "", "apple_music_radio")


def test_airplay_choice(monkeypatch):
    sent = []
    monkeypatch.setattr(media, "on_mac", lambda: True)
    monkeypatch.setattr(
        media,
        "airplay_devices",
        lambda: ["Computer", "Living Room", "Kitchen HomePod"],
    )
    monkeypatch.setattr(media, "_osa", lambda s: sent.append(s) or "")
    assert media.set_airplay(["kitchen"]) == "Kitchen HomePod"
    assert 'AirPlay device "Kitchen HomePod"' in sent[-1]
    with pytest.raises(media.MediaError):
        media.set_airplay(["garage"])


def test_tool_routes_radio(monkeypatch):
    from flash import tools

    got = {}
    monkeypatch.setattr(
        media,
        "play",
        lambda q, d, s: (
            got.update(q=q, d=d, s=s) or "Playing WBUR on Kitchen."
        ),
    )
    out = tools.play_music("WBUR", "radio", "play", device="kitchen")
    assert out.startswith("Playing")
    assert got == {"q": "WBUR", "d": "kitchen", "s": "radio"}
    monkeypatch.setattr(media, "move", lambda d: f"Moved to {d}.")
    assert (
        tools.play_music("", "radio", "move", device="bedroom")
        == "Moved to bedroom."
    )
