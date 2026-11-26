"""Videos made from web pages: an HTML composition rendered to MP4.

A page that animates (CSS animations and transitions, the Web
Animations API, requestAnimationFrame, a canvas, <video>) is played
frame by frame on a clock Flash holds, not the real one: before each
frame the clock is set to that frame's time, every animation is moved
there, and the page's own window.flashSeek(seconds), if it has one, is
called to draw it. So each frame is exact however slow the computer is,
and a 60-frame-a-second video of a heavy page comes out smooth. The
frames go straight to ffmpeg.

A composition can say its own length and size: <html data-duration="6"
data-width="1280" data-height="720">, or the same as meta tags.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Optional

MOST_SECONDS = 120
MOST_FPS = 60
MOST_SIDE = 3840
SIZE = (1280, 720)
NAVIGATION_TIMEOUT_MS = 30000

# Runs before the page's own scripts: time is whatever Flash says.
CLOCK = r"""
(() => {
  let now = 0;
  let queue = [];
  let nextId = 1;
  const start = Date.UTC(2026, 0, 1);
  const RealDate = Date;
  const realTimeout = window.setTimeout.bind(window);
  performance.now = () => now;
  class FrozenDate extends RealDate {
    constructor(...args) { if (args.length) super(...args);
                           else super(start + now); }
    static now() { return start + now; }
  }
  window.Date = FrozenDate;
  window.requestAnimationFrame = (fn) => {
    const id = nextId++; queue.push([id, fn]); return id;
  };
  window.cancelAnimationFrame = (id) => {
    queue = queue.filter(([i]) => i !== id);
  };
  const timers = [];
  window.setTimeout = (fn, ms = 0, ...a) => {
    const id = nextId++;
    timers.push({id, at: now + Number(ms || 0), fn, a, every: 0});
    return id;
  };
  window.setInterval = (fn, ms = 0, ...a) => {
    const id = nextId++;
    timers.push({id, at: now + Number(ms || 0), fn, a,
                 every: Math.max(1, Number(ms || 0))});
    return id;
  };
  window.clearTimeout = window.clearInterval = (id) => {
    const i = timers.findIndex(t => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  };
  window.__flashFrame = async (ms) => {
    now = ms;
    for (let guard = 0; guard < 1000; guard++) {
      const due = timers.filter(t => t.at <= now)
                        .sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      if (due.every) due.at += due.every;
      else timers.splice(timers.indexOf(due), 1);
      try { typeof due.fn === 'function' && due.fn(...due.a); }
      catch (e) { console.error(e); }
    }
    for (const a of document.getAnimations()) {
      try { a.pause(); a.currentTime = ms; } catch (e) {}
    }
    const waits = [];
    for (const v of document.querySelectorAll('video')) {
      v.pause();
      const at = Math.min(ms / 1000, (v.duration || Infinity) - 0.001);
      if (Math.abs(v.currentTime - at) > 0.001) {
        waits.push(new Promise(r => {
          v.addEventListener('seeked', r, {once: true});
          v.currentTime = at;
        }));
      }
    }
    if (typeof window.flashSeek === 'function') {
      await window.flashSeek(ms / 1000);
    }
    const ran = queue; queue = [];
    for (const [, fn] of ran) {
      try { fn(ms); } catch (e) { console.error(e); }
    }
    // A <video> that never says it has seeked holds a frame 2 s at most.
    await Promise.race([Promise.all(waits),
                        new Promise(r => realTimeout(r, 2000))]);
    for (const img of document.images) {
      if (!img.complete) await img.decode().catch(() => {});
    }
  };
})();
"""


class VideoError(Exception):
    """A video that could not be made, in words."""


def _number(found: Optional[str]) -> float:
    try:
        return float(found) if found else 0.0
    except ValueError:
        return 0.0


def declared(html: str) -> dict:
    """The length and size the composition gives for itself, if any."""

    said: dict = {}
    for key in ("duration", "width", "height", "fps"):
        found = re.search(rf'data-{key}\s*=\s*["\']?([\d.]+)', html) or \
            re.search(rf'<meta[^>]+name=["\']{key}["\'][^>]+content=["\']'
                      rf'([\d.]+)', html, re.I)
        if found and _number(found.group(1)):
            said[key] = _number(found.group(1))
    return said


def settle(seconds: float, fps: float, width: int, height: int
           ) -> tuple[float, int, int, int]:
    """The length, frame rate and size, kept to what is sensible:
    ffmpeg's encoder wants even sides."""

    seconds = max(0.1, min(MOST_SECONDS, float(seconds or 5)))
    fps = int(max(1, min(MOST_FPS, round(float(fps or 30)))))
    width = max(16, min(MOST_SIDE, int(width or SIZE[0]))) // 2 * 2
    height = max(16, min(MOST_SIDE, int(height or SIZE[1]))) // 2 * 2
    return seconds, fps, width, height


def render(source: str, out: Path, seconds: float = 0, fps: float = 0,
           width: int = 0, height: int = 0) -> dict:
    """SOURCE (a path to an .html file, or the HTML itself) as an MP4 at
    OUT. What the composition declares fills in what is not given."""

    if not shutil.which("ffmpeg"):
        raise VideoError("Making a video needs ffmpeg: install it "
                         "(brew install ffmpeg, apt install ffmpeg, or "
                         "winget install ffmpeg).")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        from .browser import launch_chromium
    except ImportError as exc:
        raise VideoError("Making a video needs Playwright: pip install "
                         "playwright, then playwright install chromium"
                         ) from exc
    path = Path(source).expanduser() if len(source) < 1024 and \
        "\n" not in source and source.strip().lower().endswith(
            (".html", ".htm")) else None
    if path is not None and not path.is_file():
        raise VideoError(f"There is no file at {path}.")
    html = path.read_text(encoding="utf-8", errors="replace") if path \
        else source
    said = declared(html)
    seconds, fps, width, height = settle(
        seconds or said.get("duration", 0), fps or said.get("fps", 0),
        width or said.get("width", 0), height or said.get("height", 0))
    frames = max(1, round(seconds * fps))
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    encoder = subprocess.Popen(  # nosec B603 B607 -- a fixed program
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe",
         "-framerate", str(fps), "-c:v", "png", "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags",
         "+faststart", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    # Flash's clock is set up as a page opens, so HTML given as text is
    # opened from a file too.
    scratch = None
    page_path = path
    if page_path is None:
        scratch = tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8")
        scratch.write(html)
        scratch.close()
        page_path = Path(scratch.name)
    try:
        with sync_playwright() as driver:
            browser = launch_chromium(driver)
            try:
                page = browser.new_page(viewport={"width": width,
                                                  "height": height})
                page.add_init_script(CLOCK)
                page.goto(page_path.resolve().as_uri(), wait_until="load",
                          timeout=NAVIGATION_TIMEOUT_MS)
                for n in range(frames):
                    page.evaluate("ms => window.__flashFrame(ms)",
                                  n * 1000 / fps)
                    encoder.stdin.write(page.screenshot(type="png"))
            finally:
                browser.close()
    except PlaywrightError as exc:
        encoder.kill()
        raise VideoError(f"The page could not be played: {exc}") from None
    except BrokenPipeError:
        pass
    finally:
        if encoder.stdin:
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
        if scratch is not None:
            Path(scratch.name).unlink(missing_ok=True)
    err = encoder.stderr.read() if encoder.stderr else b""
    encoder.wait(timeout=300)
    if encoder.returncode:
        raise VideoError("ffmpeg could not make it: "
                         + err.decode(errors="replace").strip()[-300:])
    return {"path": str(out), "seconds": seconds, "fps": fps,
            "frames": frames, "width": width, "height": height}
