"""Tests for flash.youtube: links, captions and storyboards, offline."""

from flash import tools, youtube


def test_video_id_forms():
    vid = "d04gm9xVWbw"
    for url in (
        vid,
        f"https://www.youtube.com/watch?v={vid}&t=3s",
        f"youtube.com/shorts/{vid}",
        f"https://youtu.be/{vid}?si=abc",
        f"https://m.youtube.com/live/{vid}",
        f"https://www.youtube.com/embed/{vid}",
    ):
        assert youtube.video_id(url) == vid, url  # nosec B101
    for url in ("https://example.com/watch?v=d04gm9xVWbw",
                "https://youtube.com/@channel", "not a link"):
        assert youtube.video_id(url) is None, url  # nosec B101


def test_player_from_page():
    page = ('<script>var ytInitialPlayerResponse = {"videoDetails": '
            '{"title": "A {b}", "lengthSeconds": "26"}};var x=1;</script>')
    player = youtube.player_from_page(page)
    assert player["videoDetails"]["title"] == "A {b}"  # nosec B101
    assert youtube.player_from_page("nothing") == {}  # nosec B101


def test_parse_captions_both_formats():
    new = ('<body><p t="654" d="2020">[music]</p><p t="5229"> </p>'
           '<p t="61000"><s>it&#39;s</s><s> live</s></p></body>')
    assert youtube.parse_captions(new) == (  # nosec B101
        "[0:00] [music]\n[1:01] it's live"
    )
    old = '<text start="2.5" dur="1">Tom &amp;amp; Jerry</text>'
    assert youtube.parse_captions(old) == "[0:02] Tom & Jerry"  # nosec B101


def test_pick_track_prefers_written_english():
    tracks = [{"languageCode": "en", "kind": "asr"},
              {"languageCode": "de"}, {"languageCode": "en-GB"}]
    assert youtube.pick_track(tracks)["languageCode"] == "en-GB"  # nosec B101
    assert youtube.pick_track([]) is None  # nosec B101


def test_storyboard_urls_spread():
    spec = ("https://i.ytimg.com/sb/ID/storyboard3_L$L/$N.jpg?sqp=x|"
            "48#27#100#10#10#0#default#rs$a|"
            "101#180#100#3#3#1000#M$M#rs$b")
    urls = youtube.storyboard_urls(spec)
    assert len(urls) == 4  # nosec B101
    assert urls[0].endswith("storyboard3_L1/M0.jpg?sqp=x&sigh=rs$b")  # nosec B101
    assert "/M11.jpg" in urls[-1]  # nosec B101  (12 sheets of 9)
    assert youtube.storyboard_urls("") == []  # nosec B101


def test_tool_registered():
    assert "watch_youtube" in tools.FUNCTIONS  # nosec B101
    assert "watch_youtube" in tools.SPARK_TOOL_NAMES  # nosec B101
    assert "watch_youtube" not in tools.SUBAGENT_TOOL_NAMES  # nosec B101
    assert tools.watch_youtube("https://example.com").startswith(  # nosec B101
        "Error"
    )
