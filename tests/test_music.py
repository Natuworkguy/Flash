# pylint: disable=C0114,C0116

import pytest

from flash import music, theme, tools, voice, web


@pytest.mark.parametrize("said, wanted", [
    ("Hey Flash, pause the music", "pause"),
    ("stop the song", "pause"),
    ("resume the music please", "resume"),
    ("play the music", "resume"),
    ("skip this", "next"),
    ("next song", "next"),
    ("turn it down a bit", "quieter"),
    ("louder", "louder"),
    ("what's the weather", ""),
    ("pause", ""),
])
def test_what_is_said_to_the_music(said, wanted):
    assert music.intent(said) == wanted


def test_pausing_the_music_is_not_pausing_voice_mode():
    heard = voice.voice_command("pause the music")
    assert heard["music"] == "pause" and not heard["pause"]
    assert voice.voice_command("resume the music")["resume"] is False
    assert voice.voice_command("pause")["pause"] is True


def test_youtube_is_found_by_search(monkeypatch):
    class Fake:
        def videos(self, query, max_results=10):
            return [{"content": "https://vimeo.com/1", "title": "no"},
                    {"content": "https://www.youtube.com/watch?v="
                                "dQw4w9WgXcQ", "title": "The song"}]

    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", Fake)
    found = music.find_youtube("a song")
    assert found == {"id": "dQw4w9WgXcQ", "title": "The song",
                     "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    assert music.find_youtube("https://youtu.be/dQw4w9WgXcQ")["id"] == \
        "dQw4w9WgXcQ"


def test_it_plays_in_the_page_and_is_paused(monkeypatch):
    session = web.Session()
    session.hub = web.Hub()
    seen = []
    session.hub.publish = seen.append
    chat = session.new_chat()
    monkeypatch.setattr(theme, "_sink", lambda: web._sink(session, chat))
    monkeypatch.setattr(music, "find_youtube", lambda q: {
        "id": "abcdefghijk", "title": "Song", "url": "u"})
    said = tools.play_music("song")
    assert said == "Playing Song in Flash's own player."
    assert session.music == {"id": "abcdefghijk", "title": "Song",
                             "playing": True}
    assert tools.play_music(action="pause") == "Paused."
    assert session.music["playing"] is False
    assert [e["action"] for e in seen if e["type"] == "music"] == [
        "play", "pause"]
    # Said by voice on a page with no player: the other page's is paused.
    web.command(session, {"name": "music", "arg": "resume"})
    assert session.music["playing"] is True
    web.command(session, {"name": "music", "arg": "stop"})
    assert session.music == {}


def test_in_the_terminal_without_mpv_it_says_how(monkeypatch):
    monkeypatch.setattr(theme, "_sink", lambda: None)
    monkeypatch.setattr(music, "find_youtube", lambda q: {
        "id": "abcdefghijk", "title": "Song", "url": "u"})
    monkeypatch.setattr(music, "terminal_can_play", lambda: False)
    assert "mpv" in tools.play_music("song")


def test_an_app_s_music_is_paused_by_the_computer(monkeypatch):
    from flash import computer

    monkeypatch.setattr(theme, "_sink", lambda: None)
    asked = []
    monkeypatch.setattr(computer, "do",
                        lambda action, value="": asked.append(action) or "ok")
    assert tools.play_music(action="pause") == "ok"
    assert tools.play_music(action="next") == "ok"
    assert asked == ["pause", "next_track"]


def test_spotify_without_a_client_opens_its_search(monkeypatch):
    opened = []
    monkeypatch.setattr(music, "_open", opened.append)
    said = music.play_spotify("blue in green")
    assert opened == ["spotify:search:blue%20in%20green"]
    assert music.SPOTIFY_ID in said
