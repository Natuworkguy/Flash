"""HTML compositions rendered to MP4 on Flash's own clock."""

import shutil
import subprocess

import pytest

from flash import tools, video

PAGE = """<html data-duration="0.5" data-width="64" data-height="32"
 data-fps="10"><body style="margin:0;background:#000">
<div style="width:8px;height:8px;background:#fff;position:absolute;
 animation:m 0.5s linear forwards"></div>
<style>@keyframes m{from{left:0}to{left:56px}}</style></body></html>"""


def test_declared_and_settled():
    said = video.declared(PAGE)
    assert said == {"duration": 0.5, "width": 64, "height": 32, "fps": 10}
    assert video.settle(0, 0, 0, 0) == (5.0, 30, 1280, 720)
    assert video.settle(999, 500, 101, 9999) == (
        video.MOST_SECONDS, video.MOST_FPS, 100, video.MOST_SIDE)


def test_missing_file_and_ffmpeg(tmp_path, monkeypatch):
    if shutil.which("ffmpeg"):
        with pytest.raises(video.VideoError, match="no file"):
            video.render(str(tmp_path / "gone.html"), tmp_path / "v.mp4")
    monkeypatch.setattr(video.shutil, "which", lambda _: None)
    with pytest.raises(video.VideoError, match="ffmpeg"):
        video.render(PAGE, tmp_path / "v.mp4")
    assert tools.make_video(PAGE, str(tmp_path / "v")).startswith("Error")


def _chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        from flash.browser import launch_chromium

        with sync_playwright() as driver:
            launch_chromium(driver).close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not shutil.which("ffmpeg") or not _chromium(),
                    reason="needs ffmpeg and Chromium")
def test_renders_the_frames(tmp_path):
    out = tools.make_video(PAGE, str(tmp_path / "clip"))
    assert out.startswith("Made ") and "64x32 at 10 fps" in out
    clip = tmp_path / "clip.mp4"
    frames = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
         str(clip)], capture_output=True, text=True, check=True)
    assert frames.stdout.strip() == "5"
