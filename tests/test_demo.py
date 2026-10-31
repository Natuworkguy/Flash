"""Tests for learning a task from a screen recording."""

import shutil
import subprocess  # nosec B404

import pytest

from flash import demo, tools

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="needs ffmpeg")


@pytest.fixture
def recording(tmp_path):
    """Three seconds of screen: red, then blue, then green, with a tone
    under it."""

    out = tmp_path / "demo.mp4"
    subprocess.run(  # nosec B603 B607
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=red:s=320x240:d=1:r=10",
         "-f", "lavfi", "-i", "color=blue:s=320x240:d=1:r=10",
         "-f", "lavfi", "-i", "color=green:s=320x240:d=1:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-map", "3:a", "-pix_fmt", "yuv420p", str(out)],
        check=True, timeout=60,
    )
    return out


def test_thin_keeps_the_ends():
    assert demo.thin(list(range(10)), 4) == [0, 3, 6, 9]
    assert demo.thin([1, 2], 4) == [1, 2]


def test_words_go_with_the_still_they_were_said_over():
    stills = [{"at": 0.0, "said": ""}, {"at": 2.0, "said": ""}]
    pcm = b"\0" * demo.SAMPLE_RATE * 2 * 3
    heard = []

    def hear(chunk):
        heard.append(len(chunk))
        return f"part {len(heard)}", ""

    assert demo.said_over(stills, pcm, hear) == ""
    assert [s["said"] for s in stills] == ["part 1", "part 2"]
    assert heard == [demo.SAMPLE_RATE * 4, demo.SAMPLE_RATE * 2]
    assert demo.said_over(stills, pcm, lambda c: ("", "no model")) == \
        "no model"
    said = demo.note("x.mov", stills)
    assert '1. at 0:00, the user saying: "part 1"' in said
    assert "skill_manage" in said


@needs_ffmpeg
def test_stills_come_as_the_screen_changes(recording):
    stills, why = demo.read(recording, lambda pcm: ("next I click", ""))
    assert why == ""
    assert [round(s["at"]) for s in stills] == [0, 1, 2]
    assert all(s["jpeg"].startswith(b"\xff\xd8") for s in stills)
    assert stills[0]["said"] == "next I click"


def test_what_is_not_a_recording_is_refused(tmp_path):
    with pytest.raises(demo.DemoError, match="no file"):
        demo.read(tmp_path / "missing.mp4")
    text = tmp_path / "notes.txt"
    text.write_text("x")
    with pytest.raises(demo.DemoError, match="not a video"):
        demo.read(text)


@needs_ffmpeg
def test_the_tool_shows_the_model_the_stills(recording, monkeypatch):
    monkeypatch.setattr(tools, "model_sees_images", lambda host, model: True)
    monkeypatch.setattr("flash.voice.transcribe", lambda pcm: ("", "none"))
    tools.take_pending_images()
    said = tools.learn_from_recording(str(recording))
    assert said.startswith(f"Attached screen recording: {recording}.")
    assert "could not be heard: none" in said
    assert len(tools.take_pending_images()) == 3
    monkeypatch.setattr(tools, "model_sees_images", lambda host, model: False)
    assert "cannot see images" in tools.learn_from_recording(str(recording))
