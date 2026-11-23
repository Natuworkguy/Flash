"""Siri's voices, to speak with on a Mac.

macOS keeps its Siri voices out of `say` and AppleScript: `say -v` quietly
speaks in the default voice for a name it does not know, and AppleScript's
`say using` fails with "Voice wasn't found". The voices are there, though,
for AVSpeechSynthesizer, but only to Apple's own signed programs. So Flash
runs a small helper (siri_voice.swift) with `swift`, which is one, keeps it
running, and asks it for each line as audio.

The voices are the ones downloaded under System Settings, Accessibility,
Spoken Content, System Voice, Manage Voices. Only those installed are
offered. Hearing Siri is not here: macOS has no way to use Siri to listen.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess  # nosec B404 -- our own helper, run from our source
import threading
import time
from pathlib import Path
from typing import Optional

SOURCE = Path(__file__).with_name("siri_voice.swift")
PREFIX = "com.apple.siri."
# A voice is named siri-<what follows PREFIX>: siri-natural.Nora.
NAME = "siri-"
HOW_TO_GET = (
    "No Siri voices on this Mac yet. Download one in System Settings, "
    "Accessibility, Spoken Content, System Voice, Manage Voices."
)
NO_TOOLS = (
    "Siri voices need Xcode's command line tools. Run xcode-select "
    "--install in a terminal."
)
LIST_SECONDS = 30
SAY_SECONDS = 60
# How long the helper stays running after its last line.
IDLE_SECONDS = 300
# How long a list of the voices is trusted: a voice can be downloaded while
# Flash is open.
LIST_KEPT = 30

_lock = threading.Lock()
_helper: Optional[_Helper] = None
_listed: tuple[float, list[dict]] = (0.0, [])
_tools: Optional[bool] = None


class SiriError(Exception):
    pass


def is_siri(name: str) -> bool:
    return str(name or "").startswith(NAME)


def identifier(name: str) -> str:
    """The voice's own identifier, from its Flash name."""

    return PREFIX + str(name)[len(NAME):]


def name_of(voice_id: str) -> str:
    return NAME + voice_id[len(PREFIX):]


def tools_installed() -> bool:
    """Whether Xcode's command line tools are here. Asked first: with
    none, running `swift` opens macOS's install prompt."""

    global _tools

    if platform.system() != "Darwin" or not shutil.which("swift"):
        return False
    if _tools is None:
        try:
            _tools = subprocess.run(  # nosec B603 B607 -- fixed command
                ["xcode-select", "-p"], capture_output=True, timeout=10,
                check=False).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _tools = False
    return _tools


def _swift() -> list[str]:
    if not tools_installed():
        raise SiriError(NO_TOOLS)
    return [shutil.which("swift") or "swift", str(SOURCE)]


class _Helper:
    """The running helper: a line out, an answer back."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(  # nosec B603 -- our own source
            _swift(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.used = time.monotonic()
        threading.Thread(target=self._retire, daemon=True).start()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _retire(self) -> None:
        while self.alive():
            time.sleep(15)
            if time.monotonic() - self.used > IDLE_SECONDS:
                with _lock:
                    self.stop()
                return

    def stop(self) -> None:
        global _helper

        if _helper is self:
            _helper = None
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def ask(self, request: dict, seconds: int) -> tuple[dict, bytes]:
        # A helper that stops answering is ended, which ends the read.
        watchdog = threading.Timer(seconds, self.proc.kill)
        watchdog.start()
        try:
            self.proc.stdin.write((json.dumps(request) + "\n").encode())
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                raise SiriError("The Siri voice helper stopped.")
            head = json.loads(line)
            audio = self.proc.stdout.read(int(head.get("bytes") or 0))
        except (OSError, ValueError) as exc:
            raise SiriError(f"The Siri voice helper failed: {exc}") from None
        finally:
            watchdog.cancel()
            self.used = time.monotonic()
        if not head.get("ok"):
            raise SiriError(str(head.get("error") or "Siri could not speak."))
        return head, audio


def _ask(request: dict, seconds: int) -> tuple[dict, bytes]:
    global _helper

    with _lock:
        if _helper is None or not _helper.alive():
            _helper = _Helper()
        return _helper.ask(request, seconds)


def voices(refresh: bool = False) -> list[dict]:
    """The Siri voices installed: {"id", "name" (Flash's), "label",
    "language"}. [] anywhere but a Mac with the command line tools."""

    global _listed

    if not tools_installed():
        return []
    when, found = _listed
    if not refresh and time.monotonic() - when < LIST_KEPT and when:
        return found
    try:
        head, _ = _ask({"cmd": "list"}, LIST_SECONDS)
    except SiriError:
        # Not asked again at once: it would only fail the same way.
        _listed = (time.monotonic(), found)
        return found
    found = []
    for item in head.get("voices") or []:
        voice_id = str(item.get("id") or "")
        if not voice_id.startswith(PREFIX) or not re.fullmatch(
                r"[A-Za-z0-9._-]+", voice_id[len(PREFIX):]):
            continue
        found.append({
            "id": voice_id, "name": name_of(voice_id),
            "label": f"Siri {item.get('name') or voice_id.rsplit('.', 1)[-1]}"
                     f", {item.get('language') or ''}".rstrip(", "),
            "language": str(item.get("language") or ""),
        })
    found.sort(key=lambda v: (not v["language"].startswith("en"),
                              v["language"], v["label"]))
    _listed = (time.monotonic(), found)
    return found


def installed(name: str) -> bool:
    return is_siri(name) and any(v["name"] == name for v in voices())


def note() -> str:
    """What to tell the user when there are no Siri voices to offer, or ""
    when there are, or off a Mac."""

    if platform.system() != "Darwin":
        return ""
    if not tools_installed():
        return NO_TOOLS
    return "" if voices() else HOW_TO_GET


def speak_pcm(name: str, text: str, pace: float = 1.0) -> tuple[bytes, int]:
    """TEXT in voice NAME: 16-bit mono audio, and its sample rate. A pace
    above 1 is slower."""

    rate = min(0.65, max(0.3, 0.5 / max(pace, 0.5)))
    head, audio = _ask({"cmd": "say", "voice": identifier(name),
                        "text": text, "rate": rate}, SAY_SECONDS)
    return audio, int(head.get("rate") or 48000)


def stop() -> None:
    """End the helper, if it is running."""

    with _lock:
        if _helper is not None:
            _helper.stop()
